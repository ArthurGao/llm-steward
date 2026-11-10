import * as T from '@radix-ui/react-tabs'
import type { ReactNode } from 'react'

export function Tabs({ value, onValueChange, tabs, children, label }: { value: string; onValueChange: (v: string) => void; tabs: { value: string; label: ReactNode }[]; children: ReactNode; label: string }) {
  return (
    <T.Root value={value} onValueChange={onValueChange} className="flex min-h-0 flex-1 flex-col">
      <T.List aria-label={label} className="flex shrink-0 gap-6 overflow-x-auto border-b border-rule px-5 sm:px-7">
        {tabs.map((t) => (
          <T.Trigger key={t.value} value={t.value} className="-mb-px border-b-2 border-transparent py-2.5 font-medium text-ink-2 hover:text-ink data-[state=active]:border-ink data-[state=active]:text-ink">
            {t.label}
          </T.Trigger>
        ))}
      </T.List>
      {children}
    </T.Root>
  )
}

export const TabPanel = T.Content
