"""Structured LLM calls via an OpenAI-compatible endpoint (llm-gateway in dev, a vendor in prod).

The OpenAI SDK is used instead of LiteLLM (design §3): it already ships with mem0, speaks to the
gateway and to every OpenAI-compatible vendor endpoint, and keeps the dependency tree small.
"""

from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from llm_steward.config import Settings
from llm_steward.llm.roles import Provenance, Role, RoleTarget, resolve


class LLMOutputError(RuntimeError):
    """The model answered, but not with what the role's schema requires."""


class LLM(Protocol):
    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]: ...


class OpenAICompatibleLLM:
    def __init__(self, settings: Settings, roles: tuple[Role, ...]) -> None:
        # Resolving every role up front makes a misconfiguration fail at startup (§1.5).
        self._targets: dict[Role, RoleTarget] = {r: resolve(settings, r) for r in roles}
        self._timeout_s = settings.llm_timeout_s
        self._clients: dict[tuple[str, str], AsyncOpenAI] = {}

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]:
        target = self._targets[role]
        response = await self._client(target).chat.completions.create(
            model=target.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
            timeout=self._timeout_s,
        )
        provenance = Provenance(role.value, response.model or target.model, target.endpoint)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMOutputError(f"{role.value}: reply truncated ({provenance.model})")
        try:
            return schema.model_validate_json(choice.message.content or ""), provenance
        except ValidationError as exc:
            raise LLMOutputError(f"{role.value}: reply does not match {schema.__name__}") from exc

    def _client(self, target: RoleTarget) -> AsyncOpenAI:
        key = (target.base_url, target.api_key)
        if key not in self._clients:
            self._clients[key] = AsyncOpenAI(base_url=target.base_url, api_key=target.api_key)
        return self._clients[key]
