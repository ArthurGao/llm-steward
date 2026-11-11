export interface Problem {
  title: string
  status?: number
  detail?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly problem: Problem

  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title)
    this.name = 'ApiError'
    this.status = status
    this.problem = problem
  }
}

// The mock API's transport (MSW answers in the browser); the live API uses the generated client.
const BASE = '/admin/v1'

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const problem: Problem = await res.json().catch(() => ({ title: res.statusText || `HTTP ${res.status}` }))
    throw new ApiError(res.status, problem)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
