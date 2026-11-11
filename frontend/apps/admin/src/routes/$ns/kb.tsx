import { useQuery } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import { oneOf, str } from '@/lib/search'
import { REVIEW_FILTERS, type KbDocument, type KbEntry, type ReviewFilter } from '@/api/types'
import { DataTable, type Column } from '@/components/DataTable'
import { DocumentStack } from '@/components/DocumentStack'
import { EmptyState, ErrorNote, Loading, Mono, NOT_IN_DEMO, PageTitle } from '@/components/misc'
import { Pager } from '@/components/Pager'
import { DocumentFrame, SplitView } from '@/components/SplitView'
import { StatusBadge, RiskBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { SearchBox, Select } from '@/components/ui/field'
import { TabPanel, Tabs } from '@/components/ui/tabs'
import { EntryDocument } from '@/features/kb/EntryDocument'
import { cn } from '@/lib/cn'
import { age, bytes } from '@/lib/format'

interface Search {
  tab?: 'documents'
  collection?: string
  entry?: string
  doc?: string
  status?: KbEntry['status']
  risk?: KbEntry['risk_level']
  review?: ReviewFilter
  q?: string
  cursor?: string
}

// The entry filters; "Clear filters" sets them all back.
const CLEARED = { status: undefined, risk: undefined, review: undefined, q: undefined }

export const Route = createFileRoute('/$ns/kb')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    tab: s.tab === 'documents' ? 'documents' : undefined,
    collection: str(s.collection),
    entry: str(s.entry),
    doc: str(s.doc),
    status: str(s.status) as Search['status'],
    risk: str(s.risk) as Search['risk'],
    review: oneOf(REVIEW_FILTERS)(s.review),
    q: str(s.q),
    cursor: str(s.cursor),
  }),
  component: KnowledgeBase,
})

function KnowledgeBase() {
  const { ns } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const set = (patch: Partial<Search>) => void navigate({ search: (s) => ({ ...s, cursor: undefined, ...patch }) })
  const collections = useQuery({ queryKey: [ns, 'collections'], queryFn: () => api.kb.collections(ns) })
  const tab = search.tab ?? 'entries'

  return (
    <>
      <PageTitle title="Knowledge Base" count={collections.data && `${collections.data.items.reduce((n, c) => n + c.entry_count, 0)} entries in ${collections.data.items.length} collections`}>
        <Button disabledReason={NOT_IN_DEMO}>Upload document</Button>
        <Button variant="primary" disabledReason="New entries come from review in this demo: promote a memory or add a Playground gap">New entry</Button>
      </PageTitle>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <nav aria-label="Collections" className="flex gap-1 overflow-x-auto border-b border-rule px-5 py-2 lg:w-56 lg:shrink-0 lg:flex-col lg:border-r lg:border-b-0 lg:px-3 lg:py-4">
          {[{ id: undefined, name: 'All collections', entry_count: undefined }, ...(collections.data?.items ?? [])].map((c) => (
            <button
              key={c.id ?? 'all'}
              type="button"
              aria-current={search.collection === c.id || undefined}
              onClick={() => set({ collection: c.id, entry: undefined, doc: undefined })}
              className={cn('flex shrink-0 justify-between gap-3 px-2.5 py-1.5 text-left whitespace-nowrap hover:bg-wash', search.collection === c.id && 'bg-select font-bold hover:bg-select')}
            >
              <span>{c.name}</span>
              {c.entry_count !== undefined && <Mono className="text-ink-3">{c.entry_count}</Mono>}
            </button>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <Tabs label="Knowledge" value={tab} onValueChange={(v) => set({ tab: v === 'documents' ? 'documents' : undefined, entry: undefined, doc: undefined })} tabs={[{ value: 'entries', label: 'Entries' }, { value: 'documents', label: 'Documents' }]}>
            <TabPanel value="entries" className="flex min-h-0 flex-1 flex-col">
              {tab === 'entries' && <Entries ns={ns} search={search} set={set} />}
            </TabPanel>
            <TabPanel value="documents" className="flex min-h-0 flex-1 flex-col">
              {tab === 'documents' && <Documents ns={ns} search={search} set={set} />}
            </TabPanel>
          </Tabs>
        </div>
      </div>
    </>
  )
}

function Entries({ ns, search, set }: { ns: string; search: Search; set: (p: Partial<Search>) => void }) {
  const query = { collection_id: search.collection, status: search.status, risk_level: search.risk, review: search.review, q: search.q, cursor: search.cursor }
  const list = useQuery({ queryKey: [ns, 'entries', query], queryFn: () => api.kb.entries(ns, query) })
  const columns: Column<KbEntry>[] = [
    { key: 'key', header: 'Key', cell: (e) => <Mono className="text-ink-2">{e.key}</Mono> },
    { key: 'title', header: 'Entry', cell: (e) => <span className={cn('font-medium', e.status === 'deprecated' && 'text-ink-3 line-through decoration-1')}>{e.title}</span> },
    { key: 'status', header: 'Status', cell: (e) => <StatusBadge status={e.status} /> },
    { key: 'risk', header: 'Risk', cell: (e) => <RiskBadge risk={e.risk_level} />, wide: true },
    { key: 'v', header: 'Ver', align: 'right', cell: (e) => <Mono>v{e.version}</Mono>, wide: true },
    { key: 'hits', header: 'Hits', align: 'right', cell: (e) => <Mono>{e.hits}</Mono>, wide: true },
    { key: 'sync', header: 'Sync', cell: (e) => <StatusBadge status={e.sync_status} />, wide: true },
  ]
  const filtered = Object.keys(CLEARED).some((k) => search[k as keyof Search])
  const open = list.data?.items.find((x) => x.id === search.entry)
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <Select aria-label="Status" value={search.status ?? ''} onChange={(e) => set({ status: (e.target.value || undefined) as Search['status'] })}>
          <option value="">Any status</option>
          {['draft', 'active', 'verified', 'deprecated', 'rejected'].map((s) => <option key={s} value={s}>{s[0]?.toUpperCase() + s.slice(1)}</option>)}
        </Select>
        <Select aria-label="Risk" value={search.risk ?? ''} onChange={(e) => set({ risk: (e.target.value || undefined) as Search['risk'] })}>
          <option value="">Any risk</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </Select>
        <Select aria-label="Review" value={search.review ?? ''} onChange={(e) => set({ review: (e.target.value || undefined) as Search['review'] })}>
          <option value="">Any review state</option>
          <option value="due">Due for review</option>
          <option value="verify">Worth verifying</option>
        </Select>
        <SearchBox aria-label="Search entries" placeholder="Search" value={search.q} onCommit={(v) => set({ q: v })} />
      </div>
      <SplitView
        open={!!search.entry}
        onBack={() => set({ entry: undefined })}
        listWidth="lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]"
        list={
          <>
            {list.isPending && <Loading />}
            {list.isError && <div className="p-5"><ErrorNote error={list.error} /></div>}
            {list.data && (
              <DataTable
                label="Entries"
                rows={list.data.items}
                columns={search.entry ? columns.filter((c) => !c.wide) : columns}
                rowKey={(e) => e.id}
                selectedKey={search.entry}
                onOpen={(e) => set({ entry: e.id })}
                empty={<EmptyState title={filtered ? 'No entries match' : 'No entries yet'} action={filtered ? <button type="button" className="text-link" onClick={() => set(CLEARED)}>Clear filters</button> : undefined} />}
              />
            )}
            {list.data && <Pager cursor={search.cursor ?? null} next={list.data.next_cursor} total={list.data.total} shown={list.data.items.length} onChange={(cursor) => set({ cursor: cursor ?? undefined, entry: search.entry })} />}
          </>
        }
        detail={
          search.entry ? (
            <DocumentStack baseLabel={open ? `${open.key} v${open.version} ${open.title}` : 'Entry'} resetKey={search.entry}>
              <EntryDocument ns={ns} id={search.entry} actions />
            </DocumentStack>
          ) : (
            <EmptyState title="Pick an entry">Its header, current text, versions and the entries it obsoletes open here.</EmptyState>
          )
        }
      />
    </>
  )
}

function Documents({ ns, search, set }: { ns: string; search: Search; set: (p: Partial<Search>) => void }) {
  const docs = useQuery({ queryKey: [ns, 'documents', search.collection], queryFn: () => api.kb.documents(ns, search.collection) })
  const columns: Column<KbDocument>[] = [
    { key: 'name', header: 'File', cell: (d) => <span className="font-medium">{d.filename}</span> },
    { key: 'status', header: 'Parse', cell: (d) => <StatusBadge status={d.status} /> },
    { key: 'chunks', header: 'Chunks', align: 'right', cell: (d) => <Mono>{d.chunk_count}</Mono> },
    { key: 'size', header: 'Size', align: 'right', cell: (d) => <Mono>{bytes(d.size_bytes)}</Mono>, wide: true },
    { key: 'sync', header: 'Sync', cell: (d) => <StatusBadge status={d.sync_status} />, wide: true },
    { key: 'at', header: 'Uploaded', align: 'right', cell: (d) => <Mono className="text-ink-2">{age(d.created_at)}</Mono>, wide: true },
  ]
  const doc = docs.data?.items.find((d) => d.id === search.doc)
  return (
    <SplitView
      open={!!search.doc}
      onBack={() => set({ doc: undefined })}
      listWidth="lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      list={
        <>
          {docs.isPending && <Loading />}
          {docs.data && (
            <DataTable label="Documents" rows={docs.data.items} columns={columns} rowKey={(d) => d.id} selectedKey={search.doc} onOpen={(d) => set({ doc: d.id })} empty={<EmptyState title="No documents in this collection" />} />
          )}
        </>
      }
      detail={doc ? <DocumentChunks ns={ns} doc={doc} /> : <EmptyState title="Pick a document">The chunks the engine made from it open here.</EmptyState>}
    />
  )
}

function DocumentChunks({ ns, doc }: { ns: string; doc: KbDocument }) {
  const chunks = useQuery({ queryKey: [ns, 'chunks', doc.id], queryFn: () => api.kb.chunks(ns, doc.id) })
  return (
    <DocumentFrame>
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[18px] font-bold">{doc.filename}</h2>
        <StatusBadge status={doc.status} />
        <div className="ml-auto">
          <Button size="sm" disabledReason={NOT_IN_DEMO}>Reprocess</Button>
        </div>
      </div>
      {doc.error && <p className="mt-3 border border-rejected bg-rejected-wash px-3 py-2 text-rejected">{doc.error}</p>}
      {chunks.data?.items.length === 0 && <p className="mt-4 text-ink-2">No chunks: the engine has not parsed this document.</p>}
      <ol aria-label="Chunks" className="mt-4 border-t border-rule">
        {chunks.data?.items.map((c) => (
          <li key={c.id} className="grid grid-cols-[2.5rem_1fr_auto] gap-3 border-b border-rule py-2.5">
            <Mono className="text-ink-3">#{c.index + 1}</Mono>
            <p className="max-w-[68ch]">{c.text}</p>
            <Mono className="text-ink-3">{c.tokens} tok</Mono>
          </li>
        ))}
      </ol>
    </DocumentFrame>
  )
}
