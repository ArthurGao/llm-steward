import { Button } from './ui/button'

/**
 * Cursor paging. With a total (the mock's offset cursors) it reads "1–25 of 38" with previous /
 * next; without one (the backend's keyset cursors, which only go forward) it offers first / next.
 */
export function Pager({ cursor, next, total, shown, onChange }: { cursor: string | null; next: string | null; total: number | null; shown: number; onChange: (cursor: string | null) => void }) {
  if (total === null) {
    if (!cursor && !next) return null
    return (
      <nav aria-label="Pagination" className="flex items-center gap-3 px-5 py-3 text-[13px] text-ink-2 sm:px-7">
        <span className="font-mono tabular-nums">{shown} shown</span>
        <Button size="sm" disabled={!cursor} onClick={() => onChange(null)}>
          First page
        </Button>
        <Button size="sm" disabled={!next} onClick={() => onChange(next)}>
          Next
        </Button>
      </nav>
    )
  }
  const start = Number(cursor ?? 0)
  if (total <= shown && start === 0) return null
  const prev = start === 0 ? null : String(Math.max(0, start - 25))
  return (
    <nav aria-label="Pagination" className="flex items-center gap-3 px-5 py-3 text-[13px] text-ink-2 sm:px-7">
      <span className="font-mono tabular-nums">
        {start + 1}–{start + shown} of {total}
      </span>
      <Button size="sm" disabled={start === 0} onClick={() => onChange(prev === '0' ? null : prev)}>
        Previous
      </Button>
      <Button size="sm" disabled={!next} onClick={() => onChange(next)}>
        Next
      </Button>
    </nav>
  )
}
