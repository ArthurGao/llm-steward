import { createStore, FIX } from './index'

const NS = 'acme-support'

test('a refund question ranks the refund policy first through every stage', () => {
  const s = createStore()
  const r = s.retrieve(NS, { query: 'refund approval', sources: ['memory', 'kb'] })
  expect(r.stages.final[0]?.id).toBe(FIX.refundEntry)
  expect(r.stages.vector!.length).toBeGreaterThan(0)
  expect(r.stages.keyword!.length).toBeGreaterThan(0)
  expect(r.stages.rrf!.length).toBeGreaterThan(0)
  expect(r.gap).toBe(false)
  expect(r.context).toContain('Refunds over $1,000')
})

test('a question the KB cannot answer is logged as a gap', () => {
  const s = createStore()
  const r = s.retrieve(NS, { query: 'data retention period', sources: ['memory', 'kb'] })
  expect(r.gap).toBe(true)
  expect(r.stages.final[0]?.score ?? 0).toBeLessThan(0.35)
  expect(s.listRetrievalLogs(NS, {}).items[0]).toMatchObject({ id: r.log_id, query: 'data retention period', gap: true })
})

test('deprecated knowledge is never retrieved', () => {
  const s = createStore()
  s.deprecateEntry(NS, FIX.refundEntry, 'gone', { type: 'admin', id: 'r@demo' })
  const r = s.retrieve(NS, { query: 'refund approval', sources: ['kb'] })
  expect(r.stages.final.map((h) => h.id)).not.toContain(FIX.refundEntry)
})

test('adding a gap to the KB creates a candidate awaiting review', () => {
  const s = createStore()
  const r = s.retrieve(NS, { query: 'data retention period', sources: ['kb'] })
  const w = s.addGapToKb(NS, r.log_id, { title: 'Data retention', content: 'Conversation data is kept for 30 days.' }, { type: 'admin', id: 'r@demo' })
  expect(w.entry).toBeNull()
  expect(w.candidate).toMatchObject({ origin: 'api', stage: 'awaiting_review', draft: { title: 'Data retention' } })
})

test('approving a new entry gives it the next free key', () => {
  const s = createStore()
  const r = s.retrieve(NS, { query: 'data retention period', sources: ['kb'] })
  const reviewer = { type: 'admin' as const, id: 'r@demo' }
  const w = s.addGapToKb(NS, r.log_id, { title: 'Data retention', content: 'Kept for 30 days.' }, reviewer)
  expect(s.decide(NS, w.candidate!.id, 'accept', {}, reviewer).entry?.key).toBe('KB-0405')
})
