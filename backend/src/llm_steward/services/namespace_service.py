"""Namespaces and API keys (design §7, the P1 subset).

Key format `stw_<prefix>_<secret>`: the prefix is stored in clear for lookup, the secret only as
sha256. Keys and namespaces are created from the CLI, never from an HTTP route.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, NotFound
from llm_steward.models import ApiKey, Namespace
from llm_steward.models.base import utcnow
from llm_steward.models.enums import ActorType, ApiScope
from llm_steward.services.governance_service import Actor

KEY_PREFIX = "stw"
DEFAULT_SCOPES = (ApiScope.READ, ApiScope.WRITE)
# `last_used_at` is informational; writing it on every request would turn reads into writes.
LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class Principal:
    namespace_id: uuid.UUID
    key_id: uuid.UUID
    scopes: frozenset[str]

    @property
    def actor(self) -> Actor:
        """Writes made with an API key are attributed to that key (the host app's server). A key
        with the `review` scope may also review (decided 2026-11-09, until Admin login in P5)."""
        return Actor(
            ActorType.HOST_APP, f"api_key:{self.key_id}", can_review=ApiScope.REVIEW in self.scopes
        )

    def acting_for(self, user_id: str) -> Actor:
        """The end user the host app speaks for (design §7: the key-holding server is trusted with
        user ids). REST and MCP both build it here."""
        return Actor(ActorType.END_USER, user_id)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_key(token: str) -> tuple[str, str] | None:
    """`stw_<prefix>_<secret>` → (prefix, secret); anything else → None."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


class NamespaceService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_namespace(self, slug: str, name: str | None = None) -> Namespace:
        async with self._sessions() as s:
            namespace = Namespace(slug=slug, name=name or slug)
            s.add(namespace)
            try:
                await s.commit()
            except IntegrityError as exc:
                raise Conflict(f"namespace {slug!r} already exists") from exc
            return namespace

    async def create_key(
        self, slug: str, scopes: tuple[ApiScope, ...] = DEFAULT_SCOPES, name: str = ""
    ) -> str:
        """Returns the plaintext key — the only time it exists outside the caller's hands."""
        async with self._sessions() as s, s.begin():
            namespace = await s.scalar(select(Namespace).where(Namespace.slug == slug))
            if namespace is None:
                raise NotFound(f"namespace {slug!r} not found")
            prefix, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
            s.add(
                ApiKey(
                    namespace_id=namespace.id,
                    name=name,
                    prefix=prefix,
                    key_hash=_hash(secret),
                    scopes=[str(sc) for sc in scopes],
                )
            )
        return f"{KEY_PREFIX}_{prefix}_{secret}"

    async def revoke_key(self, prefix: str) -> None:
        async with self._sessions() as s, s.begin():
            key = await s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
            if key is None:
                raise NotFound(f"API key {prefix!r} not found")
            key.revoked_at = key.revoked_at or utcnow()

    async def authenticate(self, token: str) -> Principal | None:
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
            now = utcnow()
            if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
                key.last_used_at = now
            return Principal(key.namespace_id, key.id, frozenset(key.scopes))
