import { cn } from '@/lib/cn'

export interface Rung {
  label: string
  state: 'done' | 'now' | 'next' | 'stopped'
  note?: string
}

/** A standards-track ladder: where this object is on its way to trusted knowledge. */
export function StatusLadder({ rungs }: { rungs: Rung[] }) {
  return (
    <ol aria-label="Lifecycle" className="flex flex-col text-[13px] sm:flex-row">
      {rungs.map((r) => (
        <li
          key={r.label}
          aria-current={r.state === 'now' ? 'step' : undefined}
          className={cn(
            '-mt-px flex-1 border px-3 py-2 first:mt-0 sm:mt-0 sm:-ml-px sm:min-w-28 sm:first:ml-0',
            r.state === 'done' && 'border-rule text-ink',
            r.state === 'now' && 'relative z-10 border-awaiting font-bold text-awaiting',
            r.state === 'next' && 'border-rule text-ink-3',
            r.state === 'stopped' && 'relative z-10 border-rejected font-bold text-rejected',
          )}
        >
          <span className="block">{r.label}</span>
          {r.note && <span className="block text-[12.5px] font-normal text-ink-2">{r.note}</span>}
        </li>
      ))}
    </ol>
  )
}
