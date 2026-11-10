import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Join class names; later Tailwind utilities win over earlier conflicting ones (w-36 over w-full). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
