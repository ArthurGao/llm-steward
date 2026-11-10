"""Each llm-steward operation as a `Call` (method, path, body, response model): written once and run
by the sync and the async client alike. Request fields are sent as given (None left out); the
server validates them and answers 422 with the reasons."""

from typing import Any, Literal
from uuid import UUID

from llm_steward_client import _models as m
from llm_steward_client._core import Call

Id = UUID | str


def _body(**fields: Any) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items() if v is not None}


# --- memories ----------------------------------------------------------------------------------


def memory_create(content: str, **fields: Any) -> Call[m.MemoryOut]:
    return Call("POST", "/v1/memories", json=_body(content=content, **fields), model=m.MemoryOut)


def memory_list(**filters: Any) -> Call[m.MemoryPage]:
    return Call("GET", "/v1/memories", params=_body(**filters), model=m.MemoryPage)


def memory_get(memory_id: Id) -> Call[m.MemoryOut]:
    return Call("GET", f"/v1/memories/{memory_id}", model=m.MemoryOut)


def memory_update(memory_id: Id, **patch: Any) -> Call[m.MemoryOut]:
    return Call("PATCH", f"/v1/memories/{memory_id}", json=_body(**patch), model=m.MemoryOut)


def memory_delete(memory_id: Id) -> Call[m.MemoryOut]:
    return Call("DELETE", f"/v1/memories/{memory_id}", model=m.MemoryOut)


def memory_history(memory_id: Id) -> Call[m.MemoryHistoryOut]:
    return Call("GET", f"/v1/memories/{memory_id}/history", model=m.MemoryHistoryOut)


def memory_promote(memory_id: Id, collection_id: Id, user_id: str | None = None) -> Call[m.Written]:
    body = _body(collection_id=collection_id, user_id=user_id)
    return Call("POST", f"/v1/memories/{memory_id}/promote", json=body, model=m.Written)


def erase_user(user_id: str) -> Call[m.ErasedOut]:
    return Call("DELETE", "/v1/memories", params={"user_id": user_id}, model=m.ErasedOut)


def profile(user_id: str | None = None) -> Call[m.ProfileOut]:
    return Call("GET", "/v1/profile", params=_body(user_id=user_id), model=m.ProfileOut)


# --- episodes ----------------------------------------------------------------------------------


def episode_add(
    messages: list[dict[str, str]],
    *,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    mode: Literal["async", "sync"] = "async",
) -> Call[m.EpisodeAccepted]:
    body = _body(
        messages=messages, user_id=user_id, agent_id=agent_id, session_id=session_id, mode=mode
    )
    return Call("POST", "/v1/episodes", json=body, model=m.EpisodeAccepted)


def episode_flush(
    *, user_id: str | None = None, agent_id: str | None = None, session_id: str | None = None
) -> Call[m.FlushResult]:
    body = _body(user_id=user_id, agent_id=agent_id, session_id=session_id)
    return Call("POST", "/v1/episodes/flush", json=body, model=m.FlushResult)


# --- retrieval ---------------------------------------------------------------------------------


def retrieve(
    query: str,
    *,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    format: Literal["items", "context"] = "items",
    **options: Any,
) -> Call[m.RetrieveOut]:
    scope = _body(user_id=user_id, agent_id=agent_id, session_id=session_id)
    body = _body(query=query, scope=scope, format=format, **options)
    return Call("POST", "/v1/retrieve", json=body, model=m.RetrieveOut)


def feedback(
    retrieval_id: Id, item_id: Id, *, helpful: bool, note: str | None = None
) -> Call[None]:
    body = _body(item_id=item_id, helpful=helpful, note=note)
    return Call("POST", f"/v1/retrieve/{retrieval_id}/feedback", json=body)


# --- knowledge ---------------------------------------------------------------------------------


def collection_list() -> Call[m.CollectionPage]:
    return Call("GET", "/v1/kb/collections", model=m.CollectionPage)


def collection_create(name: str, **fields: Any) -> Call[m.CollectionOut]:
    body = _body(name=name, **fields)
    return Call("POST", "/v1/kb/collections", json=body, model=m.CollectionOut)


def collection_update(collection_id: Id, **patch: Any) -> Call[m.CollectionOut]:
    path = f"/v1/kb/collections/{collection_id}"
    return Call("PATCH", path, json=_body(**patch), model=m.CollectionOut)


def entry_create(collection_id: Id, title: str, content: str, **fields: Any) -> Call[m.Written]:
    body = _body(collection_id=collection_id, title=title, content=content, **fields)
    return Call("POST", "/v1/kb/entries", json=body, model=m.Written)


def entry_list(**filters: Any) -> Call[m.EntryPage]:
    return Call("GET", "/v1/kb/entries", params=_body(**filters), model=m.EntryPage)


def entry_get(entry_id: Id) -> Call[m.EntryOut]:
    return Call("GET", f"/v1/kb/entries/{entry_id}", model=m.EntryOut)


def entry_update(entry_id: Id, **patch: Any) -> Call[m.Written]:
    return Call("PATCH", f"/v1/kb/entries/{entry_id}", json=_body(**patch), model=m.Written)


def entry_verify(entry_id: Id) -> Call[m.EntryOut]:
    return Call("POST", f"/v1/kb/entries/{entry_id}/verify", model=m.EntryOut)


def entry_deprecate(entry_id: Id) -> Call[m.EntryOut]:
    return Call("POST", f"/v1/kb/entries/{entry_id}/deprecate", model=m.EntryOut)


def document_upload(
    collection_id: Id,
    *,
    file: tuple[str, bytes, str] | None = None,
    url: str | None = None,
    title: str | None = None,
    risk_level: str | None = None,
) -> Call[m.DocumentWritten]:
    """`file` is (filename, bytes, content type); or a `url` the server fetches. Exactly one."""
    form = _body(collection_id=collection_id, url=url, title=title, risk_level=risk_level)
    return Call(
        "POST",
        "/v1/kb/documents",
        data=form,
        files={"file": file} if file is not None else None,
        model=m.DocumentWritten,
    )


def document_list(**filters: Any) -> Call[m.DocumentPage]:
    return Call("GET", "/v1/kb/documents", params=_body(**filters), model=m.DocumentPage)


def document_get(document_id: Id) -> Call[m.DocumentOut]:
    return Call("GET", f"/v1/kb/documents/{document_id}", model=m.DocumentOut)


def document_delete(document_id: Id) -> Call[m.DocumentOut]:
    return Call("DELETE", f"/v1/kb/documents/{document_id}", model=m.DocumentOut)


# --- governance --------------------------------------------------------------------------------


def suggest(collection_id: Id, text: str, **fields: Any) -> Call[m.Written]:
    body = _body(collection_id=collection_id, text=text, **fields)
    return Call("POST", "/v1/suggestions", json=body, model=m.Written)


def candidate_list(**filters: Any) -> Call[m.CandidatePage]:
    return Call("GET", "/v1/candidates", params=_body(**filters), model=m.CandidatePage)


def candidate_decide(
    candidate_id: Id,
    to: Literal["accept", "reject"],
    *,
    user_id: str | None = None,
    scope: Literal["personal", "team"] | None = None,
    draft: dict[str, Any] | None = None,
    note: str | None = None,
) -> Call[m.DecisionOut]:
    body = _body(user_id=user_id, scope=scope, draft=draft, note=note)
    return Call("POST", f"/v1/candidates/{candidate_id}/{to}", json=body, model=m.DecisionOut)


def candidate_accept(candidate_id: Id, **decision: Any) -> Call[m.DecisionOut]:
    """Decision: user_id (the end user answering their card), scope, draft, note."""
    return candidate_decide(candidate_id, "accept", **decision)


def candidate_reject(candidate_id: Id, **decision: Any) -> Call[m.DecisionOut]:
    return candidate_decide(candidate_id, "reject", **decision)


# --- access and status -------------------------------------------------------------------------


def user_token(user_id: str, ttl_seconds: int | None = None) -> Call[m.UserTokenOut]:
    body = _body(user_id=user_id, ttl_seconds=ttl_seconds)
    return Call("POST", "/v1/tokens/user", json=body, model=m.UserTokenOut)


def job_get(job_id: Id) -> Call[m.JobOut]:
    return Call("GET", f"/v1/jobs/{job_id}", model=m.JobOut)
