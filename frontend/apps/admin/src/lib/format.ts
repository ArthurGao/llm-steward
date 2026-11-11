import { clockNow } from './clock'

export function age(iso: string, now: number = clockNow()): string {
  const diff = now - Date.parse(iso)
  const future = diff < 0
  const m = Math.floor(Math.abs(diff) / 60_000)
  let s: string
  if (m < 1) return 'now'
  else if (m < 60) s = `${m}m`
  else if (m < 48 * 60) s = `${Math.round(m / 60)}h`
  else if (m < 60 * 24 * 60) s = `${Math.round(m / 1440)}d`
  else return iso.slice(0, 10)
  return future ? `in ${s}` : s
}

export function stamp(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

export function day(iso: string): string {
  return iso.slice(0, 10)
}

export function bytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`
  if (n >= 1_000) return `${Math.round(n / 1_000)} KB`
  return `${n} B`
}

export function pct(x: number | null): string {
  if (x === null) return '—'
  const v = x * 100
  return `${v < 10 ? v.toFixed(1) : Math.round(v)}%`
}

export function score(x: number | null): string {
  return x === null ? '—' : x.toFixed(2)
}
