import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import { str } from '@/lib/search'
import type { Memory } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { HeaderBlock } from '@/components/HeaderBlock'
import { EmptyState, ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { Pager } from '@/components/Pager'
import { DocumentError, DocumentFrame, SplitView } from '@/components/SplitView'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { SearchBox, Select } from '@/components/ui/field'
import { cn } from '@/lib/cn'
import { age, stamp, day } from '@/lib/format'

interface Search {
  selected?: string
  user?: string
  type?: string
  status?: Memory['status']
  superseded?: boolean
  q?: string
  cursor?: string
}

export const Route = createFileRoute('/$ns/memories')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    selected: str(s.selected),
    user: str(s.user),
    type: str(s.type),
    status: str(s.status) as Search['status'],
    superseded: s.superseded === true || s.superseded === 'true' ? true : undefined,
    q: str(s.q),
    cursor: str(s.cursor),
  }),
  component: Memories,
})

const TYPES = ['semantic', 'preference', 'profile', 'episodic']

function Memories() {
  const { ns } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const set = (patch: Partial<Search>) => void navigate({ search: (s) => ({ ...s, cursor: undefined, ...patch }) })
  const query = { user_id: search.user, memory_type: search.type, status: search.status, include_superseded: search.superseded, q: search.q, cursor: search.cursor }
  const list = useQuery({ queryKey: [ns, 'memories', query], queryFn: () => api.memories.list(ns, query) })
  const filtered = !!(search.user || search.type || search.status || search.q)

  const columns: Column<Memory>[] = [
    { key: 'content', header: 'Memory', cell: (m) => (
      <span className={cn(m.status !== 'active' && 'text-ink-3 line-through decoration-1')}>
        {m.pinned && <span className="mr-1.5 font-mono text-[11px] text-ink-2" title="Pinned">PIN</span>}
        {m.content}
      </span>
    ) },
    { key: 'user', header: 'User', cell: (m) => <Mono className="text-ink-2">{m.user_id ?? 'shared'}</Mono> },
    { key: 'type', header: 'Type', cell: (m) => m.memory_type, wide: true },
    { key: 'status', header: 'Status', cell: (m) => <StatusBadge status={m.status} />, wide: true },
    { key: 'imp', header: 'Imp.', align: 'right', cell: (m) => <Mono>{m.importance.toFixed(2)}</Mono>, wide: true },
    { key: 'conf', header: 'Conf.', align: 'right', cell: (m) => <Mono>{m.confidence.toFixed(2)}</Mono>, wide: true },
    { key: 'updated', header: 'Updated', align: 'right', cell: (m) => <Mono className="text-ink-2">{age(m.updated_at)}</Mono> },
  ]

  return (
    <>
      <PageTitle title="Memories" count={list.data && `${list.data.total} ${search.superseded ? 'including superseded' : 'active'}`} />
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <SearchBox aria-label="User id" placeholder="User id" value={search.user} className="w-36 font-mono" onCommit={(v) => set({ user: v })} />
        <Select aria-label="Type" value={search.type ?? ''} onChange={(e) => set({ type: e.target.value || undefined })}>
          <option value="">Any type</option>
          {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </Select>
        <SearchBox aria-label="Search memories" placeholder="Search" value={search.q} onCommit={(v) => set({ q: v })} />
        <label className="flex items-center gap-2 px-1">
          <input type="checkbox" checked={!!search.superseded} onChange={(e) => set({ superseded: e.target.checked || undefined })} className="size-4 accent-ink" />
          Show superseded
        </label>
      </div>
      <SplitView
        open={!!search.selected}
        onBack={() => set({ selected: undefined })}
        listWidth="lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]"
        list={
          <>
            {list.isPending && <Loading />}
            {list.isError && <div className="p-5"><ErrorNote error={list.error} /></div>}
            {list.data && (
              <DataTable
                label="Memories"
                rows={list.data.items}
                columns={search.selected ? columns.filter((c) => !c.wide) : columns}
                rowKey={(m) => m.id}
                selectedKey={search.selected}
                onOpen={(m) => set({ selected: m.id, cursor: search.cursor })}
                empty={
                  <EmptyState title={filtered ? 'No memories match' : 'No memories yet'} action={filtered ? <Button size="sm" onClick={() => set({ user: undefined, type: undefined, status: undefined, q: undefined })}>Clear filters</Button> : undefined}>
                    {filtered ? 'Nothing in this namespace matches these filters.' : 'Memories appear as agents send conversations.'}
                  </EmptyState>
                }
              />
            )}
            {list.data && <Pager cursor={search.cursor ?? null} next={list.data.next_cursor} total={list.data.total} shown={list.data.items.length} onChange={(cursor) => set({ cursor: cursor ?? undefined, selected: search.selected })} />}
          </>
        }
        detail={search.selected ? <MemoryDocument ns={ns} id={search.selected} /> : <EmptyState title="Pick a memory">Its scope, provenance and the facts it replaced open here.</EmptyState>}
      />
    </>
  )
}

function MemoryDocument({ ns, id }: { ns: string; id: string }) {
  const memory = useQuery({ queryKey: [ns, 'memory', id], queryFn: () => api.memories.get(ns, id) })
  const history = useQuery({ queryKey: [ns, 'memoryHistory', id], queryFn: () => api.memories.history(ns, id) })
  if (memory.isPending) return <Loading />
  if (memory.isError) return <DocumentError error={memory.error} notFound="Memory not found" hint="It may have been erased with its user’s data." />
  const m = memory.data
  const chain = history.data?.chain ?? []
  return (
    <DocumentFrame>
      <HeaderBlock
        left={[
          ['Status', <StatusBadge key="s" status={m.status} className="font-mono" />],
          ['User', m.user_id ?? 'shared'],
          ['Agent', m.agent_id ?? '—'],
          ['Session', m.session_id ?? '—'],
        ]}
        right={[
          ['Memory', m.id],
          ['Type', m.memory_type],
          ['Created', stamp(m.created_at)],
          ['Source', m.source_refs.map((r) => r.id).join(', ') || m.origin],
        ]}
      />
      <h2 className="mt-6 max-w-[60ch] text-[20px] leading-snug font-bold">{m.content}</h2>
      <p className="mt-2 font-mono text-[13px] text-ink-2">
        importance {m.importance.toFixed(2)} · confidence {m.confidence.toFixed(2)} · origin {m.origin} · sync {m.sync_status}
        {m.pinned && ' · pinned'}
      </p>
      <MemoryActions ns={ns} memory={m} />
      {chain.length > 1 && (
        <section className="mt-8">
          <h3 className="mb-2 font-bold">Supersession chain</h3>
          <ol aria-label="Supersession chain" className="border-t border-rule">
            {chain.map((c) => (
              <li key={c.id} className="grid grid-cols-[1fr_auto] gap-3 border-b border-rule py-2">
                <span className={cn(c.status !== 'active' && 'text-ink-3 line-through decoration-1', c.id === m.id && 'font-medium')}>{c.content}</span>
                <Mono className="text-ink-3">{c.valid_to ? `until ${day(c.valid_to)}` : 'current'}</Mono>
              </li>
            ))}
          </ol>
        </section>
      )}
    </DocumentFrame>
  )
}

function MemoryActions({ ns, memory: m }: { ns: string; memory: Memory }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [confirmDelete, setConfirmDelete] = useState(false)
  const done = () => qc.invalidateQueries({ queryKey: [ns] })
  const pin = useMutation({ mutationFn: () => api.memories.patch(ns, m.id, { pinned: !m.pinned }), onSuccess: done })
  const remove = useMutation({ mutationFn: () => api.memories.remove(ns, m.id), onSuccess: () => { setConfirmDelete(false); void done() } })
  const promote = useMutation({ mutationFn: () => api.memories.promote(ns, m.id), onSuccess: done })
  const inactive = m.status !== 'active' ? `A ${m.status} memory cannot change` : null
  const error = pin.error ?? remove.error ?? promote.error

  return (
    <div className="mt-5 grid gap-3">
      {promote.data && (
        <p role="status" className="border border-awaiting bg-awaiting-wash px-3 py-2 text-awaiting">
          Promoted as{' '}
          <Link to="/$ns/review" params={{ ns }} search={{ selected: promote.data.id }}>{promote.data.key}</Link>, waiting for review. A reviewer rewrites it as team knowledge or rejects it.
        </p>
      )}
      {error && <ErrorNote error={error} />}
      <div className="flex flex-wrap gap-2">
        <Button disabledReason={inactive} disabled={pin.isPending} onClick={() => pin.mutate()}>{m.pinned ? 'Unpin' : 'Pin'}</Button>
        <Button disabledReason={inactive} disabled={promote.isPending || !!promote.data} onClick={() => promote.mutate()}>Promote to KB</Button>
        <Button variant="danger" disabledReason={inactive} onClick={() => setConfirmDelete(true)}>Delete…</Button>
        {m.user_id && (
          <Button variant="ghost" onClick={() => void navigate({ to: '/$ns/users', params: { ns }, search: { selected: m.user_id ?? undefined } })}>
            All of {m.user_id}
          </Button>
        )}
      </div>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this memory?" description="It stops being retrieved and is removed from the memory engine. The record and its history stay, marked deleted.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete</Button>
        </div>
      </Dialog>
    </div>
  )
}
