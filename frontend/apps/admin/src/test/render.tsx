import { keepPreviousData, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createMemoryHistory, createRouter } from '@tanstack/react-router'
import { render } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { createHandlers } from '@/mocks/handlers'
import { createStore } from '@/mocks/store'
import { routeTree } from '@/routeTree.gen'
import { setSession } from '@/auth/session'
import type { Role } from '@/api/types'

export const server = setupServer()

/** Mount the whole app at `path` against a fresh mock store, signed in as `role`. */
export function renderApp(path: string, role: Role | null = 'reviewer') {
  const store = createStore()
  server.resetHandlers(...createHandlers(store))
  if (role) setSession({ email: `${role}@demo`, role })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, placeholderData: keepPreviousData } } })
  const router = createRouter({ routeTree, history: createMemoryHistory({ initialEntries: [path] }) })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { ...utils, router, store }
}
