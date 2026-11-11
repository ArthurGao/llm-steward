// MSW handlers: the mock /admin/v1. Each handler parses the request, calls the store and
// maps StoreError to RFC 9457 Problem Details, so pages see what the real API will send.
import { delay, HttpResponse, type HttpResponseResolver } from 'msw'
import { http } from 'msw/http'
import { oneOf } from '@/lib/search'
import { StoreError, type Actor, type Store } from './store'

// Query-string enums are validated, so an unknown value means "no filter" rather than a bad type.
const memoryStatus = oneOf(['active', 'superseded', 'deleted'] as const)
const kbStatus = oneOf(['draft', 'active', 'verified', 'deprecated', 'rejected'] as const)
const risk = oneOf(['low', 'medium', 'high'] as const)
const stage = oneOf(['awaiting_user', 'awaiting_review'] as const)
const candidateStatus = oneOf(['pending', 'accepted', 'rejected', 'expired', 'all'] as const)
const origin = oneOf(['extraction', 'promote', 'user_suggestion', 'api'] as const)
const objectType = oneOf(['memory', 'kb_entry', 'kb_document', 'kb_collection', 'candidate', 'episode', 'user'] as const)
const actorType = oneOf(['ai', 'end_user', 'admin', 'system', 'provider', 'host_app'] as const)
const changeOp = oneOf(['create', 'update', 'transition', 'delete', 'erase'] as const)
const jobStatus = oneOf(['pending', 'running', 'done', 'failed'] as const)

const BASE = '*/admin/v1'
// The demo has no sign-in: every request acts as the one admin.
const ADMIN: Actor = { type: 'admin', id: 'admin@demo' }

function problem(status: number, title: string) {
  return HttpResponse.json({ title, status }, { status, headers: { 'Content-Type': 'application/problem+json' } })
}

type Ctx = { req: Request; actor: Actor; params: Record<string, string>; url: URL }
type Handler = (ctx: Ctx) => unknown | Promise<unknown>

function route(handler: Handler): HttpResponseResolver {
  return async ({ request, params }) => {
    // A small, fixed latency so loading states are visible but tests stay fast.
    if (import.meta.env.MODE !== 'test') await delay(120)
    const actor = ADMIN
    try {
      const body = await handler({ req: request, actor, params: params as Record<string, string>, url: new URL(request.url) })
      return body === undefined ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(body as never)
    } catch (e) {
      if (e instanceof StoreError) return problem(e.status, e.message)
      throw e
    }
  }
}

function query(url: URL): Record<string, string> {
  return Object.fromEntries(url.searchParams.entries())
}

function bool(v: string | undefined): boolean | undefined {
  return v === undefined ? undefined : v === 'true'
}

async function body<T>(req: Request): Promise<T> {
  return (await req.json().catch(() => ({}))) as T
}

export function createHandlers(store: Store) {
  const ns = (p: Record<string, string>) => p.ns ?? ''
  return [
    http.get(`${BASE}/namespaces`, route(() => ({ items: store.listNamespaces() }))),
    http.get(`${BASE}/ns/:ns/stats`, route(({ params }) => store.stats(ns(params)))),

    http.get(`${BASE}/ns/:ns/memories`, route(({ params, url }) => {
      const q = query(url)
      return store.listMemories(ns(params), { ...q, pinned: bool(q.pinned), include_superseded: bool(q.include_superseded), limit: q.limit ? Number(q.limit) : undefined, status: memoryStatus(q.status) })
    })),
    http.delete(`${BASE}/ns/:ns/memories`, route(({ params, url, actor }) => {
      const userId = url.searchParams.get('user_id')
      if (!userId) throw new StoreError(422, 'user_id is required')
      return store.eraseUser(ns(params), userId, actor)
    })),
    http.get(`${BASE}/ns/:ns/memories/:id`, route(({ params }) => store.getMemory(ns(params), params.id ?? ''))),
    http.get(`${BASE}/ns/:ns/memories/:id/history`, route(({ params }) => store.memoryHistory(ns(params), params.id ?? ''))),
    http.patch(`${BASE}/ns/:ns/memories/:id`, route(async ({ params, req, actor }) => store.patchMemory(ns(params), params.id ?? '', await body(req), actor))),
    http.delete(`${BASE}/ns/:ns/memories/:id`, route(({ params, actor }) => store.deleteMemory(ns(params), params.id ?? '', actor))),
    http.post(`${BASE}/ns/:ns/memories/:id/promote`, route(({ params, actor }) => store.promoteMemory(ns(params), params.id ?? '', actor))),

    http.get(`${BASE}/ns/:ns/users`, route(({ params, url }) => store.listUsers(ns(params), query(url)))),
    http.get(`${BASE}/ns/:ns/users/:id`, route(({ params }) => store.getUser(ns(params), params.id ?? ''))),

    http.get(`${BASE}/ns/:ns/kb/collections`, route(({ params }) => ({ items: store.listCollections(ns(params)) }))),
    http.get(`${BASE}/ns/:ns/kb/entries`, route(({ params, url }) => {
      const q = query(url)
      return store.listEntries(ns(params), { ...q, status: kbStatus(q.status), risk_level: risk(q.risk_level) })
    })),
    http.get(`${BASE}/ns/:ns/kb/entries/:id`, route(({ params }) => store.getEntry(ns(params), params.id ?? ''))),
    http.get(`${BASE}/ns/:ns/kb/entries/:id/history`, route(({ params }) => store.entryHistory(ns(params), params.id ?? ''))),
    http.patch(`${BASE}/ns/:ns/kb/entries/:id`, route(async ({ params, req, actor }) => store.patchEntry(ns(params), params.id ?? '', await body(req), actor))),
    http.post(`${BASE}/ns/:ns/kb/entries/:id/deprecate`, route(async ({ params, req, actor }) =>
      store.deprecateEntry(ns(params), params.id ?? '', (await body<{ reason?: string }>(req)).reason ?? '', actor))),
    http.get(`${BASE}/ns/:ns/kb/documents`, route(({ params, url }) => ({ items: store.listDocuments(ns(params), url.searchParams.get('collection_id') ?? undefined) }))),
    http.get(`${BASE}/ns/:ns/kb/documents/:id/chunks`, route(({ params }) => ({ items: store.listChunks(ns(params), params.id ?? '') }))),

    http.get(`${BASE}/ns/:ns/candidates`, route(({ params, url }) => {
      const q = query(url)
      return store.listCandidates(ns(params), { ...q, stage: stage(q.stage), status: candidateStatus(q.status), risk_level: risk(q.risk_level), origin: origin(q.origin) })
    })),
    http.get(`${BASE}/ns/:ns/candidates/:id`, route(({ params }) => store.getCandidate(ns(params), params.id ?? ''))),
    http.post(`${BASE}/ns/:ns/candidates/:id/:verdict`, route(async ({ params, req, actor }) => {
      const verdict = params.verdict
      if (verdict !== 'accept' && verdict !== 'reject') throw new StoreError(404, 'Unknown decision')
      return store.decide(ns(params), params.id ?? '', verdict, await body(req), actor)
    })),

    http.post(`${BASE}/ns/:ns/retrieve`, route(async ({ params, req }) => {
      const b = await body<{ query?: string; user_id?: string; sources?: ('memory' | 'kb')[]; top_k?: number }>(req)
      if (!b.query?.trim()) throw new StoreError(422, 'Type a question to retrieve')
      return store.retrieve(ns(params), { query: b.query, user_id: b.user_id || undefined, sources: b.sources?.length ? b.sources : ['memory', 'kb'], top_k: b.top_k })
    })),
    http.get(`${BASE}/ns/:ns/retrieval-logs`, route(({ params, url }) => store.listRetrievalLogs(ns(params), { ...query(url), gap: bool(url.searchParams.get('gap') ?? undefined) }))),
    http.post(`${BASE}/ns/:ns/retrieval-logs/:id/add-to-kb`, route(async ({ params, req, actor }) =>
      store.addGapToKb(ns(params), params.id ?? '', await body<{ title: string; content: string; collection_id?: string }>(req), actor))),

    http.get(`${BASE}/ns/:ns/activity`, route(({ params, url }) => {
      const q = query(url)
      return store.activity(ns(params), { ...q, object_type: objectType(q.object_type), actor_type: actorType(q.actor_type), op: changeOp(q.op) })
    })),

    http.get(`${BASE}/ns/:ns/jobs`, route(({ params, url }) => store.listJobs(ns(params), { ...query(url), status: jobStatus(url.searchParams.get('status')) }))),
    http.post(`${BASE}/ns/:ns/jobs/:id/retry`, route(({ params }) => store.retryJob(ns(params), params.id ?? ''))),
    http.post(`${BASE}/ns/:ns/jobs/:id/cancel`, route(({ params }) => store.cancelJob(ns(params), params.id ?? ''))),

    http.get(`${BASE}/ns/:ns/providers`, route(({ params }) => ({ items: store.providers(ns(params)) }))),
    http.get(`${BASE}/ns/:ns/sync`, route(({ params }) => ({ items: store.listSyncRefs(ns(params)) }))),
    http.post(`${BASE}/ns/:ns/sync/:id/retry`, route(({ params }) => store.retrySync(ns(params), params.id ?? ''))),

    http.get(`${BASE}/ns/:ns/settings`, route(({ params }) => store.settings(ns(params)))),
    http.get(`${BASE}/ns/:ns/keys`, route(({ params }) => ({ items: store.listKeys(ns(params)) }))),
    http.post(`${BASE}/ns/:ns/keys`, route(async ({ params, req }) => store.createKey(ns(params), await body(req)))),
    http.delete(`${BASE}/ns/:ns/keys/:id`, route(({ params }) => store.revokeKey(ns(params), params.id ?? ''))),
  ]
}
