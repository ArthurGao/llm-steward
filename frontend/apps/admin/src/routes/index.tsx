import { createFileRoute, redirect } from '@tanstack/react-router'
import { getSession } from '@/auth/session'

export const DEFAULT_NS = 'acme-support'

export const Route = createFileRoute('/')({
  beforeLoad: () => {
    throw getSession() ? redirect({ to: '/$ns/dashboard', params: { ns: DEFAULT_NS } }) : redirect({ to: '/login' })
  },
})
