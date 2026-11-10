import { ArrowLeft } from 'lucide-react'
import type { ReactNode } from 'react'
import { ApiError } from '@/api/client'
import { ErrorNote } from './misc'
import { cn } from '@/lib/cn'

/**
 * List on the left, the open document on the right. Below lg the two take turns:
 * with a document open only the document shows, with a back link to the list.
 */
export function SplitView({ list, detail, open, onBack, backLabel = 'Back to the list', listWidth = 'lg:grid-cols-[minmax(340px,440px)_1fr]' }: {
  list: ReactNode
  detail: ReactNode
  open: boolean
  onBack: () => void
  backLabel?: string
  listWidth?: string
}) {
  return (
    <div className={cn('grid min-h-0 flex-1 lg:grid lg:overflow-hidden', listWidth)}>
      <section aria-label="List" className={cn('min-w-0 border-rule lg:block lg:overflow-y-auto lg:border-r', open && 'hidden')}>
        {list}
      </section>
      <section aria-label="Document" className={cn('min-w-0 lg:overflow-y-auto', !open && 'hidden lg:block')}>
        {open && (
          <button type="button" onClick={onBack} className="flex items-center gap-1.5 px-5 pt-4 text-link lg:hidden">
            <ArrowLeft aria-hidden className="size-4" strokeWidth={1.75} />
            {backLabel}
          </button>
        )}
        {detail}
      </section>
    </div>
  )
}

export function DocumentFrame({ children }: { children: ReactNode }) {
  return <article className="max-w-[920px] px-5 py-6 sm:px-8 lg:py-7">{children}</article>
}

/** A document that failed to load: "not found" only for a 404, the real error otherwise. */
export function DocumentError({ error, notFound, hint }: { error: unknown; notFound: string; hint: string }) {
  return (
    <DocumentFrame>
      {error instanceof ApiError && error.status === 404 ? (
        <>
          <p className="font-bold">{notFound}</p>
          <p className="text-ink-2">{hint}</p>
        </>
      ) : (
        <ErrorNote error={error} />
      )}
    </DocumentFrame>
  )
}
