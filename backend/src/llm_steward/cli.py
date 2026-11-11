"""`steward` command line.

Namespaces, their config and API keys are managed here, never over HTTP; `mcp --stdio` serves
MCP to a local agent. Further commands (resync) arrive with their stage.
"""

import asyncio
import os
import signal
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.config import Settings
from llm_steward.errors import StewardError
from llm_steward.models import Job
from llm_steward.models.enums import ApiScope
from llm_steward.services.admin_service import AdminService
from llm_steward.services.container import runtime
from llm_steward.services.job_queue import JobQueue
from llm_steward.services.namespace_service import DEFAULT_SCOPES, NamespaceService
from llm_steward.services.resync import ResyncReport, resync
from llm_steward.storage.db import engine_scope, session_factory

app = typer.Typer(no_args_is_help=True, add_completion=False)
ns_app = typer.Typer(no_args_is_help=True, help="Namespaces (one per integrating project).")
key_app = typer.Typer(no_args_is_help=True, help="API keys (Bearer stw_<prefix>_<secret>).")
app.add_typer(ns_app, name="ns")
jobs_app = typer.Typer(no_args_is_help=True, help="Outbox jobs that ran out of attempts.")
app.add_typer(key_app, name="key")
app.add_typer(jobs_app, name="jobs")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str, schema: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Passed as objects, not ini options: no ConfigParser escaping, read by migrations/env.py.
    cfg.attributes["database_url"] = database_url
    cfg.attributes["schema"] = schema
    return cfg


def _with_namespaces[T](action: Callable[[NamespaceService], Awaitable[T]]) -> T:
    return _with_sessions(lambda sessions: action(NamespaceService(sessions)))


def _with_sessions[T](action: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    settings = Settings()

    async def run() -> T:
        async with engine_scope(settings.database_url, schema=settings.db_schema) as engine:
            return await action(session_factory(engine))

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


@ns_app.command("apply")
def ns_apply(
    file: Annotated[Path, typer.Option("--file", "-f", exists=True, dir_okay=False)],
) -> None:
    """Create or update a namespace from a YAML/JSON file: `slug`, optional `name`, then the
    config sections (design §8). The file replaces the whole config; it is validated first."""
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("slug"), str):
        typer.echo("error: the file must be a mapping with a `slug`", err=True)
        raise typer.Exit(1)
    slug, name = raw.pop("slug"), raw.pop("name", None)
    namespace, created = _with_namespaces(lambda svc: svc.apply_config(slug, raw, name))
    typer.echo(f"namespace {namespace.slug} ({namespace.id}) {'created' if created else 'updated'}")


@ns_app.command("show")
def ns_show(slug: str) -> None:
    """Print a namespace's stored config as YAML (only what was set; the rest are defaults)."""
    namespace = _with_namespaces(lambda svc: svc.get_namespace(slug))
    document = {"slug": namespace.slug, "name": namespace.name, **namespace.config}
    typer.echo(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), nl=False)


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


@jobs_app.command("failed")
def jobs_failed(
    namespace: Annotated[str | None, typer.Option("--ns", help="Namespace slug")] = None,
    limit: int = 100,
) -> None:
    """List failed jobs: id, type, attempts, when, and the last error (already redacted)."""

    async def run(sessions: async_sessionmaker[AsyncSession]) -> list[Job]:
        found = await NamespaceService(sessions).get_namespace(namespace) if namespace else None
        return await JobQueue(sessions).failed(found.id if found else None, limit)

    for job in _with_sessions(run):
        typer.echo(
            f"{job.id}  {job.type}  attempts={job.attempts}  {job.updated_at:%Y-%m-%d %H:%M}"
            f"  {job.error or ''}"
        )


@jobs_app.command("retry")
def jobs_retry(
    job_ids: Annotated[list[uuid.UUID] | None, typer.Argument()] = None,
    all_failed: Annotated[bool, typer.Option("--all-failed")] = False,
) -> None:
    """Requeue failed jobs with fresh attempts: the given ids, or --all-failed."""
    if bool(job_ids) == all_failed:
        typer.echo("error: give job ids or --all-failed (not both)", err=True)
        raise typer.Exit(1)
    count = _with_sessions(lambda sessions: JobQueue(sessions).retry(job_ids or None))
    typer.echo(f"requeued {count} job(s)")
    if job_ids and count < len(job_ids):
        typer.echo("note: only failed jobs are requeued; the others were left alone", err=True)


@app.command("resync")
def resync_command(
    namespaces: Annotated[
        list[str] | None, typer.Option("--ns", help="Namespace slug; repeatable; default: all")
    ] = None,
    kb: Annotated[bool, typer.Option("--kb/--no-kb")] = True,
    memory: Annotated[bool, typer.Option("--memory/--no-memory")] = True,
) -> None:
    """After switching STEWARD_KB_PROVIDER or STEWARD_MEMORY_PROVIDER: queue every effective
    object the configured engines do not hold yet; the worker pushes them (run it, or serve).
    Governance state and history do not change. A parsed document's file is gone (its engine has
    it), so such documents are listed for re-upload instead."""
    settings = Settings()

    async def run(sessions: async_sessionmaker[AsyncSession]) -> list[tuple[str, ResyncReport]]:
        service = NamespaceService(sessions)
        if namespaces:
            chosen = [await service.get_namespace(slug) for slug in namespaces]
        else:
            chosen = await AdminService(sessions).namespaces()  # by slug
        reports = []
        for n in chosen:
            report = await resync(
                sessions,
                n.id,
                kb_provider=settings.kb_provider,
                memory_provider=settings.memory_provider,
                kb=kb,
                memory=memory,
            )
            reports.append((n.slug, report))
        return reports

    for slug, report in _with_sessions(run):
        parts = []
        if kb:
            parts.append(
                f"{report.entries} entries, {report.documents} documents for {settings.kb_provider}"
            )
        if memory:
            parts.append(f"{report.memories} memories for {settings.memory_provider}")
        parts.append(f"{report.removed} removals")  # copies of what was retired meanwhile
        typer.echo(f"{slug}: queued {'; '.join(parts)}")
        if report.documents_without_file:
            ids = " ".join(str(i) for i in report.documents_without_file)
            typer.echo(f"{slug}: re-upload (the old engine holds their only copy): {ids}")


@app.command()
def mcp(
    stdio: Annotated[bool, typer.Option("--stdio", help="Serve MCP on stdin/stdout")] = False,
    user_id: Annotated[
        str | None, typer.Option(help="Pin every call to this end user (like a user token)")
    ] = None,
) -> None:
    """MCP over stdio for a local agent (e.g. Claude Code). Authenticated like HTTP: the API key
    in STEWARD_API_KEY, checked at start; there is no local bypass. Over HTTP, use `/mcp` on
    `steward serve` instead. The outbox worker runs alongside unless STEWARD_RUN_WORKER=false."""
    if not stdio:
        typer.echo("error: only --stdio is served here; HTTP is /mcp on `steward serve`", err=True)
        raise typer.Exit(1)
    key = os.environ.get("STEWARD_API_KEY", "")
    if not key:
        typer.echo("error: set STEWARD_API_KEY to an API key of the namespace", err=True)
        raise typer.Exit(1)
    settings = Settings()

    async def run() -> None:
        async with runtime(settings) as services:
            principal = await services.namespaces.authenticate(key)
            if principal is None or principal.user_id is not None:
                typer.echo("error: STEWARD_API_KEY is not a valid API key", err=True)
                raise typer.Exit(1)
            if user_id is not None:
                principal = principal.for_user(user_id)  # like a user token
            stop = asyncio.Event()
            worker = (
                asyncio.create_task(services.worker.run_forever(stop))
                if settings.run_worker
                else None
            )
            from llm_steward.mcp.server import build_server  # the MCP SDK, only for this command

            try:
                await build_server(lambda: services, principal=principal).run_stdio_async()
            finally:
                stop.set()
                if worker is not None:
                    await worker

    asyncio.run(run())


if __name__ == "__main__":
    app()
