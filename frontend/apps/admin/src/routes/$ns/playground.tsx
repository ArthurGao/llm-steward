import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '@/api/endpoints'
import { str } from '@/lib/search'
import type { RetrievalHit, RetrievalResult } from '@/api/types'
import { ErrorNote, Mono, PageTitle } from '@/components/misc'
import { Button } from '@/components/ui/button'
import { Field, Input, Select, Textarea } from '@/components/ui/field'
import { cn } from '@/lib/cn'
import { age, score } from '@/lib/format'

export const Route = createFileRoute('/$ns/playground')({
  validateSearch: (s: Record<string, unknown>): { q?: string } => ({ q: str(s.q) }),
  component: Playground,
})

const GAP = 0.35

function Playground() {
  const { ns } = Route.useParams()
  const { q } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const qc = useQueryClient()
  const [query, setQuery] = useState(q ?? '')
  const [userId, setUserId] = useState('')
  const [sources, setSources] = useState({ memory: true, kb: true })
  const run = useMutation({
    mutationFn: (text: string) => api.retrieval.run(ns, { query: text, user_id: userId.trim() || undefined, sources: (['memory', 'kb'] as const).filter((s) => sources[s]) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [ns, 'retrievalLogs'] }),
  })
  const logs = useQuery({ queryKey: [ns, 'retrievalLogs'], queryFn: () => api.retrieval.logs(ns, { limit: 8 }) })

  // A question arriving by link (?q=) runs once.
  const ran = useRef<string | null>(null)
  useEffect(() => {
    if (q && ran.current !== q) {
      ran.current = q
      setQuery(q)
      run.mutate(q)
    }
  }, [q, run])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    void navigate({ search: { q: query.trim() } })
    if (q === query.trim()) run.mutate(query.trim())
  }

  return (
    <>
      <PageTitle title="Retrieval Playground" count="Run a query through every stage and see what the agent would get" />
      <form onSubmit={submit} className="grid gap-3 border-b border-rule px-5 py-4 sm:px-7 lg:grid-cols-[minmax(0,1fr)_12rem_auto_auto] lg:items-end">
        <Field label="Question">{(id) => <Input id={id} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Who approves a $700 refund?" />}</Field>
        <Field label="As user (optional)">{(id) => <Input id={id} value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="u_8f2a" className="font-mono" />}</Field>
        <fieldset className="flex h-9 items-center gap-4">
          <legend className="sr-only">Sources</legend>
          {(['memory', 'kb'] as const).map((s) => (
            <label key={s} className="flex items-center gap-1.5">
              <input type="checkbox" className="size-4 accent-ink" checked={sources[s]} onChange={(e) => setSources((x) => ({ ...x, [s]: e.target.checked }))} />
              {s === 'kb' ? 'Knowledge' : 'Memories'}
            </label>
          ))}
        </fieldset>
        <Button type="submit" variant="primary" disabled={!query.trim() || run.isPending || (!sources.memory && !sources.kb)}>
          {run.isPending ? 'Retrieving…' : 'Retrieve'}
        </Button>
      </form>
      {run.error && <div className="px-5 pt-4 sm:px-7"><ErrorNote error={run.error} /></div>}
      {run.data ? (
        <Result ns={ns} result={run.data} />
      ) : (
        <section className="px-5 py-6 sm:px-7">
          <h2 className="font-bold">Recent queries</h2>
          <ul className="mt-2 max-w-3xl">
            {logs.data?.items.map((l) => (
              <li key={l.id} className="grid grid-cols-[1fr_auto_auto] gap-4 border-b border-rule py-2">
                <Link to="/$ns/playground" params={{ ns }} search={{ q: l.query }}>{l.query}</Link>
                <Mono className={l.gap ? 'text-rejected' : 'text-ink-2'}>{score(l.top_score)}</Mono>
                <Mono className="text-ink-3">{age(l.created_at)}</Mono>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function Stage({ title, hits, note }: { title: string; hits: RetrievalHit[] | null; note: string }) {
  return (
    <section className="min-w-0 border-rule lg:border-r lg:pr-5 lg:last:border-r-0">
      <h3 className="font-bold">{title}</h3>
      <p className="text-[12.5px] text-ink-2">{note}</p>
      {hits === null ? (
        <p className="mt-2 text-ink-3">Not reported by this backend</p>
      ) : hits.length === 0 ? (
        <p className="mt-2 text-ink-3">No hits</p>
      ) : (
        <ol className="mt-2">
          {hits.slice(0, 5).map((h) => (
            <li key={h.id} className="border-b border-rule py-1.5">
              <div className="flex justify-between gap-2 font-mono text-[12.5px]">
                <span className={h.source === 'kb' ? 'text-ink' : 'text-ink-2'}>{h.source === 'kb' ? h.key : `mem ${h.id.slice(-4)}`}</span>
                <span>{h.score.toFixed(title === 'RRF' ? 4 : 2)}</span>
              </div>
              <p className="line-clamp-2 text-[13px] text-ink-2">{h.text}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function Result({ ns, result }: { ns: string; result: RetrievalResult }) {
  const top = result.stages.final[0]?.score ?? 0
  return (
    <div className="grid gap-6 px-5 py-5 sm:px-7">
      <p className="font-mono text-[13px] text-ink-2">
        {result.stages.final.length} results · top {score(top)} · {result.latency_ms} ms{result.degraded.length > 0 && ` · degraded: ${result.degraded.join(', ')}`}
      </p>
      {result.gap && <Gap ns={ns} result={result} />}
      <div className="grid gap-6 lg:grid-cols-4 lg:gap-5">
        <Stage title="Vector" hits={result.stages.vector} note="semantic similarity" />
        <Stage title="Keyword" hits={result.stages.keyword} note="full-text match" />
        <Stage title="RRF" hits={result.stages.rrf} note="reciprocal-rank fusion" />
        <Stage title="Final" hits={result.stages.final} note="× trust: verified 1.0, active 0.85" />
      </div>
      <section>
        <h3 className="font-bold">Context the agent receives</h3>
        <pre aria-label="Assembled context" className={cn('mt-2 max-w-4xl overflow-x-auto border border-rule bg-wash px-4 py-3 font-mono text-[13px] leading-6 whitespace-pre-wrap', !result.context && 'text-ink-3')}>
          {result.context || 'Nothing passed the filters, so the agent gets no context.'}
        </pre>
      </section>
    </div>
  )
}

function Gap({ ns, result }: { ns: string; result: RetrievalResult }) {
  const qc = useQueryClient()
  const collections = useQuery({ queryKey: [ns, 'collections'], queryFn: () => api.kb.collections(ns) })
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [collection, setCollection] = useState('')
  const add = useMutation({
    mutationFn: () => api.retrieval.addToKb(ns, result.log_id, { title, content, collection_id: collection || undefined }),
    onSuccess: () => qc.invalidateQueries({ queryKey: [ns] }),
  })
  const top = result.stages.final[0]?.score ?? 0
  return (
    <section className="max-w-3xl border border-awaiting bg-awaiting-wash px-4 py-3">
      <h3 className="font-bold text-awaiting">No confident answer</h3>
      <p className="text-ink-2">
        The best result scored {score(top)}, under the {GAP} threshold, so this question is logged as a knowledge gap.
      </p>
      {add.data?.candidate ? (
        <p role="status" className="mt-2 text-ink">
          Sent to review as{' '}
          <Link to="/$ns/review" params={{ ns }} search={{ selected: add.data.candidate.id }}>{add.data.candidate.key}</Link>. It becomes knowledge only after a reviewer approves it.
        </p>
      ) : open ? (
        <div className="mt-3 grid gap-3 bg-paper p-4">
          <Field label="Title">{(id) => <Input id={id} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
          <Field label="Answer">{(id) => <Textarea id={id} value={content} onChange={(e) => setContent(e.target.value)} />}</Field>
          <Field label="Collection">
            {(id) => (
              <Select id={id} value={collection} onChange={(e) => setCollection(e.target.value)}>
                {collections.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            )}
          </Field>
          {add.error && <ErrorNote error={add.error} />}
          <div className="flex gap-2">
            <Button variant="primary" disabled={!title.trim() || !content.trim() || add.isPending} onClick={() => add.mutate()}>Send to review</Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="mt-3">
          <Button onClick={() => setOpen(true)}>Add the missing answer to the KB</Button>
        </div>
      )}
    </section>
  )
}
