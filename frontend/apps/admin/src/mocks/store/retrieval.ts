// A toy retrieval pipeline for the demo: keyword overlap stands in for the vector and
// BM25 stages, then reciprocal-rank fusion and trust weighting as in design §6.
import type { KbEntry, Memory, RetrievalHit, RetrievalRequest, RetrievalResult } from '@/api/types'

export const GAP_THRESHOLD = 0.35
const RRF_K = 60
const STOP = new Set(['a', 'an', 'the', 'is', 'are', 'do', 'does', 'we', 'i', 'to', 'of', 'for', 'in', 'on', 'how', 'what', 'who', 'can', 'my', 'our'])
// Stand-in for semantic similarity: a few hand-written neighbours.
const NEAR: Record<string, string[]> = {
  refund: ['reimburs', 'chargeback'],
  approv: ['sign'],
  sso: ['saml', 'oidc', 'sign-on'],
  hour: ['time', 'availabl'],
  webhook: ['callback'],
}

function stem(word: string): string {
  return word.replace(/(ing|ed|al|es|s|e)$/, '')
}

export function tokens(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9$-]+/)
    .filter((w) => w && !STOP.has(w))
    .map(stem)
}

interface Doc {
  hit: Omit<RetrievalHit, 'score'>
  terms: Set<string>
}

function keywordScore(q: string[], d: Doc): number {
  return q.filter((t) => d.terms.has(t)).length / q.length
}

function vectorScore(q: string[], d: Doc): number {
  const matched = q.filter((t) => d.terms.has(t) || (NEAR[t] ?? []).some((n) => d.terms.has(n))).length
  return Math.round((matched / q.length) * 0.92 * 1000) / 1000
}

function ranked(docs: Doc[], score: (d: Doc) => number): RetrievalHit[] {
  return docs
    .map((d) => ({ ...d.hit, score: score(d) }))
    .filter((h) => h.score > 0)
    .sort((a, b) => b.score - a.score || a.key.localeCompare(b.key))
    .slice(0, 8)
}

export function runRetrieval(
  req: RetrievalRequest,
  entries: KbEntry[],
  memories: Memory[],
  now: string,
): Omit<RetrievalResult, 'log_id'> {
  const q = tokens(req.query)
  const docs: Doc[] = []
  if (req.sources.includes('kb')) {
    for (const e of entries) {
      // Safety net (rule 4): only effective knowledge inside its validity window.
      if (e.status !== 'active' && e.status !== 'verified') continue
      if (e.valid_to && e.valid_to <= now) continue
      docs.push({
        hit: { source: 'kb', id: e.id, key: `${e.key} v${e.version}`, text: e.content, trust: e.status === 'verified' ? 1 : 0.85 },
        terms: new Set(tokens(`${e.title} ${e.content}`)),
      })
    }
  }
  if (req.sources.includes('memory')) {
    for (const m of memories) {
      if (m.status !== 'active') continue
      if (req.user_id && m.user_id !== req.user_id) continue
      docs.push({
        hit: { source: 'memory', id: m.id, key: m.id, text: m.content, trust: Math.round(0.7 * m.confidence * 100) / 100 },
        terms: new Set(tokens(m.content)),
      })
    }
  }
  if (q.length === 0) {
    return { query: req.query, stages: { vector: [], keyword: [], rrf: [], final: [] }, context: '', degraded: [], latency_ms: 0, gap: true }
  }

  const vector = ranked(docs, (d) => vectorScore(q, d))
  const keyword = ranked(docs, (d) => keywordScore(q, d))
  const fused = new Map<string, RetrievalHit>()
  for (const list of [vector, keyword]) {
    list.forEach((h, rank) => {
      const prev = fused.get(h.id)
      fused.set(h.id, { ...h, score: (prev?.score ?? 0) + 1 / (RRF_K + rank + 1) })
    })
  }
  const rrf = [...fused.values()].sort((a, b) => b.score - a.score).map((h) => ({ ...h, score: Math.round(h.score * 10_000) / 10_000 }))
  const vScore = new Map(vector.map((h) => [h.id, h.score]))
  const kScore = new Map(keyword.map((h) => [h.id, h.score]))
  const final = rrf
    .map((h) => {
      const v = vScore.get(h.id) ?? 0
      const k = kScore.get(h.id) ?? 0
      return { ...h, score: Math.round((0.6 * v + 0.4 * k) * h.trust * 1000) / 1000 }
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, req.top_k ?? 8)

  const gap = (final[0]?.score ?? 0) < GAP_THRESHOLD
  const kb = final.filter((h) => h.source === 'kb')
  const mem = final.filter((h) => h.source === 'memory')
  const context = [
    mem.length ? `## About the user\n${mem.map((h) => `- ${h.text}`).join('\n')}` : '',
    kb.length ? `## Knowledge base\n${kb.map((h) => `- [${h.key}] ${h.text}`).join('\n')}` : '',
  ]
    .filter(Boolean)
    .join('\n\n')
  const latency = 180 + q.length * 37 + docs.length * 3
  return { query: req.query, stages: { vector, keyword, rrf, final }, context, degraded: [], latency_ms: latency, gap }
}
