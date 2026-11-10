import { useMutation } from '@tanstack/react-query'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, type FormEvent } from 'react'
import { api } from '@/api/endpoints'
import type { Role } from '@/api/types'
import { setSession } from '@/auth/session'
import { ErrorNote } from '@/components/misc'
import { Button } from '@/components/ui/button'
import { Field, Input, Select } from '@/components/ui/field'

export const Route = createFileRoute('/login')({ component: Login })

const ROLE_HELP: Record<Role, string> = {
  reviewer: 'Approves or rejects candidates and edits knowledge.',
  owner: 'Everything a reviewer can do, plus keys, jobs and user erasure.',
  viewer: 'Reads everything, changes nothing.',
}

function Login() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('reviewer@demo')
  const [password, setPassword] = useState('demo')
  const [role, setRole] = useState<Role>('reviewer')
  const login = useMutation({
    mutationFn: () => api.login(email, password, role),
    onSuccess: (session) => {
      setSession(session)
      void navigate({ to: '/$ns/dashboard', params: { ns: 'acme-support' } })
    },
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate()
  }
  return (
    <main className="grid min-h-dvh place-items-center bg-paper px-4 py-10">
      <div className="w-full max-w-[440px] border border-ink bg-paper">
        <div className="border-b border-ink px-6 py-3 font-mono text-[13px] leading-6">
          <div className="flex justify-between"><span>llm-steward</span><span className="text-ink-3">Admin</span></div>
          <div className="flex justify-between text-ink-2"><span>Status: Demo</span><span>Data: synthetic</span></div>
        </div>
        <form onSubmit={submit} className="grid gap-4 px-6 py-6">
          <div>
            <h1 className="text-[22px] font-bold">Sign in to llm-steward</h1>
            <p className="mt-1 text-ink-2">
              A governance layer for AI memory and knowledge. This demo runs on invented data in your browser; changes reset when you reload.
            </p>
          </div>
          <Field label="Email">{(id) => <Input id={id} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
          <Field label="Password" hint="The demo password is “demo”.">
            {(id) => <Input id={id} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          <Field label="Role" hint={ROLE_HELP[role]}>
            {(id) => (
              <Select id={id} value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-full">
                <option value="reviewer">Reviewer</option>
                <option value="owner">Owner</option>
                <option value="viewer">Viewer</option>
              </Select>
            )}
          </Field>
          {login.error && <ErrorNote error={login.error} />}
          <Button type="submit" variant="primary" disabled={login.isPending}>
            {login.isPending ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </div>
    </main>
  )
}
