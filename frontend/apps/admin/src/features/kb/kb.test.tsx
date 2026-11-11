import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FIX } from '@/mocks/store'
import { renderApp } from '@/test/render'

test('an entry shows its versions and opens an earlier one as a stacked document', async () => {
  renderApp(`/acme-support/kb?entry=${FIX.refundEntry}`)
  const versions = await screen.findByRole('list', { name: 'Versions' })
  expect(within(versions).getAllByRole('listitem')).toHaveLength(3)
  await userEvent.click(within(versions).getByRole('button', { name: /v1/ }))
  expect(await screen.findByText('Refunds over $2,000 require approval from a team lead.')).toBeInTheDocument()
})

test('a deprecated entry links to the entry that obsoleted it', async () => {
  renderApp(`/acme-support/kb?entry=${FIX.oldRefundEntry}`)
  expect(await screen.findByRole('button', { name: 'KB-0142 v3' })).toBeInTheDocument()
})

test('editing a verified entry sends it to review instead of changing it', async () => {
  renderApp(`/acme-support/kb?entry=${FIX.refundEntry}`)
  await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))
  const box = screen.getByLabelText('Statement')
  await userEvent.clear(box)
  await userEvent.type(box, 'Refunds over $250 need approval.')
  await userEvent.click(screen.getByRole('button', { name: 'Submit for review' }))
  expect(await screen.findByRole('status')).toHaveTextContent(/Sent to review as C-\w+\./)
  expect(screen.getAllByText('Refunds over $1,000 require approval from a team lead.').length).toBeGreaterThan(0)
  expect(screen.queryByText('Refunds over $250 need approval.')).not.toBeInTheDocument()
})

test('the documents tab lists uploads with their chunk counts; upload is disabled in the demo', async () => {
  renderApp('/acme-support/kb?tab=documents')
  expect(await screen.findByText('support-handbook-2026-10.pdf')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Upload document' })).toHaveAttribute('aria-disabled', 'true')
})

test('a historic version walks forward to the version that obsoleted it', async () => {
  renderApp(`/acme-support/kb?entry=${FIX.refundEntry}`)
  const versions = await screen.findByRole('list', { name: 'Versions' })
  await userEvent.click(within(versions).getByRole('button', { name: /v1/ }))
  await userEvent.click(await screen.findByRole('button', { name: 'KB-0142 v2' }))
  expect(await screen.findByText('Refunds over $1,000 need a team lead to approve them.', { selector: 'p' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: 'Open documents' })).toHaveTextContent('KB-0142 v1')
})

test('the review filter lists the knowledge flagged for review', async () => {
  const { router } = renderApp('/acme-support/kb')
  const table = await screen.findByRole('table')
  expect(within(table).getAllByRole('row').length).toBeGreaterThan(3)
  await userEvent.selectOptions(screen.getByLabelText('Review'), 'due')
  await within(table).findByText('Refund approval threshold')
  const keys = within(screen.getByRole('table')).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0]?.textContent)
  expect(keys).toEqual(['KB-0142', 'KB-0301']) // the two the demo flagged
  expect(router.state.location.search).toMatchObject({ review: 'due' })
})

test('worth verifying lists the well rated active knowledge', async () => {
  renderApp('/acme-support/kb?review=verify')
  const table = await screen.findByRole('table')
  await within(table).findByText('Chargeback ownership')
  const keys = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0]?.textContent)
  expect(keys).toEqual(['KB-0144']) // active, 85% helpful; verified ones are already there
})
