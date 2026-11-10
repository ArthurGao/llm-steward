import { useQuery } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import { oneOf, str } from '@/lib/search'
import type { Candidate } from '@/api/types'
import { DocumentStack } from '@/components/DocumentStack'
import { EmptyState, ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { Pager } from '@/components/Pager'
import { SplitView } from '@/components/SplitView'
import { SearchBox, Select } from '@/components/ui/field'
import { CandidateDocument } from '@/features/review/CandidateDocument'
import { cn } from '@/lib/cn'
import { age } from '@/lib/format'

interface Search {
  selected?: string
  stage?: 'awaiting_review' | 'awaiting_user' | 'any'
  risk?: 'high' | 'medium' | 'low'
  origin?: Candidate['origin']
  q?: string
  cursor?: string
}

export const Route = createFileRoute('/$ns/review')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    selected: str(s.selected),
    stage: oneOf(['awaiting_user', 'any'] as const)(s.stage),
    risk: oneOf(['high', 'medium', 'low'] as const)(s.risk),
    origin: oneOf(['extraction', 'promote', 'user_suggestion', 'api'] as const)(s.origin),
    q: str(s.q),
    cursor: str(s.cursor),
  }),
  component: Review,
})

const RISK_TEXT = { high: 'text-rejected', medium: 'text-awaiting', low: 'text-ink-2' } as const

function Review() {
  const { ns } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const set = (patch: Partial<Search>) => void navigate({ search: (s) => ({ ...s, cursor: undefined, ...patch }) })
  const stage = search.stage ?? 'awaiting_review'
  const query = { stage: stage === 'any' ? undefined : stage, risk_level: search.risk, origin: search.origin, q: search.q, cursor: search.cursor }
  const list = useQuery({ queryKey: [ns, 'candidates', query], queryFn: () => api.candidates.list(ns, query) })
  const stats = useQuery({ queryKey: [ns, 'stats'], queryFn: () => api.stats(ns) })
  const filtered = !!(search.risk || search.origin || search.q)
  const open = list.data?.items.find((x) => x.id === search.selected)

  return (
    <>
      <PageTitle
        title="Review Queue"
        count={stats.data && `${stats.data.pending_reviews} awaiting review · ${stats.data.awaiting_user} awaiting users`}
      />
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <Select aria-label="Stage" value={stage} onChange={(e) => set({ stage: e.target.value === 'awaiting_review' ? undefined : (e.target.value as Search['stage']) })}>
          <option value="awaiting_review">Awaiting review</option>
          <option value="awaiting_user">Awaiting user</option>
          <option value="any">All pending</option>
        </Select>
        <Select aria-label="Risk" value={search.risk ?? ''} onChange={(e) => set({ risk: (e.target.value || undefined) as Search['risk'] })}>
          <option value="">Any risk</option>
          <option value="high">High risk</option>
          <option value="medium">Medium risk</option>
          <option value="low">Low risk</option>
        </Select>
        <Select aria-label="Origin" value={search.origin ?? ''} onChange={(e) => set({ origin: (e.target.value || undefined) as Search['origin'] })}>
          <option value="">Any origin</option>
          <option value="extraction">Extraction</option>
          <option value="promote">Promote</option>
          <option value="user_suggestion">User suggestion</option>
          <option value="api">API</option>
        </Select>
        <SearchBox aria-label="Search candidates" placeholder="Search" value={search.q} onCommit={(v) => set({ q: v })} />
        <span className="ml-auto hidden text-[12.5px] text-ink-2 lg:inline">
          Keys: <Mono>A</Mono> approve · <Mono>E</Mono> edit · <Mono>R</Mono> reject · <Mono>Esc</Mono> close
        </span>
      </div>
      <SplitView
        open={!!search.selected}
        onBack={() => set({ selected: undefined })}
        backLabel="Back to the queue"
        list={
          <>
            {list.isPending && <Loading />}
            {list.isError && <div className="p-5"><ErrorNote error={list.error} /></div>}
            {list.data && list.data.items.length === 0 && (
              <EmptyState
                title={filtered ? 'Nothing matches these filters' : 'The queue is clear'}
                action={filtered ? <button type="button" className="text-link" onClick={() => set({ risk: undefined, origin: undefined, q: undefined })}>Clear filters</button> : undefined}
              >
                {filtered ? undefined : 'Every candidate has been decided. New ones arrive as agents propose knowledge.'}
              </EmptyState>
            )}
            {list.data && list.data.items.length > 0 && (
              <ul aria-label="Candidates">
                {list.data.items.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      aria-current={search.selected === c.id || undefined}
                      onClick={() => set({ selected: c.id })}
                      className={cn('grid w-full grid-cols-[3.25rem_1fr] gap-x-3 border-b border-rule px-5 py-3 text-left hover:bg-wash sm:px-7', search.selected === c.id && 'bg-select hover:bg-select')}
                    >
                      <Mono className="row-span-2 pt-px text-ink-3">{c.key}</Mono>
                      <span className={cn('font-medium', search.selected === c.id && 'font-bold')}>{c.draft.title}</span>
                      <span className="text-[13px] text-ink-2">
                        <span className={cn('font-medium capitalize', RISK_TEXT[c.risk_level])}>{c.risk_level}</span>
                        {c.screening.pii.length > 0 && ' · PII'} · {c.origin.replace('_', ' ')}
                        {c.requested_for_user && ` · ${c.requested_for_user}`} · <span className="font-mono">{age(c.created_at)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {list.data && <Pager cursor={search.cursor ?? null} next={list.data.next_cursor} total={list.data.total} shown={list.data.items.length} onChange={(cursor) => void navigate({ search: (s) => ({ ...s, cursor: cursor ?? undefined }) })} />}
          </>
        }
        detail={
          search.selected ? (
            <DocumentStack baseLabel={open ? `${open.key} ${open.draft.title}` : 'Candidate'} resetKey={search.selected}>
              <CandidateDocument ns={ns} id={search.selected} />
            </DocumentStack>
          ) : (
            <EmptyState title="Pick a candidate">Its draft, the knowledge it would change and why it was flagged open here.</EmptyState>
          )
        }
      />
    </>
  )
}
