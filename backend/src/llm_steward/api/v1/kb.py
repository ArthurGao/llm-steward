import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Query, Response, UploadFile

from llm_steward.api.deps import Reader, Reviewer, ServicesDep, Writer
from llm_steward.errors import Unprocessable
from llm_steward.models.enums import KbStatus, RiskLevel
from llm_steward.schemas.kb import (
    CandidateOut,
    CollectionCreate,
    CollectionOut,
    CollectionPage,
    CollectionPatch,
    DocumentOut,
    DocumentPage,
    DocumentWritten,
    EntryCreate,
    EntryOut,
    EntryPage,
    EntryPatch,
    Written,
)
from llm_steward.services.document_service import DocumentFilter
from llm_steward.services.governance_service import (
    CreateCollection,
    RetireKbDocument,
    TransitionKbEntry,
    UpdateCollection,
)
from llm_steward.services.kb_service import EntryFilter, Submitted
from llm_steward.services.paging import MAX_PAGE

router = APIRouter(prefix="/kb", tags=["kb"])


def written(result: Submitted, response: Response, live: int = 201) -> Written:
    """`live` (201 created, 200 edited) for live knowledge, 202 for a waiting candidate."""
    response.status_code = live if result.entry is not None else 202
    return Written(
        entry=EntryOut.model_validate(result.entry) if result.entry else None,
        candidate=CandidateOut.model_validate(result.candidate) if result.candidate else None,
    )


# --- collections (their review policy is a reviewer's decision) --------------------------------


@router.post("/collections", status_code=201)
async def create_collection(
    body: CollectionCreate, principal: Reviewer, services: ServicesDep
) -> CollectionOut:
    op = CreateCollection(principal.namespace_id, body.name, body.default_risk, body.review_policy)
    return CollectionOut.model_validate(await services.governance.apply(op, principal.actor))


@router.get("/collections")
async def list_collections(principal: Reader, services: ServicesDep) -> CollectionPage:
    rows = await services.kb.list_collections(principal.namespace_id)
    return CollectionPage(items=[CollectionOut.model_validate(c) for c in rows])


@router.patch("/collections/{collection_id}")
async def patch_collection(
    collection_id: uuid.UUID, body: CollectionPatch, principal: Reviewer, services: ServicesDep
) -> CollectionOut:
    op = UpdateCollection(
        principal.namespace_id, collection_id, body.default_risk, body.review_policy
    )
    return CollectionOut.model_validate(await services.governance.apply(op, principal.actor))


# --- entries -----------------------------------------------------------------------------------


@router.post("/entries", status_code=201, responses={202: {"model": Written}})
async def create_entry(
    body: EntryCreate, principal: Writer, services: ServicesDep, response: Response
) -> Written:
    """Submitted, then routed by risk (design §5): low goes live, medium asks `user_id` (or goes
    live without one), high waits for a reviewer. Personal data is redacted (namespace policy)."""
    result = await services.kb.submit(
        principal.namespace_id,
        body.collection_id,
        body.title,
        body.content,
        actor=principal.actor,
        risk_level=body.risk_level,
        user_id=body.user_id,
        tags=body.tags,
    )
    return written(result, response)


@router.get("/entries")
async def list_entries(
    principal: Reader,
    services: ServicesDep,
    collection_id: uuid.UUID | None = None,
    status: KbStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> EntryPage:
    rows, next_cursor = await services.kb.list_entries(
        principal.namespace_id, EntryFilter(collection_id, status), cursor=cursor, limit=limit
    )
    return EntryPage(items=[EntryOut.model_validate(e) for e in rows], next_cursor=next_cursor)


@router.get("/entries/{entry_id}")
async def get_entry(entry_id: uuid.UUID, principal: Reader, services: ServicesDep) -> EntryOut:
    return EntryOut.model_validate(await services.kb.get_entry(principal.namespace_id, entry_id))


@router.patch("/entries/{entry_id}", responses={202: {"model": Written}})
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    principal: Writer,
    services: ServicesDep,
    response: Response,
) -> Written:
    """A new version. Verified knowledge, or a high-risk edit, waits for a reviewer (202)."""
    result = await services.kb.revise(
        principal.namespace_id,
        entry_id,
        actor=principal.actor,
        title=body.title,
        content=body.content,
    )
    return written(result, response, live=200)


@router.post("/entries/{entry_id}/verify")
async def verify_entry(entry_id: uuid.UUID, principal: Reviewer, services: ServicesDep) -> EntryOut:
    op = TransitionKbEntry(principal.namespace_id, entry_id, KbStatus.VERIFIED)
    return EntryOut.model_validate(await services.governance.apply(op, principal.actor))


@router.post("/entries/{entry_id}/deprecate")
async def deprecate_entry(
    entry_id: uuid.UUID, principal: Reviewer, services: ServicesDep
) -> EntryOut:
    """Out of retrieval at once (the safety net), out of the provider when the Syncer runs."""
    op = TransitionKbEntry(principal.namespace_id, entry_id, KbStatus.DEPRECATED)
    return EntryOut.model_validate(await services.governance.apply(op, principal.actor))


# --- documents (design §4: governed as a whole; the KB provider parses and chunks them) ---------


@router.post("/documents", status_code=202)
async def upload_document(
    principal: Writer,
    services: ServicesDep,
    collection_id: Annotated[uuid.UUID, Form()],
    file: UploadFile | None = None,
    url: Annotated[str | None, Form(max_length=2000)] = None,
    title: Annotated[str | None, Form(max_length=500)] = None,
    risk_level: Annotated[RiskLevel, Form()] = RiskLevel.LOW,
) -> DocumentWritten:
    """A `file`, or a `url` to import (exactly one). Stored, then handed to the KB provider by the
    worker (202); a URL is fetched by the worker first (public https only; the namespace's
    `documents.url_allowlist` limits hosts), and a fetch that cannot succeed fails the document.
    Routed by the collection: high risk waits for a reviewer (`candidate`), a reviewer's upload
    is verified, others go live. The title is screened like an entry's; the content is parsed by
    the provider, not screened."""
    documents = services.documents
    if url is not None and file is None:
        uploaded = await documents.import_url(
            principal.namespace_id,
            collection_id,
            url,
            actor=principal.actor,
            title=title or None,
            risk_level=risk_level,
        )
    elif file is not None and url is None:
        data = await file.read(documents.max_bytes + 1)  # one byte over says "too large"
        uploaded = await documents.upload(
            principal.namespace_id,
            collection_id,
            file.filename or "",
            data,
            actor=principal.actor,
            mime=file.content_type,
            title=title or None,
            risk_level=risk_level,
        )
    else:
        raise Unprocessable("send either a file or a url")
    candidate = uploaded.candidate
    return DocumentWritten(
        document=DocumentOut.model_validate(uploaded.document),
        candidate=CandidateOut.model_validate(candidate) if candidate else None,
    )


@router.get("/documents")
async def list_documents(
    principal: Reader,
    services: ServicesDep,
    collection_id: uuid.UUID | None = None,
    state: KbStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> DocumentPage:
    rows, next_cursor = await services.documents.list(
        principal.namespace_id, DocumentFilter(collection_id, state), cursor=cursor, limit=limit
    )
    items = [DocumentOut.model_validate(d) for d in rows]
    return DocumentPage(items=items, next_cursor=next_cursor)


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID, principal: Reader, services: ServicesDep
) -> DocumentOut:
    document = await services.documents.get(principal.namespace_id, document_id)
    return DocumentOut.model_validate(document)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID, principal: Reviewer, services: ServicesDep
) -> DocumentOut:
    """Out of retrieval at once, out of the provider when the Syncer runs (the row is kept); a
    document waiting for review is rejected instead."""
    op = RetireKbDocument(principal.namespace_id, document_id)
    return DocumentOut.model_validate(await services.governance.apply(op, principal.actor))
