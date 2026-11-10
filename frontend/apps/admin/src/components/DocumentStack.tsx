import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { CornerLeftUp } from 'lucide-react'
import { cn } from '@/lib/cn'

export interface StackedDoc {
  id: string
  label: string
  render: () => ReactNode
}

interface StackApi {
  push: (doc: StackedDoc) => void
  depth: number
}

const Ctx = createContext<StackApi>({ push: () => {}, depth: 0 })

/** Open a related document (an older version, the entry it obsoletes) on top of the current one. */
export function useDocumentStack(): StackApi {
  return useContext(Ctx)
}

/**
 * The Standards Track signature: following Obsoletes / Obsoleted-by or a version opens that
 * document above the current one, which folds into a spine you can click to return to.
 * Escape closes the top document.
 */
export function DocumentStack({ baseLabel, resetKey, children }: { baseLabel: string; resetKey: string; children: ReactNode }) {
  const [stack, setStack] = useState<StackedDoc[]>([])
  const [prevKey, setPrevKey] = useState(resetKey)
  if (prevKey !== resetKey) {
    setPrevKey(resetKey)
    setStack([])
  }
  const push = useCallback((doc: StackedDoc) => setStack((s) => (s.some((d) => d.id === doc.id) ? s : [...s, doc])), [])
  const api = useMemo(() => ({ push, depth: stack.length }), [push, stack.length])

  useEffect(() => {
    if (stack.length === 0) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setStack((s) => s.slice(0, -1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stack.length])

  const spines = [{ id: '__base', label: baseLabel }, ...stack.slice(0, -1)]
  const top = stack.at(-1)
  return (
    <Ctx.Provider value={api}>
      {top && (
        <nav aria-label="Open documents" className="flex flex-col">
          {spines.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setStack((st) => st.slice(0, i))}
              className="flex items-center gap-2 border-b border-rule bg-wash px-5 py-1.5 text-left font-mono text-[12.5px] text-ink-2 hover:bg-select sm:px-8"
            >
              <CornerLeftUp aria-hidden className="size-3.5 shrink-0 text-ink-3" strokeWidth={1.75} />
              <span className="truncate">{s.label}</span>
            </button>
          ))}
        </nav>
      )}
      <div key={top?.id ?? '__base'} className={cn(top && 'animate-[stack-in_180ms_cubic-bezier(0.16,1,0.3,1)]')}>
        {top ? top.render() : children}
      </div>
    </Ctx.Provider>
  )
}
