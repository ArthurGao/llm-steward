"""Prompt templates (Jinja2, one per LLM role and the retrieval context), loaded with
`PackageLoader("llm_steward", "prompts")`."""

from importlib import resources


def templates() -> list[tuple[str, str]]:
    """Every template as (name, source), by name (the Admin shows them read-only)."""
    folder = resources.files(__package__)
    return sorted(
        (p.name.removesuffix(".j2"), p.read_text(encoding="utf-8"))
        for p in folder.iterdir()
        if p.name.endswith(".j2")
    )
