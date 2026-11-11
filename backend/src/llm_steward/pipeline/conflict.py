"""Conflict compensation (design §5 step 4): for an engine that only ever ADDs, llm-steward decides
whether a new memory updates, retires or duplicates an existing one.

The model proposes; code decides (KB llm-app-architecture §4): an action that names a memory outside
the candidates is not applied — the new memory is simply kept (ADD).
"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from llm_steward import prompts
from llm_steward.llm.client import LLM
from llm_steward.llm.roles import Provenance, Role

Action = Literal["ADD", "UPDATE", "DELETE", "NOOP"]
SYSTEM_PROMPT = prompts.load("resolve_conflict.j2")


class ConflictDecision(BaseModel):
    """The role's structured output. `target_id` names an EXISTING fact for UPDATE / DELETE."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    target_id: str | None
    reason: str


@dataclass(frozen=True)
class Fact:
    id: str
    content: str


@dataclass(frozen=True)
class Resolution:
    action: Action
    target_id: str | None
    reason: str
    provenance: Provenance | None  # None: decided by code, no model call


async def resolve_conflict(llm: LLM, new: Fact, existing: list[Fact]) -> Resolution:
    if not existing:
        return Resolution("ADD", None, "no related memory in this scope", None)
    payload = {"NEW": new.content, "EXISTING": [{"id": f.id, "fact": f.content} for f in existing]}
    decision, provenance = await llm.structured(
        Role.MEMORY_RESOLVE,
        SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False),
        ConflictDecision,
    )
    if decision.action not in ("UPDATE", "DELETE"):
        return Resolution(decision.action, None, decision.reason, provenance)
    if decision.target_id not in {f.id for f in existing}:
        return Resolution(
            "ADD",
            None,
            f"model chose {decision.action} of unknown memory {decision.target_id!r}; kept both",
            provenance,
        )
    return Resolution(decision.action, decision.target_id, decision.reason, provenance)
