"""llm-steward's own fact extraction (for a memory engine that cannot extract): the model proposes
facts, code cleans them (design §5 step 2; KB llm-app-architecture §4)."""

from llm_steward.llm.roles import Provenance
from llm_steward.pipeline.extract import MAX_FACT_CHARS, MAX_FACTS, extract_facts
from tests.fakes.llm import FakeLLM

CHAT = [
    {"role": "user", "content": "我住北京 喜欢绿茶"},
    {"role": "assistant", "content": "好的 记住了"},
]


def _answering(*facts: str) -> FakeLLM:
    return FakeLLM(memory_extract=lambda p: {"facts": list(facts)})


async def test_the_role_sees_the_conversation_and_its_facts_come_back_with_provenance() -> None:
    llm = _answering("用户住在北京", "用户喜欢绿茶")
    facts, provenance = await extract_facts(llm, CHAT)
    assert facts == ["用户住在北京", "用户喜欢绿茶"]
    assert provenance == Provenance("memory.extract", "fake-model", "fake")
    [(role, payload)] = llm.calls
    assert role == "memory.extract"
    assert payload == {
        "MESSAGES": [
            {"role": "user", "content": "我住北京 喜欢绿茶"},
            {"role": "assistant", "content": "好的 记住了"},
        ]
    }


async def test_code_cleans_what_the_model_proposes() -> None:
    llm = _answering("  用户住在北京 ", "", "   ", "用户住在北京", "用户喜欢绿茶")
    facts, _ = await extract_facts(llm, CHAT)
    assert facts == ["用户住在北京", "用户喜欢绿茶"]  # trimmed, no blanks, no repeats, in order


async def test_too_many_or_too_long_facts_are_capped() -> None:
    many = [f"fact {i}" for i in range(MAX_FACTS + 5)]
    facts, _ = await extract_facts(_answering(*many), CHAT)
    assert facts == many[:MAX_FACTS]
    long = "x" * (MAX_FACT_CHARS + 10)
    [fact], _ = await extract_facts(_answering(long), CHAT)
    assert len(fact) == MAX_FACT_CHARS


async def test_nothing_worth_keeping_is_an_empty_list() -> None:
    facts, _ = await extract_facts(_answering(), CHAT)
    assert facts == []
