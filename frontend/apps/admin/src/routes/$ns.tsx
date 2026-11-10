import { createFileRoute, Outlet, redirect } from '@tanstack/react-router'
import { getSession } from '@/auth/session'
import { TopBar } from '@/components/shell/TopBar'

export const Route = createFileRoute('/$ns')({
  beforeLoad: ({ location }) => {
    if (!getSession()) throw redirect({ to: '/login', search: { from: location.href } as never })
  },
  component: Layout,
})

function Layout() {
  return (
    <div className="flex min-h-dvh flex-col lg:h-dvh">
      <TopBar />
      <main id="main" className="flex min-h-0 flex-1 flex-col lg:overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
