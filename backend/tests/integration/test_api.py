"""HTTP API end to end on both dialects: auth, memories, retrieval, namespace isolation."""

import uuid
from typing import Any

import pytest
from starlette.routing import Route

from llm_steward.api.deps import UNAUTHENTICATED_PATHS
from llm_steward.models.enums import ApiScope, KbStatus
from llm_steward.services.governance_service import (
    CreateCollection,
    TransitionKbEntry,
)
from tests.conftest import ADMIN, AppClient, kb_entry_op, provider_has


def _post_memory(c: AppClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = c.http.post("/v1/memories", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _retrieve(c: AppClient, headers: dict[str, str], **body: Any) -> Any:
    return c.http.post("/v1/retrieve", json=body, headers=headers)


# --- auth -------------------------------------------------------------------------------------


def test_every_route_but_the_listed_ones_requires_a_key(app_client: AppClient) -> None:
    """Default deny, checked from the outside: an operation guarded by the API-key dependency
    carries an OpenAPI security requirement; the docs routes are plain Starlette routes."""
    app = app_client.http.app
    spec = app.openapi()  # type: ignore[attr-defined]
    open_operations = {
        path
        for path, operations in spec["paths"].items()
        for operation in operations.values()
        if not operation.get("security")
    }
    docs_routes = {r.path for r in app.routes if isinstance(r, Route)}  # type: ignore[attr-defined]
    assert open_operations == {"/health"}
    assert open_operations | docs_routes == UNAUTHENTICATED_PATHS


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer nope", "Bearer stw_deadbeef_wrongsecret", "Basic dXNlcjpwYXNz"],
)
def test_missing_or_bad_keys_get_401_problem(
    app_client: AppClient, authorization: str | None
) -> None:
    app_client.new_key("acme")
    headers = {"Authorization": authorization} if authorization else {}
    response = app_client.http.get("/v1/memories", headers=headers)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"


def test_a_revoked_key_stops_working(app_client: AppClient) -> None:
    headers = app_client.new_key("acme")
    assert app_client.http.get("/v1/memories", headers=headers).status_code == 200
    prefix = headers["Authorization"].split("_")[1]
    app_client.call(app_client.services.namespaces.revoke_key, prefix)
    assert app_client.http.get("/v1/memories", headers=headers).status_code == 401


def test_a_read_only_key_cannot_write(app_client: AppClient) -> None:
    headers = app_client.new_key("acme", ApiScope.READ)
    response = app_client.http.post("/v1/memories", json={"content": "x"}, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


# --- memories ---------------------------------------------------------------------------------


def test_memory_lifecycle_through_the_api(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    created = _post_memory(
        app_client, h, content="Prefers short answers", user_id="u1", tags=["pref"]
    )
    assert (created["status"], created["sync_status"], created["origin"]) == (
        "active",
        "pending",
        "direct",
    )

    assert app_client.drain() == 1
    fetched = app_client.http.get(f"/v1/memories/{created['id']}", headers=h).json()
    assert fetched["sync_status"] == "synced"

    deleted = app_client.http.delete(f"/v1/memories/{created['id']}", headers=h).json()
    assert deleted["status"] == "deleted" and deleted["valid_to"] is not None
    assert app_client.drain() == 1
    assert (
        app_client.http.get(f"/v1/memories/{created['id']}", headers=h).json()["sync_status"]
        == "deleted"
    )
    again = app_client.http.delete(f"/v1/memories/{created['id']}", headers=h)
    assert again.status_code == 409 and again.json()["code"] == "conflict"


def test_list_filters_and_pages(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    ids = [
        _post_memory(app_client, h, content=f"fact {i}", user_id="u1", tags=["a"] if i % 2 else [])[
            "id"
        ]
        for i in range(5)
    ]
    _post_memory(app_client, h, content="someone else", user_id="u2")

    page1 = app_client.http.get("/v1/memories?user_id=u1&limit=2", headers=h).json()
    page2 = app_client.http.get(
        f"/v1/memories?user_id=u1&limit=2&cursor={page1['next_cursor']}", headers=h
    ).json()
    page3 = app_client.http.get(
        f"/v1/memories?user_id=u1&limit=2&cursor={page2['next_cursor']}", headers=h
    ).json()
    seen = [m["id"] for p in (page1, page2, page3) for m in p["items"]]
    assert seen == list(reversed(ids))  # newest first, every item exactly once
    assert page3["next_cursor"] is None

    tagged = app_client.http.get("/v1/memories?tag=a", headers=h).json()["items"]
    assert sorted(m["id"] for m in tagged) == sorted([ids[1], ids[3]])


def test_infer_without_an_extracting_provider_is_422(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    response = app_client.http.post("/v1/memories", json={"content": "x", "infer": True}, headers=h)
    assert response.status_code == 422
    assert response.json()["code"] == "unprocessable"


def test_validation_errors_are_problems(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    response = app_client.http.post("/v1/memories", json={"content": ""}, headers=h)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error" and body["errors"][0]["loc"][-1] == "content"


def test_another_namespace_cannot_see_or_touch_my_memories(app_client: AppClient) -> None:
    mine = app_client.new_key("acme")
    theirs = app_client.new_key("globex")
    memory = _post_memory(app_client, mine, content="acme secret plan")
    app_client.drain()

    assert app_client.http.get(f"/v1/memories/{memory['id']}", headers=theirs).status_code == 404
    assert app_client.http.delete(f"/v1/memories/{memory['id']}", headers=theirs).status_code == 404
    assert app_client.http.get("/v1/memories", headers=theirs).json()["items"] == []
    found = _retrieve(app_client, theirs, query="acme secret plan").json()["items"]
    assert found == []
    # and the owner still has it, untouched
    assert (
        app_client.http.get(f"/v1/memories/{memory['id']}", headers=mine).json()["status"]
        == "active"
    )


# --- retrieval --------------------------------------------------------------------------------


def _kb_entry(c: AppClient, ns_id: uuid.UUID, status: KbStatus) -> Any:
    collection = c.apply(CreateCollection(ns_id, "faq"))
    return c.apply(kb_entry_op(ns_id, collection.id, status))


def test_retrieve_returns_memories_and_kb_with_citations(app_client: AppClient) -> None:
    h, ns_id = app_client.new_tenant("acme")
    memory = _post_memory(app_client, h, content="Order 123 asked for a refund", user_id="u1")
    entry = _kb_entry(app_client, ns_id, KbStatus.VERIFIED)
    app_client.drain()

    body = _retrieve(
        app_client, h, query="refund", scope={"user_id": "u1"}, format="context"
    ).json()
    assert {(i["source"], i["id"]) for i in body["items"]} == {
        ("memory", memory["id"]),
        ("kb", str(entry.id)),
    }
    assert body["degraded"] == []
    assert f"[mem:{memory['id']}]" in body["context"]
    assert f"[kb:{entry.id}] Refund time" in body["context"]
    assert "(verified)" in body["context"]  # in the query's language (English here)


def test_users_only_see_their_own_and_shared_memories(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    alice = _post_memory(app_client, h, content="alice likes green tea", user_id="alice")
    _post_memory(app_client, h, content="bob likes green tea", user_id="bob")
    shared = _post_memory(app_client, h, content="the office serves green tea")
    app_client.drain()

    items = _retrieve(app_client, h, query="green tea", scope={"user_id": "alice"}).json()["items"]
    assert {i["id"] for i in items} == {alice["id"], shared["id"]}
    anonymous = _retrieve(app_client, h, query="green tea").json()["items"]
    assert {i["id"] for i in anonymous} == {shared["id"]}


def test_safety_net_hides_retired_knowledge_even_before_sync(app_client: AppClient) -> None:
    h, ns_id = app_client.new_tenant("acme")
    entry = _kb_entry(app_client, ns_id, KbStatus.ACTIVE)
    app_client.drain()
    assert [i["id"] for i in _retrieve(app_client, h, query="refunds").json()["items"]] == [
        str(entry.id)
    ]

    app_client.apply(TransitionKbEntry(ns_id, entry.id, KbStatus.DEPRECATED))
    # worker NOT drained: the provider still holds the entry
    hits = app_client.call(
        app_client.services.providers.kb.search,
        "refunds",
        [app_client.collection_ext_id(entry)],
        top_k=5,
    )
    assert [h_.external_id for h_ in hits] == [str(entry.id)]
    assert _retrieve(app_client, h, query="refunds").json()["items"] == []


def test_drafts_never_reach_the_provider(app_client: AppClient) -> None:
    _, ns_id = app_client.new_tenant("acme")
    entry = _kb_entry(app_client, ns_id, KbStatus.DRAFT)
    assert app_client.drain() == 0
    hits = app_client.call(
        app_client.services.providers.kb.search,
        "refunds",
        [app_client.collection_ext_id(entry)],
        top_k=5,
    )
    assert hits == []


def test_one_failing_source_degrades_and_all_failing_is_503(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns_id = app_client.new_tenant("acme")
    _post_memory(app_client, h, content="refund requested", user_id="u1")
    _kb_entry(app_client, ns_id, KbStatus.ACTIVE)
    app_client.drain()

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("provider down")

    monkeypatch.setattr(app_client.services.providers.kb, "search", broken)
    partial = _retrieve(app_client, h, query="refund", scope={"user_id": "u1"})
    assert partial.status_code == 200
    assert partial.json()["degraded"] == ["kb"]
    assert [i["source"] for i in partial.json()["items"]] == ["memory"]

    monkeypatch.setattr(app_client.services.providers.memory, "search", broken)
    total = _retrieve(app_client, h, query="refund", scope={"user_id": "u1"})
    assert total.status_code == 503
    assert total.json()["code"] == "provider_unavailable"


def test_a_collection_is_created_in_the_provider_once(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns_id = app_client.new_tenant("acme")
    kb = app_client.services.providers.kb
    calls: list[object] = []
    original = kb.ensure_collection

    async def counting(ref: Any) -> str:
        calls.append(ref)
        return await original(ref)

    monkeypatch.setattr(kb, "ensure_collection", counting)
    collection = app_client.apply(CreateCollection(ns_id, "faq"))
    for title in ("Refund time", "Refund fees", "Refund channel"):
        app_client.apply(kb_entry_op(ns_id, collection.id, KbStatus.ACTIVE, title=title))
    assert app_client.drain() == 3
    assert len(calls) == 1
    found = _retrieve(app_client, h, query="refund", top_k=5).json()["items"]
    assert len(found) == 3


def test_a_failed_sync_is_recorded_and_retried(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = app_client.new_key("acme")
    memory = _post_memory(app_client, h, content="flaky")

    async def down(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("engine unreachable")

    monkeypatch.setattr(app_client.services.providers.memory, "add", down)
    assert app_client.drain() == 1  # one attempt, then the job backs off
    view = app_client.http.get(f"/v1/memories/{memory['id']}", headers=h).json()
    assert view["sync_status"] == "failed"

    async def last_error() -> str | None:
        from sqlalchemy import select

        from llm_steward.models import ExternalRef, Job
        from llm_steward.storage.db import session_factory

        async with session_factory(app_client.services.engine)() as s:
            job = (await s.scalars(select(Job))).one()
            assert (job.status, job.attempts) == ("pending", 1)
            return await s.scalar(select(ExternalRef.last_error))

    assert app_client.call(last_error) == "ConnectionError: engine unreachable"


def _ref(c: AppClient, object_id: str) -> Any:
    async def read() -> Any:
        from sqlalchemy import select

        from llm_steward.models import ExternalRef
        from llm_steward.storage.db import session_factory

        async with session_factory(c.services.engine)() as s:
            return await s.scalar(
                select(ExternalRef).where(ExternalRef.object_id == uuid.UUID(object_id))
            )

    return c.call(read)


def test_a_stalled_upsert_cannot_resurrect_a_deleted_memory(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upsert reads the memory, stalls in the provider call; meanwhile the memory is deleted
    and the remove job finishes first. The late upsert must not leave it in the provider."""
    h, ns_id = app_client.new_tenant("acme")
    memory = _post_memory(app_client, h, content="soon gone")
    provider = app_client.services.providers.memory
    original_add = provider.add

    async def add_while_deleted(*args: Any, **kwargs: Any) -> Any:
        from llm_steward.services.governance_service import DeleteMemory

        await app_client.services.governance.apply(
            DeleteMemory(ns_id, uuid.UUID(memory["id"])), ADMIN
        )
        assert await app_client.services.worker.run_once()  # the remove job wins the race
        return await original_add(*args, **kwargs)

    monkeypatch.setattr(provider, "add", add_while_deleted)
    app_client.drain()

    assert not provider_has(app_client, "memory", memory["id"])
    assert _ref(app_client, memory["id"]).sync_status == "deleted"


def test_a_stale_kb_upsert_cannot_overwrite_a_newer_version(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ns_id = app_client.new_tenant("acme")
    entry = _kb_entry(app_client, ns_id, KbStatus.ACTIVE)
    kb = app_client.services.providers.kb
    original = kb.upsert_entry
    raced = False

    async def upsert_racing_an_edit(
        doc: Any, collection: str, existing_id: str | None = None
    ) -> str:
        nonlocal raced
        if not raced:  # the first (v1) job stalls while v2 is written and synced
            raced = True
            from llm_steward.services.governance_service import UpdateKbEntry

            await app_client.services.governance.apply(
                UpdateKbEntry(ns_id, entry.id, content="Refunds now take 7 days"), ADMIN
            )
            assert await app_client.services.worker.run_once()
        return await original(doc, collection, existing_id)

    monkeypatch.setattr(kb, "upsert_entry", upsert_racing_an_edit)
    app_client.drain()

    hits = app_client.call(kb.search, "refunds", [app_client.collection_ext_id(entry)], top_k=5)
    assert [h_.text.splitlines()[-1] for h_ in hits] == ["Refunds now take 7 days"]
    assert _ref(app_client, str(entry.id)).synced_version == 2


def test_an_outdated_push_hands_its_engine_id_to_the_retry(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry changed while it was being pushed: the retry must replace what the engine just
    stored (its id is recorded), not create a second copy that nothing would ever remove."""
    _, ns_id = app_client.new_tenant("acme")
    entry = _kb_entry(app_client, ns_id, KbStatus.ACTIVE)
    kb = app_client.services.providers.kb
    original = kb.upsert_entry
    seen: list[str | None] = []

    async def upsert(doc: Any, collection: str, existing_id: str | None = None) -> str:
        seen.append(existing_id)
        if len(seen) == 1:  # an edit lands while the first push is in flight
            from llm_steward.services.governance_service import UpdateKbEntry

            await app_client.services.governance.apply(
                UpdateKbEntry(ns_id, entry.id, content="Refunds now take 7 days"), ADMIN
            )
        return await original(doc, collection, existing_id)

    monkeypatch.setattr(kb, "upsert_entry", upsert)
    app_client.drain()
    assert seen[0] is None and seen[1:] and all(e == str(entry.id) for e in seen[1:])


def test_a_push_undone_after_a_deprecation_is_retried_until_it_is_gone(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deprecated while being pushed, the remove job already ran (nothing synced yet), and taking
    the pushed copy back out fails once: the removal is retried, not lost with the push job."""
    from llm_steward.errors import ProviderUnavailable

    _, ns_id = app_client.new_tenant("acme")
    entry = _kb_entry(app_client, ns_id, KbStatus.ACTIVE)
    kb = app_client.services.providers.kb
    upsert, remove = kb.upsert_entry, kb.remove
    failures = [ProviderUnavailable("engine down")]

    async def upsert_racing(doc: Any, collection: str, existing_id: str | None = None) -> str:
        await app_client.services.governance.apply(
            TransitionKbEntry(ns_id, entry.id, KbStatus.DEPRECATED), ADMIN
        )
        await app_client.services.worker.drain()  # the remove job runs first
        return await upsert(doc, collection, existing_id)

    async def flaky_remove(external_id: str) -> None:
        if failures:
            raise failures.pop()
        await remove(external_id)

    monkeypatch.setattr(kb, "upsert_entry", upsert_racing)
    monkeypatch.setattr(kb, "remove", flaky_remove)
    app_client.drain()
    app_client.make_jobs_due()
    app_client.drain()
    from llm_steward.providers.base import KbCollectionRef

    ext = app_client.call(kb.ensure_collection, KbCollectionRef(entry.collection_id, ns_id, ""))
    hits = app_client.call(kb.search, "refunds", [ext], top_k=5)
    assert failures == [] and hits == []


def test_a_memory_erased_mid_push_is_removed_even_if_the_first_removal_fails(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row itself is gone (per-user erasure, design rule 6's exception) while its push is in
    flight: the copy the push just made is removed by a job that carries its engine id, and that
    job retries — the push job finding no row cannot drop the removal."""
    from sqlalchemy import delete

    from llm_steward.errors import ProviderUnavailable
    from llm_steward.models import Memory
    from llm_steward.storage.db import session_factory

    h, _ = app_client.new_tenant("acme")
    memory = _post_memory(app_client, h, content="soon erased")
    provider = app_client.services.providers.memory
    add, remove = provider.add, provider.delete
    failures: list[Exception] = []

    async def add_while_erased(*args: Any, **kwargs: Any) -> Any:
        async with session_factory(app_client.services.engine)() as s, s.begin():
            await s.execute(delete(Memory).where(Memory.id == uuid.UUID(memory["id"])))
        events = await add(*args, **kwargs)
        failures.append(ProviderUnavailable("engine down"))  # the first removal fails
        return events

    async def flaky_remove(external_id: str) -> None:
        if failures:
            raise failures.pop()
        await remove(external_id)

    monkeypatch.setattr(provider, "add", add_while_erased)
    monkeypatch.setattr(provider, "delete", flaky_remove)
    app_client.drain()
    app_client.make_jobs_due()
    app_client.drain()
    assert failures == []  # the failing removal did happen
    assert not provider_has(app_client, "memory", memory["id"])
