"""`steward` command line.

Namespaces and API keys are created here, never over HTTP. Further commands (ns apply, resync, mcp)
arrive with their stage.
"""

import asyncio
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from llm_steward.config import Settings
from llm_steward.errors import StewardError
from llm_steward.models.enums import ApiScope
from llm_steward.services.container import runtime
from llm_steward.services.namespace_service import DEFAULT_SCOPES, NamespaceService
from llm_steward.storage.db import engine_scope, session_factory

app = typer.Typer(no_args_is_help=True, add_completion=False)
ns_app = typer.Typer(no_args_is_help=True, help="Namespaces (one per integrating project).")
key_app = typer.Typer(no_args_is_help=True, help="API keys (Bearer stw_<prefix>_<secret>).")
app.add_typer(ns_app, name="ns")
app.add_typer(key_app, name="key")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str, schema: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Passed as objects, not ini options: no ConfigParser escaping, read by migrations/env.py.
    cfg.attributes["database_url"] = database_url
    cfg.attributes["schema"] = schema
    return cfg


def _with_namespaces[T](action: Callable[[NamespaceService], Awaitable[T]]) -> T:
    settings = Settings()

    async def run() -> T:
        async with engine_scope(settings.database_url, schema=settings.db_schema) as engine:
            return await action(NamespaceService(session_factory(engine)))

    try:
        return asyncio.run(run())
    except StewardError as exc:
        typer.echo(f"error: {exc.detail}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8600, reload: bool = False) -> None:
    """Run the HTTP API (and the outbox worker, unless STEWARD_RUN_WORKER=false)."""
    uvicorn.run("llm_steward.main:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations up to REVISION."""
    settings = Settings()
    command.upgrade(alembic_config(settings.database_url, settings.db_schema), revision)
    typer.echo(f"Migrated {make_url(settings.database_url).render_as_string()} to {revision}")


@app.command()
def worker() -> None:
    """Run the outbox worker on its own (with STEWARD_RUN_WORKER=false on the API)."""
    settings = Settings()

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        async with runtime(settings) as services:
            typer.echo("worker started")
            await services.worker.run_forever(stop)

    asyncio.run(run())


@ns_app.command("create")
def ns_create(slug: str, name: Annotated[str | None, typer.Option()] = None) -> None:
    """Create a namespace."""
    namespace = _with_namespaces(lambda svc: svc.create_namespace(slug, name))
    typer.echo(f"namespace {namespace.slug} ({namespace.id})")


@key_app.command("create")
def key_create(
    slug: str,
    scope: Annotated[
        list[ApiScope] | None, typer.Option(help="Repeatable; default read+write")
    ] = None,
    name: Annotated[str, typer.Option()] = "",
) -> None:
    """Create an API key for a namespace. The key is printed once and cannot be shown again."""
    scopes = tuple(scope) if scope else DEFAULT_SCOPES
    key = _with_namespaces(lambda svc: svc.create_key(slug, scopes, name))
    typer.echo(key)


@key_app.command("revoke")
def key_revoke(prefix: str) -> None:
    """Revoke an API key by its prefix (the part after `stw_`)."""
    _with_namespaces(lambda svc: svc.revoke_key(prefix))
    typer.echo(f"revoked {prefix}")


if __name__ == "__main__":
    app()
