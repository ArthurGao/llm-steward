"""The LLM role layer (KB llm-app-architecture §1).

Call sites name a role; configuration says which endpoint and model serve it. No business code
imports a vendor SDK or names a model. A role with no model or endpoint is a startup error, not a
first-call surprise (§1.5). There is no fallback chain (§3.2): a failed call raises.
"""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from llm_steward.config import Settings


class Role(StrEnum):
    MEMORY_EXTRACT = "memory.extract"  # fact extraction (by mem0, or by llm-steward itself)
    MEMORY_RESOLVE = "memory.resolve"  # ADD / UPDATE / DELETE / NOOP for a new memory vs. old ones
    KB_RISK = "kb.risk"  # label the risk of knowledge text (rules may raise it further)
    KB_WORTHINESS = "kb.worthiness"  # five scores per new memory: is it worth a KB entry?
    KB_DRAFT = "kb.draft"  # rewrite a memory / a suggestion as general knowledge


class LLMConfigError(ValueError):
    """Raised at startup when a role that will be used cannot be served."""


@dataclass(frozen=True)
class RoleTarget:
    model: str
    base_url: str
    api_key: str

    @property
    def endpoint(self) -> str:
        """Host only, for provenance: never the key, never a path with a token in it."""
        return urlsplit(self.base_url).netloc or self.base_url


@dataclass(frozen=True)
class Provenance:
    """Who actually answered (§1.4): stored with whatever the answer produced."""

    role: str
    model: str

    @property
    def tag(self) -> str:
        """`role:model`, as recorded with what the answer produced (AGENTS.md rule 10)."""
        return f"{self.role}:{self.model}"

    endpoint: str


def resolve(settings: Settings, role: Role) -> RoleTarget:
    unknown = set(settings.llm_roles) - {r.value for r in Role}
    if unknown:
        raise LLMConfigError(f"STEWARD_LLM_ROLES names unknown roles: {sorted(unknown)}")
    model = settings.llm_roles.get(role.value) or settings.llm_model
    if not model:
        raise LLMConfigError(f"no model for LLM role {role.value!r}: set STEWARD_LLM_MODEL")
    if not settings.llm_base_url:
        raise LLMConfigError(f"no endpoint for LLM role {role.value!r}: set STEWARD_LLM_BASE_URL")
    # A local gateway ignores the key; a vendor endpoint will reject a missing one on first use.
    return RoleTarget(model, settings.llm_base_url, settings.llm_api_key or "unused")
