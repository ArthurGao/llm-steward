import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import type { EntryHistory, EntryVersion, KbEntry } from '@/api/types'
import { DiffView } from '@/components/DiffView'
import { useDocumentStack } from '@/components/DocumentStack'
import { HeaderBlock } from '@/components/HeaderBlock'
import { ErrorNote, Loading, Mono } from '@/components/misc'
import { DocumentError, DocumentFrame } from '@/components/SplitView'
import { StatusBadge, RiskBadge } from '@/components/StatusBadge'
import { Button, linkButton } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field, Textarea } from '@/components/ui/field'
import { age, pct, stamp } from '@/lib/format'

type Brief = EntryHistory['obsoletes'][number]

/** A link-styled button that opens another entry on the document stack. */
export function EntryRef({ ns, entry }: { ns: string; entry: Pick<Brief, 'id' | 'key' | 'version'> }) {
  const { push } = useDocumentStack()
  return (
    <button type="button" className={linkButton} onClick={() => push({ id: entry.id, label: `${entry.key} v${entry.version}`, render: () => <EntryDocument ns={ns} id={entry.id} /> })}>
      {entry.key} v{entry.version}
    </button>
  )
}

export function EntryDocument({ ns, id, actions = false }: { ns: string; id: string; actions?: boolean }) {
  const entry = useQuery({ queryKey: [ns, 'entry', id], queryFn: () => api.kb.entry(ns, id) })
  const history = useQuery({ queryKey: [ns, 'entryHistory', id], queryFn: () => api.kb.history(ns, id) })
  const collections = useQuery({ queryKey: [ns, 'collections'], queryFn: () => api.kb.collections(ns) })
  const { push } = useDocumentStack()

  if (entry.isPending) return <Loading />
  if (entry.isError) return <DocumentError error={entry.error} notFound="Entry not found" hint="It may belong to another namespace." />
  const e = entry.data
  const h = history.data
  const collection = collections.data?.items.find((c) => c.id === e.collection_id)?.name ?? '—'

  return (
    <DocumentFrame>
      <HeaderBlock
        left={[
          ['Collection', collection],
          ['Status', <StatusBadge key="s" status={e.status} className="font-mono" />],
          ['Risk', <RiskBadge key="r" risk={e.risk_level} />],
          ...(h?.obsoletes.length ? [['Obsoletes', <span key="o" className="inline-flex gap-2">{h.obsoletes.map((o) => <EntryRef key={o.id} ns={ns} entry={o} />)}</span>] as [string, React.ReactNode]] : []),
          ...(h?.obsoleted_by ? [['Obsoleted by', <EntryRef key="ob" ns={ns} entry={h.obsoleted_by} />] as [string, React.ReactNode]] : []),
        ]}
        right={[
          ['Key', `${e.key} v${e.version}`],
          ['Updated', stamp(e.updated_at)],
          ['Verified by', e.verified_by ?? '—'],
          ['Sync', <StatusBadge key="y" status={e.sync_status} className="font-mono" />],
        ]}
      />
      <h2 className="mt-6 text-[22px] leading-snug font-bold">{e.title}</h2>
      <p className="mt-2 max-w-[68ch] text-[16px] leading-7">{e.content}</p>
      <p className="mt-3 font-mono text-[13px] text-ink-2">
        {e.hits} hits · helpful {e.helpful_rate === null ? '—' : pct(e.helpful_rate)} · origin {e.origin.replace('_', ' ')}
        {e.review_due_at && ` · review due ${age(e.review_due_at)}`}
        {e.valid_to && ` · valid until ${stamp(e.valid_to)}`}
      </p>
      {actions && <EntryActions ns={ns} entry={e} />}
      {h && (
        <section className="mt-8">
          <h3 className="mb-2 font-bold">Versions</h3>
          <ol aria-label="Versions" className="border-t border-rule">
            {h.versions.map((v, i) => (
              <li key={v.version} className="border-b border-rule">
                <button
                  type="button"
                  disabled={i === 0}
                  onClick={() => push({ id: `${e.id}@${v.version}`, label: `${e.key} v${v.version}`, render: () => <VersionDocument entry={e} versions={h.versions} index={i} /> })}
                  className="grid w-full grid-cols-[3rem_1fr_auto] items-baseline gap-3 py-2 text-left enabled:hover:bg-wash disabled:cursor-default"
                >
                  <Mono className={i === 0 ? 'font-medium' : 'text-link'}>v{v.version}</Mono>
                  <span className="truncate text-ink-2">{v.content}</span>
                  <Mono className="text-ink-3">{i === 0 ? 'current' : age(v.changed_at)}</Mono>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
    </DocumentFrame>
  )
}

function VersionDocument({ entry, versions, index }: { entry: KbEntry; versions: EntryVersion[]; index: number }) {
  const { push } = useDocumentStack()
  const version = versions[index]!
  const newer = versions[index - 1]
  const current = index === 0
  const forward = newer && (
    <button
      type="button"
      className={linkButton}
      onClick={() => push({ id: `${entry.id}@${newer.version}`, label: `${entry.key} v${newer.version}`, render: () => <VersionDocument entry={entry} versions={versions} index={index - 1} /> })}
    >
      {entry.key} v{newer.version}
    </button>
  )
  return (
    <DocumentFrame>
      <HeaderBlock
        left={[
          ['Status', current ? 'Current' : 'Historic'],
          ['Obsoleted by', forward ?? '—'],
        ]}
        right={[
          ['Key', `${entry.key} v${version.version}`],
          ['Changed', stamp(version.changed_at)],
          ['By', version.actor_id ?? version.actor_type],
        ]}
      />
      <h2 className="mt-6 text-[22px] leading-snug font-bold">{version.title}</h2>
      <p className="mt-2 max-w-[68ch] text-[16px] leading-7">{version.content}</p>
      {newer && (
        <div className="mt-6">
          <DiffView label={`v${version.version} → v${newer.version}`} before={version.content} after={newer.content} />
        </div>
      )}
    </DocumentFrame>
  )
}

function EntryActions({ ns, entry }: { ns: string; entry: KbEntry }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(entry.content)
  const [deprecating, setDeprecating] = useState(false)
  const [reason, setReason] = useState('')
  const invalidate = () => qc.invalidateQueries({ queryKey: [ns] })
  const save = useMutation({ mutationFn: () => api.kb.patch(ns, entry.id, { content: text.trim() }), onSuccess: () => { setEditing(false); void invalidate() } })
  const deprecate = useMutation({ mutationFn: () => api.kb.deprecate(ns, entry.id, reason.trim()), onSuccess: () => { setDeprecating(false); void invalidate() } })
  const effective = entry.status === 'active' || entry.status === 'verified'
  const frozen = !effective && entry.status !== 'draft' ? `A ${entry.status} entry is history and cannot change` : null

  return (
    <div className="mt-5">
      {save.data?.candidate && (
        <p role="status" className="mb-3 border border-awaiting bg-awaiting-wash px-3 py-2 text-awaiting">
          Sent to review as{' '}
          <Link to="/$ns/review" params={{ ns }} search={{ selected: save.data.candidate.id }}>
            {save.data.candidate.key}
          </Link>
          . Verified knowledge only changes through review; the current text stays live until then.
        </p>
      )}
      {editing ? (
        <div className="grid gap-3 border border-rule p-4">
          <Field label="Statement" hint={entry.status === 'verified' ? 'This entry is verified, so your edit becomes a candidate for review.' : 'Saving creates a new version.'}>
            {(id) => <Textarea id={id} value={text} onChange={(e) => setText(e.target.value)} />}
          </Field>
          {save.error && <ErrorNote error={save.error} />}
          <div className="flex gap-2">
            <Button variant="primary" disabled={!text.trim() || text.trim() === entry.content || save.isPending} onClick={() => save.mutate()}>
              {entry.status === 'verified' ? 'Submit for review' : 'Save new version'}
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button disabledReason={frozen} onClick={() => { setText(entry.content); setEditing(true) }}>Edit</Button>
          <Button variant="danger" disabledReason={effective ? null : 'Only active or verified knowledge can be deprecated'} onClick={() => setDeprecating(true)}>
            Deprecate…
          </Button>
        </div>
      )}
      <Dialog open={deprecating} onOpenChange={setDeprecating} title={`Deprecate ${entry.key}?`} description="It stops being retrieved and is removed from the engine. The entry and its history stay here.">
        <div className="grid gap-3">
          <Field label="Reason">{(id) => <Textarea id={id} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this is no longer true" />}</Field>
          {deprecate.error && <ErrorNote error={deprecate.error} />}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDeprecating(false)}>Cancel</Button>
            <Button variant="danger" disabled={!reason.trim() || deprecate.isPending} onClick={() => deprecate.mutate()}>Deprecate</Button>
          </div>
        </div>
      </Dialog>
    </div>
  )
}
