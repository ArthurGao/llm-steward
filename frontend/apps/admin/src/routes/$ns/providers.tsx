import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { ProviderStatus, SyncRef } from '@/api/types'
import { NOT_IN_DEMO, useDenied } from '@/auth/can'
import { DataTable, type Column } from '@/components/DataTable'
import { HeaderBlock } from '@/components/HeaderBlock'
import { EmptyState, Loading, Mono, PageTitle } from '@/components/misc'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { age, pct } from '@/lib/format'

export const Route = createFileRoute('/$ns/providers')({ component: Providers })

function Engine({ p }: { p: ProviderStatus }) {
  return (
    <section className="border-b border-rule px-5 py-5 sm:px-7">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[17px] font-bold">{p.kind === 'kb' ? 'Knowledge engine' : 'Memory engine'}</h2>
        <StatusBadge status={p.health} />
      </div>
      <div className="mt-3 max-w-[920px]">
        <HeaderBlock
          left={[['Engine', `${p.engine} ${p.version}`], ['Capabilities', p.capabilities.join(', ')], ['Last reconciled', p.last_reconciled_at ? `${age(p.last_reconciled_at)} ago` : 'never']]}
          right={[['p95 latency', `${p.latency_p95_ms} ms`], ['Error rate', pct(p.error_rate)], ['Queue', `${p.queue.pending} pending · ${p.queue.failed} failed`], ['Drift', String(p.reconcile_drift)]]}
        />
      </div>
      {p.health !== 'healthy' && (
        <p className="mt-3 max-w-[72ch] text-awaiting">
          Retrieval still returns within its time budget: slow engine calls are cut off and the answer is marked degraded.
        </p>
      )}
    </section>
  )
}

function RetrySync({ ns, r }: { ns: string; r: SyncRef }) {
  const denied = useDenied('manageJobs')
  const qc = useQueryClient()
  const retry = useMutation({ mutationFn: () => api.providers.retrySync(ns, r.id), onSuccess: () => qc.invalidateQueries({ queryKey: [ns] }) })
  if (r.sync_status !== 'failed') return null
  return <Button size="sm" disabledReason={denied} disabled={retry.isPending} onClick={() => retry.mutate()}>Retry</Button>
}

function Providers() {
  const { ns } = Route.useParams()
  const providers = useQuery({ queryKey: [ns, 'providers'], queryFn: () => api.providers.list(ns) })
  const refs = useQuery({ queryKey: [ns, 'sync'], queryFn: () => api.providers.syncRefs(ns) })
  const [all, setAll] = useState(false)
  const rows = (refs.data?.items ?? []).filter((r) => all || (r.sync_status !== 'synced' && r.sync_status !== 'deleted')).sort((a, b) => (a.sync_status === 'failed' ? -1 : 0) - (b.sync_status === 'failed' ? -1 : 0))
  const columns: Column<SyncRef>[] = [
    { key: 'obj', header: 'Object', cell: (r) => r.label },
    { key: 'p', header: 'Engine', cell: (r) => <Mono className="text-ink-2">{r.provider}</Mono>, wide: true },
    { key: 's', header: 'Sync', cell: (r) => <StatusBadge status={r.sync_status} /> },
    { key: 'v', header: 'Synced', align: 'right', cell: (r) => <Mono>{r.synced_version ? `v${r.synced_version}` : '—'}</Mono>, wide: true },
    { key: 'e', header: 'Error', cell: (r) => <span className="text-[13px] text-rejected">{r.last_error ?? ''}</span>, wide: true },
    { key: 'at', header: 'Updated', align: 'right', cell: (r) => <Mono className="text-ink-2">{age(r.updated_at)}</Mono> },
    { key: 'act', header: '', align: 'right', cell: (r) => <RetrySync ns={ns} r={r} /> },
  ]
  return (
    <>
      <PageTitle title="Providers" count="The engines this namespace drives, and what is in flight to them">
        <Button disabledReason={NOT_IN_DEMO}>Reconcile now</Button>
        <Button disabledReason={NOT_IN_DEMO}>Switch provider…</Button>
      </PageTitle>
      {providers.isPending && <Loading />}
      {providers.data?.items.map((p) => <Engine key={p.name} p={p} />)}
      <section className="pt-5">
        <div className="flex flex-wrap items-baseline gap-3 px-5 pb-2 sm:px-7">
          <h2 className="text-[17px] font-bold">Sync queue</h2>
          <p className="text-ink-2">Only active and verified knowledge is pushed; deprecated knowledge is removed.</p>
          <label className="ml-auto flex items-center gap-2">
            <input type="checkbox" className="size-4 accent-ink" checked={all} onChange={(e) => setAll(e.target.checked)} />
            Show synced and removed
          </label>
        </div>
        {refs.isPending ? <Loading /> : <DataTable label="Sync queue" rows={rows} columns={columns} rowKey={(r) => r.id} empty={<EmptyState title="Everything is in sync" />} />}
      </section>
    </>
  )
}
