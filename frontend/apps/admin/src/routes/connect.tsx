import { useMutation } from '@tanstack/react-query'
import { createFileRoute, redirect, useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { api } from '@/api/endpoints'
import { connect, disconnect, MODE } from '@/auth/session'
import { ErrorNote } from '@/components/misc'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/field'
import { DEFAULT_NS } from './index'

export const Route = createFileRoute('/connect')({
  beforeLoad: () => {
    if (MODE === 'mock') throw redirect({ to: '/$ns/dashboard', params: { ns: DEFAULT_NS } })
  },
  component: Connect,
})

function Connect() {
  const navigate = useNavigate()
  const [baseUrl, setBaseUrl] = useState<string>(import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8600')
  const [token, setToken] = useState('')
  const go = useMutation({
    mutationFn: async () => {
      connect(baseUrl, token)
      try {
        return await api.namespaces()
      } catch (error) {
        disconnect() // a wrong URL or token is not kept
        throw error
      }
    },
    onSuccess: ({ items }) => {
      const first = items[0]?.slug
      if (first) void navigate({ to: '/$ns/dashboard', params: { ns: first } })
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    go.mutate()
  }
  return (
    <main className="grid min-h-dvh place-items-center bg-paper px-4 py-10">
      <div className="w-full max-w-[440px] border border-ink bg-paper">
        <div className="border-b border-ink px-6 py-3 font-mono text-[13px] leading-6">
          <div className="flex justify-between"><span>llm-steward</span><span className="text-ink-3">Admin</span></div>
          <div className="flex justify-between text-ink-2"><span>Status: Live</span><span>No accounts</span></div>
        </div>
        <form onSubmit={submit} className="grid gap-4 px-6 py-6">
          <div>
            <h1 className="text-[22px] font-bold">Connect to llm-steward</h1>
            <p className="mt-1 text-ink-2">
              The backend's address and its admin token (STEWARD_ADMIN_TOKEN). Both stay in this browser tab only.
            </p>
          </div>
          <Field label="Backend URL">{(id) => <Input id={id} type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />}</Field>
          <Field label="Admin token">{(id) => <Input id={id} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />}</Field>
          {go.error && <ErrorNote error={go.error} />}
          {go.data && go.data.items.length === 0 && <p className="text-ink-2">Connected, but there is no namespace yet: create one with `steward ns create`.</p>}
          <Button type="submit" variant="primary" disabled={go.isPending || !token}>
            {go.isPending ? 'Connecting…' : 'Connect'}
          </Button>
        </form>
      </div>
    </main>
  )
}
