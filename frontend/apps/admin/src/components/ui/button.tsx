import * as Tooltip from '@radix-ui/react-tooltip'
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'

const VARIANT: Record<Variant, string> = {
  primary: 'border-ink bg-ink text-paper hover:bg-[#2b2b2b]',
  secondary: 'border-ink bg-paper text-ink hover:bg-wash',
  ghost: 'border-transparent bg-transparent text-ink hover:bg-wash',
  danger: 'border-rejected bg-paper text-rejected hover:bg-rejected-wash',
}

/** Classes for things that act like buttons but are links (or link-styled buttons). */
export const linkButton = 'text-link underline-offset-3 hover:underline'
export function buttonClass(variant: Variant = 'secondary', size: 'sm' | 'md' = 'md'): string {
  return cn('inline-flex items-center justify-center gap-1.5 border font-medium whitespace-nowrap no-underline transition-colors duration-100', size === 'md' ? 'h-9 px-3.5 text-[14px]' : 'h-8 px-2.5 text-[13px]', VARIANT[variant])
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: 'sm' | 'md'
  /** When set, the button is disabled and this explains why on hover and focus. */
  disabledReason?: string | null
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', disabledReason, disabled, icon, className, children, type = 'button', onClick, ...rest },
  ref,
) {
  const off = disabled || !!disabledReason
  const button = (
    <button
      ref={ref}
      type={type}
      aria-disabled={off || undefined}
      disabled={disabled}
      onClick={off ? (e) => e.preventDefault() : onClick}
      className={cn(
        buttonClass(variant, size),
        off && 'cursor-not-allowed border-rule bg-wash text-ink-3 hover:bg-wash',
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  )
  if (!disabledReason) return button
  return (
    <Tooltip.Root delayDuration={150}>
      <Tooltip.Trigger asChild>{button}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content sideOffset={6} className="z-50 max-w-64 bg-ink px-2.5 py-1.5 text-[12.5px] text-paper">
          {disabledReason}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
})
