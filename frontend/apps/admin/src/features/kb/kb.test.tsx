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
