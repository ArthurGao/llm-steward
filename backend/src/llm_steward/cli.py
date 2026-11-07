"""`steward` command line.

Further commands (worker, ns apply, resync, mcp) arrive with their stage.
"""

from pathlib import Path

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from llm_steward.config import Settings

app = typer.Typer(no_args_is_help=True, add_completion=False)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str, schema: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Passed as objects, not ini options: no ConfigParser escaping, read by migrations/env.py.
    cfg.attributes["database_url"] = database_url
    cfg.attributes["schema"] = schema
    return cfg


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8600, reload: bool = False) -> None:
    """Run the HTTP API."""
    uvicorn.run("llm_steward.main:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations up to REVISION."""
    settings = Settings()
    command.upgrade(alembic_config(settings.database_url, settings.db_schema), revision)
    typer.echo(f"Migrated {make_url(settings.database_url).render_as_string()} to {revision}")


if __name__ == "__main__":
    app()
