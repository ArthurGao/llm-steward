"""The KB risk label's response cache (design §11 P10): lite-cache's AsyncCacheHook on the real
lite-model client, its store in the test database. The model is a mock HTTP endpoint: never a
real one. PostgreSQL only (lite-cache's store has no SQLite counterpart)."""

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import lite_model
import psycopg
import pytest
from psycopg import sql
from pydantic import ValidationError

from llm_steward.config import Settings
from llm_steward.llm.client import LiteModelLLM, cache_scope, open_cache_store, register_cache
from llm_steward.llm.roles import ROLES, Role
from llm_steward.pipeline.kb_llm import WorthinessReply, label_risk, score_memories
from llm_steward.services.container import caches
from llm_steward.storage.db import libpq_url
from tests.fakes.http import chat_reply, mock_http

# Not *_test: Settings reserves those for the fixtures that drop them; this one drops its own.
SCHEMA = "steward_cache_tests"
SQLITE = "sqlite+aiosqlite:///./x.db"


@pytest.fixture
def cache_url(pg_url: str) -> Iterator[str]:
    url = libpq_url(pg_url)
    assert url is not None

    def drop() -> None:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))

    drop()
    yield pg_url
    drop()


class Endpoint:
    """Chat completions: every risk label `medium`, every worthiness batch empty; recorded."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        name = body["response_format"]["json_schema"]["name"]
        answer = (
            {"items": []} if name == "WorthinessReply" else {"risk_level": "medium", "reason": "r"}
        )
        return chat_reply("gpt-x", json.dumps(answer))


def _settings(database_url: str, **values: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=database_url,
        llm={
            "backend": "openai",
            "model": "gpt-x",
            "max_retries": 0,
            "backends": {"openai": {"base_url": "http://vendor.test/v1", "keyless": True}},
        },
        cache={"enabled": True, "db_schema": SCHEMA},
        **values,
    )


@pytest.fixture
def cached(cache_url: str) -> Iterator[tuple[LiteModelLLM, Endpoint]]:
    """The adapter as `role_llm` builds it with the cache on, and its endpoint."""
    settings = _settings(cache_url)
    assert settings.llm is not None
    up = Endpoint()
    model = lite_model.build_async(settings.llm, ROLES, http_client=mock_http(up))
    store = open_cache_store(settings)
    try:
        yield LiteModelLLM(model, cache=register_cache(model, store, settings.cache.ttl_s)), up
    finally:
        store.close()


async def test_a_repeated_risk_label_in_a_namespace_is_answered_from_the_cache(
    cached: tuple[LiteModelLLM, Endpoint],
) -> None:
    llm, up = cached
    a, b = cache_scope("ns-a"), cache_scope("ns-b")
    first = await label_risk(llm, "Refunds", "5 days", cache_scope=a)
    again = await label_risk(llm, "Refunds", "5 days", cache_scope=a)
    await label_risk(llm, "Refunds", "5 days", cache_scope=b)  # another namespace: never shared
    await label_risk(llm, "Refunds", "6 days", cache_scope=a)  # other text: another key
    sent = [json.loads(r["messages"][1]["content"])["content"] for r in up.requests]
    assert sent == ["5 days", "5 days", "6 days"]
    assert (again.level, again.reason, again.provenance) == (
        first.level, first.reason, first.provenance,
    )  # fmt: skip


async def test_only_the_risk_label_is_cached(cached: tuple[LiteModelLLM, Endpoint]) -> None:
    llm, up = cached
    scope = cache_scope("ns-a")
    for _ in range(2):
        await label_risk(llm, "Refunds", "5 days")  # no scope
        await score_memories(llm, [("m1", "Refunds take 5 days")])  # kb.worthiness
        # even with a scope, a role outside CACHED_ROLES is never answered from the cache
        await llm.structured(Role.KB_WORTHINESS, "s", "u", WorthinessReply, cache_scope=scope)
    assert len(up.requests) == 6


async def test_without_a_policy_nothing_is_cached(cache_url: str) -> None:
    settings = _settings(cache_url)
    assert settings.llm is not None
    up = Endpoint()
    llm = LiteModelLLM(lite_model.build_async(settings.llm, ROLES, http_client=mock_http(up)))
    for _ in range(2):
        await label_risk(llm, "Refunds", "5 days", cache_scope=cache_scope("ns-a"))
    assert len(up.requests) == 2


def test_the_cache_opens_only_when_a_kb_label_can_use_it(cache_url: str) -> None:
    assert caches(_settings(cache_url, kb_llm=True))
    assert not caches(_settings(cache_url, kb_llm=False))  # memory roles are never cached


def test_the_cache_needs_postgresql() -> None:
    with pytest.raises(ValidationError, match="needs PostgreSQL"):
        _settings(SQLITE)


@pytest.mark.parametrize("schema", ["steward", "steward_test"])
def test_the_cache_schema_is_never_the_governance_or_a_test_schema(
    cache_url: str, schema: str
) -> None:
    # Codex P10: in the governance schema lite-cache's migrations would run beside its tables;
    # a *_test schema is one the test fixtures drop
    with pytest.raises(ValidationError, match="STEWARD_CACHE__DB_SCHEMA"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url=cache_url,
            db_schema="steward",
            cache={"enabled": True, "db_schema": schema},
        )
