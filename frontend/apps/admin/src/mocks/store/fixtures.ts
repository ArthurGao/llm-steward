// Deterministic demo data. Everything here is synthetic: the namespaces, people,
// policies and numbers are invented for the demo and labelled "Demo data" in the UI.
import type {
  ApiKey,
  Candidate,
  Change,
  Chunk,
  Collection,
  Episode,
  Job,
  KbDocument,
  KbEntry,
  Memory,
  Namespace,
  NamespaceSettings,
  ProviderStatus,
  RetrievalLog,
  SyncRef,
} from '@/api/types'

export const NOW = '2026-11-09T09:00:00.000Z'
const NOW_MS = Date.parse(NOW)

/** ISO timestamp `minutes` before NOW. */
export function ago(minutes: number): string {
  return new Date(NOW_MS - minutes * 60_000).toISOString()
}
const H = 60
const D = 24 * H

/** Ids of the objects the demo story and the tests refer to. */
export const FIX = {
  refundEntry: 'kbe_0142',
  oldRefundEntry: 'kbe_0098',
  refundCandidate: 'cand_01jc4q',
  hoursCandidate: 'cand_01jc4j',
  piiCandidate: 'cand_01jc4h',
  promotableMemory: 'mem_0007',
  storyEpisode: 'ep_01jc3z',
} as const

export interface NsData {
  namespace: Namespace
  memories: Memory[]
  episodes: Episode[]
  collections: Collection[]
  entries: KbEntry[]
  documents: KbDocument[]
  chunks: Record<string, Chunk[]>
  candidates: Candidate[]
  changes: Change[] // newest first
  jobs: Job[]
  syncRefs: SyncRef[]
  providers: ProviderStatus[]
  retrievalLogs: RetrievalLog[] // newest first
  keys: ApiKey[]
  settings: NamespaceSettings
  seq: number
}

// ---------------------------------------------------------------- memories

type MemSeed = [id: number, user: string, content: string, type: string, importance: number, minutesAgo: number, extra?: Partial<Memory>]

const MEMORY_SEEDS: MemSeed[] = [
  [1, 'u_8f2a', 'Prefers replies in English with short bullet points', 'preference', 0.82, 3 * D, { pinned: true, metadata: { key: 'reply_style' } }],
  [2, 'u_8f2a', 'Works in the Australia/Sydney time zone', 'profile', 0.7, 9 * D, { metadata: { key: 'timezone', value: 'Australia/Sydney' } }],
  [3, 'u_8f2a', 'Runs Postgres 15 in production', 'semantic', 0.6, 40 * D, { status: 'superseded', superseded_by: 'mem_0004', valid_to: ago(2 * D) }],
  [4, 'u_8f2a', 'Runs Postgres 16 in production after the October upgrade', 'semantic', 0.66, 2 * D],
  [5, 'u_8f2a', 'Is on the Enterprise plan with 120 seats', 'profile', 0.9, 12 * D, { pinned: true, metadata: { key: 'plan', value: 'Enterprise' } }],
  [6, 'u_8f2a', 'Asked twice about webhook retries this month', 'episodic', 0.35, 5 * D],
  [7, 'u_8f2a', 'Was told refunds over $500 now need a second support lead to approve', 'semantic', 0.74, 18 * H],
  [8, 'u_31c0', 'Billing contact is the finance team, not the account owner', 'semantic', 0.71, 6 * D],
  [9, 'u_31c0', 'Prefers phone follow-ups for outages', 'preference', 0.52, 15 * D, { metadata: { key: 'contact_channel', value: 'phone' } }],
  [10, 'u_31c0', 'Works 08:00–20:00 AEST and expects support in those hours', 'profile', 0.6, 4 * D, { metadata: { key: 'hours', value: '08:00–20:00 AEST' } }],
  [11, 'u_31c0', 'Integrates through the Python SDK', 'semantic', 0.58, 21 * D],
  [12, 'u_31c0', 'Escalated a chargeback in October', 'episodic', 0.4, 26 * D],
  [13, 'u_5d19', 'Prefers concise replies without greetings', 'preference', 0.64, 1 * D, { pinned: true, metadata: { key: 'reply_style' } }],
  [14, 'u_5d19', 'Is evaluating SSO via OIDC with Okta', 'semantic', 0.69, 3 * D],
  [15, 'u_5d19', 'Team uses the EU region', 'profile', 0.77, 30 * D, { metadata: { key: 'region', value: 'EU' } }],
  [16, 'u_5d19', 'Reported slow exports on large workspaces', 'episodic', 0.45, 8 * D],
  [17, 'u_a7e4', 'Manages twelve agents in the help-desk workspace', 'profile', 0.55, 11 * D, { metadata: { key: 'role', value: 'support lead' } }],
  [18, 'u_a7e4', 'Wants weekly digest emails on Mondays', 'preference', 0.5, 19 * D],
  [19, 'u_a7e4', 'Asked how to bulk-close stale tickets', 'episodic', 0.3, 2 * D],
  [20, 'u_a7e4', 'Uses the Zendesk importer for historical tickets', 'semantic', 0.62, 33 * D],
  [21, 'u_c2b8', 'On the Starter plan, considering an upgrade', 'profile', 0.6, 7 * D, { metadata: { key: 'plan', value: 'Starter' } }],
  [22, 'u_c2b8', 'Prefers email over chat', 'preference', 0.48, 14 * D, { metadata: { key: 'contact_channel', value: 'email' } }],
  [23, 'u_c2b8', 'Hit the API rate limit during a migration', 'episodic', 0.42, 6 * D],
  [24, 'u_c2b8', 'Needs invoices addressed to a subsidiary', 'semantic', 0.57, 16 * D],
  [25, 'u_e910', 'Works in the America/Denver time zone', 'profile', 0.66, 22 * D, { metadata: { key: 'timezone', value: 'America/Denver' } }],
  [26, 'u_e910', 'Builds an internal agent on the MCP endpoint', 'semantic', 0.73, 4 * D],
  [27, 'u_e910', 'Wants agent answers to cite knowledge-base entries', 'preference', 0.8, 9 * D, { pinned: true }],
  [28, 'u_e910', 'Asked whether drafts are ever sent to the engine', 'episodic', 0.38, 1 * D],
  [29, 'u_f03d', 'Admin of two workspaces: support and sales', 'profile', 0.6, 13 * D],
  [30, 'u_f03d', 'Rotates API keys every 90 days', 'semantic', 0.68, 17 * D],
  [31, 'u_f03d', 'Requested an audit export of review decisions', 'episodic', 0.44, 3 * D],
  [32, 'u_b6a1', 'Prefers dark mode screenshots in replies', 'preference', 0.2, 25 * D],
  [33, 'u_b6a1', 'Uses the webhook v1 endpoint', 'semantic', 0.61, 29 * D, { status: 'superseded', superseded_by: 'mem_0034', valid_to: ago(6 * D) }],
  [34, 'u_b6a1', 'Migrated webhooks to v2 in November', 'semantic', 0.63, 6 * D],
  [35, 'u_b6a1', 'Asked about the v1 webhook retirement date', 'episodic', 0.4, 6 * D],
  [36, 'u_8f2a', 'Asked for a Japanese invoice template', 'episodic', 0.25, 27 * D],
  [37, 'u_5d19', 'Needs SAML for the parent company', 'semantic', 0.71, 2 * D],
  [38, 'u_a7e4', 'Prefers macros over canned AI replies', 'preference', 0.47, 10 * D],
  [39, 'u_e910', 'Session agent noted the user was frustrated by repeated questions', 'episodic', 0.33, 40, { agent_id: 'agent_triage', session_id: 's_7c21' }],
  [40, 'u_f03d', 'Keeps sales and support knowledge separate', 'semantic', 0.6, 20 * D],
]

function memoryFromSeed([n, user, content, type, importance, minutesAgo, extra = {}]: MemSeed): Memory {
  const id = `mem_${String(n).padStart(4, '0')}`
  const created = ago(minutesAgo)
  return {
    id,
    content,
    user_id: user,
    agent_id: n % 5 === 0 ? 'agent_support' : null,
    session_id: n % 3 === 0 ? `s_${(n * 7919).toString(16).slice(-4)}` : null,
    memory_type: type,
    status: 'active',
    origin: n % 7 === 0 ? 'direct' : 'extraction',
    pinned: false,
    importance,
    confidence: Math.round((0.62 + ((n * 37) % 35) / 100) * 100) / 100,
    tags: [],
    metadata: {},
    valid_from: created,
    valid_to: null,
    created_at: created,
    updated_at: created,
    sync_status: 'synced',
    source_refs: [{ type: 'episode', id: `ep_${user.slice(2)}_${(n % 3) + 1}` }],
    superseded_by: null,
    ...extra,
  }
}

// ---------------------------------------------------------------- knowledge base

const COLLECTIONS: Omit<Collection, 'entry_count' | 'document_count'>[] = [
  { id: 'col_billing', name: 'Billing policy', default_risk: 'high', review_policy: { high: 'reviewer', medium: 'reviewer', low: 'reviewer' }, created_at: ago(120 * D) },
  { id: 'col_product', name: 'Product facts', default_risk: 'medium', review_policy: { high: 'reviewer', medium: 'reviewer', low: 'user_confirm' }, created_at: ago(118 * D) },
  { id: 'col_support', name: 'Support operations', default_risk: 'low', review_policy: { high: 'reviewer', medium: 'user_confirm', low: 'auto' }, created_at: ago(118 * D) },
  { id: 'col_security', name: 'Security & privacy', default_risk: 'high', review_policy: { high: 'reviewer', medium: 'reviewer', low: 'reviewer' }, created_at: ago(90 * D) },
]

type EntrySeed = [n: number, col: string, title: string, content: string, status: KbEntry['status'], risk: KbEntry['risk_level'], version: number, minutesAgo: number, extra?: Partial<KbEntry>]

const ENTRY_SEEDS: EntrySeed[] = [
  [98, 'col_billing', 'Refund approval (2024 policy)', 'All refunds require approval from a team lead.', 'deprecated', 'high', 2, 300 * D, { superseded_by: FIX.refundEntry, valid_to: ago(150 * D), sync_status: 'deleted' }],
  [142, 'col_billing', 'Refund approval threshold', 'Refunds over $1,000 require approval from a team lead.', 'verified', 'high', 3, 150 * D, { hits: 412, helpful_rate: 0.91, review_due_at: ago(-20 * D) }],
  [143, 'col_billing', 'Invoice currency', 'Invoices are issued in the currency of the billing address; AUD, USD, EUR and GBP are supported.', 'verified', 'medium', 1, 110 * D, { hits: 133, helpful_rate: 0.88 }],
  [144, 'col_billing', 'Chargeback ownership', 'Chargebacks are opened by finance; support only links the ticket.', 'active', 'medium', 1, 40 * D, { hits: 21, helpful_rate: 0.85 }],
  [145, 'col_billing', 'Proration on upgrade', 'Upgrades mid-cycle are prorated to the day; downgrades apply at the next renewal.', 'verified', 'medium', 2, 95 * D, { hits: 207, helpful_rate: 0.86 }],
  [146, 'col_billing', 'Subsidiary invoicing', 'An invoice can be addressed to a subsidiary when the parent account owner approves it in writing.', 'draft', 'high', 1, 2 * D],
  [201, 'col_product', 'Enterprise single sign-on', 'The Enterprise plan includes SSO via SAML 2.0.', 'verified', 'medium', 2, 80 * D, { hits: 188, helpful_rate: 0.84 }],
  [202, 'col_product', 'Starter plan seat limit', 'The Starter plan allows up to 10 seats.', 'verified', 'low', 1, 100 * D, { hits: 96, helpful_rate: 0.93 }],
  [203, 'col_product', 'Regions', 'Workspaces can be hosted in the US, EU or Australia regions; the region is chosen at creation.', 'verified', 'medium', 1, 70 * D, { hits: 64, helpful_rate: 0.9 }],
  [204, 'col_product', 'Webhook versions', 'Webhooks v1 and v2 are both available; v2 signs every request.', 'active', 'medium', 2, 45 * D, { hits: 58, helpful_rate: 0.71 }],
  [205, 'col_product', 'API rate limits', 'The API allows 600 requests per minute per key; bulk endpoints count as ten.', 'verified', 'medium', 3, 60 * D, { hits: 142, helpful_rate: 0.8 }],
  [206, 'col_product', 'Bulk ticket close', 'Admins can bulk-close tickets older than 30 days from the Views page.', 'active', 'low', 1, 20 * D, { hits: 17, helpful_rate: 0.76 }],
  [207, 'col_product', 'Zendesk importer', 'The Zendesk importer brings tickets, users and macros; attachments over 20 MB are skipped.', 'verified', 'low', 1, 55 * D, { hits: 39, helpful_rate: 0.87 }],
  [208, 'col_product', 'MCP endpoint', 'Agents can connect to the workspace over MCP; tools can propose knowledge but never publish it.', 'active', 'medium', 1, 10 * D, { hits: 12, helpful_rate: null }],
  [301, 'col_support', 'Support hours', 'Live support is available 09:00–17:00 AEST on business days.', 'active', 'low', 1, 200 * D, { hits: 301, helpful_rate: 0.62, review_due_at: ago(5 * D) }],
  [302, 'col_support', 'Escalation path', 'Severity 1 incidents page the on-call engineer within 15 minutes.', 'verified', 'medium', 2, 130 * D, { hits: 77, helpful_rate: 0.95 }],
  [303, 'col_support', 'Weekly digest', 'Support leads receive a weekly digest every Monday at 08:00 local time.', 'active', 'low', 1, 35 * D, { hits: 9, helpful_rate: 0.66 }],
  [304, 'col_support', 'Macro vs AI reply', 'Agents should prefer approved macros for billing questions; AI drafts need a human send.', 'verified', 'medium', 1, 25 * D, { hits: 44, helpful_rate: 0.89 }],
  [305, 'col_support', 'Phone follow-up', 'Phone follow-ups are offered for Enterprise outages only.', 'rejected', 'low', 1, 12 * D, { sync_status: 'deleted' }],
  [306, 'col_support', 'Stale ticket policy', 'Tickets without a reply for 14 days are closed automatically with a survey.', 'active', 'low', 1, 18 * D, { hits: 26, helpful_rate: 0.73 }],
  [401, 'col_security', 'Encryption at rest', 'Customer content is encrypted at rest with AES-256; keys rotate yearly.', 'verified', 'high', 1, 85 * D, { hits: 51, helpful_rate: 0.94 }],
  [402, 'col_security', 'PII in tickets', 'Agents must not paste card numbers or government ids into tickets; the screener redacts them.', 'verified', 'high', 2, 75 * D, { hits: 33, helpful_rate: 0.97 }],
  [403, 'col_security', 'API key rotation', 'API keys should be rotated at least every 90 days; revoked keys stop working immediately.', 'verified', 'high', 1, 65 * D, { hits: 29, helpful_rate: 0.9 }],
  [404, 'col_security', 'Audit export', 'Owners can export review decisions and the change log as CSV.', 'active', 'medium', 1, 15 * D, { hits: 6, helpful_rate: null }],
  [177, 'col_billing', 'Refund method', 'Refunds return to the original payment method within 5–10 business days.', 'verified', 'medium', 1, 50 * D, { hits: 120, helpful_rate: 0.85, sync_status: 'failed' }],
]

/** Earlier wordings, newest first, for entries with version > 1. */
const EARLIER_VERSIONS: Record<number, string[]> = {
  142: ['Refunds over $1,000 need a team lead to approve them.', 'Refunds over $2,000 require approval from a team lead.'],
  98: ['All refunds must be approved by a manager.'],
  145: ['Upgrades are prorated by month.'],
  201: ['The Enterprise plan includes SSO.'],
  204: ['Webhooks v1 are available.'],
  205: ['The API allows 600 requests per minute per key.', 'The API allows 300 requests per minute per key.'],
  302: ['Severity 1 incidents page the on-call engineer.'],
  402: ['Agents must not paste card numbers into tickets.'],
}

function entryFromSeed([n, col, title, content, status, risk, version, minutesAgo, extra = {}]: EntrySeed): KbEntry {
  const created = ago(minutesAgo)
  const verified = status === 'verified' || (status === 'deprecated' && n === 98)
  const effective = status === 'active' || status === 'verified'
  return {
    id: `kbe_${String(n).padStart(4, '0')}`,
    key: `KB-${String(n).padStart(4, '0')}`,
    collection_id: col,
    title,
    content,
    status,
    risk_level: risk,
    origin: n % 4 === 0 ? 'promoted_memory' : n % 3 === 0 ? 'import' : 'manual',
    author_type: n % 4 === 0 ? 'ai' : 'human',
    version,
    created_by: n % 4 === 0 ? 'ai' : 'owner@demo',
    verified_by: verified ? 'reviewer@demo' : null,
    verified_at: verified ? ago(minutesAgo - 60) : null,
    valid_from: created,
    valid_to: null,
    tags: [],
    source_refs: n % 4 === 0 ? [{ type: 'memory', id: `mem_${String((n % 40) + 1).padStart(4, '0')}` }] : [],
    created_at: created,
    updated_at: ago(Math.max(minutesAgo - 30 * D, 60)),
    superseded_by: null,
    sync_status: effective ? 'synced' : status === 'draft' ? 'pending' : 'deleted',
    hits: 0,
    helpful_rate: null,
    review_due_at: null,
    ...extra,
  }
}

const DOCUMENTS: KbDocument[] = [
  { id: 'doc_handbook', collection_id: 'col_support', filename: 'support-handbook-2026-10.pdf', mime_type: 'application/pdf', size_bytes: 1_842_311, status: 'ready', chunk_count: 6, sync_status: 'synced', created_at: ago(30 * D), error: null },
  { id: 'doc_pricing', collection_id: 'col_billing', filename: 'pricing-and-plans.md', mime_type: 'text/markdown', size_bytes: 18_204, status: 'ready', chunk_count: 4, sync_status: 'synced', created_at: ago(60 * D), error: null },
  { id: 'doc_dpa', collection_id: 'col_security', filename: 'data-processing-addendum.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size_bytes: 402_118, status: 'failed', chunk_count: 0, sync_status: 'failed', created_at: ago(3 * D), error: 'Engine timed out while parsing page 14 (retry scheduled)' },
]

const CHUNKS: Record<string, string[]> = {
  doc_handbook: [
    '1. Purpose. This handbook describes how the support team handles tickets, escalations and refunds.',
    '2. Refunds. Refunds over $500 require approval from a second support lead before they are issued.',
    '3. Hours. Live support runs 08:00–20:00 AEST, Monday to Friday.',
    '4. Escalation. Severity 1 incidents page the on-call engineer within 15 minutes.',
    '5. Macros. Prefer approved macros for billing questions.',
    '6. Closing. Tickets without a reply for 14 days close with a survey.',
  ],
  doc_pricing: [
    'Starter: up to 10 seats, email support.',
    'Team: up to 50 seats, chat support, SSO add-on.',
    'Enterprise: unlimited seats, SAML and OIDC single sign-on, 24/7 Sev-1 response.',
    'Upgrades are prorated to the day; downgrades apply at renewal.',
  ],
}

// ---------------------------------------------------------------- candidates

function candidate(c: Partial<Candidate> & Pick<Candidate, 'id' | 'key' | 'draft' | 'risk_level' | 'origin' | 'stage' | 'created_at'>): Candidate {
  return {
    target_type: 'kb_entry',
    op: 'ADD',
    target_id: null,
    collection_id: null,
    reason: null,
    status: 'pending',
    requested_for_user: null,
    decided_by: null,
    decided_at: null,
    decision_note: null,
    expires_at: null,
    episode_id: null,
    similar: null,
    screening: { reasons: [], pii: [] },
    ...c,
  }
}

function candidates(): Candidate[] {
  return [
    candidate({
      id: FIX.refundCandidate, key: 'C-4Q', op: 'UPDATE', target_id: FIX.refundEntry, collection_id: 'col_billing',
      draft: { title: 'Refund approval threshold', content: 'Refunds over $500 require approval from a second support lead before they are issued.' },
      reason: 'Conflicts with KB-0142 on the threshold. The source conversation cites the October support handbook.',
      risk_level: 'high', origin: 'promote', stage: 'awaiting_review', created_at: ago(2 * H), episode_id: FIX.storyEpisode,
      similar: { entry_id: FIX.refundEntry, key: 'KB-0142', title: 'Refund approval threshold', content: 'Refunds over $1,000 require approval from a team lead.', version: 3, status: 'verified', similarity: 0.87 },
      screening: { reasons: ['money amount', 'policy change'], pii: [] },
    }),
    candidate({
      id: 'cand_01jc4m', key: 'C-4M', op: 'UPDATE', target_id: 'kbe_0201', collection_id: 'col_product',
      draft: { title: 'Enterprise single sign-on', content: 'The Enterprise plan includes SSO via SAML 2.0 and OIDC.' },
      reason: 'Two conversations this week mention OIDC with Okta on Enterprise.',
      risk_level: 'medium', origin: 'extraction', stage: 'awaiting_review', created_at: ago(3 * H), episode_id: 'ep_5d19_1',
      similar: { entry_id: 'kbe_0201', key: 'KB-0201', title: 'Enterprise single sign-on', content: 'The Enterprise plan includes SSO via SAML 2.0.', version: 2, status: 'verified', similarity: 0.81 },
      screening: { reasons: ['plan entitlement'], pii: [] },
    }),
    candidate({
      id: FIX.hoursCandidate, key: 'C-4J', op: 'UPDATE', target_id: 'kbe_0301', collection_id: 'col_support',
      draft: { title: 'Support hours', content: 'Live support is available 08:00–20:00 AEST, Monday to Friday.' },
      reason: 'The customer corrected the hours during a chat; waiting for them to confirm the wording.',
      risk_level: 'low', origin: 'user_suggestion', stage: 'awaiting_user', requested_for_user: 'u_31c0', created_at: ago(5 * H), expires_at: ago(-2 * D),
      similar: { entry_id: 'kbe_0301', key: 'KB-0301', title: 'Support hours', content: 'Live support is available 09:00–17:00 AEST on business days.', version: 1, status: 'active', similarity: 0.78 },
    }),
    candidate({
      id: FIX.piiCandidate, key: 'C-4H', collection_id: 'col_support',
      draft: { title: 'Customer callback number', content: 'Customer [REDACTED: name] can be reached at [REDACTED: phone] for outage follow-ups.' },
      reason: 'Extracted from a call transcript. Personal data was redacted before review.',
      risk_level: 'high', origin: 'extraction', stage: 'awaiting_review', created_at: ago(6 * H), episode_id: 'ep_31c0_2',
      screening: { reasons: ['personal data'], pii: ['name', 'phone number'] },
    }),
    candidate({
      id: 'cand_01jc49', key: 'C-49', op: 'UPDATE', target_id: 'kbe_0204', collection_id: 'col_product',
      draft: { title: 'Webhook versions', content: 'Use webhooks v2 for new integrations; v1 retires on 2027-03-31.' },
      reason: 'Announced in the November changelog; three customers asked about it.',
      risk_level: 'medium', origin: 'extraction', stage: 'awaiting_review', created_at: ago(1 * D), episode_id: 'ep_b6a1_3',
      similar: { entry_id: 'kbe_0204', key: 'KB-0204', title: 'Webhook versions', content: 'Webhooks v1 and v2 are both available; v2 signs every request.', version: 2, status: 'active', similarity: 0.69 },
      screening: { reasons: ['date commitment'], pii: [] },
    }),
    candidate({
      id: 'cand_01jc47', key: 'C-47', op: 'UPDATE', target_id: 'kbe_0144', collection_id: 'col_billing',
      draft: { title: 'Chargeback ownership', content: 'Chargebacks are handled by finance end to end; support never contacts the bank.' },
      reason: 'Promoted from a support lead’s memory after an escalation.',
      risk_level: 'medium', origin: 'promote', stage: 'awaiting_review', created_at: ago(1 * D + 3 * H), episode_id: 'ep_31c0_3',
      similar: { entry_id: 'kbe_0144', key: 'KB-0144', title: 'Chargeback ownership', content: 'Chargebacks are opened by finance; support only links the ticket.', version: 1, status: 'active', similarity: 0.74 },
      screening: { reasons: ['money process'], pii: [] },
    }),
    candidate({
      id: 'cand_01jc45', key: 'C-45', target_type: 'memory', collection_id: null,
      draft: { title: 'Reply style', content: 'Prefers concise replies without greetings.' },
      reason: 'Asked the user to confirm this preference before saving it.',
      risk_level: 'low', origin: 'extraction', stage: 'awaiting_user', requested_for_user: 'u_5d19', created_at: ago(1 * D + 6 * H), expires_at: ago(-1 * D),
    }),
    // already decided: history only
    candidate({
      id: 'cand_01jc3k', key: 'C-3K', collection_id: 'col_support', status: 'rejected', decided_by: 'reviewer@demo', decided_at: ago(12 * D),
      decision_note: 'Phone follow-ups are not offered on every plan.',
      draft: { title: 'Phone follow-up', content: 'Phone follow-ups are offered for Enterprise outages only.' },
      risk_level: 'low', origin: 'extraction', stage: 'awaiting_review', created_at: ago(13 * D),
    }),
    candidate({
      id: 'cand_01jc2w', key: 'C-2W', op: 'UPDATE', target_id: FIX.refundEntry, collection_id: 'col_billing', status: 'accepted', decided_by: 'reviewer@demo', decided_at: ago(150 * D - 60),
      draft: { title: 'Refund approval threshold', content: 'Refunds over $1,000 require approval from a team lead.' },
      risk_level: 'high', origin: 'api', stage: 'awaiting_review', created_at: ago(151 * D),
    }),
  ]
}

// ---------------------------------------------------------------- operations

function jobs(): Job[] {
  const j = (id: string, kind: string, status: Job['status'], attempts: number, minutesAgo: number, extra: Partial<Job> = {}): Job => ({
    id, kind, status, attempts, max_attempts: 5, object_type: null, object_id: null, last_error: null,
    run_after: ago(minutesAgo), created_at: ago(minutesAgo + 5), updated_at: ago(minutesAgo), ...extra,
  })
  return [
    j('job_0912', 'sync.push', 'failed', 5, 50, { object_type: 'kb_entry', object_id: 'kbe_0177', last_error: 'weknora: 504 Gateway Timeout after 30s (attempt 5 of 5)' }),
    j('job_0911', 'document.parse', 'failed', 3, 3 * D, { object_type: 'kb_document', object_id: 'doc_dpa', max_attempts: 3, last_error: 'Engine timed out while parsing page 14' }),
    j('job_0915', 'sync.push', 'running', 1, 2, { object_type: 'kb_entry', object_id: 'kbe_0206' }),
    j('job_0916', 'episode.extract', 'pending', 0, -3, { object_type: 'episode', object_id: 'ep_e910_1' }),
    j('job_0917', 'maintenance.consolidate', 'pending', 0, -6 * H),
    j('job_0910', 'episode.extract', 'done', 1, 2 * H + 10, { object_type: 'episode', object_id: FIX.storyEpisode }),
    j('job_0909', 'sync.push', 'done', 1, 1 * D, { object_type: 'kb_entry', object_id: 'kbe_0208' }),
    j('job_0908', 'sync.remove', 'done', 2, 12 * D, { object_type: 'kb_entry', object_id: 'kbe_0305' }),
    j('job_0907', 'maintenance.gap_report', 'done', 1, 1 * D + 2 * H),
  ]
}

function providers(): ProviderStatus[] {
  return [
    { name: 'memory', kind: 'memory', engine: 'mem0', version: '2.2.1', capabilities: ['extract', 'update', 'delete', 'search', 'history'], health: 'healthy', latency_p95_ms: 182, error_rate: 0.002, queue: { pending: 0, failed: 0 }, last_reconciled_at: ago(9 * H), reconcile_drift: 0 },
    { name: 'kb', kind: 'kb', engine: 'weknora', version: '0.9.0', capabilities: ['entries', 'documents', 'hybrid_search', 'rerank'], health: 'degraded', latency_p95_ms: 1840, error_rate: 0.021, queue: { pending: 1, failed: 1 }, last_reconciled_at: ago(1 * D), reconcile_drift: 1 },
  ]
}

const LOG_SEEDS: [string, number, number, number, boolean, number][] = [
  ['how long do we keep chat transcripts', 0.22, 2, 410, false, 35],
  ['refund over 700 dollars who approves', 0.88, 6, 380, false, 70],
  ['does enterprise include okta', 0.74, 5, 2410, true, 2 * H],
  ['webhook v1 end of life', 0.69, 4, 350, false, 5 * H],
  ['how long do we keep chat transcripts', 0.21, 2, 395, false, 7 * H],
  ['can I get invoices in yen', 0.31, 3, 330, false, 9 * H],
  ['what are support hours', 0.83, 6, 290, false, 1 * D],
  ['sso for starter plan', 0.46, 4, 410, false, 1 * D + 4 * H],
  ['who handles chargebacks', 0.79, 5, 360, false, 2 * D],
  ['can I get invoices in yen', 0.29, 2, 300, false, 3 * D],
  ['how long do we keep chat transcripts', 0.23, 2, 420, false, 4 * D],
  ['rate limit per key', 0.9, 6, 280, false, 5 * D],
]

function retrievalLogs(): RetrievalLog[] {
  return LOG_SEEDS.map(([query, top, hits, ms, degraded, minutes], i) => ({
    id: `rl_${String(200 - i).padStart(4, '0')}`,
    query, user_id: i % 3 === 0 ? 'u_e910' : null, top_score: top, hit_count: hits, latency_ms: ms, degraded,
    gap: top < 0.35, created_at: ago(minutes),
  }))
}

function settings(slug: string): NamespaceSettings {
  return {
    yaml: `namespace: ${slug}
providers:
  memory: { engine: mem0, version: 2.2.1 }
  kb: { engine: weknora, version: 0.9.0 }
review:
  default_risk: medium
  awaiting_user_ttl: 72h
  review_due_after: 180d
retrieval:
  sources: [memory, kb]
  top_k: 8
  fusion: rrf
  weights: { verified: 1.0, active: 0.85, memory: 0.7 }
  decay_half_life: 90d
  token_budget: 1200
pii:
  redact: [email, phone, card_number, government_id]
`,
    templates: [
      { name: 'extract_facts', body: 'Extract durable facts about the user from the conversation below.\nReturn one fact per item; skip small talk.\n\n{{ messages }}' },
      { name: 'classify_risk', body: 'Classify the risk of publishing this statement to the team knowledge base.\nConsider money, legal, security and personal data.\n\n{{ draft }}' },
      { name: 'promote_memory', body: 'Rewrite this personal memory as a general statement that is true for every customer, or answer NONE.\n\n{{ memory }}' },
      { name: 'context_render', body: '## About the user\n{% for m in memories %}- {{ m.text }}\n{% endfor %}\n## Knowledge base\n{% for e in entries %}- [{{ e.key }} v{{ e.version }}] {{ e.text }}\n{% endfor %}' },
    ],
    embedding: { model: 'fastembed paraphrase-multilingual-MiniLM-L12-v2', dimensions: 384, space_id: 'emb_minilm_384', rebuild_progress: null },
  }
}

// ---------------------------------------------------------------- assembly

function changesFor(memories: Memory[], entries: KbEntry[], cands: Candidate[]): Change[] {
  const rows: Change[] = []
  let n = 0
  const id = () => `chg_${String(++n).padStart(5, '0')}`
  for (const m of memories) {
    rows.push({ id: id(), object_type: 'memory', object_id: m.id, op: 'create', actor_type: m.origin === 'direct' ? 'host_app' : 'provider', actor_id: m.origin === 'direct' ? 'key:stw_k3f9a1' : 'mem0', reason: null, before: null, after: { content: m.content, user_id: m.user_id, status: 'active' }, created_at: m.created_at })
    if (m.status === 'superseded' && m.valid_to) {
      rows.push({ id: id(), object_type: 'memory', object_id: m.id, op: 'transition', actor_type: 'system', actor_id: 'conflict', reason: 'Superseded by a newer fact', before: { status: 'active', user_id: m.user_id }, after: { status: 'superseded', superseded_by: m.superseded_by, user_id: m.user_id }, created_at: m.valid_to })
    }
  }
  for (const e of entries) {
    const earlier = EARLIER_VERSIONS[Number(e.key.slice(3))] ?? []
    const versions = [...earlier].reverse() // oldest first
    const start = Date.parse(e.created_at)
    const step = (Date.parse(e.updated_at) - start) / Math.max(versions.length, 1)
    let prev: string | null = null
    versions.forEach((text, i) => {
      const at = new Date(start + step * i).toISOString()
      rows.push(prev === null
        ? { id: id(), object_type: 'kb_entry', object_id: e.id, op: 'create', actor_type: e.author_type === 'ai' ? 'ai' : 'admin', actor_id: e.created_by, reason: null, before: null, after: { version: 1, title: e.title, content: text, status: 'active' }, created_at: at }
        : { id: id(), object_type: 'kb_entry', object_id: e.id, op: 'update', actor_type: 'admin', actor_id: 'reviewer@demo', reason: 'Approved revision', before: { version: i, content: prev }, after: { version: i + 1, content: text, status: 'verified' }, created_at: at })
      prev = text
    })
    rows.push(prev === null
      ? { id: id(), object_type: 'kb_entry', object_id: e.id, op: 'create', actor_type: e.author_type === 'ai' ? 'ai' : 'admin', actor_id: e.created_by, reason: null, before: null, after: { version: 1, title: e.title, content: e.content, status: e.status === 'verified' ? 'verified' : 'active' }, created_at: e.created_at }
      : { id: id(), object_type: 'kb_entry', object_id: e.id, op: 'update', actor_type: 'admin', actor_id: 'reviewer@demo', reason: 'Approved revision', before: { version: e.version - 1, content: prev }, after: { version: e.version, content: e.content, status: e.status }, created_at: e.updated_at })
    if (e.status === 'deprecated' || e.status === 'rejected') {
      rows.push({ id: id(), object_type: 'kb_entry', object_id: e.id, op: 'transition', actor_type: 'admin', actor_id: 'reviewer@demo', reason: e.superseded_by ? 'Obsoleted by KB-0142' : 'Rejected in review', before: { status: 'active' }, after: { status: e.status, superseded_by: e.superseded_by }, created_at: e.valid_to ?? e.updated_at })
    }
  }
  for (const c of cands) {
    rows.push({ id: id(), object_type: 'candidate', object_id: c.id, op: 'create', actor_type: c.origin === 'promote' ? 'admin' : c.origin === 'user_suggestion' ? 'end_user' : 'ai', actor_id: c.origin === 'promote' ? 'lead@demo' : c.requested_for_user, reason: c.reason, before: null, after: { title: c.draft.title, risk_level: c.risk_level, stage: c.stage }, created_at: c.created_at })
    if (c.status !== 'pending' && c.decided_at) {
      rows.push({ id: id(), object_type: 'candidate', object_id: c.id, op: 'transition', actor_type: 'admin', actor_id: c.decided_by, reason: c.decision_note, before: { status: 'pending' }, after: { status: c.status }, created_at: c.decided_at })
    }
  }
  return rows.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
}

function episodesFor(memories: Memory[]): Episode[] {
  const seen = new Map<string, Episode>()
  for (const m of memories) {
    for (const ref of m.source_refs) {
      if (ref.type !== 'episode' || seen.has(ref.id)) continue
      seen.set(ref.id, { id: ref.id, kind: 'conversation', user_id: m.user_id, session_id: m.session_id, summary: `Support conversation with ${m.user_id}`, created_at: m.created_at })
    }
  }
  seen.set(FIX.storyEpisode, { id: FIX.storyEpisode, kind: 'conversation', user_id: 'u_8f2a', session_id: 's_91f0', summary: 'Customer asked who approves a $700 refund; agent quoted the October handbook.', created_at: '2026-11-08T14:12:00.000Z' })
  return [...seen.values()]
}

function syncRefsFor(memories: Memory[], entries: KbEntry[], documents: KbDocument[]): SyncRef[] {
  const refs: SyncRef[] = []
  for (const e of entries) {
    if (e.sync_status === 'deleted' && e.status === 'draft') continue
    refs.push({ id: `ref_${e.id}`, object_type: 'kb_entry', object_id: e.id, label: `${e.key} ${e.title}`, provider: 'weknora', sync_status: e.sync_status ?? 'pending', synced_version: e.sync_status === 'synced' ? e.version : null, last_error: e.sync_status === 'failed' ? 'weknora: 504 Gateway Timeout after 30s' : null, updated_at: e.updated_at })
  }
  for (const d of documents) {
    refs.push({ id: `ref_${d.id}`, object_type: 'kb_document', object_id: d.id, label: d.filename, provider: 'weknora', sync_status: d.sync_status ?? 'pending', synced_version: d.sync_status === 'synced' ? 1 : null, last_error: d.error, updated_at: d.created_at })
  }
  for (const m of memories.slice(0, 6)) {
    refs.push({ id: `ref_${m.id}`, object_type: 'memory', object_id: m.id, label: m.content, provider: 'mem0', sync_status: 'synced', synced_version: 1, last_error: null, updated_at: m.updated_at })
  }
  return refs
}

function buildSupport(): NsData {
  const memories = MEMORY_SEEDS.map(memoryFromSeed)
  const entries = ENTRY_SEEDS.map(entryFromSeed)
  const cands = candidates()
  const documents = DOCUMENTS.map((d) => ({ ...d }))
  const collections = COLLECTIONS.map((c) => ({
    ...c,
    entry_count: entries.filter((e) => e.collection_id === c.id).length,
    document_count: documents.filter((d) => d.collection_id === c.id).length,
  }))
  const chunks: Record<string, Chunk[]> = {}
  for (const [doc, texts] of Object.entries(CHUNKS)) {
    chunks[doc] = texts.map((text, index) => ({ id: `${doc}_c${index}`, index, text, tokens: Math.ceil(text.split(/\s+/).length * 1.3) }))
  }
  return {
    namespace: { id: 'ns_support', slug: 'acme-support', name: 'Acme Support', memory_provider: 'mem0 2.2.1', kb_provider: 'weknora 0.9.0' },
    memories,
    episodes: episodesFor(memories),
    collections,
    entries,
    documents,
    chunks,
    candidates: cands,
    changes: changesFor(memories, entries, cands),
    jobs: jobs(),
    syncRefs: syncRefsFor(memories, entries, documents),
    providers: providers(),
    retrievalLogs: retrievalLogs(),
    keys: [
      { id: 'key_01', name: 'support-bot (production)', prefix: 'k3f9a1', scopes: ['read', 'write'], created_at: ago(80 * D), last_used_at: ago(4), revoked_at: null },
      { id: 'key_02', name: 'review dashboard', prefix: 'p7d2c0', scopes: ['read', 'review'], created_at: ago(40 * D), last_used_at: ago(2 * D), revoked_at: null },
      { id: 'key_03', name: 'old analytics export', prefix: 'z0e8b4', scopes: ['read'], created_at: ago(200 * D), last_used_at: ago(95 * D), revoked_at: ago(90 * D) },
    ],
    settings: settings('acme-support'),
    seq: 1000,
  }
}

function buildSales(): NsData {
  const memories: Memory[] = [
    memoryFromSeed([101, 'u_s001', 'Champion at a logistics company, budget approved for Q1', 'semantic', 0.8, 4 * D]),
    memoryFromSeed([102, 'u_s001', 'Wants a security review before signing', 'semantic', 0.7, 3 * D]),
    memoryFromSeed([103, 'u_s002', 'Prefers a call on Thursdays', 'preference', 0.4, 9 * D]),
  ]
  const entries: KbEntry[] = [
    entryFromSeed([501, 'col_sales', 'Discount ceiling', 'Account executives can discount up to 15% without approval.', 'verified', 'high', 1, 60 * D, { hits: 48, helpful_rate: 0.9 }]),
    entryFromSeed([502, 'col_sales', 'Trial length', 'Trials last 14 days and can be extended once by 7 days.', 'active', 'low', 1, 30 * D, { hits: 22, helpful_rate: 0.8 }]),
  ]
  const collections: Collection[] = [{ id: 'col_sales', name: 'Sales playbook', default_risk: 'medium', review_policy: { high: 'reviewer', medium: 'reviewer', low: 'auto' }, created_at: ago(90 * D), entry_count: 2, document_count: 0 }]
  return {
    namespace: { id: 'ns_sales', slug: 'acme-sales', name: 'Acme Sales', memory_provider: 'mem0 2.2.1', kb_provider: 'literag 0.4.0' },
    memories,
    episodes: episodesFor(memories),
    collections,
    entries,
    documents: [],
    chunks: {},
    candidates: [],
    changes: changesFor(memories, entries, []),
    jobs: [],
    syncRefs: syncRefsFor(memories, entries, []),
    providers: [
      { name: 'memory', kind: 'memory', engine: 'mem0', version: '2.2.1', capabilities: ['extract', 'update', 'delete', 'search', 'history'], health: 'healthy', latency_p95_ms: 170, error_rate: 0, queue: { pending: 0, failed: 0 }, last_reconciled_at: ago(9 * H), reconcile_drift: 0 },
      { name: 'kb', kind: 'kb', engine: 'literag', version: '0.4.0', capabilities: ['entries', 'hybrid_search'], health: 'healthy', latency_p95_ms: 120, error_rate: 0, queue: { pending: 0, failed: 0 }, last_reconciled_at: ago(9 * H), reconcile_drift: 0 },
    ],
    retrievalLogs: [],
    keys: [],
    settings: settings('acme-sales'),
    seq: 1000,
  }
}

export function buildFixtures(): Map<string, NsData> {
  return new Map([
    ['acme-support', buildSupport()],
    ['acme-sales', buildSales()],
  ])
}
