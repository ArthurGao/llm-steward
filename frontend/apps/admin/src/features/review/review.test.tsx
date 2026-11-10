import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FIX } from '@/mocks/store'
import { renderApp } from '@/test/render'

const queue = () => screen.findByRole('list', { name: 'Candidates' })

test('pressing a approves the open candidate: it leaves the queue and KB-0142 becomes v4 verified', async () => {
  const { store } = renderApp(`/acme-support/review?selected=${FIX.refundCandidate}`)
  await screen.findByRole('heading', { name: /Refunds over \$500 require approval/ })
  await userEvent.keyboard('a')
  const ladder = await screen.findByRole('list', { name: 'Lifecycle' })
  // after approval the open step is the sync to the engine, with Verified behind it
  expect(await within(ladder).findByText('pending')).toBeInTheDocument()
  expect(within(ladder).getByText('Synced').closest('li')).toHaveAttribute('aria-current', 'step')
  expect(within(ladder).getByText('Verified').closest('li')).not.toHaveAttribute('aria-current')
  expect(await screen.findByText(/KB-0142 is now v4, verified/)).toBeInTheDocument()
  expect(within(await queue()).queryByText('Refunds over $500 need a second approver')).not.toBeInTheDocument()
  expect(store.getEntry('acme-support', FIX.refundEntry)).toMatchObject({ version: 4, status: 'verified' })
})

test('a decided candidate offers no second decision', async () => {
  const { store } = renderApp(`/acme-support/review?selected=${FIX.refundCandidate}`)
  await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
  await screen.findByText(/KB-0142 is now v4, verified/)
  expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument()
  expect(store.getEntry('acme-support', FIX.refundEntry).version).toBe(4)
})

test('rejecting needs a reason', async () => {
  renderApp(`/acme-support/review?selected=${FIX.refundCandidate}`)
  await screen.findByRole('heading', { name: /Refunds over \$500 require approval/ })
  await userEvent.keyboard('r')
  await userEvent.click(await screen.findByRole('button', { name: 'Confirm rejection' }))
  expect(await screen.findByText('A reason is required')).toBeInTheDocument()
})

test('a link to a candidate that does not exist shows Not found', async () => {
  renderApp('/acme-support/review?selected=cand_nope')
  expect(await screen.findByText('Candidate not found')).toBeInTheDocument()
  expect(await queue()).toBeInTheDocument()
})

test('a viewer sees approval disabled with the reason', async () => {
  renderApp(`/acme-support/review?selected=${FIX.refundCandidate}`, 'viewer')
  const approve = await screen.findByRole('button', { name: /^Approve/ })
  expect(approve).toHaveAttribute('aria-disabled', 'true')
})

test('following Obsoletes opens the current entry above the candidate, Escape returns', async () => {
  renderApp(`/acme-support/review?selected=${FIX.refundCandidate}`)
  await userEvent.click(await screen.findByRole('button', { name: 'KB-0142 v3' }))
  expect(await screen.findByRole('heading', { name: 'Refund approval threshold' })).toBeInTheDocument()
  await userEvent.keyboard('{Escape}')
  expect(await screen.findByRole('heading', { name: /Refunds over \$500 require approval/ })).toBeInTheDocument()
})
