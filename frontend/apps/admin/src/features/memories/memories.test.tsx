import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FIX } from '@/mocks/store'
import { renderApp } from '@/test/render'

test('the table hides superseded memories unless asked', async () => {
  renderApp('/acme-support/memories?user=u_8f2a')
  const table = await screen.findByRole('table', { name: 'Memories' })
  expect(within(table).queryByText('Runs Postgres 15 in production')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('checkbox', { name: 'Show superseded' }))
  expect(await within(table).findByText('Runs Postgres 15 in production')).toBeInTheDocument()
})

test('a memory shows the chain of facts it replaced', async () => {
  renderApp('/acme-support/memories?selected=mem_0004')
  const chain = await screen.findByRole('list', { name: 'Supersession chain' })
  expect(within(chain).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
    expect.stringContaining('Runs Postgres 15 in production'),
    expect.stringContaining('Runs Postgres 16 in production after the October upgrade'),
  ])
})

test('promoting a memory sends a KB candidate to review', async () => {
  renderApp(`/acme-support/memories?selected=${FIX.promotableMemory}`, 'owner')
  await userEvent.click(await screen.findByRole('button', { name: 'Promote to KB' }))
  expect(await screen.findByRole('status')).toHaveTextContent(/Promoted as C-\w+, waiting for review/)
})

test('a viewer cannot pin or delete', async () => {
  renderApp(`/acme-support/memories?selected=${FIX.promotableMemory}`, 'viewer')
  expect(await screen.findByRole('button', { name: 'Pin' })).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('button', { name: 'Delete…' })).toHaveAttribute('aria-disabled', 'true')
})

test('a link to the second page of memories opens on that page (reload-safe cursor)', async () => {
  renderApp('/acme-support/memories?cursor=25')
  expect(await screen.findByText('26–38 of 38')).toBeInTheDocument()
})
