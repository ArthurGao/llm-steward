import { useEffect, useState } from 'react'
import { cx, errorText, useStewardApi, type Connection, type Memory } from './api'

export interface MemoryPanelProps {
  connection: Connection
  className?: string
}

/**
 * What the assistant remembers about this user (their user token decides who): read it, and
 * forget what should not be kept. Deleting asks first, inline (no browser dialog). A "don't ask
 * me about …" rule shows here too: forgetting it undoes it.
 */
export function MemoryPanel({ connection, className }: MemoryPanelProps) {
  const api = useStewardApi(connection)
  const [items, setItems] = useState<Memory[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true // an answer for a panel already gone (or reconnected) is dropped
    api
      .memories()
      .then((page) => {
        if (!live) return
        setItems(page.items)
        setNext(page.next_cursor ?? null)
      })
      .catch((e: unknown) => live && setError(errorText(e, 'Could not load your memories')))
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [api])

  async function more(cursor: string) {
    setLoading(true)
    setError(null)
    try {
      const page = await api.memories(cursor)
      setItems((shown) => [...shown, ...page.items])
      setNext(page.next_cursor ?? null)
    } catch (e) {
      setError(errorText(e, 'Could not load your memories'))
    } finally {
      setLoading(false)
    }
  }

  async function forget(id: string) {
    setError(null)
    try {
      await api.forget(id)
      setItems((shown) => shown.filter((m) => m.id !== id))
      setConfirming(null)
    } catch (e) {
      setError(errorText(e, 'Could not forget it'))
    }
  }

  return (
    <section className={cx('lsw-panel', className)} aria-label="What the assistant remembers about you">
      <h2 className="lsw-panel-title">What the assistant remembers about you</h2>
      {items.length === 0 && !loading && !error && <p className="lsw-hint">Nothing yet.</p>}
      <ul className="lsw-list">
        {items.map((m) => (
          <li key={m.id} className="lsw-item">
            <span className="lsw-item-text">{m.content}</span>
            {confirming === m.id ? (
              <span className="lsw-item-actions">
                <button type="button" className="lsw-button lsw-button-danger" onClick={() => void forget(m.id)}>
                  Forget it
                </button>
                <button type="button" className="lsw-button lsw-button-quiet" onClick={() => setConfirming(null)}>
                  Keep
                </button>
              </span>
            ) : (
              <button type="button" className="lsw-button lsw-button-quiet" aria-label={`Forget: ${m.content}`} onClick={() => setConfirming(m.id)}>
                Forget
              </button>
            )}
          </li>
        ))}
      </ul>
      {loading && <p className="lsw-hint">Loading…</p>}
      {next && !loading && (
        <button type="button" className="lsw-button" onClick={() => void more(next)}>
          Show more
        </button>
      )}
      {error && (
        <p role="alert" className="lsw-error">
          {error}
        </p>
      )}
    </section>
  )
}
