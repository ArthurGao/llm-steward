import { useQuery } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import { str } from '@/lib/search'
import type { ActorType, Change, RetrievalLog } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { HeaderBlock } from '@/components/HeaderBlock'
import { EmptyState, Loading, Mono, PageTitle } from '@/components/misc'
import { Pager } from '@/components/Pager'
import { DocumentFrame, SplitView } from '@/components/SplitView'
import { Select } from '@/components/ui/field'
import { TabPanel, Tabs } from '@/components/ui/tabs'
import { score, stamp } from '@/lib/format'

interface Search {
  tab?: 'retrievals'
  type?: Change['object_type']
  actor?: ActorType
  op?: Change['op']
  selected?: string
  cursor?: string
}

export const Route = createFileRoute('/$ns/activity')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    tab: s.tab === 'retrievals' ? 'retrievals' : undefined,
    type: str(s.type) as Search['type'],
    actor: str(s.actor) as Search['actor'],
    op: str(s.op) as Search['op'],
    selected: str(s.selected),
    cursor: str(s.cursor),
  }),
  component: Activity,
})

function Activity() {
  const { ns } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const set = (patch: Partial<Search>) => void navigate({ search: (s) => ({ ...s, cursor: undefined, ...patch }) })
  return (
    <>
      <PageTitle title="Activity" count="Every change to governed objects, and every retrieval" />
      <Tabs label="Activity" value={search.tab ?? 'changes'} onValueChange={(v) => set({ tab: v === 'retrievals' ? 'retrievals' : undefined, selected: undefined })} tabs={[{ value: 'changes', label: 'Changes' }, { value: 'retrievals', label: 'Retrievals' }]}>
        <TabPanel value="changes" className="flex min-h-0 flex-1 flex-col">{!search.tab && <Changes ns={ns} search={search} set={set} />}</TabPanel>
        <TabPanel value="retrievals">{search.tab === 'retrievals' && <Retrievals ns={ns} />}</TabPanel>
      </Tabs>
    </>
  )
}

function Changes({ ns, search, set }: { ns: string; search: Search; set: (p: Partial<Search>) => void }) {
  const query = { object_type: search.type, actor_type: search.actor, op: search.op, cursor: search.cursor }
  const list = useQuery({ queryKey: [ns, 'activity', query], queryFn: () => api.activity(ns, query) })
  const selected = list.data?.items.find((c) => c.id === search.selected)
  const columns: Column<Change>[] = [
    { key: 'at', header: 'Time', cell: (c) => <Mono className="text-ink-2">{stamp(c.created_at)}</Mono> },
    { key: 'obj', header: 'Object', cell: (c) => <span><span className="text-ink-2">{c.object_type.replace('_', ' ')}</span> <Mono>{c.object_id}</Mono></span> },
    { key: 'op', header: 'Change', cell: (c) => <span className={c.op === 'erase' ? 'font-bold text-rejected' : ''}>{c.op}</span> },
    { key: 'actor', header: 'Actor', cell: (c) => <span><Mono data-testid="actor">{c.actor_type}</Mono>{c.actor_id && <span className="text-ink-2"> {c.actor_id}</span>}</span> },
    { key: 'why', header: 'Reason', cell: (c) => <span className="line-clamp-1 text-ink-2">{c.reason ?? ''}</span>, wide: true },
  ]
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <Select aria-label="Object type" value={search.type ?? ''} onChange={(e) => set({ type: (e.target.value || undefined) as Search['type'] })}>
          <option value="">Any object</option>
          {['memory', 'kb_entry', 'candidate', 'user'].map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
        </Select>
        <Select aria-label="Actor type" value={search.actor ?? ''} onChange={(e) => set({ actor: (e.target.value || undefined) as ActorType })}>
          <option value="">Any actor</option>
          {['ai', 'end_user', 'admin', 'system', 'provider', 'host_app'].map((t) => <option key={t} value={t}>{t}</option>)}
        </Select>
        <Select aria-label="Change" value={search.op ?? ''} onChange={(e) => set({ op: (e.target.value || undefined) as Search['op'] })}>
          <option value="">Any change</option>
          {['create', 'update', 'transition', 'erase'].map((t) => <option key={t} value={t}>{t}</option>)}
        </Select>
      </div>
      <SplitView
        open={!!search.selected}
        onBack={() => set({ selected: undefined })}
        listWidth="lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]"
        list={
          <>
            {list.isPending && <Loading />}
            {list.data && (
              <DataTable label="Changes" rows={list.data.items} columns={search.selected ? columns.filter((c) => !c.wide) : columns} rowKey={(c) => c.id} selectedKey={search.selected} onOpen={(c) => set({ selected: c.id, cursor: search.cursor })} empty={<EmptyState title="No changes match these filters" />} />
            )}
            {list.data && <Pager cursor={search.cursor ?? null} next={list.data.next_cursor} total={list.data.total} shown={list.data.items.length} onChange={(cursor) => set({ cursor: cursor ?? undefined })} />}
          </>
        }
        detail={selected ? <ChangeDocument change={selected} /> : <EmptyState title="Pick a change">Its before and after open here.</EmptyState>}
      />
    </>
  )
}

function lines(obj: Record<string, unknown> | null): string[] {
  if (!obj) return []
  return Object.entries(obj).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
}

function ChangeDocument({ change: c }: { change: Change }) {
  const before = lines(c.before)
  const after = lines(c.after)
  const redacted = c.before === null && c.after === null
  return (
    <DocumentFrame>
      <HeaderBlock left={[['Object', `${c.object_type} ${c.object_id}`], ['Change', c.op], ['Reason', c.reason ?? '—']]} right={[['Change id', c.id], ['At', stamp(c.created_at)], ['Actor', `${c.actor_type}${c.actor_id ? ` ${c.actor_id}` : ''}`]]} />
      {redacted ? (
        <p className="mt-5 text-ink-2">Content redacted: this record belonged to a user whose data was erased.</p>
      ) : (
        <figure className="mt-5 border border-rule font-mono text-[13px] leading-6">
          <figcaption className="border-b border-rule bg-wash px-3.5 py-1.5 text-ink-2">before → after</figcaption>
          {before.map((l) => <p key={`b${l}`} className="bg-del-wash px-3.5 text-del">- {l}</p>)}
          {after.map((l) => <p key={`a${l}`} className="bg-ins-wash px-3.5 text-ins">+ {l}</p>)}
        </figure>
      )}
    </DocumentFrame>
  )
}

function Retrievals({ ns }: { ns: string }) {
  const logs = useQuery({ queryKey: [ns, 'retrievalLogs', 'all'], queryFn: () => api.retrieval.logs(ns, { limit: 100 }) })
  const columns: Column<RetrievalLog>[] = [
    { key: 'at', header: 'Time', cell: (l) => <Mono className="text-ink-2">{stamp(l.created_at)}</Mono> },
    { key: 'q', header: 'Question', cell: (l) => l.query },
    { key: 'u', header: 'User', cell: (l) => <Mono className="text-ink-2">{l.user_id ?? '—'}</Mono>, wide: true },
    { key: 'top', header: 'Top', align: 'right', cell: (l) => <Mono className={l.gap ? 'text-rejected' : ''}>{score(l.top_score)}</Mono> },
    { key: 'n', header: 'Hits', align: 'right', cell: (l) => <Mono>{l.hit_count}</Mono>, wide: true },
    { key: 'ms', header: 'Latency', align: 'right', cell: (l) => <Mono className={l.degraded ? 'text-awaiting' : ''}>{l.latency_ms} ms{l.degraded ? ' · degraded' : ''}</Mono> },
  ]
  if (logs.isPending) return <Loading />
  return <DataTable label="Retrievals" rows={logs.data?.items ?? []} columns={columns} rowKey={(l) => l.id} empty={<EmptyState title="No retrievals yet" />} />
}
