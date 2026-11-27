"""The LLM role layer (KB llm-app-architecture §1). Every call goes through lite-model (its design
§12): call sites name a role, `Settings.llm` (lite-model's configuration) maps each role to a model
on a backend, a failed call raises after retries on the same model (no fallback chain), and the
reply says which model answered. A role that will be used and cannot be served is a startup error.
"""

from dataclasses import dataclass
from enum import StrEnum

from lite_model import RolePolicy, RoleSpec


class Role(StrEnum):
    MEMORY_EXTRACT = "memory.extract"  # fact extraction (by mem0, or by llm-steward itself)
    MEMORY_RESOLVE = "memory.resolve"  # ADD / UPDATE / DELETE / NOOP for a new memory vs. old ones
    KB_RISK = "kb.risk"  # label the risk of knowledge text (rules may raise it further)
    KB_WORTHINESS = "kb.worthiness"  # five scores per new memory: is it worth a KB entry?
    KB_DRAFT = "kb.draft"  # rewrite a memory / a suggestion as general knowledge
    # embeddings: does a drafted entry touch effective knowledge (or a card already asked)?
    KB_SIMILAR = "kb.similar"
    # the reconcile triage assistant (P11): a tool loop that reads, then proposes an action
    AGENT_TRIAGE = "agent.triage"


# lite-model's declaration of the roles. None may go through a routing alias: each one decides or
# labels governed knowledge, and its record must name a model that can be asked again.
ROLES = RoleSpec({r.value: RolePolicy(routable=False) for r in Role if r != Role.KB_SIMILAR})
# With the embed role, declared only where it is used or configured: lite-model checks every
# declared role against its backend, and most chat backends (the gateway) serve no embeddings.
ROLES_WITH_SIMILAR = RoleSpec(
    {**ROLES.roles, Role.KB_SIMILAR.value: RolePolicy(routable=False, kind="embed")}
)


class LLMConfigError(ValueError):
    """Raised at startup when a role that will be used cannot be served."""


@dataclass(frozen=True)
class Provenance:
    """Who actually answered (§1.4): stored with whatever the answer produced."""

    role: str
    model: str

    @property
    def tag(self) -> str:
        """`role:model`, as recorded with what the answer produced (AGENTS.md rule 10)."""
        return f"{self.role}:{self.model}"
