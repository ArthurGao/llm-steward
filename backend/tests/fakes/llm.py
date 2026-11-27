"""FakeLLM: scripted answers per role at the model boundary (never the gateway, never a vendor),
over lite-model's `AsyncFakeModel`: the replies go through the same adapter as a real model's."""

import json
from collections.abc import Callable
from typing import Any

from lite_model.testing import AsyncFakeModel, FakeCall, hash_vectors
from pydantic import BaseModel

from llm_steward.llm.client import LiteModelLLM
from llm_steward.llm.roles import Provenance, Role

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeLLM(LiteModelLLM):
    """Each handler answers one role from the call's JSON payload; its provenance says
    `<role>:fake-model`, so a scripted answer is never mistaken for a model's."""

    def __init__(
        self,
        similar: Callable[[str], list[float]] | BaseException | None = None,
        **handlers: Handler,
    ) -> None:
        """`similar`: the `kb.similar` vector of one text, or an error every embedding raises;
        None: a fixed vector per text with no similarity between different texts."""

        # keyword = role with dots as underscores, e.g. memory_resolve=...
        def script(handler: Handler) -> Callable[[FakeCall], dict[str, Any]]:
            return lambda call: handler(json.loads(call.messages[-1].content))

        embed = (
            hash_vectors(64)
            if similar is None
            else similar
            if isinstance(similar, BaseException)
            else lambda call: [similar(text) for text in call.texts]
        )
        self.fake = AsyncFakeModel.with_vectors(
            {Role.KB_SIMILAR.value: embed},
            **{k.replace("_", "."): script(v) for k, v in handlers.items()},
        )
        self.scopes: list[tuple[str, str | None]] = []  # (role, cache_scope) of every call
        super().__init__(self.fake)

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T], *, cache_scope: str | None = None
    ) -> tuple[T, Provenance]:
        self.scopes.append((role.value, cache_scope))
        return await super().structured(role, system, user, schema, cache_scope=cache_scope)

    @property
    def calls(self) -> list[tuple[str, dict[str, Any]]]:
        """(role, payload) of every call, scripted or not."""
        return [(c.role, json.loads(c.messages[-1].content)) for c in self.fake.calls]


def update_when(marker: str) -> Handler:
    """memory.resolve: UPDATE the first existing fact if the new one contains `marker`, else ADD."""

    def decide(payload: dict[str, Any]) -> dict[str, Any]:
        if marker in payload["NEW"] and payload["EXISTING"]:
            return {
                "action": "UPDATE",
                "target_id": payload["EXISTING"][0]["id"],
                "reason": "moved",
            }
        return {"action": "ADD", "target_id": None, "reason": "unrelated"}

    return decide


def user_lines() -> Handler:
    """memory.extract: each user message is one fact, as said (assistant turns are skipped)."""

    def extract(payload: dict[str, Any]) -> dict[str, Any]:
        return {"facts": [m["content"] for m in payload["MESSAGES"] if m["role"] == "user"]}

    return extract
