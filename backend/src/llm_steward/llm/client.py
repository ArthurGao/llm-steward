"""Structured LLM calls through lite-model (its design §12). `LiteModelLLM` keeps llm-steward's
`LLM` Protocol (system and user text in, a validated reply and its provenance out) over a lite-model
`AsyncModel`; the backends, strict schemas, retries and provenance are lite-model's."""

import contextlib
import os
from typing import TYPE_CHECKING, Protocol

import lite_model
from lite_model import AsyncLiteModel, AsyncModel, Message, call_context
from lite_model.config import resolve, target
from pydantic import BaseModel

from llm_steward.config import Settings
from llm_steward.llm.roles import ROLES, LLMConfigError, Provenance, Role
from llm_steward.storage.db import libpq_url

if TYPE_CHECKING:  # the `cache` extra; imported only when the cache is on
    from lite_cache import Policy
    from lite_cache.store import PgStore


class LLMError(RuntimeError):
    """The call failed after lite-model's retries. The message never carries upstream text."""


class LLMOutputError(LLMError):
    """The model answered, but not with what the role's schema requires (or cut off)."""


_OUTPUT = (lite_model.Truncated, lite_model.EmptyReply, lite_model.BadStructuredReply)


class LLM(Protocol):
    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T], *, cache_scope: str | None = None
    ) -> tuple[T, Provenance]:
        """`cache_scope` (e.g. `steward/<namespace id>`): a call of a `CACHED_ROLES` role may be
        answered from the response cache when it is on (design §11 P10); answers never cross
        scopes. None, or another role: never."""
        ...


# The roles whose answers may be reused (design §11 P10): kb.risk labels one entry's text, which
# comes back unchanged on revise and re-accept. Not kb.worthiness (one prompt per batch: the key
# is the batch, which almost never repeats), kb.draft (a rewrite) or the memory roles.
CACHED_ROLES = frozenset({Role.KB_RISK})


def cache_scope(namespace_id: object) -> str:
    """The cache scope of one namespace: no answer is shared between namespaces."""
    return f"steward/{namespace_id}"


class LiteModelLLM:
    def __init__(self, model: AsyncModel, cache: "Policy | None" = None) -> None:
        """`cache`: the lite-cache `Policy` of `CACHED_ROLES` calls that name a `cache_scope`,
        when a cache hook is registered on `model`; None: no call is cached."""
        self._model = model
        self._cache = cache

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T], *, cache_scope: str | None = None
    ) -> tuple[T, Provenance]:
        messages = [Message("system", system), Message("user", user)]
        cached = (
            call_context(scope=cache_scope, cache=self._cache)
            if cache_scope is not None and self._cache is not None and role in CACHED_ROLES
            else contextlib.nullcontext()
        )
        try:
            with cached:
                reply = await self._model.structured(role.value, messages, schema)
        except _OUTPUT as exc:
            raise LLMOutputError(str(exc)) from exc
        except lite_model.ModelError as exc:
            raise LLMError(str(exc)) from exc
        p = reply.provenance
        # the model that answered, as the endpoint reports it; the requested one when it does not
        # say (what llm-steward recorded before lite-model)
        return reply.value, Provenance(role.value, p.answered or p.requested)


def role_llm(
    settings: Settings, roles: tuple[Role, ...], cache_store: "PgStore | None" = None
) -> LiteModelLLM:
    """The LLM for `roles`, the ones that will be used. Every configured role is checked now (an
    unknown role, a missing key, a missing extra): a misconfiguration fails at startup.
    `cache_store` (`open_cache_store`): lite-cache's hook is registered over it."""
    model = _build(settings, roles)
    return LiteModelLLM(model, cache=register_cache(model, cache_store, settings.cache.ttl_s))


def _build(settings: Settings, roles: tuple[Role, ...]) -> AsyncLiteModel:
    """lite-model over llm-steward's roles; a missing model or a bad config fails here."""
    if settings.llm is None:
        names = ", ".join(r.value for r in roles)
        raise LLMConfigError(
            f"no model for LLM roles {names}: set STEWARD_LLM__BACKEND and STEWARD_LLM__MODEL"
        )
    try:
        return lite_model.build_async(settings.llm, ROLES)
    except lite_model.ConfigError as exc:
        raise LLMConfigError(str(exc)) from exc


def register_cache(
    model: AsyncLiteModel, store: "PgStore | None", ttl_s: int | None
) -> "Policy | None":
    """lite-cache's hook on `model` over `store`, and the policy cached calls use; None: none."""
    if store is None:
        return None
    from lite_cache import AsyncCacheHook, Policy

    model.hooks.register(AsyncCacheHook(store))
    return Policy(ttl_s=ttl_s)


def open_cache_store(settings: Settings) -> "PgStore":
    """lite-cache's store in the governance database (schema `STEWARD_CACHE__DB_SCHEMA`,
    migrated when behind). Blocking: run it off the event loop. The caller closes it."""
    url = libpq_url(settings.database_url)
    if url is None:  # Settings refuses this; kept for a caller that builds its own
        raise LLMConfigError("STEWARD_CACHE__ENABLED needs PostgreSQL (lite-cache's store)")
    try:
        from lite_cache import open_store
        from lite_cache.config import Settings as CacheStoreSettings
    except ImportError as exc:  # the `cache` extra
        raise LLMConfigError("STEWARD_CACHE__ENABLED needs the cache extra (lite-cache)") from exc
    return open_store(
        CacheStoreSettings(database_url=url, LITECACHE_SCHEMA=settings.cache.db_schema)
    )


def tool_model(settings: Settings, role: Role) -> tuple[AsyncLiteModel, str]:
    """A lite-model client for an agent role (tool calls through `turn()`), and the `role:model`
    tag its products record (the configured model). Checked now, like every role."""
    model = _build(settings, (role,))
    return model, f"{role.value}:{model.target(role.value).model}"


def openai_endpoint(settings: Settings, role: Role) -> tuple[str, str | None, str]:
    """(model, base_url, key) of `role`, for an engine that calls the model itself through the
    OpenAI SDK (mem0): checked like every role, and refused on a backend that does not speak the
    OpenAI protocol. `base_url` None is the SDK's own default (api.openai.com)."""
    if settings.llm is None:
        raise LLMConfigError(
            f"no model for LLM role {role.value}: set STEWARD_LLM__BACKEND and STEWARD_LLM__MODEL"
        )
    with contextlib.suppress(lite_model.ConfigError):  # an unknown backend: resolve() says so
        # before resolve(), which would only ask for lite-model's anthropic extra
        if (t := target(settings.llm, role.value)).type == "anthropic":
            where = f"role {role.value} ({t.backend}:{t.model})"
            raise LLMConfigError(f"{where}: mem0 needs an OpenAI-compatible backend")
    try:
        t = resolve(settings.llm, ROLES)[role.value]
    except lite_model.ConfigError as exc:
        raise LLMConfigError(str(exc)) from exc
    # a keyless endpoint (the gateway, Ollama) ignores the key; the SDK refuses an empty one
    return t.model, t.base_url, os.environ[t.api_key_env] if t.api_key_env else "unused"
