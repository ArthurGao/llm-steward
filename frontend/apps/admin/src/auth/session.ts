import { useSyncExternalStore } from 'react'
import type { Session } from '@/api/types'

const KEY = 'steward.session'
const listeners = new Set<() => void>()

// Demo auth only: the session lives in sessionStorage, which can be unavailable
// (private mode, blocked storage), so every access is guarded.
let memoryFallback: Session | null = null
let cached: { raw: string | null; value: Session | null } = { raw: null, value: null }

function read(): string | null {
  try {
    return window.sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function getSession(): Session | null {
  const raw = read()
  if (raw === null) return memoryFallback
  if (raw !== cached.raw) {
    try {
      cached = { raw, value: JSON.parse(raw) as Session }
    } catch {
      cached = { raw, value: null }
    }
  }
  return cached.value
}

function emit() {
  for (const l of listeners) l()
}

export function setSession(session: Session): void {
  memoryFallback = session
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    // storage blocked: the in-memory fallback keeps this tab working
  }
  emit()
}

export function clearSession(): void {
  memoryFallback = null
  try {
    window.sessionStorage.removeItem(KEY)
  } catch {
    // nothing to clear
  }
  emit()
}

export function useSession(): Session | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    getSession,
    getSession,
  )
}
