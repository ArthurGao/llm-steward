// The live Admin API: the same `api` table as the mock (pages do not know which one runs), over the
// client generated from the backend's OpenAPI spec (src/client, the only contract), with adapters
// from the backend's shapes to the view types in ./types. A value the backend does not have is
// shown as unknown (null / "—"), never invented.
import { client } from '@/client/client.gen'
import * as sdk from '@/client/sdk.gen'
import type * as gen from '@/client/types.gen'
import { getConnection } from '@/auth/session'
import { ApiError, type Problem } from './client'
import type { mockApi, Query } from './endpoints'
import type * as ui from './types'

// Base URL and the admin token come from the connection (the Connect page), read per request: the
// client builds every request against a placeholder origin, and this swaps in the backend.
const PLACEHOLDER = 'http://steward.invalid'
client.setConfig({ baseUrl: PLACEHOLDER, throwOnError: true })
client.interceptors.request.use(async (request) => {
  const session = getConnection()
  if (!session) throw new ApiError(401, { title: 'Not connected to a backend' })
  const headers = new Headers(request.headers)
  headers.set('Authorization', `Bearer ${session.token}`)
  // The body is copied, not re-streamed: a streamed body needs `duplex`, which not every fetch takes.
  const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer()
  return new Request(session.baseUrl + request.url.slice(PLACEHOLDER.length), { method: request.method, headers, body, signal: request.signal })
})
// A Problem Details body becomes an ApiError; so does a network failure (status 0, its message).
client.interceptors.error.use((error, response) => {
  if (error instanceof ApiError) return error
  if (error instanceof Error) return new ApiError(response?.status ?? 0, { title: error.message })
  const problem = (error ?? {}) as Partial<Problem>
  return new ApiError(response?.status ?? 0, { title: problem.title ?? `HTTP ${response?.status ?? 0}`, status: problem.status, detail: problem.detail })
})

// Errors throw (throwOnError, mapped to ApiError above); a 2xx without a body is a contract break.
async function call<T>(promise: Promise<{ data?: T }>): Promise<T> {
  const { data } = await promise
  if (data === undefined) throw new ApiError(502, { title: 'Empty response from the backend' })
  return data
}

const path = (slug: string) => ({ slug })
const shortKey = (id: string, prefix: string) => `${prefix}-${id.slice(-6)}`
const str = (q: Query, k: string) => (q[k] === undefined || q[k] === null || q[k] === '' ? undefined : String(q[k]))

const paging = (q: Query) => ({ cursor: str(q, 'cursor'), limit: q.limit ? Number(q.limit) : undefined })

function page<T, U>(p: { items: T[]; next_cursor?: string | null }, map: (t: T) => U): ui.Page<U> {
  return { items: p.items.map(map), next_cursor: p.next_cursor ?? null, total: null }
}

// --- adapters -------------------------------------------------------------------------------------

function memory(m: gen.MemoryOut): ui.Memory {
  return { ...m, metadata: m.metadata ?? {}, source_refs: m.source_refs ?? [], superseded_by: null }
}

function change(c: gen.ChangeOut | gen.ActivityOut, object?: { type: ui.ObjectType; id: string }): ui.Change {
  return {
    id: c.id,
    object_type: ('object_type' in c ? c.object_type : object?.type ?? 'memory') as ui.ObjectType,
    object_id: 'object_id' in c ? c.object_id : object?.id ?? '',
    op: c.op as ui.ChangeOp,
    actor_type: c.actor_type as ui.ActorType,
    actor_id: c.actor_id,
    reason: c.reason,
    before: c.before ?? null,
    after: c.after ?? null,
    created_at: c.created_at,
  }
}

function entry(e: gen.EntryOut): ui.KbEntry {
  const rated = (e.helpful_count ?? 0) + (e.unhelpful_count ?? 0)
  return {
    ...e,
    key: shortKey(e.id, 'KB'),
    source_refs: (e.source_refs ?? []) as unknown as ui.SourceRef[],
    superseded_by: e.superseded_by ?? null,
    sync_status: null, // per-object sync state is on the Providers page
    hits: e.hit_count ?? 0,
    helpful_rate: rated ? (e.helpful_count ?? 0) / rated : null,
    review_due_at: null,
  }
}

function screening(reason: string | null): ui.Candidate['screening'] {
  const reasons = (reason ?? '').split(/;\s*/).filter(Boolean)
  return { reasons, pii: reasons.filter((r) => r.startsWith('pii: ')).map((r) => r.slice(5)) }
}

function candidate(c: gen.CandidateOut, similar: ui.SimilarEntry | null = null): ui.Candidate {
  const draft = c.draft as { title?: string; content?: string; collection_id?: string; source_refs?: ui.SourceRef[] }
  const episode = (draft.source_refs ?? []).find((r) => r.type === 'episode')
  return {
    ...c,
    target_type: c.target_type as ui.CandidateTarget,
    key: shortKey(c.id, 'C'),
    collection_id: draft.collection_id ?? null,
    draft: { title: draft.title ?? '', content: draft.content ?? '' },
    episode_id: episode?.id ?? null,
    similar,
    screening: screening(c.reason),
  }
}

/** For an UPDATE, the entry it would obsolete (the diff the reviewer reads). */
async function withTarget(slug: string, c: gen.CandidateOut): Promise<ui.Candidate> {
  if (c.op !== 'UPDATE' || c.target_type !== 'kb_entry' || !c.target_id) return candidate(c)
  const target = entry(await call(sdk.getEntryAdminV1NsSlugKbEntriesEntryIdGet({ path: { ...path(slug), entry_id: c.target_id } })))
  return candidate(c, { entry_id: target.id, key: target.key, title: target.title, content: target.content, version: target.version, status: target.status, similarity: 1 })
}

function written(w: gen.Written): ui.Written {
  return { entry: w.entry ? entry(w.entry) : null, candidate: w.candidate ? candidate(w.candidate) : null }
}

function job(j: gen.AdminJobOut): ui.Job {
  return { id: j.id, kind: j.type, status: j.status, attempts: j.attempts, max_attempts: j.max_attempts, object_type: null, object_id: null, last_error: j.error, run_after: j.run_after, created_at: j.created_at, updated_at: j.updated_at }
}

function syncRef(r: gen.SyncRefOut): ui.SyncRef {
  return { id: r.id, object_type: r.object_type as ui.ObjectType, object_id: r.object_id, label: shortKey(r.object_id, r.object_type === 'memory' ? 'M' : 'KB'), provider: r.provider, sync_status: r.sync_status, synced_version: r.synced_version, last_error: r.last_error, updated_at: r.last_synced_at ?? '' }
}

function hit(i: gen.RetrievedItemOut): ui.RetrievalHit {
  return { source: i.source, id: i.id, key: i.kind === 'memory' ? shortKey(i.id, 'M') : shortKey(i.id, 'KB'), text: i.title ? `${i.title}: ${i.content}` : i.content, score: i.score, trust: null }
}

// --- the table ------------------------------------------------------------------------------------

export const liveApi: typeof mockApi = {
  namespaces: async () => ({ items: (await call(sdk.namespacesAdminV1NamespacesGet())).items }),
  stats: async (slug) => {
    const s = await call(sdk.statsAdminV1NsSlugStatsGet({ path: path(slug) }))
    return {
      ...s,
      memories: s.memories as ui.Stats['memories'],
      kb_entries: s.kb_entries as ui.Stats['kb_entries'],
      writes_7d: s.writes_7d ?? [],
      retrievals_7d: s.retrievals_7d ?? [],
      gaps: s.gaps.map((g) => ({ ...g, top_score: null })),
    }
  },

  memories: {
    list: async (slug, q) =>
      page(
        await call(sdk.listMemoriesAdminV1NsSlugMemoriesGet({ path: path(slug), query: { user_id: str(q, 'user_id'), memory_type: str(q, 'memory_type'), status: str(q, 'status') as gen.MemoryStatus | undefined, tag: str(q, 'tag'), ...paging(q) } })),
        memory,
      ),
    get: async (slug, id) => memory(await call(sdk.getMemoryAdminV1NsSlugMemoriesMemoryIdGet({ path: { ...path(slug), memory_id: id } }))),
    history: async (slug, id) => {
      const h = await call(sdk.memoryHistoryAdminV1NsSlugMemoriesMemoryIdHistoryGet({ path: { ...path(slug), memory_id: id } }))
      return { memory_id: h.memory_id, changes: h.changes.map((c) => change(c, { type: 'memory', id })), chain: h.chain }
    },
    patch: async (slug, id, patch) => memory(await call(sdk.patchMemoryAdminV1NsSlugMemoriesMemoryIdPatch({ path: { ...path(slug), memory_id: id }, body: patch }))),
    remove: async (slug, id) => memory(await call(sdk.deleteMemoryAdminV1NsSlugMemoriesMemoryIdDelete({ path: { ...path(slug), memory_id: id } }))),
    promote: async (slug, id) => {
      const w = await call(sdk.promoteAdminV1NsSlugMemoriesMemoryIdPromotePost({ path: { ...path(slug), memory_id: id } }))
      if (!w.candidate) throw new ApiError(500, { title: 'Promote produced no candidate' })
      return candidate(w.candidate)
    },
    eraseUser: async (slug, userId) => {
      const e = await call(sdk.eraseUserAdminV1NsSlugMemoriesDelete({ path: path(slug), query: { user_id: userId } }))
      return { user_id: e.user_id, deleted_memories: e.memories, deleted_episodes: e.episodes }
    },
  },

  users: {
    list: async (slug) => {
      const r = await call(sdk.usersAdminV1NsSlugUsersGet({ path: path(slug) }))
      return { items: r.items, next_cursor: null, total: r.items.length }
    },
    get: async (slug, userId) => {
      const u = await call(sdk.userAdminV1NsSlugUsersUserIdGet({ path: { ...path(slug), user_id: userId } }))
      return u
    },
  },

  kb: {
    collections: async (slug) => ({
      items: (await call(sdk.collectionsAdminV1NsSlugKbCollectionsGet({ path: path(slug) }))).items.map((c) => ({ ...c, review_policy: c.review_policy ?? {} })),
    }),
    entries: async (slug, q) =>
      page(await call(sdk.listEntriesAdminV1NsSlugKbEntriesGet({ path: path(slug), query: { collection_id: str(q, 'collection_id'), status: str(q, 'status') as gen.KbStatus | undefined, ...paging(q) } })), entry),
    entry: async (slug, id) => entry(await call(sdk.getEntryAdminV1NsSlugKbEntriesEntryIdGet({ path: { ...path(slug), entry_id: id } }))),
    history: async (slug, id) => {
      const h = await call(sdk.entryHistoryAdminV1NsSlugKbEntriesEntryIdHistoryGet({ path: { ...path(slug), entry_id: id } }))
      const link = (l: gen.EntryLink) => ({ id: l.id, key: shortKey(l.id, 'KB'), title: l.title, status: l.status as ui.KbStatus, version: l.version })
      return {
        entry_id: h.entry_id,
        versions: h.versions.map((v) => ({ ...v, status: v.status as ui.KbStatus, actor_type: v.actor_type as ui.ActorType })),
        obsoletes: h.obsoletes.map(link),
        obsoleted_by: h.obsoleted_by ? link(h.obsoleted_by) : null,
      }
    },
    patch: async (slug, id, patch) => written(await call(sdk.patchEntryAdminV1NsSlugKbEntriesEntryIdPatch({ path: { ...path(slug), entry_id: id }, body: patch }))),
    deprecate: async (slug, id, reason) => entry(await call(sdk.deprecateEntryAdminV1NsSlugKbEntriesEntryIdDeprecatePost({ path: { ...path(slug), entry_id: id }, query: { reason } }))),
    documents: async (slug, collectionId) => ({
      items: (await call(sdk.listDocumentsAdminV1NsSlugKbDocumentsGet({ path: path(slug), query: { collection_id: collectionId } }))).items.map((d) => ({
        id: d.id,
        collection_id: d.collection_id,
        filename: d.filename,
        mime_type: d.mime ?? '',
        size_bytes: d.size_bytes,
        status: d.status,
        chunk_count: null,
        sync_status: null,
        created_at: d.created_at,
        error: d.error ?? null,
      })),
    }),
    chunks: () => Promise.resolve({ items: [] }), // the engine's own data: not exposed by llm-steward
  },

  candidates: {
    list: async (slug, q) => {
      const p = await call(sdk.listCandidatesAdminV1NsSlugCandidatesGet({ path: path(slug), query: { stage: str(q, 'stage') as gen.CandidateStage | undefined, status: str(q, 'status') as gen.CandidateStatus | undefined, user_id: str(q, 'user_id'), ...paging(q) } }))
      return page(p, (c) => candidate(c))
    },
    get: async (slug, id) => withTarget(slug, await call(sdk.getCandidateAdminV1NsSlugCandidatesCandidateIdGet({ path: { ...path(slug), candidate_id: id } }))),
    decide: async (slug, id, verdict, decision) => {
      const options = { path: { ...path(slug), candidate_id: id }, body: decision }
      const d = await call(verdict === 'accept' ? sdk.acceptAdminV1NsSlugCandidatesCandidateIdAcceptPost(options) : sdk.rejectAdminV1NsSlugCandidatesCandidateIdRejectPost(options))
      return { ...candidate(d), entry: d.entry ? entry(d.entry) : null }
    },
  },

  retrieval: {
    run: async (slug, body) => {
      const started = performance.now()
      const r = await call(sdk.retrieveAdminV1NsSlugRetrievePost({ path: path(slug), body: { query: body.query, sources: body.sources, top_k: body.top_k, scope: { user_id: body.user_id } } }))
      const final = r.items.map(hit)
      return { log_id: r.retrieval_id, query: body.query, stages: { vector: null, keyword: null, rrf: null, final }, context: r.context ?? '', degraded: r.degraded, latency_ms: Math.round(performance.now() - started), gap: final.length === 0 }
    },
    logs: async (slug, q) =>
      page(await call(sdk.retrievalLogsAdminV1NsSlugRetrievalLogsGet({ path: path(slug), query: { gaps: q.gaps === true || q.gaps === 'true' ? true : undefined, ...paging(q) } })), (l) => ({
        id: l.id,
        query: l.query,
        user_id: l.user_id,
        top_score: l.top_score ?? null,
        hit_count: l.hit_count,
        latency_ms: l.latency_ms ?? null,
        degraded: l.degraded.length > 0,
        gap: l.gap,
        created_at: l.created_at,
      })),
    addToKb: async (slug, logId, draft) => {
      const collection = draft.collection_id ?? (await liveApi.kb.collections(slug)).items[0]?.id
      if (!collection) throw new ApiError(422, { title: 'No collection to add it to' })
      return written(await call(sdk.addToKbAdminV1NsSlugRetrievalLogsLogIdAddToKbPost({ path: { ...path(slug), log_id: logId }, body: { collection_id: collection, title: draft.title, content: draft.content } })))
    },
  },

  activity: async (slug, q) =>
    page(await call(sdk.activityAdminV1NsSlugActivityGet({ path: path(slug), query: { object_type: str(q, 'object_type') as gen.ObjectType | undefined, actor_type: str(q, 'actor_type') as gen.ActorType | undefined, object_id: str(q, 'object_id'), ...paging(q) } })), (c) => change(c)),

  jobs: {
    list: async (slug, q) => page(await call(sdk.jobsAdminV1NsSlugJobsGet({ path: path(slug), query: { status: str(q, 'status') as gen.JobStatus | undefined, ...paging(q) } })), job),
    retry: async (slug, id) => job(await call(sdk.retryJobAdminV1NsSlugJobsJobIdRetryPost({ path: { ...path(slug), job_id: id } }))),
    cancel: async (slug, id) => job(await call(sdk.cancelJobAdminV1NsSlugJobsJobIdCancelPost({ path: { ...path(slug), job_id: id } }))),
  },

  providers: {
    list: async (slug) => ({
      items: (await call(sdk.providersAdminV1NsSlugProvidersGet({ path: path(slug) }))).items.map((p) => ({
        name: p.name,
        kind: p.role,
        engine: p.name,
        version: '',
        capabilities: p.capabilities,
        health: p.healthy ? ('healthy' as const) : ('down' as const),
        latency_p95_ms: null,
        error_rate: null,
        queue: { pending: p.pending, failed: p.failed },
        last_reconciled_at: p.last_reconciled_at ?? null,
        reconcile_drift: null,
      })),
    }),
    syncRefs: async (slug) => ({ items: (await call(sdk.syncRefsAdminV1NsSlugSyncGet({ path: path(slug) }))).items.map(syncRef) }),
    retrySync: async (slug, refId) => syncRef(await call(sdk.retrySyncAdminV1NsSlugSyncRefIdRetryPost({ path: { ...path(slug), ref_id: refId } }))),
  },

  settings: {
    get: async (slug) => {
      const s = await call(sdk.settingsAdminV1NsSlugSettingsGet({ path: path(slug) }))
      return { yaml: s.yaml, templates: s.templates, embedding: s.embedding_model ? { model: s.embedding_model, dimensions: null, space_id: null, rebuild_progress: null } : null }
    },
    keys: async (slug) => ({ items: (await call(sdk.keysAdminV1NsSlugKeysGet({ path: path(slug) }))).items.map(apiKey) }),
    createKey: async (slug, body) => {
      const k = await call(sdk.createKeyAdminV1NsSlugKeysPost({ path: path(slug), body }))
      return { ...apiKey(k), secret: k.key }
    },
    revokeKey: async (slug, id) => apiKey(await call(sdk.revokeKeyAdminV1NsSlugKeysKeyIdDelete({ path: { ...path(slug), key_id: id } }))),
  },
}

function apiKey(k: gen.ApiKeyOut): ui.ApiKey {
  return { ...k, scopes: k.scopes as ui.ApiKey['scopes'] }
}
