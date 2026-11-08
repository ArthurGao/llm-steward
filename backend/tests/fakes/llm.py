"""FakeLLM: scripted answers per role at the model boundary (never the gateway, never a vendor)."""

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from llm_steward.llm.roles import Provenance, Role

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeLLM:
    model = "fake-model"

    def __init__(self, **handlers: Handler) -> None:
        # keyword = role with dots as underscores, e.g. memory_resolve=...
        self._handlers = {k.replace("_", "."): v for k, v in handlers.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]:
        payload = json.loads(user)
        self.calls.append((role.value, payload))
        if role.value not in self._handlers:
            raise AssertionError(f"FakeLLM has no answer scripted for role {role.value!r}")
        answer = self._handlers[role.value](payload)
        return schema.model_validate(answer), Provenance(role.value, self.model, "fake")


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
