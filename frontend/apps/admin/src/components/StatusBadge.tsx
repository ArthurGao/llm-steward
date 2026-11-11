import { cn } from '@/lib/cn'

type Tone = 'awaiting' | 'verified' | 'active' | 'deprecated' | 'rejected' | 'neutral'

const STATUS: Record<string, [label: string, tone: Tone]> = {
  // knowledge
  draft: ['Draft', 'neutral'],
  active: ['Active', 'active'],
  verified: ['Verified', 'verified'],
  deprecated: ['Deprecated', 'deprecated'],
  rejected: ['Rejected', 'rejected'],
  // memories
  superseded: ['Superseded', 'deprecated'],
  deleted: ['Deleted', 'deprecated'],
  // candidates
  awaiting_review: ['Awaiting review', 'awaiting'],
  awaiting_user: ['Awaiting user', 'awaiting'],
  pending: ['Pending', 'awaiting'],
  accepted: ['Accepted', 'verified'],
  expired: ['Expired', 'deprecated'],
  // sync and jobs
  synced: ['Synced', 'verified'],
  failed: ['Failed', 'rejected'],
  deleting: ['Removing', 'awaiting'],
  running: ['Running', 'active'],
  done: ['Done', 'verified'],
  // documents and providers
  processing: ['Processing', 'awaiting'],
  ready: ['Ready', 'verified'],
  healthy: ['Healthy', 'verified'],
  degraded: ['Degraded', 'awaiting'],
  down: ['Down', 'rejected'],
  // risk
  high: ['High', 'rejected'],
  medium: ['Medium', 'awaiting'],
  low: ['Low', 'neutral'],
}

const TONE: Record<Tone, string> = {
  awaiting: 'text-awaiting',
  verified: 'text-verified',
  active: 'text-ink',
  deprecated: 'text-deprecated',
  rejected: 'text-rejected',
  neutral: 'text-ink-2',
}

const MARK: Record<Tone, string> = {
  awaiting: 'border-awaiting',
  verified: 'border-verified bg-verified',
  active: 'border-ink bg-ink',
  deprecated: 'border-deprecated',
  rejected: 'border-rejected bg-rejected',
  neutral: 'border-ink-3',
}

export function statusLabel(status: string): string {
  return STATUS[status]?.[0] ?? status
}

/** A status as text with a small square mark: filled once settled, hollow while open. */
/** A status the backend does not report (null) shows as "—". */
export function StatusBadge({ status, className }: { status: string | null; className?: string }) {
  if (status === null) return <span className={cn('text-[13px] text-ink-3', className)}>—</span>
  const [label, tone] = STATUS[status] ?? [status, 'neutral' as Tone]
  return (
    <span data-tone={tone} className={cn('inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium', TONE[tone], className)}>
      <span aria-hidden className={cn('size-2 shrink-0 border-[1.5px]', MARK[tone])} />
      {label}
    </span>
  )
}

export function RiskBadge({ risk }: { risk: string }) {
  return <StatusBadge status={risk} className="uppercase tracking-wide text-[12px]" />
}
