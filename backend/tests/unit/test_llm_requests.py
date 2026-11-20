"""The request bodies llm-steward sends for each LLM role (lite-model design §12.2 step 1).

`tests/fixtures/llm_requests.json` was captured from the client llm-steward had before it moved onto
lite-model (llm-steward dfb7c9e); the client it has now must send the same request. Only `_llm`
knows how the client is built."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx2
import lite_model
import pytest

from llm_steward.config import Settings
from llm_steward.llm.client import LLM, LiteModelLLM
from llm_steward.llm.roles import ROLES
from llm_steward.pipeline.conflict import Fact, resolve_conflict
from llm_steward.pipeline.extract import extract_facts
from llm_steward.pipeline.kb_llm import draft_entry, label_risk, score_memories

RECORDED = Path(__file__).parents[1] / "fixtures" / "llm_requests.json"

# The deployment the cases run under: one OpenAI-compatible endpoint (llm-gateway in development)
# with a key, a default model, and one role on another model (before lite-model: an endpoint, a
# key, a default model and per-role model names).
SETTINGS: dict[str, Any] = {
    "database_url": "sqlite+aiosqlite:///unused.db",
    "llm": {
        "backend": "gateway",
        "model": "gpt-sub",
        "roles": {"memory.resolve": {"model": "claude-sub"}},
        "backends": {"gateway": {"base_url": "http://gateway.test/v1",
                                 "api_key_env": "STEWARD_TEST_GATEWAY_KEY"}},
    },
}  # fmt: skip

# role -> (its reply, the call that makes it)
CASES: dict[str, tuple[dict[str, Any], Callable[[LLM], Awaitable[Any]]]] = {
    "memory.extract": (
        {"facts": ["Lives in Wellington"]},
        lambda llm: extract_facts(llm, [
            {"role": "user", "content": "我住在惠灵顿，是护士。"},
            {"role": "assistant", "content": "Noted."},
        ]),
    ),
    "memory.resolve": (
        {"action": "UPDATE", "target_id": "m1", "reason": "moved"},
        lambda llm: resolve_conflict(
            llm, Fact("new", "Lives in Auckland"), [Fact("m1", "Lives in Wellington")]
        ),
    ),
    "kb.risk": (
        {"risk_level": "low", "reason": "general"},
        lambda llm: label_risk(llm, "Pour-over ratio", "1:16 coffee to water."),
    ),
    "kb.worthiness": (
        {"items": [{"id": "m1", "reusability": 0.9, "novelty": 0.5, "timeliness": 0.8,
                    "generality": 0.9, "trust": 0.7}]},
        lambda llm: score_memories(llm, [("m1", "Pour-over uses 1:16 coffee to water.")]),
    ),
    "kb.draft": (
        {"title": "Pour-over ratio", "content": "Use 1:16.", "risk_level": "low", "reason": "ok"},
        lambda llm: draft_entry(llm, "Pour-over uses 1:16 coffee to water."),
    ),
}  # fmt: skip


def _reply(body: dict[str, Any], answer: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": body["model"],
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(answer)}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })  # fmt: skip


def _llm(monkeypatch: pytest.MonkeyPatch, http: httpx2.AsyncClient) -> LLM:
    """The client llm-steward builds, over a recording transport."""
    monkeypatch.setenv("STEWARD_TEST_GATEWAY_KEY", "gateway-key")
    settings = Settings(_env_file=None, **SETTINGS)  # type: ignore[call-arg]
    assert settings.llm is not None
    return LiteModelLLM(lite_model.build_async(settings.llm, ROLES, http_client=http))


async def record(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Every case: what reached the wire."""
    seen: dict[str, Any] = {}
    for case, (answer, call) in CASES.items():
        sent: list[httpx2.Request] = []

        def handler(
            request: httpx2.Request,
            sent: list[httpx2.Request] = sent,
            answer: dict[str, Any] = answer,
        ) -> httpx2.Response:
            sent.append(request)
            return _reply(json.loads(request.content), answer)

        http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        await call(_llm(monkeypatch, http))
        [request] = sent  # one request per call: no retry, no second call
        seen[case] = {
            "method": request.method,
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
            "body": json.loads(request.content),
        }
    return seen


async def test_every_role_sends_the_recorded_request(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    seen = await record(monkeypatch)
    assert sorted(seen) == sorted(recorded)
    for case in recorded:
        assert seen[case] == recorded[case], case
