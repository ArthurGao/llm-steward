import { server } from '@/test/render'
import { http, HttpResponse } from 'msw/http'
import { ApiError, request } from './client'

test('a Problem Details response becomes an ApiError with status and title', async () => {
  server.use(
    http.post('*/admin/v1/ns/acme-support/candidates/c1/reject', () =>
      HttpResponse.json(
        { title: 'A reason is required', status: 422 },
        { status: 422, headers: { 'Content-Type': 'application/problem+json' } },
      ),
    ),
  )
  const err = (await request('/ns/acme-support/candidates/c1/reject', { method: 'POST', body: '{}' }).catch((e: unknown) => e)) as ApiError
  expect(err).toBeInstanceOf(ApiError)
  expect(err.status).toBe(422)
  expect(err.problem.title).toBe('A reason is required')
})

