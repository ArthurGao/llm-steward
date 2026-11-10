import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/$ns/')({
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/$ns/dashboard', params })
  },
})
