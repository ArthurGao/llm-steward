"""lite-mem MemoryProvider (design §8, lite-mem S9): the owner's own memory engine, over HTTP
(default: lite-mem runs as its own service) or in-process (extra `litemem`).

lite-mem extracts **and** resolves conflicts (RESOLVES_CONFLICTS): its decisions come back as
events — a SUPERSEDE as an ADD that `replaces` the memory it closed, a DELETE as a DELETE — and the
extraction mirror applies them (services/extraction_service.py). Other lite-mem events (CORRECT,
MERGE, EXPIRE) do not arise from the calls llm-steward makes and are ignored: nothing runs
lite-mem's `maintain` on llm-steward's namespaces (lite-mem design §10).

Mapping, defined only here:
- a llm-steward namespace is the lite-mem namespace `{prefix}/{namespace_id}`; scope fields map one
  to one (all None: shared within the namespace, in both systems);
- a memory's external id is `{namespace_id}/{lite-mem memory id}`, because get / update / delete
  are given no scope;
- an inferred add carries llm-steward's idempotency key (the batch's last episode id); a governed
  memory pushed by the Syncer (`infer=False`) is lite-mem's `remember` with the key the Syncer
  gives (the memory's id), so a re-push returns the same memory, and a secret is refused instead
  of silently dropped."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import httpx2

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.providers.base import (
    DELETE,
    EXTRACT,
    METADATA_FILTER,
    RESOLVES_CONFLICTS,
    UPDATE,
    ComponentHealth,
    MemoryEvent,
    MemoryRecord,
    Message,
    ProviderHit,
    Scope,
)

if TYPE_CHECKING:
    from lite_mem.factory import Components, Engines

log = logging.getLogger(__name__)
LIST_PAGE = 1000  # lite-mem's HTTP default page; its hard maximum is 10 000


@dataclass(frozen=True)
class LiteScope:
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class LiteEvent:
    op: str  # ADD, SUPERSEDE, DELETE, CORRECT, MERGE, EXPIRE
    memory_id: str
    content: str | None = None
    produced_by: str | None = None
    replaced: str | None = None


@dataclass(frozen=True)
class LiteMemory:
    memory_id: str
    content: str
    scope: LiteScope
    metadata: dict[str, Any] = field(default_factory=dict)
    current: bool = True  # False: retired or superseded (lite-mem keeps it for its history)


@dataclass(frozen=True)
class LiteHit:
    memory_id: str
    score: float
    content: str


class Transport(Protocol):
    """lite-mem's own operations, in its own terms (namespace, scope, memory id)."""

    async def add(
        self,
        namespace: str,
        messages: list[Message],
        scope: LiteScope,
        metadata: dict[str, Any] | None,
        key: str | None,
    ) -> list[LiteEvent]: ...

    async def remember(
        self,
        namespace: str,
        content: str,
        scope: LiteScope,
        metadata: dict[str, Any],
        key: str | None,
    ) -> list[LiteEvent]: ...

    async def search(
        self,
        namespace: str,
        query: str,
        scope: LiteScope,
        top_k: int,
        metadata: dict[str, Any] | None,
    ) -> list[LiteHit]:
        """The scope's own memories and the namespace's shared ones."""
        ...

    async def get(self, namespace: str, memory_id: str) -> LiteMemory | None: ...

    async def update(self, namespace: str, memory_id: str, content: str) -> None: ...

    async def delete(self, namespace: str, memory_id: str) -> None:
        """Idempotent: a memory that is gone or no longer current is not an error."""
        ...

    async def list(self, namespace: str, scope: LiteScope) -> list[LiteMemory]:
        """Exactly the scope's current memories (not the shared ones)."""
        ...

    async def health(self) -> None:
        """Raises `ProviderUnavailable` when lite-mem cannot serve (its store or its model)."""
        ...

    async def aclose(self) -> None: ...


class LiteMemProvider:
    name = "litemem"
    capabilities = frozenset({EXTRACT, RESOLVES_CONFLICTS, UPDATE, DELETE, METADATA_FILTER})

    def __init__(self, transport: Transport, namespace_prefix: str = "steward") -> None:
        self._transport = transport
        self._prefix = namespace_prefix

    def _namespace(self, namespace_id: uuid.UUID) -> str:
        return f"{self._prefix}/{namespace_id}"

    @staticmethod
    def _external(namespace_id: uuid.UUID, memory_id: str) -> str:
        return f"{namespace_id}/{memory_id}"

    def _locate(self, external_id: str) -> tuple[uuid.UUID, str, str]:
        """(namespace id, lite-mem namespace, lite-mem memory id) of an external id."""
        try:  # two parts, both uuids: anything else is ValueError
            namespace_id, memory_id = (uuid.UUID(p) for p in external_id.split("/"))
        except ValueError as exc:  # ids come from our own external_refs: a broken invariant
            raise ValueError(f"not a lite-mem memory id: {external_id!r}") from exc
        return namespace_id, self._namespace(namespace_id), str(memory_id)

    @staticmethod
    def _scope(scope: Scope) -> LiteScope:
        return LiteScope(scope.user_id, scope.agent_id, scope.session_id)

    def _events(self, namespace_id: uuid.UUID, events: list[LiteEvent]) -> list[MemoryEvent]:
        out: list[MemoryEvent] = []
        for e in events:
            ext = self._external(namespace_id, e.memory_id)
            if e.op == "ADD":
                out.append(MemoryEvent("ADD", ext, e.content, e.produced_by))
            elif e.op == "SUPERSEDE" and e.replaced:
                replaced = self._external(namespace_id, e.replaced)
                out.append(MemoryEvent("ADD", ext, e.content, e.produced_by, replaced))
            elif e.op == "DELETE":
                out.append(MemoryEvent("DELETE", ext, e.content, e.produced_by))
            else:  # CORRECT / MERGE / EXPIRE: not from the calls llm-steward makes
                log.warning("lite-mem event %s on %s ignored", e.op, ext)
        return out

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> list[MemoryEvent]:
        ns = self._namespace(scope.namespace_id)
        if infer:
            events = await self._transport.add(
                ns, messages, self._scope(scope), metadata, idempotency_key
            )
        else:  # a governed memory, pushed as stated: lite-mem's `remember`, keyed by the caller
            content = "\n".join(m.content for m in messages if m.content.strip())
            events = await self._transport.remember(
                ns, content, self._scope(scope), dict(metadata or {}), idempotency_key
            )
        return self._events(scope.namespace_id, events)

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]:
        ns = self._namespace(scope.namespace_id)
        hits = await self._transport.search(ns, query, self._scope(scope), top_k, filters)
        return [
            ProviderHit(self._external(scope.namespace_id, h.memory_id), h.score, h.content)
            for h in hits
        ]

    async def get(self, external_id: str) -> MemoryRecord | None:
        namespace_id, ns, memory_id = self._locate(external_id)
        found = await self._transport.get(ns, memory_id)
        if found is None or not found.current:  # a retired memory is gone for llm-steward
            return None
        return self._record(namespace_id, found)

    async def update(self, external_id: str, content: str) -> None:
        _, ns, memory_id = self._locate(external_id)
        await self._transport.update(ns, memory_id, content)

    async def delete(self, external_id: str) -> None:
        _, ns, memory_id = self._locate(external_id)
        await self._transport.delete(ns, memory_id)

    async def list(self, scope: Scope) -> list[MemoryRecord]:
        ns = self._namespace(scope.namespace_id)
        found = await self._transport.list(ns, self._scope(scope))
        return [self._record(scope.namespace_id, m) for m in found]

    def _record(self, namespace_id: uuid.UUID, m: LiteMemory) -> MemoryRecord:
        s = m.scope
        scope = Scope(namespace_id, s.user_id, s.agent_id, s.session_id)
        return MemoryRecord(self._external(namespace_id, m.memory_id), m.content, scope, m.metadata)

    async def health(self) -> ComponentHealth:
        try:
            await self._transport.health()
        except ProviderUnavailable as exc:
            return ComponentHealth(self.name, "error", exc.detail)
        return ComponentHealth(self.name, "ok")

    async def aclose(self) -> None:
        await self._transport.aclose()


# --- transports ------------------------------------------------------------------------------


def _scope_body(scope: LiteScope) -> dict[str, str | None]:
    return {"user_id": scope.user_id, "agent_id": scope.agent_id, "session_id": scope.session_id}


def _event(e: dict[str, Any]) -> LiteEvent:
    return LiteEvent(e["op"], str(e["memory_id"]), e.get("content"), e.get("produced_by"),
                     None if e.get("replaced") is None else str(e["replaced"]))  # fmt: skip


def _memory(m: dict[str, Any]) -> LiteMemory:
    s = m.get("scope") or {}
    scope = LiteScope(s.get("user_id"), s.get("agent_id"), s.get("session_id"))
    return LiteMemory(str(m["id"]), m["content"], scope, m.get("metadata") or {},
                      m.get("valid_to") is None)  # fmt: skip


class HttpTransport:
    """lite-mem's HTTP API (`litemem serve`), with an API key whose namespaces cover the prefix."""

    def __init__(
        self, http: httpx2.AsyncClient, base_url: str, api_key: str | None, timeout_s: float
    ) -> None:
        self._http = http  # shared, owned by the container
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def _call(
        self,
        method: str,
        path: str,
        namespace: str | None,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ok: tuple[int, ...] = (200,),
    ) -> httpx2.Response:
        """The response when its status is in `ok`. Otherwise a lite-mem error: the title goes
        up, the detail (lite-mem's own words) to the log; a refused request (400, 409) is the
        caller's to fix, anything else lite-mem cannot serve."""
        sent = dict(headers or {})
        if namespace:
            sent["X-LiteMem-Namespace"] = namespace
        if self._api_key:
            sent["Authorization"] = f"Bearer {self._api_key}"
        what = f"lite-mem {method} {path}"
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", json=body, params=params, headers=sent,
                timeout=self._timeout_s,
            )  # fmt: skip
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable(f"{what}: {type(exc).__name__}") from exc
        if response.status_code in ok:
            return response
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        log.warning("%s: %s %s", what, response.status_code, problem)
        message = f"{what}: HTTP {response.status_code} {problem.get('title', '')}".rstrip()
        if response.status_code in (400, 409):
            raise Unprocessable(f"{message}: {problem.get('detail', '')}".rstrip(": "))
        raise ProviderUnavailable(message)

    @staticmethod
    def _key(key: str | None) -> dict[str, str]:
        return {"Idempotency-Key": key} if key else {}

    async def add(
        self,
        namespace: str,
        messages: list[Message],
        scope: LiteScope,
        metadata: dict[str, Any] | None,
        key: str | None,
    ) -> list[LiteEvent]:
        body = {"messages": [{"role": m.role, "content": m.content} for m in messages],
                "scope": _scope_body(scope), "infer": True, "metadata": metadata}  # fmt: skip
        response = await self._call("POST", "/v1/memories:add", namespace, body,
                                    headers=self._key(key))  # fmt: skip
        return [_event(e) for e in response.json()["events"]]

    async def remember(
        self,
        namespace: str,
        content: str,
        scope: LiteScope,
        metadata: dict[str, Any],
        key: str | None,
    ) -> list[LiteEvent]:
        body = {"content": content, "scope": _scope_body(scope), "metadata": metadata}
        response = await self._call("POST", "/v1/memories", namespace, body,
                                    headers=self._key(key))  # fmt: skip
        return [_event(e) for e in response.json()["events"]]

    async def search(
        self,
        namespace: str,
        query: str,
        scope: LiteScope,
        top_k: int,
        metadata: dict[str, Any] | None,
    ) -> list[LiteHit]:
        body = {"query": query, "scope": _scope_body(scope), "top_k": top_k,
                "include_shared": True, "metadata": metadata}  # fmt: skip
        response = await self._call("POST", "/v1/memories:search", namespace, body)
        return [
            LiteHit(str(h["memory"]["id"]), h["score"], h["memory"]["content"])
            for h in response.json()["hits"]
        ]

    async def get(self, namespace: str, memory_id: str) -> LiteMemory | None:
        response = await self._call("GET", f"/v1/memories/{memory_id}", namespace, ok=(200, 404))
        return None if response.status_code == 404 else _memory(response.json())

    async def update(self, namespace: str, memory_id: str, content: str) -> None:
        await self._call("PATCH", f"/v1/memories/{memory_id}", namespace, {"content": content})

    async def delete(self, namespace: str, memory_id: str) -> None:
        # 404: never there; 409: no longer current (already retired or superseded)
        await self._call("DELETE", f"/v1/memories/{memory_id}", namespace, ok=(200, 404, 409))

    async def list(self, namespace: str, scope: LiteScope) -> list[LiteMemory]:
        found: list[LiteMemory] = []
        params = {k: v for k, v in _scope_body(scope).items() if v is not None}
        while True:
            page = {**params, "limit": LIST_PAGE, "offset": len(found)}
            response = await self._call("GET", "/v1/memories", namespace, params=page)
            batch = [_memory(m) for m in response.json()["memories"]]
            found += batch
            if len(batch) < LIST_PAGE:
                return found

    async def health(self) -> None:
        await self._call("GET", "/health", None, params={"deep": "true"})

    async def aclose(self) -> None:
        pass  # the HTTP client belongs to the container


class InProcessTransport:
    """lite-mem as a library in this process (extra `litemem`). Its calls block, so they run in
    worker threads — at most `concurrency` at once (its store pool is sized to match). One engine
    per namespace, kept by lite-mem's own `Engines`."""

    def __init__(self, components: "Components", engines: "Engines", concurrency: int) -> None:
        self._components = components
        self._engines = engines
        self._slots = asyncio.Semaphore(concurrency)

    async def _run(self, what: str, call: Callable[[], Any], *, missing: Any = None) -> Any:
        """`call` in a worker thread. A memory that is not found returns `missing`; lite-mem's
        other errors become ours."""
        from lite_mem.errors import Conflict, InvalidRequest, LiteMemError, NotFound, redact

        try:
            async with self._slots:
                return await asyncio.to_thread(call)
        except NotFound:
            return missing
        except (InvalidRequest, Conflict) as exc:
            raise Unprocessable(f"lite-mem {what}: {exc}") from exc
        except LiteMemError as exc:
            # a store error can carry a database URL: never log credentials
            log.warning("lite-mem %s: %s: %s", what, type(exc).__name__, redact(str(exc)))
            raise ProviderUnavailable(f"lite-mem {what}: {type(exc).__name__}") from exc

    @staticmethod
    def _scope(scope: LiteScope) -> Any:
        from lite_mem.types import Scope as MemScope

        return MemScope(scope.user_id, scope.agent_id, scope.session_id)

    @staticmethod
    def _event(e: Any) -> LiteEvent:
        return LiteEvent(e.op, str(e.memory_id), e.content, e.produced_by,
                         None if e.replaced is None else str(e.replaced))  # fmt: skip

    @staticmethod
    def _memory(m: Any) -> LiteMemory:
        s = m.scope
        return LiteMemory(str(m.id), m.content, LiteScope(s.user_id, s.agent_id, s.session_id),
                          dict(m.metadata), m.current)  # fmt: skip

    async def add(
        self,
        namespace: str,
        messages: list[Message],
        scope: LiteScope,
        metadata: dict[str, Any] | None,
        key: str | None,
    ) -> list[LiteEvent]:
        from lite_mem.types import Message as MemMessage

        said = [MemMessage(m.role, m.content) for m in messages]
        events = await self._run("add", lambda: self._engines(namespace).add(
            said, self._scope(scope), infer=True, metadata=metadata, idempotency_key=key
        ))  # fmt: skip
        return [self._event(e) for e in events]

    async def remember(
        self,
        namespace: str,
        content: str,
        scope: LiteScope,
        metadata: dict[str, Any],
        key: str | None,
    ) -> list[LiteEvent]:
        from lite_mem.types import Fact

        fact = Fact(content, metadata=metadata)
        events = await self._run("remember", lambda: self._engines(namespace).remember(
            fact, self._scope(scope), idempotency_key=key
        ))  # fmt: skip
        return [self._event(e) for e in events]

    async def search(
        self,
        namespace: str,
        query: str,
        scope: LiteScope,
        top_k: int,
        metadata: dict[str, Any] | None,
    ) -> list[LiteHit]:
        hits = await self._run("search", lambda: self._engines(namespace).search(
            query, self._scope(scope), top_k=top_k, include_shared=True, metadata=metadata
        ))  # fmt: skip
        return [LiteHit(str(h.memory.id), h.score, h.memory.content) for h in hits]

    async def get(self, namespace: str, memory_id: str) -> LiteMemory | None:
        found = await self._run("get", lambda: self._engines(namespace).get(uuid.UUID(memory_id)))
        return None if found is None else self._memory(found)

    async def update(self, namespace: str, memory_id: str, content: str) -> None:
        await self._run("update", lambda: self._engines(namespace).update(
            uuid.UUID(memory_id), content
        ))  # fmt: skip

    async def delete(self, namespace: str, memory_id: str) -> None:
        from lite_mem.errors import Conflict

        def retire() -> None:
            try:
                self._engines(namespace).delete(uuid.UUID(memory_id))
            except Conflict:  # no longer current: already retired or superseded
                pass

        await self._run("delete", retire)

    async def list(self, namespace: str, scope: LiteScope) -> list[LiteMemory]:
        found = await self._run("list", lambda: self._engines(namespace).list_memories(
            self._scope(scope), include_shared=False
        ))  # fmt: skip
        return [self._memory(m) for m in found]

    async def health(self) -> None:
        parts = await self._run("health", lambda: self._components.health(deep=True))
        failed = sorted(k for k, v in parts.items() if v != "ok")
        if failed:
            raise ProviderUnavailable(f"lite-mem: {', '.join(failed)} failed")

    async def aclose(self) -> None:
        await asyncio.to_thread(self._components.store.close)
