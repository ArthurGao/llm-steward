import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'

test('a Playground question with no confident answer can be added to the KB as a candidate', async () => {
  const { router } = renderApp('/acme-support/playground')
  await userEvent.type(await screen.findByLabelText('Question'), 'data retention period')
  await userEvent.click(screen.getByRole('button', { name: 'Retrieve' }))
  expect(await screen.findByText('No confident answer')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add the missing answer to the KB' }))
  await userEvent.type(screen.getByLabelText('Title'), 'Data retention')
  await userEvent.type(screen.getByLabelText('Answer'), 'Conversation data is kept for 30 days.')
  await userEvent.click(screen.getByRole('button', { name: 'Send to review' }))
  const status = await screen.findByRole('status')
  expect(status).toHaveTextContent(/Sent to review as C-\w+/)

  await userEvent.click(within(status).getByRole('link'))
  expect(await screen.findByRole('heading', { name: 'Conversation data is kept for 30 days.' })).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/acme-support/review')
})

test('a confident question shows every stage and the assembled context', async () => {
  renderApp('/acme-support/playground')
  await userEvent.type(await screen.findByLabelText('Question'), 'refund approval')
  await userEvent.click(screen.getByRole('button', { name: 'Retrieve' }))
  for (const stage of ['Vector', 'Keyword', 'RRF', 'Final']) expect(await screen.findByRole('heading', { name: stage })).toBeInTheDocument()
  expect(screen.getByLabelText('Assembled context')).toHaveTextContent('[KB-0142 v3] Refunds over $1,000')
})

test('when hits come back the operator still judges them and can add an answer', async () => {
  renderApp('/acme-support/playground')
  await userEvent.type(await screen.findByLabelText('Question'), 'refund approval')
  await userEvent.click(screen.getByRole('button', { name: 'Retrieve' }))
  expect(await screen.findByRole('heading', { name: 'Do these answer it?' })).toBeInTheDocument()
  expect(screen.queryByText('No confident answer')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add the missing answer to the KB' }))
  expect(screen.getByLabelText('Answer')).toBeInTheDocument()
})

test('the dashboard counts link to their lists', async () => {
  renderApp('/acme-support/dashboard')
  const link = await screen.findByRole('link', { name: /5 awaiting review/ })
  expect(link).toHaveAttribute('href', '/acme-support/review')
  expect(await screen.findByText('how long do we keep chat transcripts')).toBeInTheDocument()
})

test('an owner retries a failed job', async () => {
  renderApp('/acme-support/jobs')
  const row = (await screen.findByText('job_0912')).closest('tr')!
  await userEvent.click(within(row).getByRole('button', { name: 'Retry' }))
  expect(await within(row).findByText('Pending')).toBeInTheDocument()
})

test('the activity log filters by actor type and shows a diff', async () => {
  renderApp('/acme-support/activity?actor=end_user')
  const table = await screen.findByRole('table', { name: 'Changes' })
  const actors = within(table).getAllByTestId('actor').map((n) => n.textContent)
  expect(actors.length).toBeGreaterThan(0)
  expect(new Set(actors)).toEqual(new Set(['end_user']))
})

test('the providers page shows the degraded engine and retries its failed sync', async () => {
  renderApp('/acme-support/providers')
  expect(await screen.findByText('Degraded')).toBeInTheDocument()
  const row = (await screen.findByText('KB-0177 Refund method')).closest('tr')!
  await userEvent.click(within(row).getByRole('button', { name: 'Retry' }))
  expect(await within(row).findByText('Pending')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Switch provider…' })).toHaveAttribute('aria-disabled', 'true')
})

test('creating an API key shows the secret once', async () => {
  renderApp('/acme-support/settings?tab=keys')
  await userEvent.type(await screen.findByLabelText('Key name'), 'staging bot')
  await userEvent.click(screen.getByRole('button', { name: 'Create key' }))
  const secret = await screen.findByLabelText('New key secret')
  expect((secret as HTMLInputElement).value).toMatch(/^stw_[a-z0-9]{6}_[A-Za-z0-9]{24}$/)
  await userEvent.click(screen.getByRole('button', { name: 'I have copied it' }))
  expect(screen.queryByLabelText('New key secret')).not.toBeInTheDocument()
  expect(screen.getByText('staging bot')).toBeInTheDocument()
})
