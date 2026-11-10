import { createStore, StoreError, FIX, NOW, type Actor } from './index'

const NS = 'acme-support'
const reviewer: Actor = { type: 'admin', id: 'reviewer@demo', role: 'reviewer' }
const owner: Actor = { type: 'admin', id: 'owner@demo', role: 'owner' }
const viewer: Actor = { type: 'admin', id: 'viewer@demo', role: 'viewer' }

function expectStoreError(fn: () => unknown, status: number) {
  try {
    fn()
  } catch (e) {
    expect(e).toBeInstanceOf(StoreError)
    expect((e as StoreError).status).toBe(status)
    return
  }
  throw new Error(`expected StoreError ${status}`)
}

test('approving the high-risk refund candidate makes KB-0142 v4 verified and queues a sync', () => {
  const s = createStore()
  const before = s.getEntry(NS, FIX.refundEntry)
  expect(before.version).toBe(3)

  const result = s.decide(NS, FIX.refundCandidate, 'accept', {}, reviewer)

  expect(result.status).toBe('accepted')
  expect(result.entry?.id).toBe(FIX.refundEntry)
  const after = s.getEntry(NS, FIX.refundEntry)
  expect(after.version).toBe(4)
  expect(after.status).toBe('verified')
  expect(after.verified_by).toBe('reviewer@demo')
  expect(after.content).toBe('Refunds over $500 require approval from a second support lead before they are issued.')
  expect(after.sync_status).toBe('pending')
  const rows = s.listChanges(NS, { object_id: FIX.refundEntry })
  expect(rows[0]).toMatchObject({ op: 'transition', actor_id: 'reviewer@demo', before: { version: 3 }, after: { version: 4, status: 'verified' } })
})

test('end-user confirmation never yields verified', () => {
  const s = createStore()
  const result = s.confirmAsEndUser(NS, FIX.hoursCandidate, 'u_31c0')
  expect(result.entry?.status).toBe('active')
  expect(result.entry?.verified_by).toBeNull()
})

test('rejecting without a note is refused', () => {
  const s = createStore()
  expectStoreError(() => s.decide(NS, FIX.refundCandidate, 'reject', { note: '  ' }, reviewer), 422)
  expect(s.getCandidate(NS, FIX.refundCandidate).status).toBe('pending')
})

test('deciding an already decided candidate is a conflict and creates nothing', () => {
  const s = createStore()
  s.decide(NS, FIX.refundCandidate, 'accept', {}, reviewer)
  const entries = s.listEntries(NS, {}).total
  expectStoreError(() => s.decide(NS, FIX.refundCandidate, 'accept', {}, reviewer), 409)
  expect(s.listEntries(NS, {}).total).toBe(entries)
  expect(s.getEntry(NS, FIX.refundEntry).version).toBe(4)
})

test('a viewer cannot decide', () => {
  const s = createStore()
  expectStoreError(() => s.decide(NS, FIX.refundCandidate, 'accept', {}, viewer), 403)
})

test('editing a verified entry yields a review candidate, not an edit', () => {
  const s = createStore()
  const written = s.patchEntry(NS, FIX.refundEntry, { content: 'Refunds over $250 need approval.' }, reviewer)
  expect(written.entry).toBeNull()
  expect(written.candidate).toMatchObject({ stage: 'awaiting_review', op: 'UPDATE', target_id: FIX.refundEntry, status: 'pending' })
  expect(s.getEntry(NS, FIX.refundEntry).content).toBe('Refunds over $1,000 require approval from a team lead.')
})

test('deprecating keeps the row, closes its validity and removes it from the provider', () => {
  const s = createStore()
  const e = s.deprecateEntry(NS, FIX.refundEntry, 'Replaced by the 2026 handbook', reviewer)
  expect(e.status).toBe('deprecated')
  expect(e.valid_to).toBe(NOW)
  expect(e.sync_status).toBe('deleting')
  expect(s.getEntry(NS, FIX.refundEntry).id).toBe(FIX.refundEntry)
})

test('erasing a user deletes only their memories and episodes and redacts their history', () => {
  const s = createStore()
  const mine = s.listMemories(NS, { user_id: 'u_8f2a', include_superseded: true }).total
  const others = s.listMemories(NS, { include_superseded: true }).total - mine
  const changed = s.listChanges(NS, { object_type: 'memory' }).filter((c) => c.after?.user_id === 'u_8f2a').map((c) => c.id)
  expect(mine).toBeGreaterThan(0)
  expect(changed.length).toBeGreaterThan(0)

  const res = s.eraseUser(NS, 'u_8f2a', owner)

  expect(res.deleted_memories).toBe(mine)
  expect(res.deleted_episodes).toBeGreaterThan(0)
  expect(s.listMemories(NS, { user_id: 'u_8f2a', include_superseded: true }).total).toBe(0)
  expect(s.listMemories(NS, { include_superseded: true }).total).toBe(others)
  const all = s.listChanges(NS, {})
  for (const id of changed) {
    const row = all.find((c) => c.id === id)
    expect(row?.before).toBeNull()
    expect(row?.after).toBeNull()
  }
  expect(all[0]).toMatchObject({ op: 'erase', object_type: 'user', object_id: 'u_8f2a', actor_id: 'owner@demo' })
})

test('only an owner can erase a user', () => {
  const s = createStore()
  expectStoreError(() => s.eraseUser(NS, 'u_8f2a', reviewer), 403)
})

test('two fresh stores are identical', () => {
  const a = createStore()
  const b = createStore()
  expect(a.listCandidates(NS, {}).items.map((c) => c.id)).toEqual(b.listCandidates(NS, {}).items.map((c) => c.id))
  expect(a.listMemories(NS, {}).total).toBe(b.listMemories(NS, {}).total)
  expect(a.listCandidates(NS, {}).total).toBe(7)
})

test('promoting a memory creates a KB candidate awaiting review', () => {
  const s = createStore()
  const c = s.promoteMemory(NS, FIX.promotableMemory, owner)
  expect(c).toMatchObject({ origin: 'promote', target_type: 'kb_entry', stage: 'awaiting_review', status: 'pending' })
  expect(s.listCandidates(NS, {}).total).toBe(8)
})

test('retrying a failed job puts it back to pending; a failed sync retries too', () => {
  const s = createStore()
  const failed = s.listJobs(NS, { status: 'failed' }).items[0]!
  expect(s.retryJob(NS, failed.id, owner).status).toBe('pending')
  const ref = s.listSyncRefs(NS).find((r) => r.sync_status === 'failed')!
  expect(s.retrySync(NS, ref.id, owner).sync_status).toBe('pending')
})

test('a created API key shows its secret once', () => {
  const s = createStore()
  const k = s.createKey(NS, { name: 'ci', scopes: ['read'] }, owner)
  expect(k.secret).toMatch(/^stw_[a-z0-9]{6}_[A-Za-z0-9]{24}$/)
  expect(s.listKeys(NS).find((x) => x.id === k.id)).not.toHaveProperty('secret')
})

test('a reviewer cannot change memories; that is an owner action', () => {
  const s = createStore()
  expectStoreError(() => s.patchMemory(NS, FIX.promotableMemory, { pinned: true }, reviewer), 403)
})

test('deleting a superseded memory records its real prior status', () => {
  const s = createStore()
  s.deleteMemory(NS, 'mem_0003', owner)
  expect(s.listChanges(NS, { object_id: 'mem_0003' })[0]).toMatchObject({ before: { status: 'superseded' }, after: { status: 'deleted' } })
})
