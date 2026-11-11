import { createFileRoute, redirect } from '@tanstack/react-router'
import { api } from '@/api/endpoints'
import { getSession, MODE } from '@/auth/session'

export const DEFAULT_NS = 'acme-support' // the demo's namespace

export const Route = createFileRoute('/')({
  beforeLoad: async () => {
    if (MODE === 'mock') throw redirect({ to: '/$ns/dashboard', params: { ns: DEFAULT_NS } })
    // Live: the backend's first namespace, or the Connect page when there is no connection yet.
    const first = getSession() ? (await api.namespaces().catch(() => ({ items: [] }))).items[0]?.slug : undefined
    throw first ? redirect({ to: '/$ns/dashboard', params: { ns: first } }) : redirect({ to: '/connect' })
  },
})
