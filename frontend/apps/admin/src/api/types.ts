// The view types the pages render. The live API (./live.ts) adapts the generated client's
// shapes (src/client, from the backend's OpenAPI spec) to these; the mock store produces them
// directly. A value the backend does not report is null and shown as "—", never invented.

import type * as gen from '@/client/types.gen'

export type ISODate = string

// Enums are the backend's (the generated client). ObjectType and ChangeOp stay wider: the demo
// also logs user-level erasure, which the backend records differently.
export type MemoryStatus = gen.MemoryStatus
export type MemoryOrigin = gen.MemoryOrigin
export type KbStatus = gen.KbStatus
export type RiskLevel = gen.RiskLevel
export type KbOrigin = gen.KbOrigin
export type AuthorType = gen.AuthorType
export type DocStatus = gen.DocStatus
export type CandidateTarget = gen.CandidateTarget
export type CandidateOp = gen.CandidateOp
export type CandidateOrigin = gen.CandidateOrigin
export type CandidateStage = gen.CandidateStage
export type CandidateStatus = gen.CandidateStatus
export type SyncStatus = gen.SyncStatus
export type ActorType = gen.ActorType
export type JobStatus = gen.JobStatus
export type ObjectType = gen.ObjectType | 'user'
export type ChangeOp = gen.ChangeOp | 'delete' | 'erase'

/** The Admin's review lists: flagged for review, or active and well rated (worth verifying). */
export const REVIEW_FILTERS = ['due', 'verify'] as const
export type ReviewFilter = (typeof REVIEW_FILTERS)[number]

export interface Page<T> {
  items: T[]
  next_cursor: string | null
  total: number | null
}

export interface Namespace {
  id: string
  slug: string
  name: string
  memory_provider: string
  kb_provider: string
}

export interface SourceRef {
  type: string
  id: string
}

export interface Memory {
  id: string
  content: string
  user_id: string | null
  agent_id: string | null
  session_id: string | null
  memory_type: string
  status: MemoryStatus
  origin: MemoryOrigin
  pinned: boolean
  importance: number
  confidence: number
  tags: string[]
  metadata: Record<string, unknown>
  valid_from: ISODate
  valid_to: ISODate | null
  created_at: ISODate
  updated_at: ISODate
  sync_status: SyncStatus
  source_refs: SourceRef[]
  superseded_by: string | null
}

export interface MemoryPatch {
  pinned?: boolean
  importance?: number
  confidence?: number
  tags?: string[]
}

export interface MemoryBrief {
  id: string
  content: string
  status: MemoryStatus
  valid_from: ISODate
  valid_to: ISODate | null
  superseded_by: string | null
}

export interface Change {
  id: string
  object_type: ObjectType
  object_id: string
  op: ChangeOp
  actor_type: ActorType
  actor_id: string | null
  reason: string | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: ISODate
}

export interface MemoryHistory {
  memory_id: string
  changes: Change[]
  chain: MemoryBrief[]
}

export interface Collection {
  id: string
  name: string
  default_risk: RiskLevel
  review_policy: Record<string, string>
  created_at: ISODate
  entry_count: number
  document_count: number
}

export interface KbEntry {
  id: string
  key: string
  collection_id: string
  title: string
  content: string
  status: KbStatus
  risk_level: RiskLevel
  origin: KbOrigin
  author_type: AuthorType
  version: number
  created_by: string | null
  verified_by: string | null
  verified_at: ISODate | null
  valid_from: ISODate
  valid_to: ISODate | null
  tags: string[]
  source_refs: SourceRef[]
  created_at: ISODate
  updated_at: ISODate
  superseded_by: string | null
  sync_status: SyncStatus | null
  hits: number
  helpful_rate: number | null
  review_due_at: ISODate | null
}

export interface EntryPatch {
  title?: string
  content?: string
}

export interface EntryVersion {
  version: number
  title: string
  content: string
  status: KbStatus
  changed_at: ISODate
  actor_type: ActorType
  actor_id: string | null
  reason: string | null
}

export interface EntryHistory {
  entry_id: string
  versions: EntryVersion[] // newest first
  obsoletes: Pick<KbEntry, 'id' | 'key' | 'title' | 'status' | 'version'>[]
  obsoleted_by: Pick<KbEntry, 'id' | 'key' | 'title' | 'status' | 'version'> | null
}

export interface KbDocument {
  id: string
  collection_id: string
  filename: string
  mime_type: string
  size_bytes: number
  status: DocStatus
  chunk_count: number | null
  sync_status: SyncStatus | null
  created_at: ISODate
  error: string | null
}

export interface Chunk {
  id: string
  index: number
  text: string
  tokens: number
}

export interface SimilarEntry {
  entry_id: string
  key: string
  title: string
  content: string
  version: number
  status: KbStatus
  similarity: number | null // the backend reports the target, not a score
}

export interface Candidate {
  id: string
  key: string
  target_type: CandidateTarget
  op: CandidateOp
  target_id: string | null
  collection_id: string | null
  draft: { title: string; content: string }
  reason: string | null
  risk_level: RiskLevel
  origin: CandidateOrigin
  stage: CandidateStage
  status: CandidateStatus
  requested_for_user: string | null
  decided_by: string | null
  decided_at: ISODate | null
  decision_note: string | null
  expires_at: ISODate | null
  created_at: ISODate
  episode_id: string | null
  similar: SimilarEntry | null
  screening: { reasons: string[]; pii: string[] }
}

export interface Decision {
  scope?: 'personal' | 'team'
  draft?: EntryPatch
  note?: string
}

export interface DecisionResult extends Candidate {
  entry: KbEntry | null
}

export interface Written {
  entry: KbEntry | null
  candidate: Candidate | null
}

export interface Episode {
  id: string
  kind: 'conversation' | 'import' | 'manual'
  user_id: string | null
  session_id: string | null
  summary: string
  created_at: ISODate
}

export interface UserSummary {
  user_id: string
  memory_count: number
  pinned_count: number
  last_active_at: ISODate
}

export interface UserDetail extends UserSummary {
  profile: Record<string, string>
  pinned: MemoryBrief[]
}

export interface Erasure {
  user_id: string
  deleted_memories: number
  deleted_episodes: number
}

export interface Job {
  id: string
  kind: string
  status: JobStatus
  attempts: number
  max_attempts: number
  object_type: ObjectType | null
  object_id: string | null
  last_error: string | null
  run_after: ISODate
  created_at: ISODate
  updated_at: ISODate
}

export interface SyncRef {
  id: string
  object_type: ObjectType
  object_id: string
  label: string
  provider: string
  sync_status: SyncStatus
  synced_version: number | null
  last_error: string | null
  updated_at: ISODate
}

export interface ProviderStatus {
  name: string
  kind: 'memory' | 'kb'
  engine: string
  version: string
  capabilities: string[]
  health: 'healthy' | 'degraded' | 'down'
  latency_p95_ms: number | null
  error_rate: number | null
  queue: { pending: number; failed: number }
  last_reconciled_at: ISODate | null
  reconcile_drift: number | null
}

export interface RetrievalRequest {
  query: string
  user_id?: string
  sources: ('memory' | 'kb')[]
  top_k?: number
}

export interface RetrievalHit {
  source: 'memory' | 'kb'
  id: string
  key: string
  text: string
  score: number
  trust: number | null
}

export interface RetrievalResult {
  log_id: string
  query: string
  // The backend reports only the final ranking; the intermediate stages are null there.
  stages: { vector: RetrievalHit[] | null; keyword: RetrievalHit[] | null; rrf: RetrievalHit[] | null; final: RetrievalHit[] }
  context: string
  degraded: ('memory' | 'kb' | 'rerank')[]
  latency_ms: number
  gap: boolean
}

export interface RetrievalLog {
  id: string
  query: string
  user_id: string | null
  top_score: number | null
  hit_count: number
  latency_ms: number | null
  degraded: boolean
  gap: boolean
  created_at: ISODate
}

export interface Stats {
  memories: Record<MemoryStatus, number>
  kb_entries: Record<KbStatus, number>
  documents: number
  pending_reviews: number
  awaiting_user: number
  writes_7d: { day: string; count: number }[]
  retrievals_7d: { day: string; count: number }[]
  retrieval_p95_ms: number | null
  failed_jobs: number
  gaps: { query: string; count: number; top_score: number | null }[]
}

export interface ApiKey {
  id: string
  name: string
  prefix: string
  scopes: ('read' | 'write' | 'review')[] // Admin access is the static admin token, not a key scope
  created_at: ISODate
  last_used_at: ISODate | null
  revoked_at: ISODate | null
}

export interface ApiKeyCreated extends ApiKey {
  secret: string
}

export interface NamespaceSettings {
  yaml: string
  templates: { name: string; body: string }[]
  embedding: { model: string; dimensions: number | null; space_id: string | null; rebuild_progress: number | null } | null
}
