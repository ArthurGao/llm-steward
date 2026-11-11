"""The KB pipeline's LLM steps (design §5 steps 5-6, and risk labelling). The model proposes; code
decides (KB llm-app-architecture §4): the threshold for "worth a KB entry" is here, not in a prompt,
and scores the model returns out of range or for ids it was not given are not trusted."""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from llm_steward import prompts
from llm_steward.llm.client import LLM
from llm_steward.llm.roles import Provenance, Role
from llm_steward.models.enums import RiskLevel

MIN_GENERALITY = 0.6  # a high mean is not enough for knowledge that is about one person
CRITERIA = ("reusability", "novelty", "timeliness", "generality", "trust")


RISK_PROMPT = prompts.load("kb_risk.j2")
WORTHINESS_PROMPT = prompts.load("kb_worthiness.j2")
DRAFT_PROMPT = prompts.load("kb_draft.j2")


class RiskLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risk_level: Literal["low", "medium", "high"]
    reason: str


class FactScores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reusability: float
    novelty: float
    timeliness: float
    generality: float
    trust: float


class WorthinessReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[FactScores]


class DraftReply(BaseModel):
    """The draft and its risk label in one call (the drafter knows what it just wrote)."""

    model_config = ConfigDict(extra="forbid")
    title: str
    content: str
    risk_level: Literal["low", "medium", "high"]
    reason: str


@dataclass(frozen=True)
class Labelled:
    level: RiskLevel
    reason: str
    provenance: Provenance


async def label_risk(llm: LLM, title: str, content: str) -> Labelled:
    reply, provenance = await llm.structured(
        Role.KB_RISK,
        RISK_PROMPT,
        json.dumps({"title": title, "content": content}, ensure_ascii=False),
        RiskLabel,
    )
    return Labelled(RiskLevel(reply.risk_level), reply.reason, provenance)


async def score_memories(
    llm: LLM, facts: list[tuple[str, str]]
) -> tuple[list[FactScores], Provenance | None]:
    """One call for the whole batch (design §5 step 5); none for an empty one."""
    if not facts:
        return [], None
    payload = {"FACTS": [{"id": i, "fact": text} for i, text in facts]}
    reply, provenance = await llm.structured(
        Role.KB_WORTHINESS,
        WORTHINESS_PROMPT,
        json.dumps(payload, ensure_ascii=False),
        WorthinessReply,
    )
    return reply.items, provenance


def worthy_ids(scores: list[FactScores], asked: set[str], *, threshold: float) -> list[str]:
    """The ids worth a KB candidate: the mean of the five scores reaches `threshold` and the fact
    is general enough. Scores are clamped to 0-1; ids that were not asked about are ignored."""
    out = []
    for item in scores:
        if item.id not in asked:
            continue
        values = {c: min(max(getattr(item, c), 0.0), 1.0) for c in CRITERIA}
        if (
            sum(values.values()) / len(CRITERIA) >= threshold
            and values["generality"] >= MIN_GENERALITY
        ):
            out.append(item.id)
    return out


async def draft_entry(llm: LLM, text: str) -> tuple[DraftReply, Provenance]:
    return await llm.structured(
        Role.KB_DRAFT,
        DRAFT_PROMPT,
        json.dumps({"TEXT": text}, ensure_ascii=False),
        DraftReply,
    )
