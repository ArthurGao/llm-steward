import { server } from '@/test/render'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/client'
import { createHandlers } from './handlers'
import { createStore, FIX } from './store'

beforeEach(() => server.resetHandlers(...createHandlers(createStore())))

const NS = 'acme-support'

async function failure(p: Promise<unknown>): Promise<ApiError> {
  const e = await p.then(() => null, (err: unknown) => err)
  expect(e).toBeInstanceOf(ApiError)
  return e as ApiError
}


test('a reviewer approval returns the verified entry', async () => {
  const r = await api.candidates.decide(NS, FIX.refundCandidate, 'accept', {})
  expect(r.entry).toMatchObject({ key: 'KB-0142', version: 4, status: 'verified', verified_by: 'admin@demo' })
})

test('memory lists paginate 25 at a time', async () => {
  const first = await api.memories.list(NS, {})
  expect(first.items).toHaveLength(25)
  expect(first.total).toBe(38)
  expect(first.next_cursor).toBe('25')
  const second = await api.memories.list(NS, { cursor: first.next_cursor })
  expect(second.items).toHaveLength(13)
  expect(second.next_cursor).toBeNull()
})

test('an unknown id is 404', async () => {
  const e = await failure(api.candidates.get(NS, 'cand_nope'))
  expect(e.status).toBe(404)
})


