// The live API against a backend stand-in at the network boundary (MSW): the generated client's
// requests (base URL, admin token) and the adapters from the backend's shapes to the view types.
import { http, HttpResponse } from 'msw/http'
import { connect, disconnect } from '@/auth/session'
import { server } from '@/test/render'
import { ApiError } from './client'
import { liveApi } from './live'

const B = 'http://steward.test:8600/admin/v1/ns/acme'
const ID = '0190f3a1-0000-7000-8000-00000000c0de'
const TARGET = '0190f3a1-0000-7000-8000-0000000e0001'
const T = '2026-11-10T09:00:00Z'

beforeEach(() => connect('http://steward.test:8600/', 'admin-token-0123456789-0123456789'))
afterEach(() => {
  disconnect()
  server.resetHandlers()
})

const entryOut = {
  id: TARGET, collection_id: 'col', title: 'Refund threshold', content: 'Refunds over $500 need approval.', status: 'verified',
  risk_level: 'high', origin: 'manual', author_type: 'human', version: 3, created_by: null, verified_by: 'admin', verified_at: T,
  valid_from: T, valid_to: null, tags: [], source_refs: [], created_at: T, updated_at: T,
  superseded_by: null, hit_count: 7, helpful_count: 3, unhelpful_count: 1,
}

const candidateOut = {
  id: ID, target_type: 'kb_entry', op: 'UPDATE', target_id: TARGET, origin: 'api', stage: 'awaiting_review', status: 'pending',
  risk_level: 'high', reason: 'keyword: refund; pii: phone; pii: email', requested_for_user: null, decided_by: null, decided_at: null,
  decision_note: null, expires_at: null, created_at: T,
  draft: { title: 'Refund threshold', content: 'Refunds over [PHONE] need approval.', collection_id: 'col', source_refs: [{ type: 'episode', id: 'ep1' }] },
}

test('requests go to the connected backend with the admin token', async () => {
  let seen: { url: string; auth: string | null } | null = null
  server.use(
    http.get('http://steward.test:8600/admin/v1/namespaces', ({ request }) => {
      seen = { url: request.url, auth: request.headers.get('Authorization') }
      return HttpResponse.json({ items: [{ id: 'n1', slug: 'acme', name: 'Acme', memory_provider: 'native', kb_provider: 'native' }] })
    }),
  )
  await expect(liveApi.namespaces()).resolves.toEqual({ items: [{ id: 'n1', slug: 'acme', name: 'Acme', memory_provider: 'native', kb_provider: 'native' }] })
  expect(seen).toEqual({ url: 'http://steward.test:8600/admin/v1/namespaces', auth: 'Bearer admin-token-0123456789-0123456789' })
})

test('without a connection nothing is sent', async () => {
  disconnect()
  const e = await liveApi.namespaces().then(() => null, (err: unknown) => err)
  expect(e).toBeInstanceOf(ApiError)
  expect((e as ApiError).status).toBe(401)
  expect((e as ApiError).problem.title).toBe('Not connected to a backend')
})

test('a Problem Details error becomes an ApiError with its status and title', async () => {
  server.use(http.post(`${B}/candidates/${ID}/reject`, () => HttpResponse.json({ title: 'A note is required to reject', status: 422 }, { status: 422 })))
  const e = await liveApi.candidates.decide('acme', ID, 'reject', {}).then(() => null, (err: unknown) => err)
  expect(e).toBeInstanceOf(ApiError)
  expect((e as ApiError).status).toBe(422)
  expect((e as ApiError).problem.title).toBe('A note is required to reject')
})

test('a candidate reads its screening from the reason and, for an UPDATE, the entry it would obsolete', async () => {
  server.use(http.get(`${B}/candidates/${ID}`, () => HttpResponse.json({ ...candidateOut, target: entryOut })))
  const c = await liveApi.candidates.get('acme', ID)
  expect(c.screening).toEqual({ reasons: ['keyword: refund', 'pii: phone', 'pii: email'], pii: ['phone', 'email'] })
  expect(c.draft).toEqual({ title: 'Refund threshold', content: 'Refunds over [PHONE] need approval.' })
  expect(c.collection_id).toBe('col')
  expect(c.episode_id).toBe('ep1')
  expect(c.key).toBe('C-00c0de')
  expect(c.similar).toEqual({ entry_id: TARGET, key: 'KB-0e0001', title: 'Refund threshold', content: 'Refunds over $500 need approval.', version: 3, status: 'verified', similarity: null })
})

test('an entry carries its hit count and helpful rate; per-object sync state is unknown here', async () => {
  server.use(http.get(`${B}/kb/entries/${TARGET}`, () => HttpResponse.json(entryOut)))
  const e = await liveApi.kb.entry('acme', TARGET)
  expect(e).toMatchObject({ key: 'KB-0e0001', hits: 7, helpful_rate: 0.75, sync_status: null, review_due_at: null })
})

test('a list from the backend has a cursor but no total', async () => {
  server.use(http.get(`${B}/kb/entries`, ({ request }) => {
    expect(new URL(request.url).searchParams.get('status')).toBe('verified')
    return HttpResponse.json({ items: [{ ...entryOut, helpful_count: 0, unhelpful_count: 0 }], next_cursor: TARGET })
  }))
  const p = await liveApi.kb.entries('acme', { status: 'verified', cursor: '' })
  expect(p.total).toBeNull()
  expect(p.next_cursor).toBe(TARGET)
  expect(p.items[0]?.helpful_rate).toBeNull() // nobody rated it: unknown, not 0%
})

test('providers report health from the probe and leave unmeasured figures unknown', async () => {
  server.use(http.get(`${B}/providers`, () => HttpResponse.json({ items: [
    { name: 'native', role: 'kb', capabilities: ['hybrid'], healthy: false, detail: 'timeout', pending: 2, failed: 1, last_reconciled_at: null },
  ] })))
  const [p] = (await liveApi.providers.list('acme')).items
  expect(p).toMatchObject({ kind: 'kb', health: 'down', queue: { pending: 2, failed: 1 }, latency_p95_ms: null, error_rate: null, reconcile_drift: null })
})

test('the Playground shows only the final ranking the backend returns', async () => {
  let sent: unknown = null
  server.use(http.post(`${B}/retrieve`, async ({ request }) => {
    sent = await request.json()
    return HttpResponse.json({ retrieval_id: 'rl1', context: 'ctx', degraded: ['rerank'], items: [
      { source: 'kb', kind: 'entry', id: TARGET, title: 'Refund threshold', content: 'Refunds over $500 need approval.', score: 0.81, verified: true, pinned: false, status: 'verified' },
    ] })
  }))
  const r = await liveApi.retrieval.run('acme', { query: 'refund', sources: ['kb'], top_k: 5, user_id: 'u1' })
  expect(sent).toEqual({ query: 'refund', sources: ['kb'], top_k: 5, scope: { user_id: 'u1' } })
  expect(r.stages).toEqual({ vector: null, keyword: null, rrf: null, final: [{ source: 'kb', id: TARGET, key: 'KB-0e0001', text: 'Refund threshold: Refunds over $500 need approval.', score: 0.81, trust: null }] })
  expect(r).toMatchObject({ log_id: 'rl1', degraded: ['rerank'], gap: false })
})

test('an added answer without a collection leaves the choice to the backend', async () => {
  let sent: unknown = null
  server.use(
    http.post(`${B}/retrieval-logs/rl1/add-to-kb`, async ({ request }) => {
      sent = await request.json()
      return HttpResponse.json({ entry: null, candidate: { ...candidateOut, op: 'ADD', target_id: null } })
    }),
  )
  const w = await liveApi.retrieval.addToKb('acme', 'rl1', { title: 'Retention', content: 'Kept 30 days.' })
  expect(sent).toEqual({ title: 'Retention', content: 'Kept 30 days.' })
  expect(w.candidate?.key).toBe('C-00c0de')
})
