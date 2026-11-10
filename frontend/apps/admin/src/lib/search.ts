// Coercers for TanStack Router validateSearch: anything unexpected becomes undefined.

export function str(v: unknown): string | undefined {
  // The router JSON-parses search values, so ?cursor=25 arrives as the number 25.
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return typeof v === 'string' && v ? v : undefined
}

export function oneOf<T extends string>(values: readonly T[]) {
  return (v: unknown): T | undefined => values.find((x) => x === v)
}
