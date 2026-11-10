import * as Tooltip from '@radix-ui/react-tooltip'
import { Link, Outlet, createRootRoute } from '@tanstack/react-router'

function NotFound() {
  return (
    <main className="mx-auto max-w-xl px-5 py-24">
      <h1 className="text-[22px] font-bold">Page not found</h1>
      <p className="mt-2 text-ink-2">This address is not part of the Admin.</p>
      <Link to="/" className="mt-4 inline-block">Go to the dashboard</Link>
    </main>
  )
}

export const Route = createRootRoute({
  component: () => (
    <Tooltip.Provider>
      <Outlet />
    </Tooltip.Provider>
  ),
  notFoundComponent: NotFound,
})
