import { useId, useState } from 'react'
import { cx, errorText, useStewardApi, type Connection, type DecisionResult, type Suggestion } from './api'

export interface SuggestionCardProps {
  connection: Connection
  /** One of the `suggestions[]` that `/v1/retrieve` or `/v1/episodes` returned for this user. */
  suggestion: Suggestion
  /** After the user decided (the card stays, showing the outcome). */
  onDecided?: (result: DecisionResult) => void
  className?: string
}

/**
 * The confirmation card (design §5): "Save this?" with what was drafted. The user can offer it to
 * the team or keep it as their own memory, edit it (then it stays theirs: the backend lets a user
 * share only what they were asked about), or say no — optionally "stop suggesting things to me".
 * Whatever the user says, it is never verified knowledge: only a reviewer verifies.
 */
export function SuggestionCard({ connection, suggestion, onDecided, className }: SuggestionCardProps) {
  const api = useStewardApi(connection)
  const radio = useId()
  const draft = suggestion.draft as { title?: string; content?: string }
  const [phase, setPhase] = useState<'asking' | 'editing' | 'rejecting'>('asking')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState(draft.title ?? '')
  const [content, setContent] = useState(draft.content ?? '')
  const [scope, setScope] = useState<'personal' | 'team'>('team')
  const [stop, setStop] = useState(false)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const edited = title !== (draft.title ?? '') || content !== (draft.content ?? '')
  const keep = edited ? 'personal' : scope // an edit is kept just for the user

  async function decide(run: () => Promise<DecisionResult>, said: string) {
    setBusy(true)
    setError(null)
    try {
      const result = await run()
      setOutcome(said)
      onDecided?.(result)
    } catch (e) {
      setError(errorText(e, 'Something went wrong'))
    } finally {
      setBusy(false)
    }
  }

  const accept = () =>
    decide(
      () => api.accept(suggestion.id, edited ? { scope: keep, draft: { title, content } } : { scope: keep }),
      keep === 'personal' ? 'Saved for you.' : 'Shared with your team for review.',
    )
  const reject = () =>
    decide(() => api.reject(suggestion.id, stop), stop ? 'Got it — no more suggestions.' : 'Dismissed.')

  return (
    <section className={cx('lsw-card', className)} aria-label="Suggested knowledge">
      <p className="lsw-card-lead">Save this for later?</p>
      {phase === 'editing' ? (
        <div className="lsw-card-edit">
          <label className="lsw-field">
            <span className="lsw-label">Title</span>
            <input className="lsw-input" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="lsw-field">
            <span className="lsw-label">Text</span>
            <textarea className="lsw-input" rows={4} value={content} onChange={(e) => setContent(e.target.value)} />
          </label>
        </div>
      ) : (
        <blockquote className="lsw-card-draft">
          {title && <strong className="lsw-card-title">{title}</strong>}
          <span>{content}</span>
        </blockquote>
      )}

      {outcome !== null ? (
        <p role="status" className="lsw-card-outcome">
          {outcome}
        </p>
      ) : phase === 'rejecting' ? (
        <div className="lsw-card-actions">
          <label className="lsw-check">
            <input type="checkbox" checked={stop} onChange={(e) => setStop(e.target.checked)} />
            Stop suggesting things to me
          </label>
          <button type="button" className="lsw-button" disabled={busy} onClick={reject}>
            Dismiss
          </button>
          <button type="button" className="lsw-button lsw-button-quiet" disabled={busy} onClick={() => setPhase('asking')}>
            Back
          </button>
        </div>
      ) : (
        <>
          <fieldset className="lsw-scope" disabled={edited}>
            <legend className="lsw-scope-legend">Keep it</legend>
            <label className="lsw-check">
              <input type="radio" name={radio} checked={keep === 'team'} onChange={() => setScope('team')} />
              For my team (a reviewer checks it first)
            </label>
            <label className="lsw-check">
              <input type="radio" name={radio} checked={keep === 'personal'} onChange={() => setScope('personal')} />
              Just for me
            </label>
            {edited && <p className="lsw-hint">An edited version is kept just for you.</p>}
          </fieldset>
          <div className="lsw-card-actions">
            <button type="button" className="lsw-button lsw-button-primary" disabled={busy} onClick={accept}>
              Save
            </button>
            {phase !== 'editing' && (
              <button type="button" className="lsw-button" disabled={busy} onClick={() => setPhase('editing')}>
                Edit
              </button>
            )}
            <button type="button" className="lsw-button lsw-button-quiet" disabled={busy} onClick={() => setPhase('rejecting')}>
              No thanks
            </button>
          </div>
        </>
      )}
      {error && (
        <p role="alert" className="lsw-error">
          {error}
        </p>
      )}
    </section>
  )
}
