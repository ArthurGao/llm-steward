import type { Role } from '@/api/types'
import { useSession } from './session'

export const ACTIONS = ['review', 'editKb', 'editMemory', 'eraseUser', 'manageJobs', 'manageKeys', 'manageSettings'] as const
export type Action = (typeof ACTIONS)[number]

const REVIEWER: ReadonlySet<Action> = new Set(['review', 'editKb'])

/** Design §9: viewer is read-only, reviewer reviews and edits KB, owner manages everything. */
export function can(role: Role, action: Action): boolean {
  if (role === 'owner') return true
  if (role === 'reviewer') return REVIEWER.has(action)
  return false
}

const NEEDS: Record<Action, string> = {
  review: 'Reviewing needs the reviewer or owner role',
  editKb: 'Editing knowledge needs the reviewer or owner role',
  editMemory: 'Changing memories needs the owner role',
  eraseUser: 'Erasing user data needs the owner role',
  manageJobs: 'Managing jobs and sync needs the owner role',
  manageKeys: 'Managing API keys needs the owner role',
  manageSettings: 'Changing settings needs the owner role',
}

/** null when allowed, otherwise the reason to show on the disabled control. */
export function useDenied(action: Action): string | null {
  const session = useSession()
  return session && can(session.role, action) ? null : NEEDS[action]
}

export const NOT_IN_DEMO = 'Not available in the demo'
