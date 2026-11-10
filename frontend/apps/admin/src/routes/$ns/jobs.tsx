import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import type { Job } from '@/api/types'
import { useDenied } from '@/auth/can'
import { DataTable, type Column } from '@/components/DataTable'
import { EmptyState, ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/field'
import { age } from '@/lib/format'

export const Route = createFileRoute('/$ns/jobs')({
  validateSearch: (s: Record<string, unknown>): { status?: Job['status'] } => ({
    status: s.status === 'pending' || s.status === 'running' || s.status === 'done' || s.status === 'failed' ? s.status : undefined,
  }),
  component: Jobs,
})

function JobActions({ ns, job }: { ns: string; job: Job }) {
  const denied = useDenied('manageJobs')
  const qc = useQueryClient()
  const done = () => qc.invalidateQueries({ queryKey: [ns] })
  const retry = useMutation({ mutationFn: () => api.jobs.retry(ns, job.id), onSuccess: done })
  const cancel = useMutation({ mutationFn: () => api.jobs.cancel(ns, job.id), onSuccess: done })
  if (job.status === 'failed') return <Button size="sm" disabledReason={denied} disabled={retry.isPending} onClick={() => retry.mutate()}>Retry</Button>
  if (job.status === 'pending') return <Button size="sm" variant="ghost" disabledReason={denied} disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel</Button>
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
    </>
  )
}
