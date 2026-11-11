import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { api } from '@/api/endpoints'
import type { ApiKey, ApiKeyCreated, NamespaceSettings } from '@/api/types'
import { NOT_IN_DEMO } from '@/auth/can'
import { DataTable, type Column } from '@/components/DataTable'
import { HeaderBlock } from '@/components/HeaderBlock'
import { ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/field'
import { TabPanel, Tabs } from '@/components/ui/tabs'
import { age, day } from '@/lib/format'

const TABS = ['namespace', 'prompts', 'keys', 'embedding'] as const
type Tab = (typeof TABS)[number]

export const Route = createFileRoute('/$ns/settings')({
  validateSearch: (s: Record<string, unknown>): { tab?: Tab } => ({ tab: TABS.find((t) => t === s.tab && t !== 'namespace') }),
  component: Settings,
})

function Settings() {
  const { ns } = Route.useParams()
  const { tab = 'namespace' } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const settings = useQuery({ queryKey: [ns, 'settings'], queryFn: () => api.settings.get(ns) })
  return (
    <>
      <PageTitle title="Settings" count={ns} />
      <Tabs
        label="Settings"
        value={tab}
        onValueChange={(v) => void navigate({ search: { tab: v === 'namespace' ? undefined : (v as Tab) } })}
        tabs={[
          { value: 'namespace', label: 'Namespace' },
          { value: 'prompts', label: 'Prompts' },
          { value: 'keys', label: 'API keys' },
          { value: 'embedding', label: 'Embedding' },
        ]}
      >
        <div className="px-5 py-5 sm:px-7">
          {settings.isPending && <Loading />}
          {settings.data && (
            <>
              <TabPanel value="namespace"><NamespaceTab s={settings.data} /></TabPanel>
              <TabPanel value="prompts"><PromptsTab s={settings.data} /></TabPanel>
              <TabPanel value="keys"><KeysTab ns={ns} /></TabPanel>
              <TabPanel value="embedding"><EmbeddingTab s={settings.data} /></TabPanel>
            </>
          )}
        </div>
      </Tabs>
    </>
  )
}

function NamespaceTab({ s }: { s: NamespaceSettings }) {
  return (
    <section className="max-w-3xl">
      <p className="text-ink-2">Configuration as code: the same YAML <Mono>steward ns apply -f ns.yaml</Mono> imports.</p>
      <pre aria-label="Namespace configuration" className="mt-3 overflow-x-auto border border-rule bg-wash px-4 py-3 font-mono text-[13px] leading-6">{s.yaml}</pre>
      <div className="mt-3 flex gap-2">
        <Button disabledReason={NOT_IN_DEMO}>Edit</Button>
        <Button disabledReason={NOT_IN_DEMO}>Validate</Button>
      </div>
    </section>
  )
}

function PromptsTab({ s }: { s: NamespaceSettings }) {
  const [open, setOpen] = useState(s.templates[0]?.name ?? '')
  const t = s.templates.find((x) => x.name === open)
  return (
    <section className="grid max-w-5xl gap-5 md:grid-cols-[14rem_1fr]">
      <ul aria-label="Templates">
        {s.templates.map((x) => (
          <li key={x.name}>
            <button type="button" aria-current={open === x.name || undefined} onClick={() => setOpen(x.name)} className="w-full px-2.5 py-1.5 text-left font-mono text-[13px] hover:bg-wash aria-[current=true]:bg-select aria-[current=true]:font-medium">
              {x.name}
            </button>
          </li>
        ))}
      </ul>
      {t && (
        <div>
          <pre className="overflow-x-auto border border-rule bg-wash px-4 py-3 font-mono text-[13px] leading-6 whitespace-pre-wrap">{t.body}</pre>
          <div className="mt-3 flex gap-2">
            <Button disabledReason={NOT_IN_DEMO}>Edit</Button>
            <Button disabledReason={NOT_IN_DEMO}>Test run</Button>
          </div>
        </div>
      )}
    </section>
  )
}

const SCOPES: ApiKey['scopes'] = ['read', 'write', 'review']

function KeysTab({ ns }: { ns: string }) {
  const qc = useQueryClient()
  const keys = useQuery({ queryKey: [ns, 'keys'], queryFn: () => api.settings.keys(ns) })
  const [name, setName] = useState('')
  const [scopes, setScopes] = useState<ApiKey['scopes']>(['read'])
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)
  const done = () => qc.invalidateQueries({ queryKey: [ns, 'keys'] })
  const create = useMutation({ mutationFn: () => api.settings.createKey(ns, { name: name.trim(), scopes }), onSuccess: (k) => { setCreated(k); setName(''); void done() } })
  const revoke = useMutation({ mutationFn: (id: string) => api.settings.revokeKey(ns, id), onSuccess: done })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }
  const columns: Column<ApiKey>[] = [
    { key: 'n', header: 'Name', cell: (k) => <span className={k.revoked_at ? 'text-ink-3 line-through decoration-1' : ''}>{k.name}</span> },
    { key: 'p', header: 'Key', cell: (k) => <Mono className="text-ink-2">stw_{k.prefix}_…</Mono> },
    { key: 's', header: 'Scopes', cell: (k) => <Mono>{k.scopes.join(' ')}</Mono> },
    { key: 'u', header: 'Last used', align: 'right', cell: (k) => <Mono className="text-ink-2">{k.last_used_at ? age(k.last_used_at) : 'never'}</Mono>, wide: true },
    { key: 'a', header: '', align: 'right', cell: (k) => (k.revoked_at ? <span className="text-[13px] text-ink-3">revoked {day(k.revoked_at)}</span> : <Button size="sm" variant="danger" onClick={() => revoke.mutate(k.id)}>Revoke</Button>) },
  ]
  return (
    <section className="grid max-w-5xl gap-5">
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <div className="w-60"><Field label="Key name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="support-bot (staging)" />}</Field></div>
        <fieldset className="flex h-9 items-center gap-3">
          <legend className="sr-only">Scopes</legend>
          {SCOPES.map((s) => (
            <label key={s} className="flex items-center gap-1.5 font-mono text-[13px]">
              <input type="checkbox" className="size-4 accent-ink" checked={scopes.includes(s)} onChange={(e) => setScopes((x) => (e.target.checked ? [...x, s] : x.filter((y) => y !== s)))} />
              {s}
            </label>
          ))}
        </fieldset>
        <Button type="submit" variant="primary" disabled={!name.trim() || scopes.length === 0 || create.isPending}>Create key</Button>
      </form>
      {(create.error ?? revoke.error) && <ErrorNote error={create.error ?? revoke.error} />}
      {keys.isPending ? <Loading /> : <DataTable label="API keys" rows={keys.data?.items ?? []} columns={columns} rowKey={(k) => k.id} />}
      <Dialog open={!!created} onOpenChange={(o) => !o && setCreated(null)} title={`Key “${created?.name ?? ''}” created`} description="Copy it now. Only its hash is stored, so it cannot be shown again.">
        <div className="grid gap-3">
          <Input aria-label="New key secret" readOnly value={created?.secret ?? ''} className="font-mono" onFocus={(e) => e.currentTarget.select()} />
          <div className="flex justify-end">
            <Button variant="primary" onClick={() => setCreated(null)}>I have copied it</Button>
          </div>
        </div>
      </Dialog>
    </section>
  )
}

function EmbeddingTab({ s }: { s: NamespaceSettings }) {
  const e = s.embedding
  if (!e) return <section className="max-w-3xl text-ink-2">This namespace's providers do not report an embedding model.</section>
  return (
    <section className="max-w-3xl">
      <HeaderBlock left={[['Model', e.model], ['Dimensions', e.dimensions === null ? '—' : String(e.dimensions)]]} right={[['Space', e.space_id ?? '—'], ['Rebuild', e.rebuild_progress === null ? 'idle' : `${e.rebuild_progress}%`]]} />
      <p className="mt-3 max-w-[68ch] text-ink-2">Switching the embedding space re-embeds every memory and entry in the background; retrieval keeps using the old space until the new one is complete.</p>
      <div className="mt-3"><Button disabledReason={NOT_IN_DEMO}>Switch embedding space…</Button></div>
    </section>
  )
}
