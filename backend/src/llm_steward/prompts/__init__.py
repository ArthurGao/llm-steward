"""Prompt templates (Jinja2, one per LLM role and the retrieval context), loaded with
`PackageLoader("llm_steward", "prompts")`."""

from importlib import resources


def load(name: str) -> str:
    """One template's source, by file name (e.g. `kb_risk.j2`)."""
    return (resources.files(__package__) / name).read_text(encoding="utf-8")


def templates() -> list[tuple[str, str]]:
    """Every template as (name, source), by name (the Admin shows them read-only)."""
    folder = resources.files(__package__)
    return sorted(
        (p.name.removesuffix(".j2"), p.read_text(encoding="utf-8"))
        for p in folder.iterdir()
        if p.name.endswith(".j2")
    )
