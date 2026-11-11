"""A real llm-steward for the Admin's live Playwright flows (design §11 P6 acceptance).

Native providers on a fresh SQLite file, the outbox worker running, a fake embedder and a fake LLM
(never a real model, never llm-gateway). It seeds one namespace through the public API and serves
until killed. Run from backend/:

    uv run python -m tests.e2e.admin_server --port 8602 --token <32+ chars> --origin http://127.0.0.1:8612

With `--kb literag` the KB engine is a running lite-rag service instead of native.
"""

import argparse
import asyncio
import tempfile
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from fastapi import FastAPI

from llm_steward.cli import alembic_config
from llm_steward.config import Settings
from llm_steward.main import create_app
from llm_steward.models.enums import ApiScope
from llm_steward.services.namespace_service import NamespaceService
from llm_steward.storage.db import engine_scope, session_factory
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.llm import FakeLLM

SLUG = "acme"


async def create_tenant(settings: Settings) -> tuple[str, str]:
    """The namespace, a reviewer key and a host-app key: what `steward ns create` and
    `steward key create` do."""
    async with engine_scope(settings.database_url, schema=settings.db_schema) as engine:
        namespaces = NamespaceService(session_factory(engine))
        await namespaces.create_namespace(SLUG, "Acme support (e2e)")
        reviewer = await namespaces.create_key(
            SLUG, (ApiScope.READ, ApiScope.WRITE, ApiScope.REVIEW), "e2e reviewer"
        )
        host = await namespaces.create_key(SLUG, (ApiScope.READ, ApiScope.WRITE), "e2e host app")
        return reviewer, host


async def seed(app: FastAPI, reviewer: str, host: str) -> None:
    """Knowledge a support team would have (written by a reviewer, so verified), plus one
    high-risk entry from the host app waiting for review. Through the public API, in-process,
    before the server listens: once /health answers, the data is there."""
    transport = httpx.ASGITransport(app=app)

    async def post(key: str, path: str, body: dict[str, object]) -> dict[str, object]:
        async with httpx.AsyncClient(transport=transport, base_url="http://seed") as http:
            r = await http.post(path, json=body, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            return dict(r.json())

    billing = (await post(reviewer, "/v1/kb/collections", {"name": "Billing policy"}))["id"]
    await post(
        reviewer,
        "/v1/kb/entries",
        {
            "collection_id": billing,
            "title": "Invoice delivery",
            "content": "Invoices are emailed on the first business day of each month.",
        },
    )
    await post(
        host,
        "/v1/kb/entries",
        {
            "collection_id": billing,
            "title": "Refund approval threshold",
            "content": "Refunds over $500 require approval from a second support lead.",
            "risk_level": "high",
        },
    )


def settings_from_args() -> tuple[Settings, int]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8602)
    parser.add_argument("--token", required=True)
    parser.add_argument("--origin", action="append", default=[])
    # literag: the KB engine is a running lite-rag service (STEWARD_LITERAG_URL, and its key in
    # STEWARD_LITERAG_API_KEY, both read from the environment), with its real embeddings
    parser.add_argument("--kb", choices=("native", "literag"), default="native")
    args = parser.parse_args()

    data = Path(tempfile.mkdtemp(prefix="steward-e2e-"))
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+aiosqlite:///{data / 'steward.db'}",
        data_dir=str(data),
        memory_provider="native",
        kb_provider=args.kb,
        admin_token=args.token,
        cors_origins=args.origin,
        rate_limit_per_minute=None,
    )
    return settings, args.port


async def main(settings: Settings, port: int) -> None:
    reviewer, host = await create_tenant(settings)

    def app() -> FastAPI:
        return create_app(settings, embedder=FakeEmbedder(), llm=FakeLLM())

    # Seeded by its own app instance (an app's MCP session manager runs once), on the same
    # database; ASGITransport does not run the lifespan, so it is entered here.
    seeder = app()
    async with seeder.router.lifespan_context(seeder):
        await seed(seeder, reviewer, host)
    print(f"llm-steward e2e backend on :{port}, data in {settings.data_dir}", flush=True)
    server = uvicorn.Server(uvicorn.Config(app(), host="127.0.0.1", port=port, log_level="warning"))
    await server.serve()


if __name__ == "__main__":
    settings, port = settings_from_args()
    # Alembic's env runs its own event loop, so the schema is created before ours starts.
    command.upgrade(alembic_config(settings.database_url, settings.db_schema), "head")
    asyncio.run(main(settings, port))
