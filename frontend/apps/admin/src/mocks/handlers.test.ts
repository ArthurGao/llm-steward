import { server } from '@/test/render'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/client'
import { clearSession, setSession } from '@/auth/session'
import { createHandlers } from './handlers'
import { createStore, FIX } from './store'

beforeEach(() => server.resetHandlers(...createHandlers(createStore())))
afterEach(() => clearSession())

const NS = 'acme-support'

async function failure(p: Promise<unknown>): Promise<ApiError> {
  const e = await p.then(() => null, (err: unknown) => err)
  expect(e).toBeInstanceOf(ApiError)
  return e as ApiError
}

test('a viewer forcing an approval gets 403 Problem Details', async () => {
  setSession({ email: 'viewer@demo', role: 'viewer' })
  const e = await failure(api.candidates.decide(NS, FIX.refundCandidate, 'accept', {}))
  expect(e.status).toBe(403)
  expect(e.problem.title).toBe('The viewer role cannot do this')
})

test('a reviewer approval returns the verified entry', async () => {
  setSession({ email: 'reviewer@demo', role: 'reviewer' })
  const r = await api.candidates.decide(NS, FIX.refundCandidate, 'accept', {})
  expect(r.entry).toMatchObject({ key: 'KB-0142', version: 4, status: 'verified', verified_by: 'reviewer@demo' })
})

test('memory lists paginate 25 at a time', async () => {
  setSession({ email: 'viewer@demo', role: 'viewer' })
  const first = await api.memories.list(NS, {})
  expect(first.items).toHaveLength(25)
  expect(first.total).toBe(38)
  expect(first.next_cursor).toBe('25')
  const second = await api.memories.list(NS, { cursor: first.next_cursor })
  expect(second.items).toHaveLength(13)
  expect(second.next_cursor).toBeNull()
})

test('an unknown id is 404', async () => {
  setSession({ email: 'viewer@demo', role: 'viewer' })
  const e = await failure(api.candidates.get(NS, 'cand_nope'))
  expect(e.status).toBe(404)
})

test('requests without a session are 401', async () => {
  const e = await failure(api.namespaces())
  expect(e.status).toBe(401)
})

test('demo login accepts the demo password only', async () => {
  await expect(api.login('reviewer@demo', 'demo', 'reviewer')).resolves.toEqual({ email: 'reviewer@demo', role: 'reviewer' })
  const e = await failure(api.login('reviewer@demo', 'wrong', 'reviewer'))
  expect(e.status).toBe(401)
})
