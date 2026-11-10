"""The MCP server (design §7, P5 acceptance): remember → recall over streamable HTTP with the same
auth as REST; an agent can only propose knowledge (rule 5). An in-process MCP client talks to the
running app; both dialects; FakeLLM for drafting."""

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from llm_steward.models import KbEntry
from llm_steward.models.enums import ApiScope, CandidateStage
from tests.conftest import AppClient, Tenant, running_app
from tests.integration.test_kb_suggestions import _llm

H = dict[str, str]


@pytest.fixture
def app(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url, settings={"kb_llm": True}, llm=_llm("low")) as client:
        yield client


async def _session_call(c: AppClient, headers: H, calls: list[tuple[str, dict[str, Any]]]) -> Any:
    """Open an MCP session over HTTP with these headers and run the calls in order."""
    transport = httpx2.ASGITransport(app=c.http.app)
    async with (
        httpx2.AsyncClient(transport=transport, base_url="http://steward", headers=headers) as http,
        streamable_http_client("http://steward/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        out: list[Any] = []
        for name, args in calls:
            if name == "list_tools":
                out.append(await session.list_tools())
            else:
                out.append(await session.call_tool(name, args))
        return out


def _mcp(c: AppClient, headers: H, *calls: tuple[str, dict[str, Any]]) -> list[Any]:
    result: list[Any] = c.call(_session_call, c, headers, list(calls))
    return result


def _text(result: Any) -> str:
    assert not result.is_error, result.content
    return "".join(part.text for part in result.content if hasattr(part, "text"))


def _data(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return dict(result.structured_content)
    return dict(json.loads(_text(result)))


def test_the_tool_list_is_exactly_the_six(app: AppClient) -> None:
    h, _ = app.new_tenant("acme")
    [tools] = _mcp(app, h, ("list_tools", {}))
    from llm_steward.mcp.server import TOOLS

    assert sorted(t.name for t in tools.tools) == sorted(TOOLS)
    assert (
        len(TOOLS) == 6
    )  # design §7: remember, recall, forget, suggest_kb, search_kb, get_profile


def test_remember_then_recall(app: AppClient) -> None:
    """The acceptance: what an agent remembers for a user, it recalls for that user."""
    h, _ = app.new_tenant("acme")
    [stored] = _mcp(app, h, ("remember", {"fact": "Ada prefers green tea", "user_id": "ada"}))
    assert _data(stored)["user_id"] == "ada"
    app.drain()  # the outbox pushes it to the memory engine
    recalled, other = _mcp(
        app,
        h,
        ("recall", {"query": "what tea does Ada like", "user_id": "ada"}),
        ("recall", {"query": "what tea does Ada like", "user_id": "bob"}),
    )
    assert "Ada prefers green tea" in _text(recalled)
    assert "green tea" not in _text(other)  # another user's memory is not recalled


def test_forget_and_profile(app: AppClient) -> None:
    h, _ = app.new_tenant("acme")
    [stored] = _mcp(app, h, ("remember", {"fact": "Ada lives in Oslo", "user_id": "ada"}))
    memory_id = _data(stored)["id"]
    forgotten, profile = _mcp(
        app, h, ("forget", {"memory_id": memory_id}), ("get_profile", {"user_id": "ada"})
    )
    assert _data(forgotten)["status"] == "deleted"
    assert _data(profile)["user_id"] == "ada"


# --- rule 5: propose, never write ------------------------------------------------------------


def test_even_a_reviewer_key_can_only_propose_knowledge(app: AppClient) -> None:
    """A low-risk suggestion from a key with the review right would go live at once over REST;
    over MCP it waits for a reviewer (governance_service routes by `propose_only`)."""
    t = Tenant(app, "acme")
    collection = t.collection(default_risk="low")
    [proposed] = _mcp(
        app,
        t.reviewer,
        ("suggest_kb", {"text": "Refunds take five days", "collection_id": collection}),
    )
    assert _data(proposed)["stage"] == CandidateStage.AWAITING_REVIEW.value
    app.drain()
    assert app.count(KbEntry) == 0  # nothing became knowledge


def test_the_mcp_package_cannot_name_a_knowledge_write() -> None:
    """Pins rule 5 in the code: MCP builds no knowledge operation and calls no decision."""
    import llm_steward.mcp as package

    source = "\n".join(p.read_text() for p in Path(package.__file__).parent.rglob("*.py"))
    for name in (
        "can_review=True",
        "SubmitKbEntry",
        "ReviseKbEntry",
        "UpdateKbEntry",
        "CreateKbEntry",
        "TransitionKbEntry",
        "DecideCandidate",
        "SubmitKbDocument",
        ".decide(",
        ".submit(",
        ".promote(",
        ".upload(",
        ".import_url(",
    ):
        assert name not in source, name
    assert "propose_only=True" in source  # the actor every tool acts as


# --- the same auth as REST -------------------------------------------------------------------


def test_mcp_without_a_key_is_401(app: AppClient) -> None:
    r = app.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"
    r = app.http.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": "Bearer stw_nope_nope"},
    )
    assert r.status_code == 401


def test_a_user_token_recalls_only_its_own_user(app: AppClient) -> None:
    h, _ = app.new_tenant("acme")
    _mcp(
        app,
        h,
        ("remember", {"fact": "Ada prefers green tea", "user_id": "ada"}),
        ("remember", {"fact": "Bob prefers black coffee", "user_id": "bob"}),
    )
    app.drain()
    r = app.http.post("/v1/tokens/user", json={"user_id": "ada"}, headers=h)
    token = {"Authorization": f"Bearer {r.json()['token']}"}
    mine, theirs = _mcp(
        app,
        token,
        ("recall", {"query": "what does everyone drink"}),
        ("recall", {"query": "coffee", "user_id": "bob"}),
    )
    assert "green tea" in _text(mine) and "coffee" not in _text(mine)
    assert theirs.is_error  # naming another user is refused


def test_a_read_only_key_cannot_remember(app: AppClient) -> None:
    app.new_tenant("acme")
    key = app.call(app.services.namespaces.create_key, "acme", (ApiScope.READ,))
    [refused] = _mcp(app, {"Authorization": f"Bearer {key}"}, ("remember", {"fact": "x"}))
    assert refused.is_error


def test_an_unknown_memory_id_is_an_error_not_a_crash(app: AppClient) -> None:
    h, _ = app.new_tenant("acme")
    missing, garbage = _mcp(
        app, h, ("forget", {"memory_id": str(uuid.uuid4())}), ("forget", {"memory_id": "x"})
    )
    assert missing.is_error and garbage.is_error


# --- stdio: one principal for the whole process ------------------------------------------------


def _stdio_principal(c: AppClient, headers: H, user_id: str | None = None) -> Any:
    key = headers["Authorization"].removeprefix("Bearer ")
    principal = c.call(c.services.namespaces.authenticate, key)
    return principal if user_id is None else principal.for_user(user_id)  # as `--user-id`


def _in_process(c: AppClient, principal: Any, *calls: tuple[str, dict[str, Any]]) -> list[Any]:
    from mcp import Client

    from llm_steward.mcp.server import build_server

    async def run() -> list[Any]:
        server = build_server(lambda: c.services, principal=principal)
        async with Client(server) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    result: list[Any] = c.call(run)
    return result


def test_a_stdio_server_acts_as_its_one_principal(app: AppClient) -> None:
    h, _ = app.new_tenant("acme")
    [stored] = _in_process(
        app, _stdio_principal(app, h), ("remember", {"fact": "Ada likes tea", "user_id": "ada"})
    )
    assert _data(stored)["user_id"] == "ada"
    pinned = _stdio_principal(app, h, user_id="bob")
    mine, theirs = _in_process(
        app, pinned, ("remember", {"fact": "Bob likes coffee"}), ("get_profile", {"user_id": "ada"})
    )
    assert _data(mine)["user_id"] == "bob"  # --user-id pins every call
    assert theirs.is_error


@pytest.mark.parametrize(
    ("args", "env", "says"),
    [
        (["mcp"], {"STEWARD_API_KEY": "stw_x_y"}, "only --stdio"),
        (["mcp", "--stdio"], {}, "set STEWARD_API_KEY"),
        (["mcp", "--stdio"], {"STEWARD_API_KEY": "stw_deadbeef_wrong"}, "not a valid API key"),
    ],
)
def test_the_stdio_command_refuses_to_start_without_a_valid_key(
    cli_db: str,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    env: dict[str, str],
    says: str,
) -> None:
    from typer.testing import CliRunner

    from llm_steward.cli import app as cli

    monkeypatch.delenv("STEWARD_API_KEY", raising=False)
    for name, value in {
        "STEWARD_MEMORY_PROVIDER": "native",
        "STEWARD_KB_PROVIDER": "native",
        **env,
    }.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1, result.output
    assert says in result.output
