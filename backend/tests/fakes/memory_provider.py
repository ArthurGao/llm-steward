"""FakeMemoryProvider: an in-memory engine that extracts like mem0 2.2.1 — ADD-only, one fact per
sentence — and searches by word overlap. Namespace- and user-isolated like a real provider."""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from llm_steward.errors import Unprocessable
from llm_steward.providers.base import (
    DELETE,
    EXTRACT,
    METADATA_FILTER,
    ComponentHealth,
    MemoryEvent,
    MemoryRecord,
    Message,
    ProviderHit,
    Scope,
)
from llm_steward.providers.native.text import tokens

_SENTENCE = re.compile(r"[^。！？.!?\n]+")


def _terms(text: str) -> set[str]:
    """Words plus single CJK characters: jieba splits 我住北京 / 我搬到上海 with no shared word,
    while a real (vector) engine still finds them related."""
    return set(tokens(text)) | {ch for ch in text if "\u4e00" <= ch <= "\u9fff"}


@dataclass
class _Record:
    content: str
    scope: Scope
    metadata: dict[str, Any] = field(default_factory=dict)


EXTRACTED_BY = "memory.extract:fake-extractor"


class FakeMemoryProvider:
    name = "fake-mem"
    capabilities = frozenset({EXTRACT, DELETE, METADATA_FILTER})

    def __init__(self) -> None:
        self.records: dict[str, _Record] = {}
        self.add_calls = 0

    async def health(self) -> ComponentHealth:
        return ComponentHealth(self.name, "ok")

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,  # not honoured: no such guarantee here
    ) -> list[MemoryEvent]:
        self.add_calls += 1
        metadata = metadata or {}
        if not infer:
            eid = str(metadata.get("steward_id") or uuid.uuid4())
            self.records[eid] = _Record("\n".join(m.content for m in messages), scope, metadata)
            return [MemoryEvent("ADD", eid, self.records[eid].content)]
        events = []
        for message in messages:
            if message.role != "user":
                continue
            for sentence in _SENTENCE.findall(message.content):
                if sentence.strip():
                    eid = str(uuid.uuid4())
                    self.records[eid] = _Record(sentence.strip(), scope, metadata)
                    events.append(MemoryEvent("ADD", eid, sentence.strip(), EXTRACTED_BY))
        return events

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]:
        wanted = _terms(query)
        hits = []
        for eid, r in self.records.items():
            if r.scope.namespace_id != scope.namespace_id:
                continue
            if r.scope.user_id not in (None, scope.user_id):
                continue
            overlap = len(wanted & _terms(r.content))
            if overlap:
                hits.append(ProviderHit(eid, overlap / max(len(wanted), 1), r.content))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]

    async def get(self, external_id: str) -> MemoryRecord | None:
        r = self.records.get(external_id)
        return MemoryRecord(external_id, r.content, r.scope, r.metadata) if r else None

    async def update(self, external_id: str, content: str) -> None:
        raise Unprocessable("fake provider has no 'update' capability")

    async def delete(self, external_id: str) -> None:
        self.records.pop(external_id, None)

    async def list(self, scope: Scope) -> list[MemoryRecord]:
        return [
            MemoryRecord(eid, r.content, r.scope, r.metadata)
            for eid, r in self.records.items()
            if r.scope.namespace_id == scope.namespace_id
            and (scope.user_id is None or r.scope.user_id == scope.user_id)
        ]
