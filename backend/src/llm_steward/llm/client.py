"""Structured LLM calls through lite-model (its design §12). `LiteModelLLM` keeps llm-steward's
`LLM` Protocol (system and user text in, a validated reply and its provenance out) over a lite-model
`AsyncModel`; the backends, strict schemas, retries and provenance are lite-model's."""

import contextlib
import os
from typing import Protocol

import lite_model
from lite_model import AsyncModel, Message
from lite_model.config import resolve, target
from pydantic import BaseModel

from llm_steward.config import Settings
from llm_steward.llm.roles import ROLES, LLMConfigError, Provenance, Role


class LLMError(RuntimeError):
    """The call failed after lite-model's retries. The message never carries upstream text."""


class LLMOutputError(LLMError):
    """The model answered, but not with what the role's schema requires (or cut off)."""


_OUTPUT = (lite_model.Truncated, lite_model.EmptyReply, lite_model.BadStructuredReply)


class LLM(Protocol):
    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]: ...


class LiteModelLLM:
    def __init__(self, model: AsyncModel) -> None:
        self._model = model

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]:
        messages = [Message("system", system), Message("user", user)]
        try:
            reply = await self._model.structured(role.value, messages, schema)
        except _OUTPUT as exc:
            raise LLMOutputError(str(exc)) from exc
        except lite_model.ModelError as exc:
            raise LLMError(str(exc)) from exc
        p = reply.provenance
        # the model that answered, as the endpoint reports it; the requested one when it does not
        # say (what llm-steward recorded before lite-model)
        return reply.value, Provenance(role.value, p.answered or p.requested)


def role_llm(settings: Settings, roles: tuple[Role, ...]) -> LiteModelLLM:
    """The LLM for `roles`, the ones that will be used. Every configured role is checked now (an
    unknown role, a missing key, a missing extra): a misconfiguration fails at startup."""
    if settings.llm is None:
        names = ", ".join(r.value for r in roles)
        raise LLMConfigError(
            f"no model for LLM roles {names}: set STEWARD_LLM__BACKEND and STEWARD_LLM__MODEL"
        )
    try:
        return LiteModelLLM(lite_model.build_async(settings.llm, ROLES))
    except lite_model.ConfigError as exc:
        raise LLMConfigError(str(exc)) from exc


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
