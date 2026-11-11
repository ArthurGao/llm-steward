import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderApp } from '@/test/render'

test('a user shows profile values and pinned memories', async () => {
  renderApp('/acme-support/users?selected=u_8f2a')
  const profile = await screen.findByRole('table', { name: 'Profile' })
  expect(within(profile).getByText('timezone')).toBeInTheDocument()
  expect(within(profile).getByText('Australia/Sydney')).toBeInTheDocument()
  expect(screen.getByText('Is on the Enterprise plan with 120 seats')).toBeInTheDocument()
})

test('an owner erases a user after typing their id; their memories are gone', async () => {
  const { router } = renderApp('/acme-support/users?selected=u_8f2a')
  await userEvent.click(await screen.findByRole('button', { name: 'Erase user data…' }))
  const erase = screen.getByRole('button', { name: 'Erase permanently' })
  expect(erase).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Type u_8f2a to confirm'), 'u_8f2a')
  await userEvent.click(erase)
  expect(await screen.findByRole('status')).toHaveTextContent('Erased 8 memories and 4 episodes of u_8f2a')
  const list = screen.getByRole('table', { name: 'Users' })
  expect(within(list).queryByText('u_8f2a')).not.toBeInTheDocument()

  await router.navigate({ to: '/$ns/memories', params: { ns: 'acme-support' }, search: { user: 'u_8f2a' } })
  expect(await screen.findByText('No memories match')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument()
})


test('the users list pages with a cursor', async () => {
  renderApp('/acme-support/users?cursor=5')
  expect(await screen.findByText('6–8 of 8')).toBeInTheDocument()
})
