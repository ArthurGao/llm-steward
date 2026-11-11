import * as Menu from '@radix-ui/react-dropdown-menu'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { ChevronDown, Menu as MenuIcon } from 'lucide-react'
import { api } from '@/api/endpoints'
import { disconnect, useSession } from '@/auth/session'
import { DemoMarker } from '@/components/misc'
import { Select } from '@/components/ui/field'

const PAGES = [
  { to: '/$ns/dashboard', label: 'Dashboard' },
  { to: '/$ns/memories', label: 'Memories' },
  { to: '/$ns/users', label: 'Users' },
  { to: '/$ns/kb', label: 'Knowledge Base' },
  { to: '/$ns/review', label: 'Review' },
  { to: '/$ns/playground', label: 'Playground' },
  { to: '/$ns/activity', label: 'Activity' },
  { to: '/$ns/jobs', label: 'Jobs' },
  { to: '/$ns/providers', label: 'Providers' },
  { to: '/$ns/settings', label: 'Settings' },
] as const

const menuItem = 'flex cursor-pointer items-center gap-2 px-3 py-2 outline-none data-[highlighted]:bg-wash'

export function TopBar() {
  const { ns } = useParams({ from: '/$ns' })
  const stats = useQuery({ queryKey: [ns, 'stats'], queryFn: () => api.stats(ns) })
  const pending = stats.data?.pending_reviews

  const label = (page: (typeof PAGES)[number]) =>
    page.label === 'Review' && pending ? (
      <>
        Review<span className="ml-1 font-mono text-[12px] text-awaiting">{pending}</span>
      </>
    ) : (
      page.label
    )

  return (
    <header className="sticky top-0 z-30 border-b border-ink bg-paper">
      {/* One row on sm+; on phones the namespace row wraps below the bar (one switcher, placed by flex order). */}
      <div className="flex flex-wrap items-center gap-x-3 px-4 sm:flex-nowrap sm:gap-x-6 sm:px-7">
        <Link to="/$ns/dashboard" params={{ ns }} className="flex h-[52px] items-center font-mono text-[15px] font-medium whitespace-nowrap text-ink no-underline">
          llm-steward
        </Link>
        <nav aria-label="Pages" className="hidden h-[52px] items-stretch gap-5 xl:flex">
          {PAGES.map((p) => (
            <Link
              key={p.to}
              to={p.to}
              params={{ ns }}
              className="flex items-center font-medium text-ink-2 no-underline hover:text-ink aria-[current=page]:text-ink aria-[current=page]:shadow-[inset_0_-2px_0_var(--color-ink)]"
            >
              {label(p)}
            </Link>
          ))}
        </nav>
        <div className="order-last -mx-4 flex basis-[calc(100%+2rem)] items-center justify-between gap-3 border-t border-rule bg-wash px-4 py-1.5 sm:order-none sm:mx-0 sm:ml-auto sm:basis-auto sm:border-0 sm:bg-transparent sm:p-0">
          <NamespaceSwitcher ns={ns} />
          <p className="text-[12px] text-ink-2 sm:hidden">Demo data · resets on reload</p>
          <span className="hidden sm:inline">
            <DemoMarker />
          </span>
        </div>
        <div className="ml-auto flex items-center gap-1.5 sm:ml-0 sm:gap-3">
          <UserMenu />
          <MobileNav ns={ns} label={label} />
        </div>
      </div>
    </header>
  )
}

function NamespaceSwitcher({ ns }: { ns: string }) {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const namespaces = useQuery({ queryKey: ['namespaces'], queryFn: api.namespaces })
  return (
    <label className="flex items-center gap-2">
      <span className="sr-only">Namespace</span>
      <Select
        aria-label="Namespace"
        value={ns}
        onChange={(e) => void navigate({ to: pathname.replace(/^\/[^/]+/, `/${e.target.value}`) })}
        className="h-8 text-[13px]"
      >
        {(namespaces.data?.items ?? [{ slug: ns }]).map((n) => (
          <option key={n.slug} value={n.slug}>
            {n.slug}
          </option>
        ))}
      </Select>
    </label>
  )
}

function UserMenu() {
  const session = useSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  if (session?.mode !== 'live') return null // the demo has no connection to end
  return (
    <Menu.Root>
      <Menu.Trigger className="flex h-8 items-center gap-1 px-2 text-[13px] hover:bg-wash" aria-label={`Connected to ${session.baseUrl}`}>
        <span className="hidden font-mono md:inline">{new URL(session.baseUrl).host}</span>
        <ChevronDown aria-hidden className="size-3.5" strokeWidth={1.75} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={6} className="z-50 min-w-60 border border-ink bg-paper py-1 text-[14px] shadow-[0_8px_24px_rgba(0,0,0,0.12)]">
          <Menu.Label className="px-3 py-1.5 font-mono text-[12.5px] text-ink-2">{session.baseUrl}</Menu.Label>
          <Menu.Separator className="my-1 h-px bg-rule" />
          <Menu.Item
            className={menuItem}
            onSelect={() => {
              disconnect()
              queryClient.clear()
              void navigate({ to: '/connect' })
            }}
          >
            Disconnect
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}

function MobileNav({ ns, label }: { ns: string; label: (p: (typeof PAGES)[number]) => React.ReactNode }) {
  return (
    <Menu.Root>
      <Menu.Trigger className="flex size-8 items-center justify-center hover:bg-wash xl:hidden" aria-label="Open page menu">
        <MenuIcon aria-hidden className="size-4" strokeWidth={1.75} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={6} className="z-50 min-w-56 border border-ink bg-paper py-1 shadow-[0_8px_24px_rgba(0,0,0,0.12)]">
          {PAGES.map((p) => (
            <Menu.Item key={p.to} asChild className={menuItem}>
              <Link to={p.to} params={{ ns }} className="text-ink no-underline aria-[current=page]:font-bold">
                {label(p)}
              </Link>
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}
