"""KB documents (design §4, §7): upload checks and reads. Writes go through GovernanceService; the
Syncer hands the bytes to the KB provider and polls its parse status.

A document can also be imported by URL (P5c2): the request records it, never fetches; the worker
fetches it through the outbound guard (`outbound.py`: public https only, checked redirects, a size
cap, the namespace's `documents.url_allowlist`) and hands the bytes to governance, after which it
follows the upload path. A fetch that cannot succeed fails the document with a fixed reason."""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward import outbound
from llm_steward.errors import TooLarge, Unprocessable
from llm_steward.models import Candidate, KbDocument
from llm_steward.models.enums import DocStatus, KbStatus, RiskLevel
from llm_steward.providers.base import KnowledgeProvider, document_parser
from llm_steward.services.governance_service import (
    SYSTEM,
    Actor,
    AttachDocumentFile,
    FailDocumentFetch,
    GovernanceService,
    SubmitKbDocument,
    load_owned,
    namespace_config,
)
from llm_steward.services.job_queue import ClaimedJob, PermanentFailure
from llm_steward.services.paging import page

# Text documents WeKnora v0.9.0 parses (its upload list minus images, audio and video, which need
# models llm-steward does not configure). Another engine fails what it cannot read at parse time.
DOCUMENT_TYPES = frozenset(
    {
        *("pdf", "txt", "md", "markdown", "html", "htm", "mhtml", "json", "csv", "epub", "rtf"),
        *("doc", "docx", "docm", "odt", "xls", "xlsx", "xlsm", "ods", "ppt", "pptx", "pptm", "odp"),
    }
)


# A URL whose name has no type: named from the answer's content type.
_TYPE_OF_CONTENT = {
    "application/pdf": "pdf",
    "text/html": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/json": "json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
# Why a fetch was refused, as stored on the document: fixed texts, never the URL.
_REFUSED: dict[outbound.UrlRefusal, str] = {
    "form": "not a public https address",  # e.g. a redirect to plain http
    "allowlist": "not on the allowlist",
    "unresolved": "the host does not resolve",
    "not_public": "not a public https address",
    "redirects": "too many redirects",
}


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
        allow_private_urls: bool = False,
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._kb = kb
        self.max_bytes = max_bytes
        self.allow_private_urls = allow_private_urls
        # The network boundary for URL imports, replaceable (tests put a fake web here).
        self.transport: httpx2.AsyncBaseTransport | None = None
        self.resolve: outbound.Resolver = outbound.system_resolve

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
        self._can_parse()
        name = PurePosixPath(filename.replace("\\", "/")).name
        _check_type(_suffix(name))
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

    async def import_url(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        url: str,
        *,
        actor: Actor,
        title: str | None = None,
        risk_level: RiskLevel = RiskLevel.LOW,
    ) -> Uploaded:
        """Recorded now, fetched by the worker. Checked here without touching the network: an
        http(s) URL with a host, and a type the engine reads when its name has one."""
        self._can_parse()
        if len(url) > 2000:
            raise Unprocessable("a document URL is at most 2000 characters")
        parts = outbound.check_form(url, allow_private=self.allow_private_urls)  # 422 if not
        name = PurePosixPath(unquote(parts.path)).name
        suffix = _suffix(name)
        if suffix:  # a type no engine reads is refused now, not after a fetch
            _check_type(suffix)
        op = SubmitKbDocument(
            namespace_id,
            collection_id,
            title=title or name or parts.hostname or url,
            filename=name if suffix else "",
            mime=None,
            data=None,
            content_hash="",
            risk_level=risk_level,
            source_uri=url,
        )
        document, candidate = await self._governance.apply(op, actor)
        return Uploaded(document, candidate)

    async def handle_fetch(self, job: ClaimedJob) -> None:
        """`kb.doc_fetch`: fetch through the guard, then hand the bytes to governance. A fetch
        that cannot succeed fails the document with a fixed reason; a busy server is retried,
        and on the job's last attempt the document fails too (and the job, for the operator)."""
        document_id = uuid.UUID(job.payload["document_id"])
        async with self._sessions() as s:
            doc = await s.get(KbDocument, document_id)
            if doc is None or doc.source_uri is None or doc.status != DocStatus.PENDING:
                return
            config = await namespace_config(s, job.namespace_id)
        try:
            async with outbound.client(self.transport) as http:
                fetched = await outbound.request(
                    http,
                    "GET",
                    doc.source_uri,
                    max_bytes=self.max_bytes,
                    follow_redirects=True,
                    allow_private=self.allow_private_urls,
                    allowlist=tuple(config.documents.url_allowlist),
                    resolve=self.resolve,
                )
        except outbound.UrlNotAllowed as exc:
            await self._fail(job, document_id, f"fetch refused: {_REFUSED[exc.reason]}")
            return
        except TooLarge:
            await self._fail(job, document_id, "fetch failed: the document is too large")
            return
        except httpx2.TransportError as exc:
            await self._busy(job, document_id, f"fetch failed: {type(exc).__name__}")
            return
        status = fetched.status_code
        if outbound.retryable(status):
            await self._busy(job, document_id, f"fetch failed: HTTP {status}")
            return
        if status != 200:
            await self._fail(job, document_id, f"fetch failed: HTTP {status}")
            return
        mime = (fetched.headers.get("content-type") or "").split(";")[0].strip().lower() or None
        filename = _name_for(fetched.url, mime)
        if filename is None or not fetched.body:
            await self._fail(job, document_id, "fetch failed: unsupported document type")
            return
        data = fetched.body
        digest = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
        op = AttachDocumentFile(job.namespace_id, document_id, data, digest, mime, filename)
        await self._governance.apply(op, SYSTEM)

    async def _fail(self, job: ClaimedJob, document_id: uuid.UUID, error: str) -> None:
        await self._governance.apply(
            FailDocumentFetch(job.namespace_id, document_id, error), SYSTEM
        )

    async def _busy(self, job: ClaimedJob, document_id: uuid.UUID, error: str) -> None:
        if not job.last_attempt:
            raise outbound.TryLater(error)
        await self._fail(job, document_id, error)
        raise PermanentFailure(error)  # the job shows up in `steward jobs failed`

    def _can_parse(self) -> None:
        if document_parser(self._kb) is None:
            raise Unprocessable(f"the KB provider {self._kb.name!r} cannot parse documents")

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


def _suffix(name: str) -> str:
    return PurePosixPath(name).suffix.lower().lstrip(".")


def _check_type(suffix: str) -> None:
    if suffix not in DOCUMENT_TYPES:
        raise Unprocessable(
            f"unsupported document type {suffix or '(none)'!r}; one of "
            f"{', '.join(sorted(DOCUMENT_TYPES))}"
        )


def _name_for(url: str, mime: str | None) -> str | None:
    """A fetched document's file name: the URL's (after redirects) when it has a type the engine
    reads, else named from the content type; None for a type no engine reads."""
    stem = PurePosixPath(unquote(urlsplit(url).path)).name
    if _suffix(stem):
        return stem if _suffix(stem) in DOCUMENT_TYPES else None
    kind = _TYPE_OF_CONTENT.get(mime or "")
    return f"{stem or 'document'}.{kind}" if kind else None
