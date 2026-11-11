"""GovernanceService on both dialects: one transaction per write, state machine, outbox rule."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, Forbidden, NotFound
from llm_steward.models import Candidate, ChangeLog, ExternalRef, Job, KbEntry, Memory
from llm_steward.models.enums import (
    ActorType,
    CandidateOp,
    CandidateOrigin,
    CandidateStage,
    CandidateStatus,
    CandidateTarget,
    ChangeOp,
    JobType,
    KbStatus,
    MemoryStatus,
    RiskLevel,
)
from llm_steward.services import job_queue
from llm_steward.services.governance_service import (
    SYSTEM,
    Actor,
    CreateCandidate,
    CreateCollection,
    CreateMemory,
    DecideCandidate,
    DeleteMemory,
    GovernanceService,
    ReviseKbEntry,
    TransitionKbEntry,
    UpdateKbEntry,
)
from llm_steward.services.namespace_service import NamespaceService
from tests.conftest import REVIEWER, governance, kb_entry_op


@dataclass
class Stack:
    sessions: async_sessionmaker[AsyncSession]
    gov: GovernanceService
    ns: uuid.UUID

    async def apply(self, op: Any, actor: Actor = SYSTEM) -> Any:
        return await self.gov.apply(op, actor)

    async def jobs(self) -> list[tuple[JobType, dict[str, Any]]]:
        async with self.sessions() as s:
            rows = await s.execute(select(Job.type, Job.payload).order_by(Job.created_at))
            return [(JobType(t), p) for t, p in rows]

    async def count(self, model: Any) -> int:
        async with self.sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(model)) or 0)

    async def entry(self, status: KbStatus = KbStatus.DRAFT, **kw: Any) -> KbEntry:
        collection = await self.apply(CreateCollection(self.ns, f"c-{uuid.uuid4().hex[:6]}"))
        return await self.apply(kb_entry_op(self.ns, collection.id, status, **kw), REVIEWER)  # type: ignore[no-any-return]


@pytest.fixture
def stack(sessions: async_sessionmaker[AsyncSession], namespace_id: uuid.UUID) -> Stack:
    return Stack(sessions, governance(sessions), namespace_id)


# --- memories -------------------------------------------------------------------------------------


async def test_create_memory_writes_object_log_and_outbox_together(stack: Stack) -> None:
    memory = await stack.apply(
        CreateMemory(stack.ns, "Prefers short answers", user_id="u1"),
        Actor(ActorType.SYSTEM, "api_key:x"),
    )
    assert memory.status == MemoryStatus.ACTIVE
    async with stack.sessions() as s:
        log = (await s.scalars(select(ChangeLog))).one()
    assert (log.object_id, log.op, log.actor_type, log.actor_id) == (
        memory.id,
        ChangeOp.CREATE,
        ActorType.SYSTEM,
        "api_key:x",
    )
    assert log.before is None and log.after is not None and log.after["content"] == memory.content
    assert await stack.jobs() == [(JobType.MEMORY_UPSERT, {"memory_id": str(memory.id)})]


async def test_a_failure_after_the_object_write_leaves_nothing(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_enqueue(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(job_queue, "enqueue", broken_enqueue)  # the outbox write
    with pytest.raises(RuntimeError, match="outbox down"):
        await stack.apply(CreateMemory(stack.ns, "never stored"))
    assert (await stack.count(Memory), await stack.count(ChangeLog), await stack.count(Job)) == (
        0,
        0,
        0,
    )


async def test_delete_memory_soft_deletes_and_queues_removal(stack: Stack) -> None:
    memory = await stack.apply(CreateMemory(stack.ns, "temp"))
    deleted = await stack.apply(DeleteMemory(stack.ns, memory.id, reason="user asked"))
    assert deleted.status == MemoryStatus.DELETED and deleted.valid_to is not None
    assert [t for t, _ in await stack.jobs()] == [JobType.MEMORY_UPSERT, JobType.MEMORY_REMOVE]
    with pytest.raises(Conflict):
        await stack.apply(DeleteMemory(stack.ns, memory.id))


async def test_another_namespaces_object_is_not_found(stack: Stack) -> None:
    memory = await stack.apply(CreateMemory(stack.ns, "mine"))
    other = await NamespaceService(stack.sessions).create_namespace("other")
    with pytest.raises(NotFound):
        await stack.apply(DeleteMemory(other.id, memory.id))


# --- knowledge base ---------------------------------------------------------------------------


async def test_drafts_never_reach_the_outbox(stack: Stack) -> None:
    entry = await stack.entry(KbStatus.DRAFT)
    await stack.apply(UpdateKbEntry(stack.ns, entry.id, content="edited draft"))
    await stack.apply(TransitionKbEntry(stack.ns, entry.id, KbStatus.REJECTED))
    assert await stack.jobs() == []


async def test_effective_changes_queue_upserts_and_retirement_queues_removal(stack: Stack) -> None:
    entry = await stack.entry(KbStatus.DRAFT)
    ref = {"entry_id": str(entry.id)}
    await stack.apply(TransitionKbEntry(stack.ns, entry.id, KbStatus.ACTIVE))
    updated = await stack.apply(UpdateKbEntry(stack.ns, entry.id, content="v2"))
    verified = await stack.apply(TransitionKbEntry(stack.ns, entry.id, KbStatus.VERIFIED), REVIEWER)
    retired = await stack.apply(TransitionKbEntry(stack.ns, entry.id, KbStatus.DEPRECATED))
    assert updated.version == 2
    assert verified.verified_by == REVIEWER.id and verified.verified_at is not None
    assert retired.valid_to is not None
    assert await stack.jobs() == [
        (JobType.KB_UPSERT, ref),
        (JobType.KB_UPSERT, ref),
        (JobType.KB_UPSERT, ref),
        (JobType.KB_REMOVE, ref),
    ]


async def test_an_unchanged_update_creates_no_version(stack: Stack) -> None:
    entry = await stack.entry(KbStatus.ACTIVE, title="T", content="C")
    same = await stack.apply(UpdateKbEntry(stack.ns, entry.id, title="T", content="C"))
    assert same.version == 1
    assert [t for t, _ in await stack.jobs()] == [JobType.KB_UPSERT]


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ((KbStatus.ACTIVE, KbStatus.DEPRECATED), KbStatus.ACTIVE),
        ((KbStatus.ACTIVE,), KbStatus.DRAFT),
        ((KbStatus.REJECTED,), KbStatus.ACTIVE),
        ((KbStatus.VERIFIED,), KbStatus.ACTIVE),
    ],
)
async def test_illegal_transitions_conflict(
    stack: Stack, path: tuple[KbStatus, ...], illegal: KbStatus
) -> None:
    entry = await stack.entry(KbStatus.DRAFT)
    for step in path:  # as a reviewer: since P3 only a reviewer may verify
        await stack.apply(TransitionKbEntry(stack.ns, entry.id, step), REVIEWER)
    with pytest.raises(Conflict):
        await stack.apply(TransitionKbEntry(stack.ns, entry.id, illegal))


async def test_a_retired_entry_cannot_be_edited(stack: Stack) -> None:
    entry = await stack.entry(KbStatus.ACTIVE)
    await stack.apply(TransitionKbEntry(stack.ns, entry.id, KbStatus.DEPRECATED))
    with pytest.raises(Conflict):
        await stack.apply(UpdateKbEntry(stack.ns, entry.id, content="sneaky"))


async def test_an_internal_update_is_screened_like_any_outside_text(stack: Stack) -> None:
    """`UpdateKbEntry` (internal and tests only) cannot slip personal data past the rules."""
    entry = await stack.entry(KbStatus.ACTIVE)
    updated = await stack.apply(
        UpdateKbEntry(stack.ns, entry.id, content="Escalate to bob@example.com"), REVIEWER
    )
    assert updated.content == "Escalate to [REDACTED:email]"
    assert updated.risk_level == RiskLevel.HIGH  # the stored risk follows the new text
    draft = await stack.entry(KbStatus.DRAFT)
    await stack.apply(UpdateKbEntry(stack.ns, draft.id, content="mail bob@example.com"))
    with pytest.raises(Forbidden):  # live knowledge, high risk, nobody reviewing
        await stack.apply(UpdateKbEntry(stack.ns, entry.id, content="call 13800138000"))
    async with stack.sessions() as s:
        assert (await s.get(KbEntry, entry.id)).content == "Escalate to [REDACTED:email]"  # type: ignore[union-attr]


async def test_a_revision_applied_at_once_raises_the_stored_risk(stack: Stack) -> None:
    """A reviewer's revision goes live without waiting; its screened risk is still recorded."""
    entry = await stack.entry(KbStatus.ACTIVE)
    revised = await stack.apply(
        ReviseKbEntry(stack.ns, entry.id, content="Escalate to bob@example.com"), REVIEWER
    )
    assert revised.content == "Escalate to [REDACTED:email]"
    assert revised.risk_level == RiskLevel.HIGH


def test_no_request_path_builds_the_unscreened_internal_operations() -> None:
    """REST and MCP reach knowledge only through Submit/Revise (screened and routed)."""
    from pathlib import Path

    import llm_steward

    root = Path(llm_steward.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for folder in ("api", "mcp")
        for path in (root / folder).rglob("*.py")
        if any(name in path.read_text() for name in ("UpdateKbEntry", "CreateKbEntry"))
    ]
    assert offenders == []


# --- candidates -------------------------------------------------------------------------------


async def test_candidates_change_state_but_never_queue_a_sync(stack: Stack) -> None:
    candidate = await stack.apply(
        CreateCandidate(
            namespace_id=stack.ns,
            target_type=CandidateTarget.KB_ENTRY,
            op=CandidateOp.ADD,
            draft={"title": "t", "content": "c"},
            risk_level=RiskLevel.HIGH,
            origin=CandidateOrigin.EXTRACTION,
            stage=CandidateStage.AWAITING_REVIEW,
        )
    )
    decided = await stack.apply(
        DecideCandidate(stack.ns, candidate.id, CandidateStatus.REJECTED, note="wrong"), REVIEWER
    )
    assert (decided.status, decided.decided_by) == (CandidateStatus.REJECTED, REVIEWER.id)
    with pytest.raises(Conflict):
        await stack.apply(DecideCandidate(stack.ns, candidate.id, CandidateStatus.ACCEPTED))
    assert await stack.jobs() == []
    assert await stack.count(Candidate) == 1


# --- external_refs opened in the write transaction --------------------------------------------


async def _ref_status(stack: Stack, object_id: uuid.UUID) -> str | None:
    async with stack.sessions() as s:
        status = await s.scalar(
            select(ExternalRef.sync_status).where(ExternalRef.object_id == object_id)
        )
        return None if status is None else str(status)


async def test_refs_are_opened_with_the_write_and_never_for_drafts(stack: Stack) -> None:
    memory = await stack.apply(CreateMemory(stack.ns, "x"))
    assert await _ref_status(stack, memory.id) == "pending"
    await stack.apply(DeleteMemory(stack.ns, memory.id))
    assert await _ref_status(stack, memory.id) == "deleting"

    draft = await stack.entry(KbStatus.DRAFT)
    assert await _ref_status(stack, draft.id) is None  # drafts never reach a provider
    await stack.apply(TransitionKbEntry(stack.ns, draft.id, KbStatus.ACTIVE))
    assert await _ref_status(stack, draft.id) == "pending"
    assert await _ref_status(stack, draft.collection_id) == "pending"
