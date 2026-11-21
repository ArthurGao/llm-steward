import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import type { Job, ReconcileFinding } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { EmptyState, ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Select } from '@/components/ui/field'
import { age } from '@/lib/format'

export const Route = createFileRoute('/$ns/jobs')({
  validateSearch: (s: Record<string, unknown>): { status?: Job['status'] } => ({
    status: s.status === 'pending' || s.status === 'running' || s.status === 'done' || s.status === 'failed' ? s.status : undefined,
  }),
  component: Jobs,
})

function JobActions({ ns, job }: { ns: string; job: Job }) {
  const qc = useQueryClient()
  const done = () => qc.invalidateQueries({ queryKey: [ns] })
  const retry = useMutation({ mutationFn: () => api.jobs.retry(ns, job.id), onSuccess: done })
  const cancel = useMutation({ mutationFn: () => api.jobs.cancel(ns, job.id), onSuccess: done })
  if (job.status === 'failed') return <Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate()}>Retry</Button>
  if (job.status === 'pending') return <Button size="sm" variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel</Button>
  return null
}

function Jobs() {
  const { ns } = Route.useParams()
  const { status } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const list = useQuery({ queryKey: [ns, 'jobs', status], queryFn: () => api.jobs.list(ns, { status, limit: 100 }) })
  const columns: Column<Job>[] = [
    { key: 'id', header: 'Job', cell: (j) => <Mono>{j.id}</Mono> },
    { key: 'kind', header: 'Kind', cell: (j) => <Mono className="text-ink-2">{j.kind}</Mono> },
    { key: 'status', header: 'Status', cell: (j) => <StatusBadge status={j.status} /> },
    { key: 'try', header: 'Attempts', align: 'right', cell: (j) => <Mono>{j.attempts}/{j.max_attempts}</Mono> },
    { key: 'obj', header: 'Object', cell: (j) => <Mono className="text-ink-2">{j.object_id ?? '—'}</Mono>, wide: true },
    { key: 'err', header: 'Last error', cell: (j) => <span className="text-[13px] text-rejected">{j.last_error ?? ''}</span>, wide: true },
    { key: 'at', header: 'Runs', align: 'right', cell: (j) => <Mono className="text-ink-2">{age(j.run_after)}</Mono> },
    { key: 'act', header: '', align: 'right', cell: (j) => <JobActions ns={ns} job={j} /> },
  ]
  return (
    <>
      <PageTitle title="Jobs" count="The outbox worker: sync to engines, extraction, maintenance" />
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <Select aria-label="Status" value={status ?? ''} onChange={(e) => void navigate({ search: { status: (e.target.value || undefined) as Job['status'] } })}>
          <option value="">Any status</option>
          <option value="pending">Pending</option>
          <option value="running">Running</option>
          <option value="failed">Failed</option>
          <option value="done">Done</option>
        </Select>
      </div>
      {list.isPending && <Loading />}
      {list.isError && <div className="p-5"><ErrorNote error={list.error} /></div>}
      {list.data && <DataTable label="Jobs" rows={list.data.items} columns={columns} rowKey={(j) => j.id} empty={<EmptyState title="No jobs with this status" />} />}
      <Findings ns={ns} />
    </>
  )
}

// What the reconcile triage assistant proposes for engine records the mirror does not point at
// (design §11 P11). Read-only: a person decides; nothing here changes data.
function Findings({ ns }: { ns: string }) {
  const list = useQuery({ queryKey: [ns, 'reconcile-findings'], queryFn: () => api.reconcile.findings(ns, { limit: 50 }) })
  const columns: Column<ReconcileFinding>[] = [
    { key: 'rec', header: 'Engine record', cell: (f) => <Mono>{f.external_id}</Mono> },
    { key: 'act', header: 'Proposed', cell: (f) => <Mono>{f.proposed_action}</Mono> },
    { key: 'why', header: 'Diagnosis', cell: (f) => <span className="text-[13px]">{f.diagnosis}</span>, wide: true },
    { key: 'ev', header: 'Evidence', cell: (f) => <span className="text-[13px] text-ink-2">{f.evidence.join(' · ')}</span>, wide: true },
    { key: 'by', header: 'By', cell: (f) => <Mono className="text-ink-2">{f.model}</Mono> },
    { key: 'at', header: 'Found', align: 'right', cell: (f) => <Mono className="text-ink-2">{age(f.created_at)}</Mono> },
    { key: 'do', header: '', align: 'right', cell: (f) => <FindingActions ns={ns} finding={f} /> },
  ]
  return (
    <section aria-label="Reconcile findings" className="border-t border-rule">
      <PageTitle title="Reconcile findings" count="Unmirrored engine records, as the triage assistant reads them: proposals, not changes" />
      {list.isPending && <Loading />}
      {list.isError && <div className="p-5"><ErrorNote error={list.error} /></div>}
      {list.data && <DataTable label="Reconcile findings" rows={list.data.items} columns={columns} rowKey={(f) => f.id} empty={<EmptyState title="No findings" />} />}
    </section>
  )
}

const EFFECT: Record<ReconcileFinding['proposed_action'], string> = {
  erase: 'The engine record is deleted from the memory engine. This cannot be undone.',
  remirror: 'The engine record becomes a governed memory of its user, with its own history.',
  ignore: 'Nothing changes; the finding is marked as handled.',
}

// A person decides on a proposal (P13): applying it queues the work (the outbox), never inline.
function FindingActions({ ns, finding }: { ns: string; finding: ReconcileFinding }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const done = () => {
    setOpen(false)
    void qc.invalidateQueries({ queryKey: [ns] })
  }
  const apply = useMutation({ mutationFn: () => api.reconcile.apply(ns, finding.id), onSuccess: done })
  const dismiss = useMutation({ mutationFn: () => api.reconcile.dismiss(ns, finding.id), onSuccess: done })
  if (finding.decision) return <span className="text-[13px] text-ink-3">{finding.decision}</span>
  const danger = finding.proposed_action === 'erase'
  return (
    <div className="flex justify-end gap-2">
      <Button size="sm" variant={danger ? 'danger' : 'primary'} onClick={() => setOpen(true)}>{`Apply ${finding.proposed_action}…`}</Button>
      <Button size="sm" variant="ghost" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>Dismiss</Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={`Apply “${finding.proposed_action}” to ${finding.external_id}?`}
        description={EFFECT[finding.proposed_action]}
        confirmLabel={`Apply ${finding.proposed_action}`}
        danger={danger}
        pending={apply.isPending}
        error={apply.error}
        onConfirm={() => apply.mutate()}
      />
    </div>
  )
}
