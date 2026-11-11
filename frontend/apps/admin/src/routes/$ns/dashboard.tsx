import { useQuery } from '@tanstack/react-query'
import { createFileRoute, Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { api } from '@/api/endpoints'
import type { Stats } from '@/api/types'
import { MODE } from '@/auth/session'
import { HeaderBlock } from '@/components/HeaderBlock'
import { ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { statusLabel } from '@/components/StatusBadge'
import { cn } from '@/lib/cn'
import { score } from '@/lib/format'

export const Route = createFileRoute('/$ns/dashboard')({ component: Dashboard })

const SEG: Record<string, string> = {
  active: 'bg-ink',
  verified: 'bg-verified',
  draft: 'bg-ink-3',
  superseded: 'bg-[#bdbdbd]',
  deprecated: 'bg-[#bdbdbd]',
  deleted: 'bg-[#e2e2e2]',
  rejected: 'bg-rejected',
}

function Distribution({ label, counts, to, ns }: { label: string; counts: Record<string, number>; to: '/$ns/memories' | '/$ns/kb'; ns: string }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const parts = Object.entries(counts).filter(([, n]) => n > 0)
  return (
    <div className="border-b border-rule py-3">
      <div className="flex items-baseline justify-between gap-3">
        <Link to={to} params={{ ns }} className="font-medium">
          {label}
        </Link>
        <Mono>{total}</Mono>
      </div>
      <div className="mt-2 flex h-2.5 w-full gap-px" aria-hidden>
        {parts.map(([k, n]) => (
          <span key={k} className={cn('h-full', SEG[k])} style={{ width: `${(n / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
        {parts.map(([k, n]) => (
          <li key={k} className="flex items-center gap-1.5">
            <span aria-hidden className={cn('size-2.5 shrink-0', SEG[k])} />
            {statusLabel(k)} <Mono className="text-ink-2">{n}</Mono>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Columns({ title, series, unit }: { title: string; series: Stats['writes_7d']; unit: string }) {
  const max = Math.max(...series.map((d) => d.count), 1)
  const total = series.reduce((a, d) => a + d.count, 0)
  return (
    <figure>
      <figcaption className="flex items-baseline justify-between">
        <span className="font-medium">{title}</span>
        <Mono className="text-ink-2">
          {total} {unit}
        </Mono>
      </figcaption>
      <div className="mt-3 grid h-28 grid-cols-7 items-end gap-2 border-b border-ink" role="img" aria-label={`${title}: ${series.map((d) => `${d.day} ${d.count}`).join(', ')}`}>
        {series.map((d, i) => (
          <div key={d.day} className="flex h-full flex-col justify-end">
            <span className={cn('block w-full', i === series.length - 1 ? 'bg-ink' : 'bg-[#9a9a9a]')} style={{ height: `${(d.count / max) * 100}%` }} title={`${d.day}: ${d.count}`} />
          </div>
        ))}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-2 font-mono text-[11.5px] text-ink-2">
        {series.map((d) => (
          <span key={d.day} className="text-center">{d.day.slice(8)}</span>
        ))}
      </div>
    </figure>
  )
}

function Dashboard() {
  const { ns } = Route.useParams()
  const stats = useQuery({ queryKey: [ns, 'stats'], queryFn: () => api.stats(ns) })
  const providers = useQuery({ queryKey: [ns, 'providers'], queryFn: () => api.providers.list(ns) })
  const namespaces = useQuery({ queryKey: ['namespaces'], queryFn: api.namespaces })
  const n = namespaces.data?.items.find((x) => x.slug === ns)

  if (stats.isPending) return <><PageTitle title="Dashboard" /><Loading /></>
  if (stats.isError) return <><PageTitle title="Dashboard" /><div className="p-5"><ErrorNote error={stats.error} /></div></>
  const s = stats.data
  const unhealthy = providers.data?.items.filter((p) => p.health !== 'healthy') ?? []
  const failedSync = providers.data?.items.reduce((a, p) => a + p.queue.failed, 0) ?? 0

  type Attention = { show: boolean; text: string; to: '/$ns/review' | '/$ns/jobs' | '/$ns/providers'; search?: Record<string, string>; tone: string }
  const attention: Attention[] = [
    { show: s.pending_reviews > 0, text: `${s.pending_reviews} awaiting review`, to: '/$ns/review', tone: 'text-awaiting' },
    { show: s.awaiting_user > 0, text: `${s.awaiting_user} waiting for end users to confirm`, to: '/$ns/review', search: { stage: 'awaiting_user' }, tone: 'text-ink' },
    { show: s.failed_jobs > 0, text: `${s.failed_jobs} failed jobs`, to: '/$ns/jobs', search: { status: 'failed' }, tone: 'text-rejected' },
    { show: failedSync > 0, text: `${failedSync} failed ${failedSync === 1 ? 'sync' : 'syncs'} to the engine`, to: '/$ns/providers', tone: 'text-rejected' },
    ...unhealthy.map((p): Attention => ({ show: true, text: `${p.engine} ${p.health}: p95 ${p.latency_p95_ms} ms`, to: '/$ns/providers', tone: 'text-awaiting' })),
  ]
  const shown = attention.filter((a) => a.show)

  return (
    <>
      <PageTitle title="Dashboard" count={n?.name} />
      <div className="px-5 py-5 sm:px-7">
        <HeaderBlock
          left={[['Namespace', ns], ['Memory engine', n?.memory_provider ?? '—'], ['Knowledge engine', n?.kb_provider ?? '—']]}
          right={[['Documents', String(s.documents)], ['Retrieval p95', s.retrieval_p95_ms === null ? '—' : `${s.retrieval_p95_ms} ms`], ...(MODE === 'mock' ? [['Clock', 'demo, fixed'] as [string, string]] : [])]}
        />
      </div>
      <div className="grid gap-x-12 gap-y-8 px-5 pb-10 sm:px-7 lg:grid-cols-2">
        <section>
          <h2 className="text-[15px] font-bold">Needs attention</h2>
          {shown.length === 0 ? (
            <p className="mt-2 text-ink-2">Nothing waiting. Reviews, jobs and sync are clear.</p>
          ) : (
            <ul className="mt-1">
              {shown.map((a) => (
                <li key={a.text} className="border-b border-rule">
                  <Link to={a.to} params={{ ns }} search={a.search as never} className={cn('flex justify-between py-2.5 font-medium no-underline hover:bg-wash', a.tone)}>
                    {a.text}
                    <ArrowRight aria-hidden className="size-4 text-ink-3" strokeWidth={1.75} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section>
          <h2 className="text-[15px] font-bold">Governance state</h2>
          <Distribution label="Memories" counts={s.memories} to="/$ns/memories" ns={ns} />
          <Distribution label="Knowledge entries" counts={s.kb_entries} to="/$ns/kb" ns={ns} />
        </section>
        <section className="grid gap-8 sm:grid-cols-2">
          <Columns title="Writes, last 7 days" series={s.writes_7d} unit="writes" />
          <Columns title="Retrievals, last 7 days" series={s.retrievals_7d} unit="queries" />
        </section>
        <section>
          <h2 className="text-[15px] font-bold">What the KB is missing</h2>
          <p className="text-[13px] text-ink-2">Questions whose best answer scored under 0.35.</p>
          {s.gaps.length === 0 ? (
            <p className="mt-2 text-ink-2">No gaps logged.</p>
          ) : (
            <table className="mt-2 w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-ink text-[12.5px] text-ink-2">
                  <th className="py-1.5 font-medium">Question</th>
                  <th className="py-1.5 text-right font-medium">Asked</th>
                  <th className="py-1.5 text-right font-medium">Best</th>
                </tr>
              </thead>
              <tbody>
                {s.gaps.map((g) => (
                  <tr key={g.query} className="border-b border-rule">
                    <td className="py-2">
                      <Link to="/$ns/playground" params={{ ns }} search={{ q: g.query }}>{g.query}</Link>
                    </td>
                    <td className="py-2 text-right"><Mono>{g.count}×</Mono></td>
                    <td className="py-2 text-right"><Mono className="text-rejected">{score(g.top_score)}</Mono></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  )
}
