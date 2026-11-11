"""Fact extraction for a memory engine that cannot extract (design §5 step 2): the
`memory.extract` role reads a conversation and proposes facts; code cleans them (KB
llm-app-architecture §4: the model proposes, code decides).

Conflicts with what the person's memory already holds are not judged here: the facts become
memories through governance, and conflict compensation (`pipeline/conflict.py`) runs on them as it
does on an engine's own extractions.
"""

import json

from pydantic import BaseModel, ConfigDict

from llm_steward import prompts
from llm_steward.llm.client import LLM
from llm_steward.llm.roles import Provenance, Role

SYSTEM_PROMPT = prompts.load("extract_facts.j2")
MAX_FACTS = 20  # per batch: more than this is the model listing the conversation, not memory
MAX_FACT_CHARS = 1000  # a fact is a sentence, not a transcript


class ExtractedFacts(BaseModel):
    """The role's structured output."""

    model_config = ConfigDict(extra="forbid")

    facts: list[str]


async def extract_facts(llm: LLM, messages: list[dict[str, str]]) -> tuple[list[str], Provenance]:
    """The facts worth remembering in `messages` (`{"role", "content"}`, as an episode stores
    them), trimmed, without blanks or repeats, in the model's order, capped in number and length."""
    payload = {"MESSAGES": [{"role": m["role"], "content": m["content"]} for m in messages]}
    answer, provenance = await llm.structured(
        Role.MEMORY_EXTRACT, SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), ExtractedFacts
    )
    facts: dict[str, None] = {}  # ordered and unique
    for raw in answer.facts:
        if fact := raw.strip()[:MAX_FACT_CHARS]:
            facts[fact] = None
            if len(facts) == MAX_FACTS:
                break
    return list(facts), provenance
