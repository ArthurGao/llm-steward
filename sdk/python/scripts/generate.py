"""Regenerate the SDK's models from the backend's OpenAPI spec.

    uv run python scripts/generate.py           # write openapi.json and the models
    uv run python scripts/generate.py --check   # fail if either is stale (CI)

The spec is exported from the backend app (no server, no database), then
`datamodel-code-generator` (pinned) turns its schemas into Pydantic models.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "openapi.json"
MODELS = ROOT / "src" / "llm_steward_client" / "_models.py"
BACKEND = ROOT.parents[1] / "backend"
EXPORT = (
    "import json; from llm_steward.config import Settings; "
    "from llm_steward.main import create_app; "
    "s = Settings(_env_file=None, database_url='sqlite+aiosqlite:///:memory:', "
    "memory_provider='native', kb_provider='native'); "
    "print(json.dumps(create_app(s).openapi(), indent=2, sort_keys=True))"
)
CODEGEN = [
    "--input-file-type",
    "openapi",
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    "3.12",
    "--use-standard-collections",
    "--use-union-operator",
    "--use-annotated",
    "--field-constraints",
    "--use-schema-description",
    "--disable-timestamp",
    "--collapse-root-models",
    "--formatters",
    "ruff-format",
    "--enum-field-as-literal",
    "all",
    "--custom-file-header",
    '"""Generated from openapi.json by scripts/generate.py. Do not edit."""',
]


def export_spec() -> str:
    """In this environment when the backend is installed (the `embedded` extra), else through
    the backend's own (`uv run --project`)."""
    try:
        from llm_steward.config import Settings
        from llm_steward.main import create_app
    except ImportError:
        pass
    else:
        settings = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///:memory:",
            memory_provider="native",
            kb_provider="native",
        )
        return json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"
    out = subprocess.run(
        ["uv", "run", "--project", str(BACKEND), "python", "-c", EXPORT],
        check=True,
        capture_output=True,
        text=True,
        cwd=BACKEND,
    )
    return out.stdout


def models_from(spec: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        source, target = Path(tmp) / "openapi.json", Path(tmp) / "models.py"
        source.write_text(spec)
        subprocess.run(
            ["datamodel-codegen", "--input", str(source), "--output", str(target), *CODEGEN],
            check=True,
        )
        return target.read_text()


def main() -> int:
    spec = export_spec()
    json.loads(spec)  # a spec, not an error message
    models = models_from(spec)
    if "--check" in sys.argv:
        stale = [p.name for p, new in ((SPEC, spec), (MODELS, models)) if p.read_text() != new]
        if stale:
            print(f"stale: {', '.join(stale)}; run scripts/generate.py", file=sys.stderr)
            return 1
        return 0
    SPEC.write_text(spec)
    MODELS.write_text(models)
    return 0


if __name__ == "__main__":
    sys.exit(main())
