import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

const control = 'border border-rule bg-paper px-2.5 text-ink hover:border-ink-3 focus-visible:border-link'

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cn(control, 'h-9 w-full', className)} {...rest} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cn(control, 'min-h-24 w-full py-2 leading-6', className)} {...rest} />
})

/** A search input that commits on Enter (empty clears) and resets when the URL value changes. */
export function SearchBox({ value, onCommit, className, ...rest }: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'defaultValue'> & { value: string | undefined; onCommit: (v: string | undefined) => void }) {
  return (
    <Input
      key={value ?? ''}
      defaultValue={value}
      className={cn('w-44', className)}
      onKeyDown={(e) => e.key === 'Enter' && onCommit(e.currentTarget.value.trim() || undefined)}
      {...rest}
    />
  )
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(control, 'h-9 pr-7', className)} {...rest}>
      {children}
    </select>
  )
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string | null; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className="grid gap-1">
      <label htmlFor={id} className="text-[13px] font-medium">
        {label}
      </label>
      {children(id)}
      {hint && !error && <p className="text-[12.5px] text-ink-2">{hint}</p>}
      {error && <p role="alert" className="text-[12.5px] text-rejected">{error}</p>}
    </div>
  )
}
