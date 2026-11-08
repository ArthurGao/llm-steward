"""Closed vocabularies of the governance model (design §4, §5); columns get CHECKs from these."""

from enum import StrEnum


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class MemoryOrigin(StrEnum):
    EXTRACTION = "extraction"
    DIRECT = "direct"
    IMPORT = "import"


class KbStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    VERIFIED = "verified"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


EFFECTIVE_KB_STATUSES = frozenset({KbStatus.ACTIVE, KbStatus.VERIFIED})


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class KbOrigin(StrEnum):
    MANUAL = "manual"
    IMPORT = "import"
    PROMOTED_MEMORY = "promoted_memory"
    USER_SUGGESTION = "user_suggestion"
    EXTRACTION = "extraction"


class AuthorType(StrEnum):
    AI = "ai"
    HUMAN = "human"


class DocStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class CandidateTarget(StrEnum):
    MEMORY = "memory"
    KB_ENTRY = "kb_entry"


class CandidateOp(StrEnum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class CandidateOrigin(StrEnum):
    EXTRACTION = "extraction"
    PROMOTE = "promote"
    USER_SUGGESTION = "user_suggestion"
    API = "api"


class CandidateStage(StrEnum):
    AWAITING_USER = "awaiting_user"
    AWAITING_REVIEW = "awaiting_review"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ObjectType(StrEnum):
    MEMORY = "memory"
    KB_ENTRY = "kb_entry"
    KB_DOCUMENT = "kb_document"
    KB_COLLECTION = "kb_collection"
    CANDIDATE = "candidate"
    EPISODE = "episode"


class SyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class ActorType(StrEnum):
    AI = "ai"
    END_USER = "end_user"
    ADMIN = "admin"
    SYSTEM = "system"  # llm-steward's own writes (maintenance, compensation)
    PROVIDER = "provider"  # content produced by a memory/KB engine (e.g. mem0 extraction)
    HOST_APP = "host_app"  # an integrating app's server, via its API key


class ChangeOp(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    TRANSITION = "transition"


class JobType(StrEnum):
    MEMORY_UPSERT = "sync.memory_upsert"
    MEMORY_REMOVE = "sync.memory_remove"
    KB_UPSERT = "sync.kb_upsert"
    KB_REMOVE = "sync.kb_remove"
    MEMORY_EXTRACT = "memory.extract"
    MEMORY_RECONCILE = "memory.reconcile"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EpisodeKind(StrEnum):
    CONVERSATION = "conversation"
    IMPORT = "import"
    MANUAL = "manual"


class AdminRole(StrEnum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class ApiScope(StrEnum):
    READ = "read"
    WRITE = "write"
    REVIEW = "review"
    ADMIN = "admin"
