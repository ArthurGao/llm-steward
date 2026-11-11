// The four calls the widget makes, over the generated client (the OpenAPI spec is the only
// contract), on a client of its own: the backend URL and a short-lived user token minted by the
// host app's backend (`POST /v1/tokens/user`). Never an API key: that would let anyone with the page
// act for every user. The token pins every call to its user (the backend enforces it).
import { useMemo } from 'react'
import { createClient, createConfig } from './client/client'
import {
  acceptV1CandidatesCandidateIdAcceptPost,
  deleteMemoryV1MemoriesMemoryIdDelete,
  listMemoriesV1MemoriesGet,
  rejectV1CandidatesCandidateIdRejectPost,
} from './client/sdk.gen'
import type { CandidateOut, DecisionOut, MemoryOut } from './client/types.gen'

export type Suggestion = CandidateOut
export type Memory = MemoryOut
export type DecisionResult = DecisionOut

/** Where the API is and who is asking: a user token, minted per session by the host's backend. */
export interface Connection {
  baseUrl: string
  token: string
}

/** A failed call: the HTTP status and the Problem Details title (never echoes what was sent). */
export class StewardError extends Error {
  readonly status: number
  constructor(status: number, title: string) {
    super(title)
    this.name = 'StewardError'
    this.status = status
  }
}

export interface Decision {
  /** `personal` keeps it as the user's own memory; `team` offers it as knowledge (reviewed). */
  scope: 'personal' | 'team'
  /** The user's edit; only allowed with `personal` (the team reads what the user was asked). */
  draft?: { title?: string; content?: string }
}

export function stewardApi(connection: Connection) {
  const client = createClient(
    createConfig({ baseUrl: connection.baseUrl.replace(/\/+$/, ''), auth: connection.token }),
  )
  async function call<T>(promise: Promise<{ data?: T; error?: unknown; response?: Response }>) {
    const { data, error, response } = await promise
    if (error !== undefined || data === undefined) {
      const title = (error as { title?: unknown } | undefined)?.title
      throw new StewardError(response?.status ?? 0, typeof title === 'string' ? title : 'Request failed')
    }
    return data
  }
  return {
    accept: (id: string, decision: Decision) =>
      call(acceptV1CandidatesCandidateIdAcceptPost({ client, path: { candidate_id: id }, body: decision })),
    /** `stopSuggestions`: no more automatic suggestions for this user (they can undo it by
     * forgetting the rule in the MemoryPanel). */
    reject: (id: string, stopSuggestions: boolean) =>
      call(
        rejectV1CandidatesCandidateIdRejectPost({
          client,
          path: { candidate_id: id },
          body: { stop_suggestions: stopSuggestions },
        }),
      ),
    memories: (cursor?: string) => call(listMemoriesV1MemoriesGet({ client, query: { cursor, limit: 20 } })),
    forget: (id: string) => call(deleteMemoryV1MemoriesMemoryIdDelete({ client, path: { memory_id: id } })),
  }
}

/** The text to show for a failed call (a StewardError carries the API's own title). */
export function errorText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

/** The client for a connection, rebuilt only when its URL or token changes: a host may pass a new
 * `{ baseUrl, token }` object on every render. */
export function useStewardApi(connection: Connection) {
  const { baseUrl, token } = connection
  return useMemo(() => stewardApi({ baseUrl, token }), [baseUrl, token])
}

export function cx(...names: (string | undefined | false)[]): string {
  return names.filter(Boolean).join(' ')
}
