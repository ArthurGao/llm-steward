import * as D from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import { ErrorNote } from '@/components/misc'
import { Button } from './button'

export function Dialog({ open, onOpenChange, title, description, children }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string; children: ReactNode }) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        <D.Content className="fixed top-1/2 left-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-1/2 border border-ink bg-paper p-6 shadow-[0_12px_40px_rgba(0,0,0,0.18)]">
          <D.Title className="text-[17px] font-bold">{title}</D.Title>
          {description && <D.Description className="mt-1 text-ink-2">{description}</D.Description>}
          <div className="mt-4">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}

/** A confirmation for an action that cannot be undone or changes what others see (rule 5):
 * Cancel, and the action itself, disabled while it runs; its error shows in the dialog. */
export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, danger = true, pending, error, onConfirm }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  danger?: boolean
  pending: boolean
  error: unknown
  onConfirm: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={title} description={description}>
      <div className="grid gap-3">
        {error ? <ErrorNote error={error} /> : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} disabled={pending} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </Dialog>
  )
}
