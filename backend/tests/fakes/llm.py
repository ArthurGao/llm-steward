"""FakeLLM: scripted answers per role at the model boundary (never the gateway, never a vendor),
over lite-model's `AsyncFakeModel`: the replies go through the same adapter as a real model's."""

import json
from collections.abc import Callable
from typing import Any

from lite_model.testing import AsyncFakeModel, FakeCall

from llm_steward.llm.client import LiteModelLLM

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeLLM(LiteModelLLM):
    """Each handler answers one role from the call's JSON payload; its provenance says
    `<role>:fake-model`, so a scripted answer is never mistaken for a model's."""

    def __init__(self, **handlers: Handler) -> None:
        # keyword = role with dots as underscores, e.g. memory_resolve=...
        def script(handler: Handler) -> Callable[[FakeCall], dict[str, Any]]:
            return lambda call: handler(json.loads(call.messages[-1].content))

        self.fake = AsyncFakeModel(**{k.replace("_", "."): script(v) for k, v in handlers.items()})
        super().__init__(self.fake)

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
