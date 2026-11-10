// The clock ages are measured against. The mock bootstrap pins it to the fixtures' "now"
// so demo data never looks stale; live mode keeps the real time.
let now: () => number = () => Date.now()

export function clockNow(): number {
  return now()
}

export function setClock(fixed: string | null): void {
  now = fixed ? () => Date.parse(fixed) : () => Date.now()
}
