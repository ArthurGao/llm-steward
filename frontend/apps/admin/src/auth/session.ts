import { useSyncExternalStore } from 'react'

// The Admin has no login and no accounts (decided 2026-11-10). Mock mode (the demo) needs no
// session. Live mode talks to a backend with its static admin token (STEWARD_ADMIN_TOKEN); the
// backend URL and the token are kept for this browser tab only (sessionStorage), never longer.
export const MODE: 'mock' | 'live' = import.meta.env.VITE_API_MODE === 'live' ? 'live' : 'mock'

export type Session = { mode: 'mock' } | { mode: 'live'; baseUrl: string; token: string }

const KEY = 'steward.connection'
const MOCK: Session = { mode: 'mock' }
const listeners = new Set<() => void>()
let memoryFallback: Session | null = null
let cached: { raw: string | null; value: Session | null } = { raw: null, value: null }

function read(): string | null {
  try {
    return window.sessionStorage.getItem(KEY)
  } catch {
    return null // storage blocked (private mode): the in-memory fallback keeps this tab working
  }
}

/** The stored backend connection, whatever the mode (the live API reads it per request). */
export function getConnection(): Extract<Session, { mode: 'live' }> | null {
  const raw = read()
  if (raw === null) return memoryFallback?.mode === 'live' ? memoryFallback : null
  if (raw !== cached.raw) {
    try {
      cached = { raw, value: JSON.parse(raw) as Session }
    } catch {
      cached = { raw, value: null }
    }
  }
  return cached.value?.mode === 'live' ? cached.value : null
}

export function getSession(): Session | null {
  return MODE === 'mock' ? MOCK : getConnection()
}

function emit() {
  for (const l of listeners) l()
}

/**
 * The backend's base URL as the admin token may be sent to it: http(s), no credentials, query or
 * fragment; a trailing slash dropped. Anything else throws, so the token never leaves for it.
 */
export function backendUrl(raw: string): string {
  let url: URL
  try {
    url = new URL(raw.trim())
  } catch {
    throw new Error('Not a URL')
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('The backend URL must be http or https')
  if (url.username || url.password || url.search || url.hash) throw new Error('The backend URL takes no credentials, query or fragment')
  return `${url.origin}${url.pathname}`.replace(/\/+$/, '')
}

export function connect(baseUrl: string, token: string): void {
  const session: Session = { mode: 'live', baseUrl: backendUrl(baseUrl), token }
  memoryFallback = session
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    // storage blocked: the in-memory fallback keeps this tab working
  }
  emit()
}

export function disconnect(): void {
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
