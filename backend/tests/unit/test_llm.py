"""llm-steward's side of the LLM (lite-model design §12): its configuration, the roles it declares,
what comes back from a call and what is refused. What reaches the wire is pinned in
test_llm_requests.py. Requests go to a mock transport, never a network."""

import json
from typing import Any

import httpx2
import lite_model
import pytest
from pydantic import BaseModel, ValidationError

from llm_steward.config import Settings
from llm_steward.llm.client import LiteModelLLM, LLMError, LLMOutputError, openai_endpoint, role_llm
from llm_steward.llm.roles import ROLES, LLMConfigError, Provenance, Role
from tests.fakes.llm import FakeLLM

DB = "sqlite+aiosqlite:///./x.db"


class Reply(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("STEWARD_LLM", "STEWARD_LLM__BACKEND", "STEWARD_LLM__MODEL", "STEWARD_LLM__ROLES"):
        monkeypatch.delenv(key, raising=False)


def _settings(**llm: Any) -> Settings:
    return Settings(_env_file=None, database_url=DB, llm=llm or None)  # type: ignore[call-arg]


def _chat(model: str, content: str, finish: str = "stop") -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })  # fmt: skip


def _llm(handler: Any) -> LiteModelLLM:
    settings = _settings(
        backend="openai",
        model="gpt-x",
        max_retries=0,
        backends={"openai": {"base_url": "http://vendor.test/v1", "keyless": True}},
    )
    assert settings.llm is not None
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return LiteModelLLM(lite_model.build_async(settings.llm, ROLES, http_client=http))


def test_the_llm_is_configured_in_lite_models_shape_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STEWARD_LLM__BACKEND", "gateway")
    monkeypatch.setenv("STEWARD_LLM__MODEL", "gpt-sub")
    roles = {"memory.resolve": {"model": "claude-sub"}}
    monkeypatch.setenv("STEWARD_LLM__ROLES", json.dumps(roles))
    llm = Settings(_env_file=None, database_url=DB).llm  # type: ignore[call-arg]
    assert llm is not None
    assert (llm.backend, llm.model, llm.roles["memory.resolve"].model) == (
        "gateway", "gpt-sub", "claude-sub"
    )  # fmt: skip
    # llm-steward's own client had a 60 s timeout and the OpenAI SDK's two retries
    assert (llm.timeout_s, llm.max_retries) == (60.0, 2)


@pytest.mark.parametrize(
    "old",
    [
        "STEWARD_LLM_BASE_URL",
        "STEWARD_LLM_API_KEY",
        "STEWARD_LLM_MODEL",
        "STEWARD_LLM_ROLES",
        "STEWARD_LLM_TIMEOUT_S",
    ],
)
def test_a_setting_from_before_lite_model_is_refused(
    monkeypatch: pytest.MonkeyPatch, old: str
) -> None:
    # silently ignored, a deployment would run without its LLM (or on another endpoint)
    monkeypatch.setenv(old, '{"kb.draft": "x"}' if old.endswith("ROLES") else "x")
    with pytest.raises(ValidationError, match="moved to lite-model's shape"):
        Settings(_env_file=None, database_url=DB)  # type: ignore[call-arg]


def test_a_role_needs_an_llm_configured() -> None:
    with pytest.raises(LLMConfigError, match=r"memory\.extract, memory\.resolve: set STEWARD_LLM"):
        role_llm(_settings(), (Role.MEMORY_EXTRACT, Role.MEMORY_RESOLVE))


@pytest.mark.parametrize(
    ("llm", "problem"),
    [
        ({"backend": "openai", "model": "m"}, "set OPENAI_API_KEY"),
        ({"backend": "nope", "model": "m"}, "unknown backend"),
        ({"backend": "gateway", "model": "m", "roles": {"memory.recall": {}}}, "not declared"),
        # a decision on governed knowledge must name a model that can be asked again
        (
            {"backend": "litellm", "model": "smart-router"},
            "routing alias and the role is not routable",
        ),
    ],
)
def test_a_role_that_cannot_be_served_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, llm: dict[str, Any], problem: str
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LITELLM_API_KEY", "k")
    with pytest.raises(LLMConfigError, match=problem):
        role_llm(_settings(**llm), (Role.KB_DRAFT,))


async def test_the_reply_names_the_model_that_answered() -> None:
    llm = _llm(lambda r: _chat("gpt-x-2026", json.dumps({"answer": "yes"})))
    value, provenance = await llm.structured(Role.KB_RISK, "sys", "{}", Reply)
    assert (value, provenance) == (Reply(answer="yes"), Provenance("kb.risk", "gpt-x-2026"))


@pytest.mark.parametrize(
    "reply",
    [
        _chat("m", '{"answer": "cut', finish="length"),
        _chat("m", '{"other": 1}'),
        _chat("m", ""),
    ],
)
async def test_a_reply_that_is_not_the_schema_is_an_output_error(reply: httpx2.Response) -> None:
    with pytest.raises(LLMOutputError, match=r"role kb\.draft \(openai:gpt-x\)"):
        await _llm(lambda r: reply).structured(Role.KB_DRAFT, "sys", "{}", Reply)


async def test_a_failed_call_is_an_llm_error_without_upstream_text() -> None:
    leaky = "401 for https://u:pw@proxy/v1 with Authorization: Bearer sk-secret-123"

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(leaky, request=request)

    with pytest.raises(LLMError) as caught:
        await _llm(handler).structured(Role.KB_DRAFT, "sys", "{}", Reply)
    assert not isinstance(caught.value, LLMOutputError)
    assert "sk-secret" not in str(caught.value) and "pw@" not in str(caught.value)
    assert caught.value.__cause__ is not None  # kept for debugging


def test_mem0_gets_the_extract_roles_openai_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VENDOR_KEY", "vendor-key")
    vendor = {"type": "openai", "base_url": "https://vendor.test/v1", "api_key_env": "VENDOR_KEY"}
    settings = _settings(
        backend="gateway",
        model="gpt-sub",
        roles={"memory.extract": {"backend": "vendor", "model": "small"}},
        backends={"vendor": vendor},
    )
    assert openai_endpoint(settings, Role.MEMORY_EXTRACT) == (
        "small", "https://vendor.test/v1", "vendor-key"
    )  # fmt: skip
    # the gateway takes no key; the OpenAI SDK refuses an empty one
    assert openai_endpoint(_settings(backend="gateway", model="gpt-sub"), Role.MEMORY_EXTRACT) == (
        "gpt-sub", "http://127.0.0.1:8787/v1", "unused"
    )  # fmt: skip


def test_mem0_cannot_extract_through_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    settings = _settings(backend="anthropic", model="claude-x", max_output_tokens=1024)
    with pytest.raises(LLMConfigError, match="mem0 needs an OpenAI-compatible backend"):
        openai_endpoint(settings, Role.MEMORY_EXTRACT)


async def test_the_fake_answers_through_the_same_adapter() -> None:
    fake = FakeLLM(kb_risk=lambda payload: {"answer": payload["q"]})
    value, provenance = await fake.structured(Role.KB_RISK, "sys", '{"q": "ok"}', Reply)
    assert (value, provenance.tag) == (Reply(answer="ok"), "kb.risk:fake-model")
    assert fake.calls == [("kb.risk", {"q": "ok"})]
    with pytest.raises(AssertionError, match=r"no reply scripted for role 'kb\.draft'"):
        await fake.structured(Role.KB_DRAFT, "sys", "{}", Reply)
