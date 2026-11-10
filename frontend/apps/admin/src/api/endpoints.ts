// One function per /admin/v1 route the Admin uses. The MSW handlers in
// src/mocks/handlers.ts implement the same table.
import { request } from './client'
import type {
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
  Session,
  Stats,
  SyncRef,
  UserDetail,
  UserSummary,
  Written,
} from './types'

export type Query = Record<string, string | number | boolean | null | undefined>

export function qs(q: Query = {}): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

const json = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })
const ns = (slug: string) => `/ns/${encodeURIComponent(slug)}`

export const api = {
  login: (email: string, password: string, role: Role) => request<Session>('/auth/login', json({ email, password, role })),
  namespaces: () => request<{ items: Namespace[] }>('/namespaces'),
  stats: (slug: string) => request<Stats>(`${ns(slug)}/stats`),

  memories: {
    list: (slug: string, q: Query) => request<Page<Memory>>(`${ns(slug)}/memories${qs(q)}`),
    get: (slug: string, id: string) => request<Memory>(`${ns(slug)}/memories/${id}`),
    history: (slug: string, id: string) => request<MemoryHistory>(`${ns(slug)}/memories/${id}/history`),
    patch: (slug: string, id: string, patch: MemoryPatch) => request<Memory>(`${ns(slug)}/memories/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    remove: (slug: string, id: string) => request<Memory>(`${ns(slug)}/memories/${id}`, { method: 'DELETE' }),
    promote: (slug: string, id: string) => request<Candidate>(`${ns(slug)}/memories/${id}/promote`, json({})),
    eraseUser: (slug: string, userId: string) => request<Erasure>(`${ns(slug)}/memories${qs({ user_id: userId })}`, { method: 'DELETE' }),
  },

  users: {
    list: (slug: string, q: Query) => request<Page<UserSummary>>(`${ns(slug)}/users${qs(q)}`),
    get: (slug: string, userId: string) => request<UserDetail>(`${ns(slug)}/users/${encodeURIComponent(userId)}`),
  },

  kb: {
    collections: (slug: string) => request<{ items: Collection[] }>(`${ns(slug)}/kb/collections`),
    entries: (slug: string, q: Query) => request<Page<KbEntry>>(`${ns(slug)}/kb/entries${qs(q)}`),
    entry: (slug: string, id: string) => request<KbEntry>(`${ns(slug)}/kb/entries/${id}`),
    history: (slug: string, id: string) => request<EntryHistory>(`${ns(slug)}/kb/entries/${id}/history`),
    patch: (slug: string, id: string, patch: EntryPatch) => request<Written>(`${ns(slug)}/kb/entries/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    deprecate: (slug: string, id: string, reason: string) => request<KbEntry>(`${ns(slug)}/kb/entries/${id}/deprecate`, json({ reason })),
    documents: (slug: string, collectionId?: string) => request<{ items: KbDocument[] }>(`${ns(slug)}/kb/documents${qs({ collection_id: collectionId })}`),
    chunks: (slug: string, docId: string) => request<{ items: Chunk[] }>(`${ns(slug)}/kb/documents/${docId}/chunks`),
  },

  candidates: {
    list: (slug: string, q: Query) => request<Page<Candidate>>(`${ns(slug)}/candidates${qs(q)}`),
    get: (slug: string, id: string) => request<Candidate>(`${ns(slug)}/candidates/${id}`),
    decide: (slug: string, id: string, verdict: 'accept' | 'reject', decision: Decision) =>
      request<DecisionResult>(`${ns(slug)}/candidates/${id}/${verdict}`, json(decision)),
  },

  retrieval: {
    run: (slug: string, body: RetrievalRequest) => request<RetrievalResult>(`${ns(slug)}/retrieve`, json(body)),
    logs: (slug: string, q: Query) => request<Page<RetrievalLog>>(`${ns(slug)}/retrieval-logs${qs(q)}`),
    addToKb: (slug: string, logId: string, draft: { title: string; content: string; collection_id?: string }) =>
      request<Written>(`${ns(slug)}/retrieval-logs/${logId}/add-to-kb`, json(draft)),
  },

  activity: (slug: string, q: Query) => request<Page<Change>>(`${ns(slug)}/activity${qs(q)}`),

  jobs: {
    list: (slug: string, q: Query) => request<Page<Job>>(`${ns(slug)}/jobs${qs(q)}`),
    retry: (slug: string, id: string) => request<Job>(`${ns(slug)}/jobs/${id}/retry`, json({})),
    cancel: (slug: string, id: string) => request<Job>(`${ns(slug)}/jobs/${id}/cancel`, json({})),
  },

  providers: {
    list: (slug: string) => request<{ items: ProviderStatus[] }>(`${ns(slug)}/providers`),
    syncRefs: (slug: string) => request<{ items: SyncRef[] }>(`${ns(slug)}/sync`),
    retrySync: (slug: string, refId: string) => request<SyncRef>(`${ns(slug)}/sync/${refId}/retry`, json({})),
  },

  settings: {
    get: (slug: string) => request<NamespaceSettings>(`${ns(slug)}/settings`),
    keys: (slug: string) => request<{ items: ApiKey[] }>(`${ns(slug)}/keys`),
    createKey: (slug: string, body: { name: string; scopes: ApiKey['scopes'] }) => request<ApiKeyCreated>(`${ns(slug)}/keys`, json(body)),
    revokeKey: (slug: string, id: string) => request<ApiKey>(`${ns(slug)}/keys/${id}`, { method: 'DELETE' }),
  },
}
