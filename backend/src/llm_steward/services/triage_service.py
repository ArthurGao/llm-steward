"""The reconcile triage assistant (design §11 P11). A reconcile reports provider records no mirror
row points at and never deletes them (a person should look); this service has an agent look first.
For each orphan it runs a lite-agent tool loop over read-only tools and stores what the agent
proposes (`ignore` / `remirror` / `erase`) as a `ReconcileFinding`. Nothing is applied: a person
decides in the Admin, and any change then goes through the governance service.

Every tool is bound to the job's namespace and the orphan's own record: the model chooses no id
that could reach another namespace (rule 7). The agent has no tool that writes governed data;
lite-agent answers a call to any other tool name with "no tool named …" and runs nothing."""

import json
import logging
import uuid

from lite_agent import Agent, Budget, SessionStore, Tool, ToolContext, tool
from lite_model import AsyncToolModel
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward import prompts
from llm_steward.llm.roles import Role
from llm_steward.models import ChangeLog, ExternalRef, Memory, ReconcileFinding
from llm_steward.models.enums import MemoryStatus, ObjectType, TriageAction
from llm_steward.providers.base import MemoryProvider, MemoryRecord
from llm_steward.services.job_queue import ClaimedJob

log = logging.getLogger(__name__)

SYSTEM = prompts.load("reconcile_triage.j2")
LIST_LIMIT = 50  # rows a read tool returns: enough to judge, small enough for the context


class NoArgs(BaseModel):
    pass


class Finding(BaseModel):
    diagnosis: str = Field(min_length=1, max_length=2000)
    proposed_action: TriageAction
    evidence: list[str] = Field(default_factory=list, max_length=20)


class TriageService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: MemoryProvider,
        model: AsyncToolModel,
        store: SessionStore,
        *,
        model_tag: str,
        max_orphans: int = 20,
        turns: int = 8,
    ) -> None:
        """`model_tag`: `role:model` of `agent.triage` as configured (lite-agent's log holds the
        model that answered each turn), recorded with each finding."""
        self._sessions = sessions
        self._provider = provider
        self._model = model
        self._store = store
        self._model_tag = model_tag
        self._max_orphans = max_orphans
        self._turns = turns

    async def handle(self, job: ClaimedJob) -> int:
        """Up to `max_orphans` of the job's orphans not triaged before: every reconcile hands over
        the same ones until a person acts, and none is looked at (or paid for) twice."""
        async with self._sessions() as s:
            seen = set(
                await s.scalars(
                    select(ReconcileFinding.external_id).where(
                        ReconcileFinding.namespace_id == job.namespace_id,
                        ReconcileFinding.provider == self._provider.name,
                    )
                )
            )
        orphans = [o for o in map(str, job.payload["orphans"]) if o not in seen]
        orphans = orphans[: self._max_orphans]
        found = 0
        for external_id in orphans:
            if await self.triage(job.namespace_id, external_id) is not None:
                found += 1
        return found

    async def triage(self, namespace_id: uuid.UUID, external_id: str) -> ReconcileFinding | None:
        """One orphan: the agent's finding, stored; None when the agent reported none or the
        record already has one. The record is read here, in trusted code: its user bounds what
        the tools show, whatever the model (or the record's own text) asks for."""
        if await self._triaged(namespace_id, external_id):
            return None
        record = await self._provider.get(external_id)
        if record is not None and record.scope.namespace_id != namespace_id:
            record = None  # another namespace's: never shown
        reported: list[Finding] = []
        tools = self._tools(namespace_id, external_id, record, reported)
        agent = Agent(
            role=Role.AGENT_TRIAGE.value,
            model=self._model,
            system=SYSTEM,
            tools=tools,
            # done once a finding is reported; a text-only answer is asked for one
            done=lambda events: None if reported else "Call report_finding with your diagnosis.",
            sessions=self._store,
            budget=Budget(turns=self._turns, auto_continues=1),
        )
        result = await agent.run(f"Look into the unmirrored {self._provider.name} record.")
        if not reported:
            error = result.error  # lite-agent's kind and type: no model or record text
            log.warning(
                "reconcile triage %s %s: no finding (%s, %s%s)",
                namespace_id,
                external_id,
                result.outcome,
                result.stop,
                f", {error.kind} {error.type}" if error else "",
            )
            return None
        finding = reported[-1]
        row = ReconcileFinding(
            namespace_id=namespace_id,
            provider=self._provider.name,
            external_id=external_id,
            diagnosis=finding.diagnosis,
            proposed_action=finding.proposed_action,
            evidence=finding.evidence,
            model=self._model_tag,
            session_id=result.session_id,
        )
        try:
            async with self._sessions() as s, s.begin():
                s.add(row)
        except IntegrityError:  # another job stored this record's finding meanwhile
            return None
        return row

    async def _triaged(self, namespace_id: uuid.UUID, external_id: str) -> bool:
        async with self._sessions() as s:
            found = await s.scalar(
                select(ReconcileFinding.id).where(
                    ReconcileFinding.namespace_id == namespace_id,
                    ReconcileFinding.provider == self._provider.name,
                    ReconcileFinding.external_id == external_id,
                )
            )
        return found is not None

    def _tools(
        self,
        namespace_id: uuid.UUID,
        external_id: str,
        record: MemoryRecord | None,
        reported: list[Finding],
    ) -> list[Tool]:
        provider, sessions = self._provider, self._sessions
        gone = "The record no longer exists in the engine."
        user_id = record.scope.user_id if record else None  # the record's, never the model's

        @tool(description="The unmirrored record: its text, user and metadata.")
        async def get_orphan(args: NoArgs, ctx: ToolContext) -> str:
            if record is None:
                return gone
            return json.dumps(
                {
                    "id": record.external_id,
                    "text": record.content,
                    "user_id": record.scope.user_id,
                    "metadata": record.metadata,
                },
                ensure_ascii=False,
                default=str,
            )

        @tool(description="Governed objects that ever pointed at this record id, and their sync.")
        async def find_external_refs(args: NoArgs, ctx: ToolContext) -> str:
            async with sessions() as s:
                rows = (
                    await s.execute(
                        select(ExternalRef).where(
                            ExternalRef.namespace_id == namespace_id,
                            ExternalRef.provider == provider.name,
                            ExternalRef.external_id == external_id,
                        )
                    )
                ).scalars()
                refs = [
                    {"object": f"{r.object_type}:{r.object_id}", "sync": r.sync_status}
                    for r in rows
                ]
            return json.dumps(refs or "none", default=str)

        @tool(description="Governed memories of the record's user in this namespace, newest first.")
        async def list_mirrored_memories(args: NoArgs, ctx: ToolContext) -> str:
            if record is None:
                return gone
            async with sessions() as s:
                rows = await s.scalars(
                    select(Memory)
                    .where(Memory.namespace_id == namespace_id, Memory.user_id == user_id)
                    .order_by(Memory.id.desc())
                    .limit(LIST_LIMIT)
                )
                memories = [{"id": str(m.id), "text": m.content, "status": m.status} for m in rows]
            return json.dumps(memories or "none", ensure_ascii=False, default=str)

        @tool(
            description="The record's user's governed memories with the same text as the record "
            "(case and spacing aside), split into active and deleted."
        )
        async def matching_memories(args: NoArgs, ctx: ToolContext) -> str:
            # P15: the comparison qwen3:8b kept getting wrong is done here, not by the model
            if record is None:
                return gone
            wanted = _same_text(record.content)
            async with sessions() as s:
                rows = await s.scalars(
                    select(Memory).where(
                        Memory.namespace_id == namespace_id, Memory.user_id == user_id
                    )
                )
                same = [m for m in rows if _same_text(m.content) == wanted]
            found: dict[str, list[str]] = {"active": [], "deleted": []}
            for m in same:
                if m.status in (MemoryStatus.ACTIVE, MemoryStatus.DELETED):
                    found[m.status.value].append(m.content)
            return json.dumps(found, ensure_ascii=False)

        @tool(description="Latest governance changes to the record's user's memories here.")
        async def recent_changes(args: NoArgs, ctx: ToolContext) -> str:
            if record is None:
                return gone
            async with sessions() as s:
                ids = select(Memory.id).where(
                    Memory.namespace_id == namespace_id, Memory.user_id == user_id
                )
                rows = await s.scalars(
                    select(ChangeLog)
                    .where(
                        ChangeLog.namespace_id == namespace_id,
                        ChangeLog.object_type == ObjectType.MEMORY,
                        ChangeLog.object_id.in_(ids),
                    )
                    .order_by(ChangeLog.id.desc())
                    .limit(LIST_LIMIT)
                )
                changes = [
                    {"memory": str(c.object_id), "op": c.op, "at": c.created_at, "reason": c.reason}
                    for c in rows
                ]
            return json.dumps(changes or "none", ensure_ascii=False, default=str)

        @tool(description="Report the finding (once).", terminal=True)
        async def report_finding(args: Finding, ctx: ToolContext) -> str:
            reported.append(args)
            return "Recorded. A person will decide."

        return [
            get_orphan,
            find_external_refs,
            list_mirrored_memories,
            matching_memories,
            recent_changes,
            report_finding,
        ]


def _same_text(text: str) -> str:
    """What makes two memory texts the same for triage: case and runs of spacing aside."""
    return " ".join(text.split()).casefold()
