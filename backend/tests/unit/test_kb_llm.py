"""The KB pipeline's LLM steps (design §5 steps 5-6): the model proposes, code decides. FakeLLM."""

from typing import Any

from llm_steward.models.enums import RiskLevel
from llm_steward.pipeline.kb_llm import (
    CRITERIA,
    draft_entry,
    label_risk,
    score_memories,
    worthy_ids,
)
from tests.fakes.llm import FakeLLM


def _scores(id_: str, value: float, generality: float | None = None) -> dict[str, Any]:
    scores = dict.fromkeys(CRITERIA, value)
    return {"id": id_, **scores, "generality": value if generality is None else generality}


async def test_the_model_labels_risk_with_a_reason_and_its_provenance() -> None:
    llm = FakeLLM(kb_risk=lambda p: {"risk_level": "high", "reason": f"pricing: {p['title']}"})
    label = await label_risk(llm, "Price list", "Gold plan is 99 a month")
    assert (label.level, label.reason) == (RiskLevel.HIGH, "pricing: Price list")
    assert label.provenance.role == "kb.risk"


async def test_code_decides_what_is_worth_a_kb_entry() -> None:
    reply = {
        "items": [
            _scores("m1", 0.8),  # mean 0.8, general: worthy
            _scores("m2", 0.9, generality=0.5),  # high mean, but personal: not worthy
            _scores("m3", 0.6),  # below the threshold
            _scores("ghost", 1.0),  # not one of ours: ignored
            # one inflated score (9) must not carry a weak fact: clamped to 1, the mean is 0.46
            {**_scores("m4", 0.2, generality=0.7), "reusability": 9.0},
        ]
    }
    llm = FakeLLM(kb_worthiness=lambda p: reply)
    scores, provenance = await score_memories(
        llm, [("m1", "a"), ("m2", "b"), ("m3", "c"), ("m4", "d")]
    )
    assert llm.calls == [("kb.worthiness", {"FACTS": [
        {"id": "m1", "fact": "a"}, {"id": "m2", "fact": "b"},
        {"id": "m3", "fact": "c"}, {"id": "m4", "fact": "d"},
    ]})]  # fmt: skip
    assert worthy_ids(scores, {"m1", "m2", "m3", "m4"}, threshold=0.7) == ["m1"]
    assert provenance.role == "kb.worthiness"


async def test_one_call_scores_the_whole_batch_and_none_is_made_for_nothing() -> None:
    llm = FakeLLM()
    assert await score_memories(llm, []) == ([], None)
    assert llm.calls == []


async def test_a_memory_is_drafted_as_a_general_statement() -> None:
    llm = FakeLLM(
        kb_draft=lambda p: {
            "title": "Refund time",
            "content": f"General: {p['TEXT']}",
            "risk_level": "medium",
            "reason": "a process fact",
        }
    )
    drafted, provenance = await draft_entry(llm, "my refund took 5 days")
    assert (drafted.title, drafted.content, drafted.risk_level) == (
        "Refund time",
        "General: my refund took 5 days",
        "medium",
    )  # one call: the draft labels its own risk
    assert provenance.role == "kb.draft"
