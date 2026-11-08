"""KB documents (design §4, §7): upload checks and reads. Writes go through GovernanceService; the
Syncer hands the bytes to the KB provider and polls its parse status."""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import TooLarge, Unprocessable
from llm_steward.models import Candidate, KbDocument
from llm_steward.models.enums import KbStatus, RiskLevel
from llm_steward.providers.base import KnowledgeProvider, document_parser
from llm_steward.services.governance_service import (
    Actor,
    GovernanceService,
    SubmitKbDocument,
    load_owned,
)
from llm_steward.services.paging import page

# Text documents WeKnora v0.9.0 parses (its upload list minus images, audio and video, which need
# models llm-steward does not configure). Another engine fails what it cannot read at parse time.
DOCUMENT_TYPES = frozenset(
    {
        *("pdf", "txt", "md", "markdown", "html", "htm", "mhtml", "json", "csv", "epub", "rtf"),
        *("doc", "docx", "docm", "odt", "xls", "xlsx", "xlsm", "ods", "ppt", "pptx", "pptm", "odp"),
    }
)


@dataclass(frozen=True)
class Uploaded:
    document: KbDocument
    candidate: Candidate | None  # set when the document waits for a reviewer


@dataclass(frozen=True)
class DocumentFilter:
    collection_id: uuid.UUID | None = None
    state: KbStatus | None = None


class DocumentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        kb: KnowledgeProvider,
        *,
        max_bytes: int,
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._kb = kb
        self.max_bytes = max_bytes

    async def upload(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        filename: str,
        data: bytes,
        *,
        actor: Actor,
        mime: str | None = None,
        title: str | None = None,
        risk_level: RiskLevel = RiskLevel.LOW,
    ) -> Uploaded:
        """Checked here, before anything is stored: the engine can parse documents, the type is
        one it reads, the file is not empty and not over the limit."""
        if document_parser(self._kb) is None:
            raise Unprocessable(f"the KB provider {self._kb.name!r} cannot parse documents")
        name = PurePosixPath(filename.replace("\\", "/")).name
        suffix = PurePosixPath(name).suffix.lower().lstrip(".")
        if suffix not in DOCUMENT_TYPES:
            raise Unprocessable(
                f"unsupported document type {suffix or '(none)'!r}; one of "
                f"{', '.join(sorted(DOCUMENT_TYPES))}"
            )
        if not data:
            raise Unprocessable("the file is empty")
        if len(data) > self.max_bytes:
            raise TooLarge(f"the file is over the {self.max_bytes} byte limit")
        op = SubmitKbDocument(
            namespace_id,
            collection_id,
            title=title or name,
            filename=name,
            mime=mime,
            data=data,
            content_hash=await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest()),
            risk_level=risk_level,
        )
        document, candidate = await self._governance.apply(op, actor)
        return Uploaded(document, candidate)

    async def get(self, namespace_id: uuid.UUID, document_id: uuid.UUID) -> KbDocument:
        async with self._sessions() as s:
            return await load_owned(
                s, KbDocument, namespace_id, document_id, "document", lock=False
            )

    async def list(
        self,
        namespace_id: uuid.UUID,
        filters: DocumentFilter,
        *,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[KbDocument], uuid.UUID | None]:
        query = select(KbDocument).where(KbDocument.namespace_id == namespace_id)
        if filters.collection_id is not None:
            query = query.where(KbDocument.collection_id == filters.collection_id)
        if filters.state is not None:
            query = query.where(KbDocument.state == filters.state)
        async with self._sessions() as s:
            return await page(s, query, KbDocument, cursor, limit)
