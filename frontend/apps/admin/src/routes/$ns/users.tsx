import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { api } from '@/api/endpoints'
import { str } from '@/lib/search'
import type { Erasure, UserSummary } from '@/api/types'
import { useDenied } from '@/auth/can'
import { DataTable, type Column } from '@/components/DataTable'
import { Pager } from '@/components/Pager'
import { HeaderBlock } from '@/components/HeaderBlock'
import { EmptyState, ErrorNote, Loading, Mono, PageTitle } from '@/components/misc'
import { DocumentError, DocumentFrame, SplitView } from '@/components/SplitView'
import { Button, buttonClass } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Field, Input, SearchBox } from '@/components/ui/field'
import { age, stamp } from '@/lib/format'

interface Search {
  selected?: string
  q?: string
  cursor?: string
}

export const Route = createFileRoute('/$ns/users')({
  validateSearch: (s: Record<string, unknown>): Search => ({
    selected: str(s.selected),
    q: str(s.q),
    cursor: str(s.cursor),
  }),
  component: Users,
})

function Users() {
  const { ns } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const set = (patch: Partial<Search>) => void navigate({ search: (s) => ({ ...s, ...patch }) })
  const list = useQuery({ queryKey: [ns, 'users', search.q, search.cursor], queryFn: () => api.users.list(ns, { q: search.q, cursor: search.cursor }) })
  const [erased, setErased] = useState<Erasure | null>(null)

  const columns: Column<UserSummary>[] = [
    { key: 'id', header: 'User', cell: (u) => <Mono>{u.user_id}</Mono> },
    { key: 'n', header: 'Memories', align: 'right', cell: (u) => <Mono>{u.memory_count}</Mono> },
    { key: 'p', header: 'Pinned', align: 'right', cell: (u) => <Mono>{u.pinned_count}</Mono> },
    { key: 'a', header: 'Last active', align: 'right', cell: (u) => <Mono className="text-ink-2">{age(u.last_active_at)}</Mono> },
  ]

  return (
    <>
      <PageTitle title="Users" count={list.data && `${list.data.total} with memories`} />
      <div className="flex flex-wrap items-center gap-2 border-b border-rule px-5 py-2.5 sm:px-7">
        <SearchBox aria-label="Search users" placeholder="User id" value={search.q} className="font-mono" onCommit={(v) => set({ q: v, cursor: undefined })} />
      </div>
      {erased && (
        <p role="status" className="border-b border-verified bg-verified-wash px-5 py-2 text-verified sm:px-7">
          Erased {erased.deleted_memories} memories and {erased.deleted_episodes} episodes of {erased.user_id}. The activity log keeps the events without their content.
        </p>
      )}
      <SplitView
        open={!!search.selected}
        onBack={() => set({ selected: undefined })}
        list={
          <>
            {list.isPending && <Loading />}
            {list.data && (
              <DataTable label="Users" rows={list.data.items} columns={columns} rowKey={(u) => u.user_id} selectedKey={search.selected} onOpen={(u) => set({ selected: u.user_id })} empty={<EmptyState title="No users match" />} />
            )}
            {list.data && <Pager cursor={search.cursor ?? null} next={list.data.next_cursor} total={list.data.total} shown={list.data.items.length} onChange={(cursor) => set({ cursor: cursor ?? undefined })} />}
          </>
        }
        detail={
          search.selected ? (
            <UserDocument
              ns={ns}
              userId={search.selected}
              onErased={(r) => {
                setErased(r)
                set({ selected: undefined })
              }}
            />
          ) : (
            <EmptyState title="Pick a user">Their profile, pinned memories and the erasure control open here.</EmptyState>
          )
        }
      />
    </>
  )
}

function UserDocument({ ns, userId, onErased }: { ns: string; userId: string; onErased: (r: Erasure) => void }) {
  const user = useQuery({ queryKey: [ns, 'user', userId], queryFn: () => api.users.get(ns, userId) })
  const denied = useDenied('eraseUser')
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const erase = useMutation({
    mutationFn: () => api.memories.eraseUser(ns, userId),
    onSuccess: (r) => {
      setOpen(false)
      qc.removeQueries({ queryKey: [ns, 'user', userId] })
      void qc.invalidateQueries({ queryKey: [ns] })
      onErased(r)
    },
  })
  if (user.isPending) return <Loading />
  if (user.isError) return <DocumentError error={user.error} notFound={`No data for ${userId}`} hint="This user has no memories in this namespace." />
  const u = user.data
  const profile = Object.entries(u.profile)
  return (
    <DocumentFrame>
      <HeaderBlock left={[['User', u.user_id], ['Memories', String(u.memory_count)]]} right={[['Pinned', String(u.pinned_count)], ['Last active', stamp(u.last_active_at)]]} />
      <h2 className="mt-6 text-[20px] font-bold">Profile</h2>
      {profile.length === 0 ? (
        <p className="mt-1 text-ink-2">No profile values extracted yet.</p>
      ) : (
        <table aria-label="Profile" className="mt-2 w-full max-w-xl border-collapse">
          <tbody>
            {profile.map(([k, v]) => (
              <tr key={k} className="border-b border-rule">
                <th scope="row" className="w-40 py-1.5 pr-4 text-left font-mono text-[13px] font-normal text-ink-2">{k}</th>
                <td className="py-1.5">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3 className="mt-6 font-bold">Pinned memories</h3>
      {u.pinned.length === 0 ? <p className="mt-1 text-ink-2">Nothing pinned.</p> : (
        <ul className="mt-1 list-disc pl-5">
          {u.pinned.map((m) => <li key={m.id}>{m.content}</li>)}
        </ul>
      )}
      <div className="mt-6 flex flex-wrap gap-2">
        <Link to="/$ns/memories" params={{ ns }} search={{ user: userId }} className={buttonClass()}>
          All memories of {userId}
        </Link>
        <Button variant="danger" disabledReason={denied} onClick={() => { setTyped(''); setOpen(true) }}>Erase user data…</Button>
      </div>
      <Dialog open={open} onOpenChange={setOpen} title={`Erase all data of ${userId}?`} description="Deletes their memories and conversations from llm-steward and the memory engine. This is the one hard delete; it cannot be undone. The activity log keeps who erased what, without content.">
        <div className="grid gap-3">
          <Field label={`Type ${userId} to confirm`}>{(id) => <Input id={id} value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} className="font-mono" />}</Field>
          {erase.error && <ErrorNote error={erase.error} />}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="danger" disabled={typed !== userId || erase.isPending} onClick={() => erase.mutate()}>Erase permanently</Button>
          </div>
        </div>
      </Dialog>
    </DocumentFrame>
  )
}
