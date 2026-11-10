import { wordDiff } from './diff'

/** Unified diff of two statements: the removed line, then the added one, changed words marked. */
export function DiffView({ label, before, after }: { label: string; before: string; after: string }) {
  const parts = wordDiff(before, after)
  return (
    <figure className="border border-rule font-mono text-[13px] leading-6">
      <figcaption className="border-b border-rule bg-wash px-3.5 py-1.5 text-ink-2">{label}</figcaption>
      <p className="bg-del-wash px-3.5 py-1.5 text-del">
        <span aria-hidden>- </span>
        <span className="sr-only">Removed: </span>
        {parts.filter((p) => p.op !== 'ins').map((p, i) => (p.op === 'del' ? <del key={i} className="bg-del/15 decoration-1">{p.text}</del> : <span key={i}>{p.text}</span>))}
      </p>
      <p className="bg-ins-wash px-3.5 py-1.5 text-ins">
        <span aria-hidden>+ </span>
        <span className="sr-only">Added: </span>
        {parts.filter((p) => p.op !== 'del').map((p, i) => (p.op === 'ins' ? <ins key={i} className="bg-ins/15 no-underline">{p.text}</ins> : <span key={i}>{p.text}</span>))}
      </p>
    </figure>
  )
}
