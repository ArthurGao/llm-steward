// The components against a stand-in for the API (msw, the network boundary): the requests they make
// (the user token, never an API key; the decision's body) and how they show the answers.
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryPanel, SuggestionCard, type Suggestion } from './index'
import { server } from './test/setup'

const BASE = 'http://steward.test'
const connection = { baseUrl: `${BASE}/`, token: 'user-token-abc' }
const T = '2026-11-11T09:00:00Z'

function card(): Suggestion {
  return {
    id: 'c1',
    target_type: 'kb_entry',
    op: 'ADD',
    target_id: null,
    origin: 'extraction',
    stage: 'awaiting_user',
    status: 'pending',
    risk_level: 'medium',
    reason: null,
    requested_for_user: 'u1',
    decided_by: null,
    decided_at: null,
    decision_note: null,
    expires_at: null,
    created_at: T,
    draft: { title: 'Refund time', content: 'Refunds take 5 business days.' },
  }
}

/** Records the decision request; answers with the decided candidate. */
function decision(verdict: 'accept' | 'reject') {
  const seen: { body?: unknown; auth?: string | null } = {}
  server.use(
    http.post(`${BASE}/v1/candidates/c1/${verdict}`, async ({ request }) => {
      seen.body = await request.json()
      seen.auth = request.headers.get('Authorization')
      return HttpResponse.json({ ...card(), status: verdict === 'accept' ? 'accepted' : 'rejected', entry: null })
    }),
  )
  return seen
}

test('saving for the team sends the scope with the user token', async () => {
  const seen = decision('accept')
  const decided = vi.fn()
  render(<SuggestionCard connection={connection} suggestion={card()} onDecided={decided} />)
  expect(screen.getByText('Refunds take 5 business days.')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Shared with your team for review.')
  expect(seen).toEqual({ body: { scope: 'team' }, auth: 'Bearer user-token-abc' })
  expect(decided).toHaveBeenCalledWith(expect.objectContaining({ id: 'c1', status: 'accepted' }))
})

test('an edited card is kept just for the user, with the edit', async () => {
  const seen = decision('accept')
  render(<SuggestionCard connection={connection} suggestion={card()} />)
  await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
  const text = screen.getByLabelText('Text')
  await userEvent.clear(text)
  await userEvent.type(text, 'Refunds take a week.')
  expect(screen.getByRole('radio', { name: /For my team/ })).toBeDisabled() // the team reads what was asked
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Saved for you.')
  expect(seen.body).toEqual({ scope: 'personal', draft: { title: 'Refund time', content: 'Refunds take a week.' } })
})

test('no thanks, and stop suggesting, rejects with stop_suggestions', async () => {
  const seen = decision('reject')
  render(<SuggestionCard connection={connection} suggestion={card()} />)
  await userEvent.click(screen.getByRole('button', { name: 'No thanks' }))
  await userEvent.click(screen.getByRole('checkbox', { name: 'Stop suggesting things to me' }))
  await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
  expect(await screen.findByRole('status')).toHaveTextContent('no more suggestions')
  expect(seen.body).toEqual({ stop_suggestions: true })
})

test('a plain no thanks only dismisses this card', async () => {
  const seen = decision('reject')
  render(<SuggestionCard connection={connection} suggestion={card()} />)
  await userEvent.click(screen.getByRole('button', { name: 'No thanks' }))
  await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Dismissed.')
  expect(seen.body).toEqual({ stop_suggestions: false })
})

test('a refused decision shows the problem and lets the user try again', async () => {
  server.use(
    http.post(`${BASE}/v1/candidates/c1/accept`, () =>
      HttpResponse.json({ title: 'This card has expired', status: 409 }, { status: 409 }),
    ),
  )
  render(<SuggestionCard connection={connection} suggestion={card()} />)
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('This card has expired')
  expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
})

function memories(pages: Record<string, { items: object[]; next_cursor: string | null }>) {
  const asked: (string | null)[] = []
  server.use(
    http.get(`${BASE}/v1/memories`, ({ request }) => {
      expect(request.headers.get('Authorization')).toBe('Bearer user-token-abc')
      const cursor = new URL(request.url).searchParams.get('cursor')
      asked.push(cursor)
      return HttpResponse.json(pages[cursor ?? ''] ?? { items: [], next_cursor: null })
    }),
  )
  return asked
}

const memory = (id: string, content: string) => ({ id, content, memory_type: 'semantic', status: 'active', created_at: T })

test('the panel lists the user’s memories and pages on', async () => {
  const asked = memories({
    '': { items: [memory('m1', 'Lives in Berlin')], next_cursor: 'm1' },
    m1: { items: [memory('m2', 'Prefers email')], next_cursor: null },
  })
  render(<MemoryPanel connection={connection} />)
  expect(await screen.findByText('Lives in Berlin')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Show more' }))
  expect(await screen.findByText('Prefers email')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
  expect(asked).toEqual([null, 'm1'])
})

test('forgetting asks first, then deletes and drops it from the list', async () => {
  memories({ '': { items: [memory('m1', 'Lives in Berlin'), memory('m2', 'Prefers email')], next_cursor: null } })
  const deleted: string[] = []
  server.use(
    http.delete(`${BASE}/v1/memories/:id`, ({ params }) => {
      deleted.push(String(params.id))
      return HttpResponse.json(memory(String(params.id), 'gone'))
    }),
  )
  render(<MemoryPanel connection={connection} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Forget: Lives in Berlin' }))
  expect(deleted).toEqual([]) // asked first
  const row = screen.getByText('Lives in Berlin').closest('li')!
  await userEvent.click(within(row).getByRole('button', { name: 'Forget it' }))
  await waitFor(() => expect(screen.queryByText('Lives in Berlin')).not.toBeInTheDocument())
  expect(deleted).toEqual(['m1'])
  expect(screen.getByText('Prefers email')).toBeInTheDocument()
})

test('a panel that cannot load says so', async () => {
  server.use(
    http.get(`${BASE}/v1/memories`, () => HttpResponse.json({ title: 'Token expired', status: 401 }, { status: 401 })),
  )
  render(<MemoryPanel connection={connection} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Token expired')
})

test('a host passing a new but equal connection object does not refetch the panel', async () => {
  const asked = memories({ '': { items: [memory('m1', 'Lives in Berlin')], next_cursor: null } })
  function Host({ tick }: { tick: number }) {
    return (
      <div data-tick={tick}>
        <MemoryPanel connection={{ baseUrl: `${BASE}/`, token: 'user-token-abc' }} />
      </div>
    )
  }
  const { rerender } = render(<Host tick={0} />)
  expect(await screen.findByText('Lives in Berlin')).toBeInTheDocument()
  rerender(<Host tick={1} />)
  rerender(<Host tick={2} />)
  await new Promise((r) => setTimeout(r, 50))
  expect(asked).toEqual([null]) // one fetch: the URL and token did not change
})
