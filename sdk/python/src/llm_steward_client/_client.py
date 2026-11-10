"""`Steward` (sync) and `AsyncSteward`: the same resources over the `_api` builders.

    st = Steward(base_url="http://127.0.0.1:8600", api_key="stw_...")
    st.episodes.add(messages=[{"role": "user", "content": "..."}], user_id="u_123")
    ctx = st.retrieve("my last refund", user_id="u_123", format="context").context

Each method is its `_api` builder run by the client (`_sync` / `_async` keep the builder's typed
parameters), so the two clients cannot drift. A browser-side caller uses a user token instead of
an API key (`st.tokens.user("u_123")` on your server); it reaches only that user's own data.
"""

from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack, ExitStack
from typing import Any, Concatenate, Self

import httpx

from llm_steward_client import _api
from llm_steward_client._core import Call, aexecute, execute

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRIES = 3


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"User-Agent": "llm-steward-client/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


# --- sync --------------------------------------------------------------------------------------


class _Sync:
    def __init__(self, client: "Steward") -> None:
        self._client = client


def _sync[**P, T](builder: Callable[P, Call[T]]) -> Callable[Concatenate[Any, P], T]:
    def method(self: Any, /, *args: P.args, **kwargs: P.kwargs) -> T:
        client: Steward = self if isinstance(self, Steward) else self._client
        return client._run(builder(*args, **kwargs))

    method.__doc__ = builder.__doc__
    return method


class Memories(_Sync):
    create = _sync(_api.memory_create)
    list = _sync(_api.memory_list)
    get = _sync(_api.memory_get)
    update = _sync(_api.memory_update)
    delete = _sync(_api.memory_delete)
    history = _sync(_api.memory_history)
    promote = _sync(_api.memory_promote)
    erase_user = _sync(_api.erase_user)


class Episodes(_Sync):
    add = _sync(_api.episode_add)
    flush = _sync(_api.episode_flush)


class Collections(_Sync):
    list = _sync(_api.collection_list)
    create = _sync(_api.collection_create)
    update = _sync(_api.collection_update)


class Entries(_Sync):
    create = _sync(_api.entry_create)
    list = _sync(_api.entry_list)
    get = _sync(_api.entry_get)
    update = _sync(_api.entry_update)
    verify = _sync(_api.entry_verify)
    deprecate = _sync(_api.entry_deprecate)


class Documents(_Sync):
    upload = _sync(_api.document_upload)
    list = _sync(_api.document_list)
    get = _sync(_api.document_get)
    delete = _sync(_api.document_delete)


class Knowledge:
    def __init__(self, client: "Steward") -> None:
        self.collections = Collections(client)
        self.entries = Entries(client)
        self.documents = Documents(client)


class Candidates(_Sync):
    list = _sync(_api.candidate_list)
    accept = _sync(_api.candidate_accept)
    reject = _sync(_api.candidate_reject)


class Tokens(_Sync):
    user = _sync(_api.user_token)


class Jobs(_Sync):
    get = _sync(_api.job_get)


class Steward:
    """Sync client. Pass `http_client` to bring your own `httpx.Client` (its base URL is used;
    it is never changed: the credential goes on each request)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8600",
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_RETRIES,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._stack = ExitStack()  # what close() releases: our own http client, an embedded app
        self._http = http_client or self._stack.enter_context(
            httpx.Client(base_url=base_url, timeout=timeout)
        )
        self._headers = _headers(api_key)
        self.max_retries = max_retries
        self.memories = Memories(self)
        self.episodes = Episodes(self)
        self.kb = Knowledge(self)
        self.candidates = Candidates(self)
        self.tokens = Tokens(self)
        self.jobs = Jobs(self)

    @classmethod
    def embedded(cls, database_url: str, **options: Any) -> "Steward":
        """llm-steward in this process (no server), e.g. on `sqlite+aiosqlite:///./steward.db`.
        Needs the `embedded` extra; options in `llm_steward_client._embedded`."""
        from llm_steward_client._embedded import embedded_sync

        return embedded_sync(cls, database_url, **options)

    def _run[T](self, call: Call[T]) -> T:
        return execute(self._http, call, max_retries=self.max_retries, headers=self._headers)

    retrieve = _sync(_api.retrieve)
    feedback = _sync(_api.feedback)
    profile = _sync(_api.profile)
    suggest = _sync(_api.suggest)

    def close(self) -> None:
        self._stack.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# --- async -------------------------------------------------------------------------------------


class _Async:
    def __init__(self, client: "AsyncSteward") -> None:
        self._client = client


def _async[**P, T](
    builder: Callable[P, Call[T]],
) -> Callable[Concatenate[Any, P], Coroutine[Any, Any, T]]:
    async def method(self: Any, /, *args: P.args, **kwargs: P.kwargs) -> T:
        client: AsyncSteward = self if isinstance(self, AsyncSteward) else self._client
        return await client._run(builder(*args, **kwargs))

    method.__doc__ = builder.__doc__
    return method


class AsyncMemories(_Async):
    create = _async(_api.memory_create)
    list = _async(_api.memory_list)
    get = _async(_api.memory_get)
    update = _async(_api.memory_update)
    delete = _async(_api.memory_delete)
    history = _async(_api.memory_history)
    promote = _async(_api.memory_promote)
    erase_user = _async(_api.erase_user)


class AsyncEpisodes(_Async):
    add = _async(_api.episode_add)
    flush = _async(_api.episode_flush)


class AsyncCollections(_Async):
    list = _async(_api.collection_list)
    create = _async(_api.collection_create)
    update = _async(_api.collection_update)


class AsyncEntries(_Async):
    create = _async(_api.entry_create)
    list = _async(_api.entry_list)
    get = _async(_api.entry_get)
    update = _async(_api.entry_update)
    verify = _async(_api.entry_verify)
    deprecate = _async(_api.entry_deprecate)


class AsyncDocuments(_Async):
    upload = _async(_api.document_upload)
    list = _async(_api.document_list)
    get = _async(_api.document_get)
    delete = _async(_api.document_delete)


class AsyncKnowledge:
    def __init__(self, client: "AsyncSteward") -> None:
        self.collections = AsyncCollections(client)
        self.entries = AsyncEntries(client)
        self.documents = AsyncDocuments(client)


class AsyncCandidates(_Async):
    list = _async(_api.candidate_list)
    accept = _async(_api.candidate_accept)
    reject = _async(_api.candidate_reject)


class AsyncTokens(_Async):
    user = _async(_api.user_token)


class AsyncJobs(_Async):
    get = _async(_api.job_get)


class AsyncSteward:
    """Async client, the same resources as `Steward` with awaitable methods."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8600",
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_RETRIES,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._stack = AsyncExitStack()
        if http_client is None:
            http_client = httpx.AsyncClient(base_url=base_url, timeout=timeout)
            self._stack.push_async_callback(http_client.aclose)
        self._http = http_client
        self._headers = _headers(api_key)
        self.max_retries = max_retries
        self.memories = AsyncMemories(self)
        self.episodes = AsyncEpisodes(self)
        self.kb = AsyncKnowledge(self)
        self.candidates = AsyncCandidates(self)
        self.tokens = AsyncTokens(self)
        self.jobs = AsyncJobs(self)

    @classmethod
    async def embedded(cls, database_url: str, **options: Any) -> "AsyncSteward":
        """llm-steward in this process (no server); close it with `aclose()` (or `async with`)."""
        from llm_steward_client._embedded import embedded_async

        return await embedded_async(cls, database_url, **options)

    async def _run[T](self, call: Call[T]) -> T:
        return await aexecute(self._http, call, max_retries=self.max_retries, headers=self._headers)

    retrieve = _async(_api.retrieve)
    feedback = _async(_api.feedback)
    profile = _async(_api.profile)
    suggest = _async(_api.suggest)

    async def aclose(self) -> None:
        await self._stack.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
