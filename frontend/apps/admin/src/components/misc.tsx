import type { HTMLAttributes, ReactNode } from 'react'
import { MODE } from '@/auth/session'
import { cn } from '@/lib/cn'

/** Why a control the backend has no route for yet is disabled. */
export const NOT_IN_DEMO = MODE === 'mock' ? 'Not available in the demo' : 'Not available in this version'

export function DemoMarker() {
  if (MODE === 'live') return null // real data: nothing to mark
  return (
    <span title="Everything on this site is synthetic demo data that resets on reload" className="border border-dashed border-ink-3 px-2 py-0.5 text-[12px] text-ink-2">
      Demo data
    </span>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="ml-1.5 font-mono text-[11px] opacity-70">{children}</kbd>
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="px-5 py-12 text-center">
      <p className="font-bold">{title}</p>
      {children && <p className="mx-auto mt-1 max-w-[46ch] text-ink-2">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Mono({ children, className, ...rest }: HTMLAttributes<HTMLSpanElement> & { 'data-testid'?: string }) {
  return (
    <span className={cn('font-mono text-[13px] tabular-nums', className)} {...rest}>
      {children}
    </span>
  )
}

export function PageTitle({ title, count, children }: { title: string; count?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2 border-b border-rule px-5 py-3.5 sm:px-7">
      <h1 className="text-[18px] font-bold">{title}</h1>
      {count !== undefined && <span className="text-ink-2">{count}</span>}
      {children && <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : 'Something went wrong'
  return <p role="alert" className="border border-rejected bg-rejected-wash px-3 py-2 text-rejected">{msg}</p>
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return <p className="px-5 py-8 text-ink-2" aria-busy="true">{label}…</p>
}
