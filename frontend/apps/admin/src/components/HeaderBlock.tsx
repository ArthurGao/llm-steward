import type { ReactNode } from 'react'

export type HeaderRow = [label: string, value: ReactNode]

/** The RFC-style header every governed object opens with: label/value pairs in two mono columns. */
export function HeaderBlock({ left, right }: { left: HeaderRow[]; right: HeaderRow[] }) {
  const col = (rows: HeaderRow[], align: 'left' | 'right') => (
    <dl className={align === 'right' ? 'sm:text-right' : ''}>
      {rows.map(([label, value]) => (
        <div key={label} className="flex gap-2 sm:block">
          <dt className="inline text-ink-3">{label}: </dt>
          <dd className="inline text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  )
  return (
    <div className="grid gap-x-10 gap-y-1 border-b border-rule pb-4 font-mono text-[13px] leading-6 sm:grid-cols-2">
      {col(left, 'left')}
      {col(right, 'right')}
    </div>
  )
}
