"""A namespace's config (design §8), validated when it is written.

Only keys llm-steward honours are accepted: an unknown key (a typo, or a design key that is not
implemented per namespace yet, such as `providers` or `llm`) is refused rather than silently
ignored. A section written with nothing under it (`kb:` in YAML) is null and means "defaults".
"""

import re
from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from llm_steward.errors import StewardError, Unprocessable

PiiAction = Literal["block", "redact", "allow"]
WebhookEvent = Literal[
    "candidate.created", "kb_entry.verified", "kb_entry.deprecated", "document.ready"
]
# The webhook secret is read from this environment variable (rules: secrets only via env).
_ENV_NAME = re.compile(r"^STEWARD_[A-Z0-9_]+$")


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_default(cls, value: Any, info: ValidationInfo) -> Any:
        """A section or list written empty is null: treat it as absent (its default applies)."""
        factory = cls.model_fields[info.field_name or ""].default_factory
        if value is None and factory is not None:
            return factory()  # type: ignore[call-arg]
        return value


class Pii(_Section):
    action: PiiAction = "redact"


class MemoryType(_Section):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: str = ""
    half_life_days: Annotated[float, Field(gt=0)] | None = None  # None: never decays


class MemoryConfig(_Section):
    types: list[MemoryType] = Field(default_factory=list)
    pii: Pii = Field(default_factory=Pii)


class KbConfig(_Section):
    suggest_threshold: Annotated[float, Field(ge=0, le=1)] = 0.7
    max_suggestions_per_session: Annotated[int, Field(ge=0)] = 2
    suggest_collection: Annotated[str, Field(min_length=1, max_length=200)] = "inbox"
    high_risk_keywords: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)


class RetrievalConfig(_Section):
    top_k: Annotated[int, Field(ge=1, le=50)] | None = None  # None: the code default
    token_budget: Annotated[int, Field(ge=1)] | None = None
    rerank: bool = False
    trust_weights: dict[Literal["verified", "active"], Annotated[float, Field(ge=0)]] = Field(
        default_factory=dict
    )
    importance_boost: Annotated[float, Field(ge=0)] | None = None


class Limits(_Section):
    requests_per_minute: Annotated[int, Field(ge=1)] | None = None  # None: the server default


class Webhook(_Section):
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    events: list[WebhookEvent] = Field(min_length=1)
    secret_env: str

    @field_validator("secret_env")
    @classmethod
    def _env_name(cls, value: str) -> str:
        if not _ENV_NAME.match(value):
            raise ValueError("must name an environment variable STEWARD_<NAME>")
        return value


class DocumentsConfig(_Section):
    url_allowlist: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)


class NamespaceConfig(_Section):
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    kb: KbConfig = Field(default_factory=KbConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    limits: Limits = Field(default_factory=Limits)
    webhooks: list[Webhook] = Field(default_factory=list)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "NamespaceConfig":
        """Validate a config being written; a bad one is a 422 that names each problem."""
        return _validate(raw, lambda why: Unprocessable(f"invalid namespace config: {why}"))

    def dump(self) -> dict[str, Any]:
        """What is stored: only what was set, so later default changes still reach it."""
        return self.model_dump(mode="json", exclude_unset=True)


class InvalidStoredConfig(StewardError):
    """A stored config that no longer validates (written before validation, or edited by hand)."""

    code = "namespace_config_invalid"


def load(raw: dict[str, Any] | None) -> NamespaceConfig:
    """A config read from the database: invalid is the operator's to fix, never silently ignored."""
    return _validate(
        raw,
        lambda why: InvalidStoredConfig(
            f"the namespace config is invalid ({why}); fix it with `steward ns apply`"
        ),
    )


def _validate(raw: dict[str, Any] | None, error: Callable[[str], StewardError]) -> NamespaceConfig:
    try:
        return NamespaceConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise error(_describe(exc)) from exc


def _describe(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()
    )
