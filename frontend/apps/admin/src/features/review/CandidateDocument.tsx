import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { api } from '@/api/endpoints'
import type { Candidate, DecisionResult } from '@/api/types'
import { DiffView } from '@/components/DiffView'
import { HeaderBlock, type HeaderRow } from '@/components/HeaderBlock'
import { ErrorNote, Kbd, Loading } from '@/components/misc'
import { DocumentError, DocumentFrame } from '@/components/SplitView'
import { StatusBadge, RiskBadge } from '@/components/StatusBadge'
import { StatusLadder, type Rung } from '@/components/StatusLadder'
import { Button } from '@/components/ui/button'
import { Field, Textarea } from '@/components/ui/field'
import { EntryRef } from '@/features/kb/EntryDocument'
import { stamp } from '@/lib/format'

function ladder(c: Candidate): Rung[] {
  const decided = c.status !== 'pending'
  const rungs: Rung[] = [
    { label: c.origin === 'promote' ? 'Promoted' : c.origin === 'user_suggestion' ? 'Suggested' : 'Extracted', state: 'done', note: c.origin.replace('_', ' ') },
    { label: 'Screened', state: 'done', note: `${c.risk_level} risk` },
  ]
  if (c.stage === 'awaiting_user') rungs.push({ label: 'User confirms', state: decided ? 'done' : 'now', note: c.requested_for_user ?? undefined })
  if (c.status === 'rejected') return [...rungs, { label: 'Rejected', state: 'stopped', note: c.decided_by ?? undefined }]
  if (c.status === 'expired') return [...rungs, { label: 'Expired', state: 'stopped' }]
  rungs.push({ label: 'Review', state: decided ? 'done' : c.stage === 'awaiting_review' ? 'now' : 'next', note: decided ? (c.decided_by ?? undefined) : undefined })
  // The end user's own confirmation makes it active; a reviewer's decision verifies it (rule 5).
  const confirmedByUser = decided && c.decided_by !== null && c.decided_by === c.requested_for_user
  const settled = decided ? (confirmedByUser ? 'Active' : 'Verified') : c.stage === 'awaiting_review' ? 'Verified' : 'Active'
  rungs.push({ label: c.target_type === 'memory' ? 'Saved' : settled, state: decided ? 'done' : 'next' })
  if (c.target_type === 'kb_entry') rungs.push({ label: 'Synced', state: decided ? 'now' : 'next', note: decided ? 'pending' : undefined })
  return rungs
}

export function CandidateDocument({ ns, id }: { ns: string; id: string }) {
  const q = useQuery({ queryKey: [ns, 'candidate', id], queryFn: () => api.candidates.get(ns, id) })
  const collections = useQuery({ queryKey: [ns, 'collections'], queryFn: () => api.kb.collections(ns) })
  const [outcome, setOutcome] = useState<DecisionResult | null>(null)

  if (q.isPending) return <Loading />
  if (q.isError) return <DocumentError error={q.error} notFound="Candidate not found" hint="The link may be old, or the candidate belongs to another namespace." />
  const c = q.data
  const collection = collections.data?.items.find((x) => x.id === c.collection_id)?.name
  const left: HeaderRow[] = [
    ['Collection', collection ?? (c.target_type === 'memory' ? `memory of ${c.requested_for_user}` : '—')],
    ['Status', <StatusBadge key="s" status={c.status === 'pending' ? c.stage : c.status} className="font-mono" />],
    ['Risk', <span key="r" className="inline-flex flex-wrap gap-x-2"><RiskBadge risk={c.risk_level} />{c.screening.reasons.length > 0 && <span className="text-ink-2">({c.screening.reasons.join(', ')})</span>}</span>],
  ]
  if (c.similar) left.push([c.op === 'UPDATE' ? 'Obsoletes' : 'Similar to', <span key="o"><EntryRef ns={ns} entry={{ id: c.similar.entry_id, key: c.similar.key, version: c.similar.version }} /> <span className="text-ink-2">({c.similar.status})</span></span>])
  const right: HeaderRow[] = [
    ['Candidate', c.key],
    ['Proposed', stamp(c.created_at)],
    ...(c.similar ? [['Similarity', c.similar.similarity.toFixed(2)] as HeaderRow] : []),
    ['Source', c.episode_id ?? c.origin.replace('_', ' ')],
  ]

  return (
    <DocumentFrame>
      <HeaderBlock left={left} right={right} />
      <h2 className="mt-6 max-w-[60ch] text-[22px] leading-snug font-bold">{c.draft.content}</h2>
      <p className="mt-1 text-ink-2">Title: {c.draft.title}</p>
      {c.reason && <p className="mt-3 max-w-[72ch] text-ink-2">Screening note: {c.reason}</p>}
      {c.status === 'pending' && c.op === 'UPDATE' && c.similar && (
        <p className="mt-2 max-w-[72ch] text-ink-2">
          Approving makes this {c.similar.key} v{c.similar.version + 1} and marks v{c.similar.version} obsoleted; v{c.similar.version} stays readable in the entry’s history. Nothing governed is deleted.
        </p>
      )}
      {c.screening.pii.length > 0 && (
        <p className="mt-2 max-w-[72ch] text-rejected">Personal data found and redacted before review: {c.screening.pii.join(', ')}.</p>
      )}
      {c.stage === 'awaiting_user' && c.status === 'pending' && (
        <p className="mt-2 max-w-[72ch] text-ink-2">
          Waiting for <span className="font-mono">{c.requested_for_user}</span> to confirm. Their confirmation would make it active, never verified; a reviewer can decide now.
        </p>
      )}
      <div className="mt-5">
        {c.similar ? (
          <DiffView label={`${c.similar.key} v${c.similar.version} → proposed`} before={c.similar.content} after={c.draft.content} />
        ) : (
          <DiffView label="New entry" before="" after={c.draft.content} />
        )}
      </div>
      <div className="mt-5">
        <StatusLadder rungs={ladder(c)} />
      </div>
      {c.status === 'pending' ? (
        <Decide ns={ns} candidate={c} onDecided={setOutcome} />
      ) : (
        <Outcome ns={ns} candidate={c} result={outcome?.id === c.id ? outcome : null} />
      )}
    </DocumentFrame>
  )
}

function Outcome({ ns, candidate: c, result }: { ns: string; candidate: Candidate; result: DecisionResult | null }) {
  const entry = result?.entry
  return (
    <p role="status" className={`mt-5 border px-3 py-2 ${c.status === 'accepted' ? 'border-verified bg-verified-wash text-verified' : 'border-rule bg-wash text-ink-2'}`}>
      {c.status === 'accepted' ? 'Accepted' : c.status === 'rejected' ? 'Rejected' : 'Expired'} by {c.decided_by ?? 'nobody'}
      {c.decided_at && ` at ${stamp(c.decided_at)}`}.
      {entry && (
        <>
          {' '}
          <Link to="/$ns/kb" params={{ ns }} search={{ entry: entry.id }}>
            {entry.key} is now v{entry.version}, {entry.status}
          </Link>{' '}
          · sync {entry.sync_status}.
        </>
      )}
      {c.decision_note && <> Reason: {c.decision_note}</>}
    </p>
  )
}

function typingInField(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  return !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
}

function Decide({ ns, candidate: c, onDecided }: { ns: string; candidate: Candidate; onDecided: (r: DecisionResult) => void }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'idle' | 'edit' | 'reject'>('idle')
  const [text, setText] = useState(c.draft.content)
  const [note, setNote] = useState('')
  const [noteError, setNoteError] = useState<string | null>(null)
  const decide = useMutation({
    mutationFn: (v: { verdict: 'accept' | 'reject'; draft?: string; note?: string }) =>
      api.candidates.decide(ns, c.id, v.verdict, { draft: v.draft ? { content: v.draft } : undefined, note: v.note }),
    onSuccess: (r) => {
      onDecided(r)
      qc.setQueryData([ns, 'candidate', c.id], r)
      void qc.invalidateQueries({ queryKey: [ns], predicate: (q) => q.queryKey[1] !== 'candidate' || q.queryKey[2] !== c.id })
    },
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typingInField(e) || e.metaKey || e.ctrlKey || e.altKey || decide.isPending) return
      if (e.key === 'a' && mode === 'idle') decide.mutate({ verdict: 'accept' })
      else if (e.key === 'e') setMode('edit')
      else if (e.key === 'r') setMode('reject')
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode, decide])

  const reject = () => {
    if (!note.trim()) {
      setNoteError('A reason is required')
      return
    }
    decide.mutate({ verdict: 'reject', note: note.trim() })
  }

  return (
    <div className="mt-6">
      {decide.error && <div className="mb-3"><ErrorNote error={decide.error} /></div>}
      {mode === 'edit' && (
        <div className="mb-3 grid gap-3 border border-rule p-4">
          <Field label="Statement to approve">{(id) => <Textarea id={id} autoFocus value={text} onChange={(e) => setText(e.target.value)} />}</Field>
          <div className="flex gap-2">
            <Button variant="primary" disabled={!text.trim() || decide.isPending} onClick={() => decide.mutate({ verdict: 'accept', draft: text.trim() })}>Save and approve</Button>
            <Button variant="ghost" onClick={() => setMode('idle')}>Cancel</Button>
          </div>
        </div>
      )}
      {mode === 'reject' && (
        <div className="mb-3 grid gap-3 border border-rule p-4">
          <Field label="Reason for rejecting" error={noteError} hint="Stored with the decision and shown in the activity log.">
            {(id) => <Textarea id={id} autoFocus value={note} onChange={(e) => { setNote(e.target.value); setNoteError(null) }} />}
          </Field>
          <div className="flex gap-2">
            <Button variant="danger" disabled={decide.isPending} onClick={reject}>Confirm rejection</Button>
            <Button variant="ghost" onClick={() => setMode('idle')}>Cancel</Button>
          </div>
        </div>
      )}
      {mode === 'idle' && (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate({ verdict: 'accept' })}>
            Approve<Kbd>A</Kbd>
          </Button>
          <Button onClick={() => setMode('edit')}>
            Edit &amp; approve<Kbd>E</Kbd>
          </Button>
          <Button onClick={() => setMode('reject')}>
            Reject…<Kbd>R</Kbd>
          </Button>
        </div>
      )}
    </div>
  )
}
