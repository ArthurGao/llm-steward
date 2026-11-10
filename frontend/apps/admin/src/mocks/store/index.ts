// In-memory stand-in for the governance backend. It follows the same rules as
// GovernanceService (AGENTS.md rules 1-7) so the demo never shows an impossible state.
import type {
  ActorType,
  ApiKey,
  ApiKeyCreated,
  Candidate,
  Change,
  Chunk,
  Collection,
  Decision,
  DecisionResult,
  EntryHistory,
  EntryPatch,
  Erasure,
  Job,
  KbDocument,
  KbEntry,
  Memory,
  MemoryHistory,
  MemoryPatch,
  Namespace,
  NamespaceSettings,
  Page,
  ProviderStatus,
  RetrievalLog,
  RetrievalRequest,
  RetrievalResult,
  Role,
  Stats,
  SyncRef,
  UserDetail,
  UserSummary,
  Written,
} from '@/api/types'
import { can, type Action } from '@/auth/can'
import { buildFixtures, NOW, type NsData } from './fixtures'
import { runRetrieval, tokens } from './retrieval'

export { FIX, NOW } from './fixtures'

export interface Actor {
  type: ActorType
  id: string
  role?: Role
}

export class StoreError extends Error {
  readonly status: 403 | 404 | 409 | 422
  constructor(status: 403 | 404 | 409 | 422, title: string) {
    super(title)
    this.name = 'StoreError'
    this.status = status
  }
}

type ProposeFields = Pick<Candidate, 'op' | 'target_id' | 'collection_id' | 'draft' | 'reason' | 'risk_level' | 'origin' | 'similar' | 'screening'> & Partial<Pick<Candidate, 'episode_id'>>

const ENTRY_ORIGIN: Record<Candidate['origin'], KbEntry['origin']> = {
  promote: 'promoted_memory',
  user_suggestion: 'user_suggestion',
  extraction: 'extraction',
  api: 'manual',
}

export interface PageQuery {
  limit?: number
  cursor?: string | null
}

const PAGE = 25

function paginate<T>(items: T[], q: PageQuery = {}): Page<T> {
  const limit = q.limit ?? PAGE
  const start = Number(q.cursor ?? 0) || 0
  const end = start + limit
  return { items: items.slice(start, end), next_cursor: end < items.length ? String(end) : null, total: items.length }
}

function byNewest<T>(key: (t: T) => string) {
  return (a: T, b: T) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0)
}

function matches(q: string | undefined, ...fields: (string | null | undefined)[]): boolean {
  if (!q) return true
  const needle = q.toLowerCase()
  return fields.some((f) => f?.toLowerCase().includes(needle))
}

const MONEY = /\$|refund|invoice|price|discount|chargeback/i

export interface MemoryQuery extends PageQuery {
  user_id?: string
  agent_id?: string
  session_id?: string
  memory_type?: string
  status?: Memory['status']
  pinned?: boolean
  q?: string
  include_superseded?: boolean
}
export interface EntryQuery extends PageQuery {
  collection_id?: string
  status?: KbEntry['status']
  risk_level?: KbEntry['risk_level']
  q?: string
}
export interface CandidateQuery extends PageQuery {
  stage?: Candidate['stage']
  status?: Candidate['status'] | 'all'
  risk_level?: Candidate['risk_level']
  origin?: Candidate['origin']
  q?: string
}
export interface ChangeQuery extends PageQuery {
  object_type?: Change['object_type']
  object_id?: string
  actor_type?: ActorType
  op?: Change['op']
  q?: string
}
export interface JobQuery extends PageQuery {
  status?: Job['status']
}
export interface LogQuery extends PageQuery {
  gap?: boolean
}

export class Store {
  private readonly data = buildFixtures()

  // ------------------------------------------------------------ helpers

  private ns(slug: string): NsData {
    const d = this.data.get(slug)
    if (!d) throw new StoreError(404, `Namespace ${slug} not found`)
    return d
  }

  private require(actor: Actor, action: Action): void {
    if (actor.type === 'admin' && !can(actor.role ?? 'viewer', action)) {
      throw new StoreError(403, `The ${actor.role ?? 'viewer'} role cannot do this`)
    }
  }

  private nextId(d: NsData, prefix: string): string {
    d.seq += 1
    return `${prefix}_${d.seq.toString(36)}`
  }

  private log(d: NsData, row: Omit<Change, 'id' | 'created_at'>): Change {
    const change: Change = { ...row, id: this.nextId(d, 'chg'), created_at: NOW }
    d.changes.unshift(change)
    return change
  }

  private enqueue(d: NsData, kind: string, objectType: Job['object_type'], objectId: string | null): void {
    d.jobs.unshift({ id: this.nextId(d, 'job'), kind, status: 'pending', attempts: 0, max_attempts: 5, object_type: objectType, object_id: objectId, last_error: null, run_after: NOW, created_at: NOW, updated_at: NOW })
  }

  private markSync(d: NsData, entry: KbEntry, status: KbEntry['sync_status']): void {
    entry.sync_status = status
    const ref = d.syncRefs.find((r) => r.object_id === entry.id)
    if (ref) Object.assign(ref, { sync_status: status, updated_at: NOW, last_error: null })
    else d.syncRefs.unshift({ id: `ref_${entry.id}`, object_type: 'kb_entry', object_id: entry.id, label: `${entry.key} ${entry.title}`, provider: d.namespace.kb_provider.split(' ')[0] ?? 'kb', sync_status: status, synced_version: null, last_error: null, updated_at: NOW })
    this.enqueue(d, status === 'deleting' ? 'sync.remove' : 'sync.push', 'kb_entry', entry.id)
  }

  private similarTo(d: NsData, text: string): Candidate['similar'] {
    const q = new Set(tokens(text))
    let best: { e: KbEntry; s: number } | null = null
    for (const e of d.entries) {
      if (e.status !== 'active' && e.status !== 'verified') continue
      const t = tokens(`${e.title} ${e.content}`)
      const s = t.filter((w) => q.has(w)).length / Math.max(q.size, 1)
      if (!best || s > best.s) best = { e, s }
    }
    if (!best || best.s < 0.3) return null
    const { e, s } = best
    return { entry_id: e.id, key: e.key, title: e.title, content: e.content, version: e.version, status: e.status, similarity: Math.round(Math.min(s, 0.95) * 100) / 100 }
  }

  /** A new pending candidate for review, recorded in the change log (the single write path). */
  private propose(d: NsData, actor: Actor, fields: ProposeFields): Candidate {
    const c: Candidate = {
      id: this.nextId(d, 'cand'),
      key: `C-${d.seq.toString(36).toUpperCase()}`,
      target_type: 'kb_entry',
      stage: 'awaiting_review',
      status: 'pending',
      requested_for_user: null,
      decided_by: null,
      decided_at: null,
      decision_note: null,
      expires_at: null,
      created_at: NOW,
      episode_id: null,
      ...fields,
    }
    d.candidates.unshift(c)
    this.log(d, { object_type: 'candidate', object_id: c.id, op: 'create', actor_type: actor.type, actor_id: actor.id, reason: c.reason, before: null, after: { title: c.draft.title, risk_level: c.risk_level, stage: c.stage } })
    return c
  }

  // ------------------------------------------------------------ namespaces & stats

  listNamespaces(): Namespace[] {
    return [...this.data.values()].map((d) => d.namespace)
  }

  stats(slug: string): Stats {
    const d = this.ns(slug)
    const count = <K extends string>(keys: readonly K[], values: K[]) =>
      Object.fromEntries(keys.map((k) => [k, values.filter((v) => v === k).length])) as Record<K, number>
    const days = Array.from({ length: 7 }, (_, i) => new Date(Date.parse(NOW) - (6 - i) * 86_400_000).toISOString().slice(0, 10))
    const writes = [38, 51, 44, 29, 12, 63, 47]
    const reads = [212, 260, 241, 198, 87, 305, 276]
    const gaps = new Map<string, { count: number; top: number }>()
    for (const l of d.retrievalLogs.filter((x) => x.gap)) {
      const g = gaps.get(l.query) ?? { count: 0, top: 0 }
      gaps.set(l.query, { count: g.count + 1, top: Math.max(g.top, l.top_score) })
    }
    return {
      memories: count(['active', 'superseded', 'deleted'] as const, d.memories.map((m) => m.status)),
      kb_entries: count(['draft', 'active', 'verified', 'deprecated', 'rejected'] as const, d.entries.map((e) => e.status)),
      documents: d.documents.length,
      pending_reviews: d.candidates.filter((c) => c.status === 'pending' && c.stage === 'awaiting_review').length,
      awaiting_user: d.candidates.filter((c) => c.status === 'pending' && c.stage === 'awaiting_user').length,
      writes_7d: days.map((day, i) => ({ day, count: d.namespace.slug === 'acme-support' ? (writes[i] ?? 0) : Math.round((writes[i] ?? 0) / 6) })),
      retrievals_7d: days.map((day, i) => ({ day, count: d.namespace.slug === 'acme-support' ? (reads[i] ?? 0) : Math.round((reads[i] ?? 0) / 8) })),
      retrieval_p95_ms: d.namespace.slug === 'acme-support' ? 1240 : 310,
      failed_jobs: d.jobs.filter((j) => j.status === 'failed').length,
      gaps: [...gaps.entries()].map(([query, g]) => ({ query, count: g.count, top_score: g.top })).sort((a, b) => b.count - a.count),
    }
  }

  // ------------------------------------------------------------ memories & users

  listMemories(slug: string, q: MemoryQuery): Page<Memory> {
    const d = this.ns(slug)
    const items = d.memories
      .filter((m) => (q.status ? m.status === q.status : q.include_superseded || m.status === 'active'))
      .filter((m) => (q.user_id ? m.user_id === q.user_id : true))
      .filter((m) => (q.agent_id ? m.agent_id === q.agent_id : true))
      .filter((m) => (q.session_id ? m.session_id === q.session_id : true))
      .filter((m) => (q.memory_type ? m.memory_type === q.memory_type : true))
      .filter((m) => (q.pinned === undefined ? true : m.pinned === q.pinned))
      .filter((m) => matches(q.q, m.content, m.user_id, m.id))
      .sort(byNewest((m) => m.updated_at))
    return paginate(items, q)
  }

  getMemory(slug: string, id: string): Memory {
    const m = this.ns(slug).memories.find((x) => x.id === id)
    if (!m) throw new StoreError(404, `Memory ${id} not found`)
    return m
  }

  memoryHistory(slug: string, id: string): MemoryHistory {
    const d = this.ns(slug)
    const m = this.getMemory(slug, id)
    // Walk back to the first fact of the chain, then forward through superseded_by.
    let first = m
    for (;;) {
      const prev = d.memories.find((x) => x.superseded_by === first.id)
      if (!prev) break
      first = prev
    }
    const chain = [first]
    while (chain.at(-1)?.superseded_by) {
      const next = d.memories.find((x) => x.id === chain.at(-1)?.superseded_by)
      if (!next) break
      chain.push(next)
    }
    return {
      memory_id: id,
      changes: d.changes.filter((c) => c.object_id === id),
      chain: chain.map(({ id: cid, content, status, valid_from, valid_to, superseded_by }) => ({ id: cid, content, status, valid_from, valid_to, superseded_by })),
    }
  }

  patchMemory(slug: string, id: string, patch: MemoryPatch, actor: Actor): Memory {
    this.require(actor, 'editMemory')
    const d = this.ns(slug)
    const m = this.getMemory(slug, id)
    if (m.status !== 'active') throw new StoreError(409, 'Only an active memory can be changed')
    const before = Object.fromEntries(Object.keys(patch).map((k) => [k, m[k as keyof Memory]]))
    Object.assign(m, patch, { updated_at: NOW })
    this.log(d, { object_type: 'memory', object_id: id, op: 'update', actor_type: actor.type, actor_id: actor.id, reason: null, before: { ...before, user_id: m.user_id }, after: { ...patch, user_id: m.user_id } })
    return m
  }

  deleteMemory(slug: string, id: string, actor: Actor): Memory {
    this.require(actor, 'editMemory')
    const d = this.ns(slug)
    const m = this.getMemory(slug, id)
    if (m.status === 'deleted') throw new StoreError(409, 'Memory already deleted')
    const prior = m.status
    // Invalidate, never delete (rule 6): the row stays with its history.
    Object.assign(m, { status: 'deleted', valid_to: NOW, updated_at: NOW, sync_status: 'deleting', pinned: false })
    this.log(d, { object_type: 'memory', object_id: id, op: 'transition', actor_type: actor.type, actor_id: actor.id, reason: 'Deleted in Admin', before: { status: prior, user_id: m.user_id }, after: { status: 'deleted', user_id: m.user_id } })
    this.enqueue(d, 'sync.remove', 'memory', id)
    return m
  }

  promoteMemory(slug: string, id: string, actor: Actor): Candidate {
    this.require(actor, 'editMemory')
    const d = this.ns(slug)
    const m = this.getMemory(slug, id)
    if (m.status !== 'active') throw new StoreError(409, 'Only an active memory can be promoted')
    const similar = this.similarTo(d, m.content)
    return this.propose(d, actor, {
      op: similar ? 'UPDATE' : 'ADD',
      target_id: similar?.entry_id ?? null,
      collection_id: similar ? (d.entries.find((e) => e.id === similar.entry_id)?.collection_id ?? null) : (d.collections[0]?.id ?? null),
      draft: { title: similar?.title ?? m.content.split(' ').slice(0, 6).join(' '), content: m.content.replace(/^(Was told|Is|Prefers|Asked)\s+/i, '') },
      reason: `Promoted from memory ${m.id} of ${m.user_id ?? 'a shared scope'}.`,
      risk_level: MONEY.test(m.content) ? 'high' : 'medium',
      origin: 'promote',
      episode_id: m.source_refs.find((r) => r.type === 'episode')?.id ?? null,
      similar,
      screening: { reasons: MONEY.test(m.content) ? ['money amount'] : [], pii: [] },
    })
  }

  listUsers(slug: string, q: PageQuery & { q?: string }): Page<UserSummary> {
    const d = this.ns(slug)
    const users = new Map<string, UserSummary>()
    for (const m of d.memories) {
      if (!m.user_id || m.status === 'deleted') continue
      const u = users.get(m.user_id) ?? { user_id: m.user_id, memory_count: 0, pinned_count: 0, last_active_at: m.updated_at }
      if (m.status === 'active') u.memory_count += 1
      if (m.pinned) u.pinned_count += 1
      if (m.updated_at > u.last_active_at) u.last_active_at = m.updated_at
      users.set(m.user_id, u)
    }
    const items = [...users.values()].filter((u) => matches(q.q, u.user_id)).sort(byNewest((u) => u.last_active_at))
    return paginate(items, q)
  }

  getUser(slug: string, userId: string): UserDetail {
    const theirs = this.ns(slug).memories.filter((m) => m.user_id === userId && m.status !== 'deleted')
    if (theirs.length === 0) throw new StoreError(404, `User ${userId} has no memories`)
    const mine = theirs.filter((m) => m.status === 'active')
    const summary: UserSummary = {
      user_id: userId,
      memory_count: mine.length,
      pinned_count: theirs.filter((m) => m.pinned).length,
      last_active_at: theirs.reduce((a, m) => (m.updated_at > a ? m.updated_at : a), theirs[0]!.updated_at),
    }
    const profile: Record<string, string> = {}
    for (const m of mine) {
      const key = m.metadata.key
      if (typeof key === 'string') profile[key] = typeof m.metadata.value === 'string' ? m.metadata.value : m.content
    }
    return {
      ...summary,
      profile,
      pinned: mine.filter((m) => m.pinned).map(({ id, content, status, valid_from, valid_to, superseded_by }) => ({ id, content, status, valid_from, valid_to, superseded_by })),
    }
  }

  eraseUser(slug: string, userId: string, actor: Actor): Erasure {
    this.require(actor, 'eraseUser')
    const d = this.ns(slug)
    const doomed = new Set(d.memories.filter((m) => m.user_id === userId).map((m) => m.id))
    const episodes = d.episodes.filter((e) => e.user_id === userId).length
    if (doomed.size === 0 && episodes === 0) throw new StoreError(404, `User ${userId} has no data`)
    // The one hard delete (rule 6 exception): rows go, history keeps the events without content.
    d.memories = d.memories.filter((m) => !doomed.has(m.id))
    d.episodes = d.episodes.filter((e) => e.user_id !== userId)
    d.syncRefs = d.syncRefs.filter((r) => !doomed.has(r.object_id))
    for (const c of d.changes) {
      if (doomed.has(c.object_id)) Object.assign(c, { before: null, after: null })
    }
    const result = { user_id: userId, deleted_memories: doomed.size, deleted_episodes: episodes }
    this.log(d, { object_type: 'user', object_id: userId, op: 'erase', actor_type: actor.type, actor_id: actor.id, reason: 'User data erasure request', before: null, after: { deleted_memories: result.deleted_memories, deleted_episodes: result.deleted_episodes } })
    this.enqueue(d, 'memory.erase', 'user', userId)
    return result
  }

  // ------------------------------------------------------------ knowledge base

  listCollections(slug: string): Collection[] {
    const d = this.ns(slug)
    return d.collections.map((c) => ({
      ...c,
      entry_count: d.entries.filter((e) => e.collection_id === c.id).length,
      document_count: d.documents.filter((x) => x.collection_id === c.id).length,
    }))
  }

  listEntries(slug: string, q: EntryQuery): Page<KbEntry> {
    const items = this.ns(slug)
      .entries.filter((e) => (q.collection_id ? e.collection_id === q.collection_id : true))
      .filter((e) => (q.status ? e.status === q.status : true))
      .filter((e) => (q.risk_level ? e.risk_level === q.risk_level : true))
      .filter((e) => matches(q.q, e.title, e.content, e.key))
      .sort(byNewest((e) => e.updated_at))
    return paginate(items, q)
  }

  getEntry(slug: string, id: string): KbEntry {
    const e = this.ns(slug).entries.find((x) => x.id === id)
    if (!e) throw new StoreError(404, `Entry ${id} not found`)
    return e
  }

  entryHistory(slug: string, id: string): EntryHistory {
    const d = this.ns(slug)
    const e = this.getEntry(slug, id)
    const versions = d.changes
      .filter((c) => c.object_type === 'kb_entry' && c.object_id === id && c.after && typeof c.after.version === 'number')
      .map((c) => ({
        version: c.after?.version as number,
        title: typeof c.after?.title === 'string' ? c.after.title : e.title,
        content: String(c.after?.content ?? ''),
        status: (c.after?.status as KbEntry['status']) ?? e.status,
        changed_at: c.created_at,
        actor_type: c.actor_type,
        actor_id: c.actor_id,
        reason: c.reason,
      }))
      .sort((a, b) => b.version - a.version)
    if (versions[0]) versions[0].status = e.status
    const brief = (x: KbEntry) => ({ id: x.id, key: x.key, title: x.title, status: x.status, version: x.version })
    const successor = e.superseded_by ? d.entries.find((x) => x.id === e.superseded_by) : undefined
    return {
      entry_id: id,
      versions,
      obsoletes: d.entries.filter((x) => x.superseded_by === id).map(brief),
      obsoleted_by: successor ? brief(successor) : null,
    }
  }

  patchEntry(slug: string, id: string, patch: EntryPatch, actor: Actor): Written {
    this.require(actor, 'editKb')
    const d = this.ns(slug)
    const e = this.getEntry(slug, id)
    if (e.status === 'deprecated' || e.status === 'rejected') throw new StoreError(409, `A ${e.status} entry cannot be edited`)
    if (e.status === 'verified') {
      // Verified knowledge only changes through review.
      const c = this.propose(d, actor, {
        op: 'UPDATE',
        target_id: e.id,
        collection_id: e.collection_id,
        draft: { title: patch.title ?? e.title, content: patch.content ?? e.content },
        reason: `Edit of verified ${e.key} by ${actor.id}`,
        risk_level: e.risk_level,
        origin: 'api',
        similar: { entry_id: e.id, key: e.key, title: e.title, content: e.content, version: e.version, status: e.status, similarity: 1 },
        screening: { reasons: MONEY.test(patch.content ?? '') ? ['money amount'] : [], pii: [] },
      })
      return { entry: null, candidate: c }
    }
    const before = { version: e.version, title: e.title, content: e.content }
    Object.assign(e, { title: patch.title ?? e.title, content: patch.content ?? e.content, version: e.version + 1, updated_at: NOW })
    this.log(d, { object_type: 'kb_entry', object_id: id, op: 'update', actor_type: actor.type, actor_id: actor.id, reason: 'Edited in Admin', before, after: { version: e.version, title: e.title, content: e.content, status: e.status } })
    if (e.status === 'active') this.markSync(d, e, 'pending')
    return { entry: e, candidate: null }
  }

  deprecateEntry(slug: string, id: string, reason: string, actor: Actor): KbEntry {
    this.require(actor, 'editKb')
    const d = this.ns(slug)
    const e = this.getEntry(slug, id)
    if (e.status !== 'active' && e.status !== 'verified') throw new StoreError(409, `A ${e.status} entry cannot be deprecated`)
    const before = { status: e.status }
    Object.assign(e, { status: 'deprecated', valid_to: NOW, updated_at: NOW })
    this.log(d, { object_type: 'kb_entry', object_id: id, op: 'transition', actor_type: actor.type, actor_id: actor.id, reason: reason || null, before, after: { status: 'deprecated' } })
    this.markSync(d, e, 'deleting')
    return e
  }

  listDocuments(slug: string, collectionId?: string): KbDocument[] {
    return this.ns(slug).documents.filter((x) => !collectionId || x.collection_id === collectionId)
  }

  listChunks(slug: string, docId: string): Chunk[] {
    const d = this.ns(slug)
    if (!d.documents.some((x) => x.id === docId)) throw new StoreError(404, `Document ${docId} not found`)
    return d.chunks[docId] ?? []
  }

  // ------------------------------------------------------------ candidates

  listCandidates(slug: string, q: CandidateQuery): Page<Candidate> {
    const status = q.status ?? 'pending'
    const items = this.ns(slug)
      .candidates.filter((c) => status === 'all' || c.status === status)
      .filter((c) => (q.stage ? c.stage === q.stage : true))
      .filter((c) => (q.risk_level ? c.risk_level === q.risk_level : true))
      .filter((c) => (q.origin ? c.origin === q.origin : true))
      .filter((c) => matches(q.q, c.draft.title, c.draft.content, c.key))
      .sort(byNewest((c) => c.created_at))
    return paginate(items, q)
  }

  getCandidate(slug: string, id: string): Candidate {
    const c = this.ns(slug).candidates.find((x) => x.id === id)
    if (!c) throw new StoreError(404, `Candidate ${id} not found`)
    return c
  }

  private materialise(d: NsData, c: Candidate, draft: { title: string; content: string }, verified: boolean, actor: Actor): KbEntry | null {
    if (c.target_type === 'memory') {
      const m: Memory = {
        id: this.nextId(d, 'mem'), content: draft.content, user_id: c.requested_for_user, agent_id: null, session_id: null, memory_type: 'preference',
        status: 'active', origin: 'direct', pinned: false, importance: 0.5, confidence: 0.9, tags: [], metadata: {}, valid_from: NOW, valid_to: null,
        created_at: NOW, updated_at: NOW, sync_status: 'pending', source_refs: [{ type: 'candidate', id: c.id }], superseded_by: null,
      }
      d.memories.unshift(m)
      this.log(d, { object_type: 'memory', object_id: m.id, op: 'create', actor_type: actor.type, actor_id: actor.id, reason: `From candidate ${c.key}`, before: null, after: { content: m.content, user_id: m.user_id, status: 'active' } })
      return null
    }
    const status = verified ? 'verified' : 'active'
    const target = c.target_id ? d.entries.find((e) => e.id === c.target_id) : undefined
    if (target) {
      const before = { version: target.version, content: target.content, status: target.status }
      Object.assign(target, {
        title: draft.title, content: draft.content, version: target.version + 1, status,
        verified_by: verified ? actor.id : target.verified_by, verified_at: verified ? NOW : target.verified_at, updated_at: NOW,
      })
      this.log(d, { object_type: 'kb_entry', object_id: target.id, op: 'transition', actor_type: actor.type, actor_id: actor.id, reason: `Accepted ${c.key}`, before, after: { version: target.version, title: target.title, content: target.content, status } })
      this.markSync(d, target, 'pending')
      return target
    }
    const n = Math.max(0, ...d.entries.map((x) => Number(x.key.slice(3)))) + 1
    const e: KbEntry = {
      id: this.nextId(d, 'kbe'), key: `KB-${String(n).padStart(4, '0')}`, collection_id: c.collection_id ?? d.collections[0]?.id ?? '', title: draft.title, content: draft.content,
      status, risk_level: c.risk_level, origin: ENTRY_ORIGIN[c.origin],
      author_type: c.origin === 'extraction' ? 'ai' : 'human', version: 1, created_by: actor.id, verified_by: verified ? actor.id : null, verified_at: verified ? NOW : null,
      valid_from: NOW, valid_to: null, tags: [], source_refs: c.episode_id ? [{ type: 'episode', id: c.episode_id }] : [], created_at: NOW, updated_at: NOW,
      superseded_by: null, sync_status: 'pending', hits: 0, helpful_rate: null, review_due_at: null,
    }
    d.entries.unshift(e)
    this.log(d, { object_type: 'kb_entry', object_id: e.id, op: 'create', actor_type: actor.type, actor_id: actor.id, reason: `Accepted ${c.key}`, before: null, after: { version: 1, title: e.title, content: e.content, status } })
    this.markSync(d, e, 'pending')
    return e
  }

  decide(slug: string, id: string, verdict: 'accept' | 'reject', decision: Decision, actor: Actor): DecisionResult {
    this.require(actor, 'review')
    const d = this.ns(slug)
    const c = this.getCandidate(slug, id)
    if (c.status !== 'pending') throw new StoreError(409, `Candidate ${c.key} was already ${c.status}`)
    const note = decision.note?.trim() ?? ''
    if (verdict === 'reject' && !note) throw new StoreError(422, 'A reason is required to reject')
    const draft = { title: decision.draft?.title ?? c.draft.title, content: decision.draft?.content ?? c.draft.content }
    // A reviewer's decision is what verifies knowledge (rule 5).
    const entry = verdict === 'accept' ? this.materialise(d, c, draft, true, actor) : null
    Object.assign(c, { status: verdict === 'accept' ? 'accepted' : 'rejected', decided_by: actor.id, decided_at: NOW, decision_note: note || null, draft })
    this.log(d, { object_type: 'candidate', object_id: c.id, op: 'transition', actor_type: actor.type, actor_id: actor.id, reason: note || null, before: { status: 'pending' }, after: { status: c.status } })
    return { ...c, entry }
  }

  confirmAsEndUser(slug: string, id: string, userId: string): DecisionResult {
    const d = this.ns(slug)
    const c = this.getCandidate(slug, id)
    if (c.stage !== 'awaiting_user' || c.requested_for_user !== userId) throw new StoreError(403, 'This candidate is not waiting for this user')
    if (c.status !== 'pending') throw new StoreError(409, `Candidate ${c.key} was already ${c.status}`)
    const actor: Actor = { type: 'end_user', id: userId }
    // End-user confirmation says "useful", never "correct": the entry stays active (rule 5).
    const entry = this.materialise(d, c, c.draft, false, actor)
    Object.assign(c, { status: 'accepted', decided_by: userId, decided_at: NOW })
    this.log(d, { object_type: 'candidate', object_id: c.id, op: 'transition', actor_type: 'end_user', actor_id: userId, reason: 'Confirmed by the user', before: { status: 'pending' }, after: { status: 'accepted' } })
    return { ...c, entry }
  }

  // ------------------------------------------------------------ retrieval

  retrieve(slug: string, req: RetrievalRequest): RetrievalResult {
    const d = this.ns(slug)
    const r = runRetrieval(req, d.entries, d.memories, NOW)
    const log: RetrievalLog = {
      id: this.nextId(d, 'rl'), query: req.query, user_id: req.user_id ?? null, top_score: r.stages.final[0]?.score ?? 0,
      hit_count: r.stages.final.length, latency_ms: r.latency_ms, degraded: r.degraded.length > 0, gap: r.gap, created_at: NOW,
    }
    d.retrievalLogs.unshift(log)
    return { ...r, log_id: log.id }
  }

  listRetrievalLogs(slug: string, q: LogQuery): Page<RetrievalLog> {
    const items = this.ns(slug).retrievalLogs.filter((l) => (q.gap === undefined ? true : l.gap === q.gap))
    return paginate(items, q)
  }

  addGapToKb(slug: string, logId: string, draft: { title: string; content: string; collection_id?: string }, actor: Actor): Written {
    this.require(actor, 'editKb')
    const d = this.ns(slug)
    const log = d.retrievalLogs.find((l) => l.id === logId)
    if (!log) throw new StoreError(404, `Retrieval ${logId} not found`)
    if (!draft.title.trim() || !draft.content.trim()) throw new StoreError(422, 'Title and answer are required')
    const c = this.propose(d, actor, {
      op: 'ADD',
      target_id: null,
      collection_id: draft.collection_id ?? d.collections[0]?.id ?? null,
      draft: { title: draft.title.trim(), content: draft.content.trim() },
      reason: `Gap found in the Playground: “${log.query}” (top score ${log.top_score.toFixed(2)})`,
      risk_level: 'medium',
      origin: 'api',
      similar: null,
      screening: { reasons: ['new knowledge'], pii: [] },
    })
    return { entry: null, candidate: c }
  }

  // ------------------------------------------------------------ activity, jobs, providers

  listChanges(slug: string, q: ChangeQuery): Change[] {
    return this.ns(slug)
      .changes.filter((c) => (q.object_type ? c.object_type === q.object_type : true))
      .filter((c) => (q.object_id ? c.object_id === q.object_id : true))
      .filter((c) => (q.actor_type ? c.actor_type === q.actor_type : true))
      .filter((c) => (q.op ? c.op === q.op : true))
      .filter((c) => matches(q.q, c.object_id, c.actor_id, c.reason))
  }

  activity(slug: string, q: ChangeQuery): Page<Change> {
    return paginate(this.listChanges(slug, q), q)
  }

  listJobs(slug: string, q: JobQuery): Page<Job> {
    return paginate(this.ns(slug).jobs.filter((j) => (q.status ? j.status === q.status : true)), q)
  }

  private job(slug: string, id: string): Job {
    const j = this.ns(slug).jobs.find((x) => x.id === id)
    if (!j) throw new StoreError(404, `Job ${id} not found`)
    return j
  }

  retryJob(slug: string, id: string, actor: Actor): Job {
    this.require(actor, 'manageJobs')
    const j = this.job(slug, id)
    if (j.status !== 'failed') throw new StoreError(409, 'Only a failed job can be retried')
    return Object.assign(j, { status: 'pending', last_error: null, run_after: NOW, updated_at: NOW })
  }

  cancelJob(slug: string, id: string, actor: Actor): Job {
    this.require(actor, 'manageJobs')
    const j = this.job(slug, id)
    if (j.status !== 'pending') throw new StoreError(409, 'Only a pending job can be cancelled')
    return Object.assign(j, { status: 'failed', last_error: `Cancelled by ${actor.id}`, updated_at: NOW })
  }

  listSyncRefs(slug: string): SyncRef[] {
    return this.ns(slug).syncRefs
  }

  retrySync(slug: string, refId: string, actor: Actor): SyncRef {
    this.require(actor, 'manageJobs')
    const d = this.ns(slug)
    const ref = d.syncRefs.find((r) => r.id === refId)
    if (!ref) throw new StoreError(404, `Sync ${refId} not found`)
    if (ref.sync_status !== 'failed') throw new StoreError(409, 'Only a failed sync can be retried')
    Object.assign(ref, { sync_status: 'pending', last_error: null, updated_at: NOW })
    const entry = d.entries.find((e) => e.id === ref.object_id)
    if (entry) entry.sync_status = 'pending'
    this.enqueue(d, 'sync.push', ref.object_type, ref.object_id)
    return ref
  }

  providers(slug: string): ProviderStatus[] {
    const d = this.ns(slug)
    return d.providers.map((p) => {
      if (p.kind !== 'kb') return p
      const refs = d.syncRefs.filter((r) => r.object_type !== 'memory')
      return { ...p, queue: { pending: refs.filter((r) => r.sync_status === 'pending' || r.sync_status === 'deleting').length, failed: refs.filter((r) => r.sync_status === 'failed').length } }
    })
  }

  // ------------------------------------------------------------ settings & keys

  settings(slug: string): NamespaceSettings {
    return this.ns(slug).settings
  }

  listKeys(slug: string): ApiKey[] {
    return this.ns(slug).keys
  }

  createKey(slug: string, body: { name: string; scopes: ApiKey['scopes'] }, actor: Actor): ApiKeyCreated {
    this.require(actor, 'manageKeys')
    const d = this.ns(slug)
    if (!body.name.trim()) throw new StoreError(422, 'A key needs a name')
    if (body.scopes.length === 0) throw new StoreError(422, 'Pick at least one scope')
    const alnum = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
    let x = d.seq * 2654435761
    const rand = (n: number, chars: string) =>
      Array.from({ length: n }, () => {
        x = (x * 1103515245 + 12345) % 2147483648
        return chars[x % chars.length]
      }).join('')
    const prefix = rand(6, 'abcdefghijklmnopqrstuvwxyz0123456789')
    const key: ApiKey = { id: this.nextId(d, 'key'), name: body.name.trim(), prefix, scopes: body.scopes, created_at: NOW, last_used_at: null, revoked_at: null }
    d.keys.unshift(key)
    // Only the hash would be stored by the real backend (rule 10); the secret is returned once.
    return { ...key, secret: `stw_${prefix}_${rand(24, alnum)}` }
  }

  revokeKey(slug: string, id: string, actor: Actor): ApiKey {
    this.require(actor, 'manageKeys')
    const k = this.ns(slug).keys.find((x) => x.id === id)
    if (!k) throw new StoreError(404, `Key ${id} not found`)
    if (k.revoked_at) throw new StoreError(409, 'Key already revoked')
    k.revoked_at = NOW
    return k
  }
}

export function createStore(): Store {
  return new Store()
}
