"""Namespaces, API keys and user tokens (design §7).

Key format `stw_<prefix>_<secret>`: the prefix is stored in clear for lookup, the secret only as
sha256. Namespaces are created from the CLI (`steward ns create` / `ns apply`); keys from the CLI or
the Admin (`/admin/v1`, behind the admin token), never from a `/v1` route.

A user token (P5) is a short-lived HS256 JWT a host app's server mints with its key for one end
user, so a browser can call llm-steward directly. It names the key that issued it (`kid`) and is
checked against that key on every use: revoking the key ends its tokens. It carries the key's
`read` / `write` scopes at most (never `review`), and the principal it yields is pinned to its
user (`Principal.pin_user`); which routes accept it at all is decided in `api/deps.py`.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal, overload

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, Forbidden, NotFound, StewardError, Unprocessable
from llm_steward.models import ApiKey, Namespace
from llm_steward.models.base import utcnow
from llm_steward.models.enums import ActorType, ApiScope
from llm_steward.namespace_config import NamespaceConfig
from llm_steward.services.governance_service import Actor

KEY_PREFIX = "stw"
DEFAULT_SCOPES = (ApiScope.READ, ApiScope.WRITE)
# `last_used_at` is informational; writing it on every request would turn reads into writes.
LAST_USED_RESOLUTION = timedelta(minutes=1)

# Where a request's resolved caller is kept in the ASGI scope's state (REST routes, the
# idempotency middleware and the MCP tools all read it there).
PRINCIPAL_STATE_KEY = "steward_principal"

TOKEN_ISSUER = "llm-steward"
TOKEN_AUDIENCE = "llm-steward:user"
TOKEN_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(minutes=15)  # the default; at most TOKEN_MAX_TTL
TOKEN_MAX_TTL = timedelta(hours=1)
USER_TOKEN_SCOPES = frozenset({ApiScope.READ, ApiScope.WRITE})
_REQUIRED_CLAIMS = ["iss", "aud", "sub", "ns", "kid", "iat", "exp", "jti"]


class UserTokensDisabled(StewardError):
    status = 503
    code = "user_tokens_disabled"


@dataclass(frozen=True)
class IssuedToken:
    token: str
    user_id: str
    expires_at: datetime


@dataclass(frozen=True)
class Principal:
    namespace_id: uuid.UUID
    key_id: uuid.UUID  # the API key, or the key that issued the user token
    scopes: frozenset[str]
    user_id: str | None = None  # set: a user token, pinned to this end user

    @property
    def actor(self) -> Actor:
        """Writes made with an API key are attributed to that key (the host app's server); a key
        with the `review` scope may also review (decided 2026-11-09; Admin has no login). A user
        token acts as its end user, who never reviews."""
        if self.user_id is not None:
            return Actor(ActorType.END_USER, self.user_id)
        return Actor(
            ActorType.HOST_APP, f"api_key:{self.key_id}", can_review=ApiScope.REVIEW in self.scopes
        )

    def for_user(self, user_id: str) -> "Principal":
        """This key's caller acting for one end user only: a user token's principal (no more
        than `read` and `write`, never `review`)."""
        return replace(self, user_id=user_id, scopes=self.scopes & USER_TOKEN_SCOPES)

    def pin_user(self, requested: str | None) -> str | None:
        """The user a request may concern. An API key's server is trusted with any user id
        (design §7); a user token concerns its own user only: none given means that user, any
        other is refused."""
        if self.user_id is None:
            return requested
        if requested not in (None, self.user_id):
            raise Forbidden("a user token only reaches its own user's data")
        return self.user_id

    def acting_for(self, user_id: str) -> Actor:
        """The end user the host app speaks for (design §7: the key-holding server is trusted with
        user ids); under a user token, only its own user. REST and MCP both build it here."""
        self.pin_user(user_id)  # refuses another user under a user token
        return Actor(ActorType.END_USER, user_id)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_key(token: str) -> tuple[str, str] | None:
    """`stw_<prefix>_<secret>` → (prefix, secret); anything else → None."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


@overload
async def _by_slug(s: AsyncSession, slug: str) -> Namespace: ...
@overload
async def _by_slug(
    s: AsyncSession, slug: str, *, missing_ok: Literal[True]
) -> Namespace | None: ...
async def _by_slug(s: AsyncSession, slug: str, *, missing_ok: bool = False) -> Namespace | None:
    namespace = await s.scalar(select(Namespace).where(Namespace.slug == slug))
    if namespace is None and not missing_ok:
        raise NotFound(f"namespace {slug!r} not found")
    return namespace


class NamespaceService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], token_secret: str | None = None
    ) -> None:
        self._sessions = sessions
        self._token_secret = token_secret

    async def create_namespace(self, slug: str, name: str | None = None) -> Namespace:
        async with self._sessions() as s:
            namespace = Namespace(slug=slug, name=name or slug)
            s.add(namespace)
            try:
                await s.commit()
            except IntegrityError as exc:
                raise Conflict(f"namespace {slug!r} already exists") from exc
            return namespace

    async def apply_config(
        self, slug: str, config: dict[str, Any] | None, name: str | None = None
    ) -> tuple[Namespace, bool]:
        """Config as code (`steward ns apply`, design §8): validate, then create the namespace or
        replace its whole config. Returns the namespace and whether it was created."""
        parsed = NamespaceConfig.parse(config)  # before any write: a bad file changes nothing
        async with self._sessions() as s, s.begin():
            namespace = await _by_slug(s, slug, missing_ok=True)
            created = namespace is None
            if namespace is None:
                namespace = Namespace(slug=slug, name=name or slug)
                s.add(namespace)
            elif name:
                namespace.name = name
            namespace.config = parsed.dump()
        return namespace, created

    async def get_namespace(self, slug: str) -> Namespace:
        async with self._sessions() as s:
            return await _by_slug(s, slug)

    async def create_key(
        self, slug: str, scopes: tuple[ApiScope, ...] = DEFAULT_SCOPES, name: str = ""
    ) -> str:
        """Returns the plaintext key — the only time it exists outside the caller's hands."""
        _, key = await self.issue_key(slug, scopes, name)
        return key

    async def issue_key(
        self, slug: str, scopes: tuple[ApiScope, ...] = DEFAULT_SCOPES, name: str = ""
    ) -> tuple[ApiKey, str]:
        """The stored key (only its hash) and the plaintext key, shown to the caller once."""
        async with self._sessions() as s, s.begin():
            namespace = await _by_slug(s, slug)
            prefix, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
            row = ApiKey(
                namespace_id=namespace.id,
                name=name,
                prefix=prefix,
                key_hash=_hash(secret),
                scopes=[str(sc) for sc in scopes],
            )
            s.add(row)
            await s.flush()
        return row, f"{KEY_PREFIX}_{prefix}_{secret}"

    async def revoke_key(self, prefix: str) -> None:
        async with self._sessions() as s, s.begin():
            key = await s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
            if key is None:
                raise NotFound(f"API key {prefix!r} not found")
            key.revoked_at = key.revoked_at or utcnow()

    async def authenticate(self, token: str) -> Principal | None:
        """An API key (`stw_…`) or a user token; None for anything invalid, expired or revoked."""
        if token.startswith(f"{KEY_PREFIX}_"):
            return await self._api_key(token)
        return await self._user_token(token)

    async def issue_user_token(
        self, principal: Principal, user_id: str, ttl: timedelta = TOKEN_TTL
    ) -> IssuedToken:
        """A token for one end user, minted with an API key (never with another user token)."""
        if self._token_secret is None:
            raise UserTokensDisabled("user tokens are not configured (STEWARD_TOKEN_SECRET)")
        if principal.user_id is not None:
            raise Forbidden("a user token cannot mint tokens")
        if not timedelta(0) < ttl <= TOKEN_MAX_TTL:
            raise Unprocessable(f"a user token lives at most {TOKEN_MAX_TTL}")
        now = utcnow().replace(microsecond=0)
        expires_at = now + ttl
        claims = {
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "sub": user_id,
            "ns": str(principal.namespace_id),
            "kid": str(principal.key_id),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": secrets.token_urlsafe(12),
        }
        token = jwt.encode(claims, self._token_secret, algorithm=TOKEN_ALGORITHM)
        return IssuedToken(token, user_id, expires_at)

    async def _api_key(self, token: str) -> Principal | None:
        parsed = parse_key(token)
        if parsed is None:
            return None
        prefix, secret = parsed
        async with self._sessions() as s, s.begin():
            key = await s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
            if key is None or key.revoked_at is not None:
                return None
            if not hmac.compare_digest(key.key_hash, _hash(secret)):
                return None
            _touch(key)
            return Principal(key.namespace_id, key.id, frozenset(key.scopes))

    async def _user_token(self, token: str) -> Principal | None:
        if self._token_secret is None:
            return None
        try:
            claims = jwt.decode(
                token,
                self._token_secret,
                algorithms=[TOKEN_ALGORITHM],  # pinned: never `none`, never one the token names
                audience=TOKEN_AUDIENCE,
                issuer=TOKEN_ISSUER,
                options={"require": _REQUIRED_CLAIMS},
            )
            key_id, namespace_id = uuid.UUID(claims["kid"]), uuid.UUID(claims["ns"])
            user_id = claims["sub"]
        except (jwt.InvalidTokenError, ValueError, TypeError):
            return None
        if not isinstance(user_id, str) or not 0 < len(user_id) <= 200:
            return None
        async with self._sessions() as s, s.begin():
            key = await s.get(ApiKey, key_id)
            if key is None or key.revoked_at is not None or key.namespace_id != namespace_id:
                return None
            _touch(key)
            return Principal(key.namespace_id, key.id, frozenset(key.scopes)).for_user(user_id)


def _touch(key: ApiKey) -> None:
    now = utcnow()
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
        key.last_used_at = now
