import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'

test('the demo needs no sign-in: / opens the dashboard of acme-support', async () => {
  const { router } = renderApp('/')
  expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/acme-support/dashboard')
})

test('the demo has no Connect page: it redirects to the dashboard', async () => {
  const { router } = renderApp('/connect')
  expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  expect(router.state.location.pathname).toBe('/acme-support/dashboard')
})

test('the top bar lists every page and marks the current one', async () => {
  renderApp('/acme-support/memories')
  const nav = await screen.findByRole('navigation', { name: 'Pages' })
  await within(nav).findByText('5')
  const links = within(nav).getAllByRole('link').map((a) => a.textContent)
  expect(links).toEqual(['Dashboard', 'Memories', 'Users', 'Knowledge Base', 'Review5', 'Playground', 'Activity', 'Jobs', 'Providers', 'Settings'])
  expect(within(nav).getByRole('link', { name: 'Memories' })).toHaveAttribute('aria-current', 'page')
})

test('switching namespace keeps the page', async () => {
  const { router } = renderApp('/acme-support/memories')
  await screen.findByRole('option', { name: 'acme-sales' })
  await userEvent.selectOptions(screen.getByLabelText('Namespace'), 'acme-sales')
  await screen.findByText('Champion at a logistics company, budget approved for Q1')
  expect(router.state.location.pathname).toBe('/acme-sales/memories')
})

test('every signed-in page says it shows demo data', async () => {
  renderApp('/acme-support/jobs')
  expect(await screen.findByText('Demo data')).toBeInTheDocument()
})
