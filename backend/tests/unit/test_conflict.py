import pytest

from llm_steward.llm.roles import Provenance
from llm_steward.pipeline.conflict import Fact, resolve_conflict
from tests.fakes.llm import FakeLLM

OLD = Fact("m-old", "用户住在北京")
NEW = Fact("m-new", "我搬到上海了")


async def test_no_related_memory_is_an_add_without_a_model_call() -> None:
    llm = FakeLLM()
    resolution = await resolve_conflict(llm, NEW, [])
    assert (resolution.action, resolution.provenance) == ("ADD", None)
    assert llm.calls == []


async def test_update_names_the_replaced_memory_and_records_provenance() -> None:
    llm = FakeLLM(
        memory_resolve=lambda p: {"action": "UPDATE", "target_id": "m-old", "reason": "moved"}
    )
    resolution = await resolve_conflict(llm, NEW, [OLD])
    assert (resolution.action, resolution.target_id) == ("UPDATE", "m-old")
    assert resolution.provenance == Provenance("memory.resolve", "fake-model", "fake")
    [(_, payload)] = llm.calls
    assert payload == {"NEW": NEW.content, "EXISTING": [{"id": "m-old", "fact": OLD.content}]}


@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
async def test_a_target_outside_the_candidates_is_not_applied(action: str) -> None:
    llm = FakeLLM(
        memory_resolve=lambda p: {"action": action, "target_id": "made-up", "reason": "x"}
    )
    resolution = await resolve_conflict(llm, NEW, [OLD])
    assert (resolution.action, resolution.target_id) == ("ADD", None)
    assert "made-up" in resolution.reason


async def test_noop_and_add_carry_no_target() -> None:
    llm = FakeLLM(
        memory_resolve=lambda p: {"action": "NOOP", "target_id": "m-old", "reason": "same"}
    )
    resolution = await resolve_conflict(llm, NEW, [OLD])
    assert (resolution.action, resolution.target_id) == ("NOOP", None)
