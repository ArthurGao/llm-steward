import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

export interface Column<T> {
  key: string
  header: string
  cell: (row: T) => ReactNode
  align?: 'left' | 'right'
  className?: string
  /** Hidden below the md breakpoint. */
  wide?: boolean
}

/** A dense, keyboard-navigable table: j / k or arrows move, Enter opens. */
export function DataTable<T>({ rows, columns, rowKey, selectedKey, onOpen, empty, label }: {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  selectedKey?: string | null
  onOpen?: (row: T) => void
  empty?: ReactNode
  label: string
}) {
  const body = useRef<HTMLTableSectionElement>(null)
  useEffect(() => {
    if (!selectedKey) return
    body.current?.querySelector<HTMLElement>(`[data-key="${CSS.escape(selectedKey)}"]`)?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedKey])

  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    const tr = e.currentTarget
    if (e.key === 'Enter') onOpen?.(row)
    else if (e.key === 'j' || e.key === 'ArrowDown') (tr.nextElementSibling as HTMLElement | null)?.focus()
    else if (e.key === 'k' || e.key === 'ArrowUp') (tr.previousElementSibling as HTMLElement | null)?.focus()
    else return
    e.preventDefault()
  }

  if (rows.length === 0) return <>{empty}</>
  return (
    <table aria-label={label} className="w-full border-collapse text-left">
      <thead>
        <tr className="border-b border-ink text-[12.5px] text-ink-2">
          {columns.map((c) => (
            <th key={c.key} scope="col" className={cn('px-3 py-2 font-medium first:pl-5 last:pr-5 sm:first:pl-7', c.align === 'right' && 'text-right', c.wide && 'hidden md:table-cell', c.className)}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody ref={body}>
        {rows.map((row) => {
          const k = rowKey(row)
          return (
            <tr
              key={k}
              data-key={k}
              tabIndex={onOpen ? 0 : undefined}
              aria-selected={selectedKey === k || undefined}
              onClick={onOpen ? () => onOpen(row) : undefined}
              onKeyDown={onOpen ? (e) => onKey(e, row) : undefined}
              className={cn('border-b border-rule align-top', onOpen && 'cursor-pointer hover:bg-wash', selectedKey === k && 'bg-select hover:bg-select')}
            >
              {columns.map((c) => (
                <td key={c.key} className={cn('px-3 py-2.5 first:pl-5 last:pr-5 sm:first:pl-7', c.align === 'right' && 'text-right', c.wide && 'hidden md:table-cell', c.className)}>
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
