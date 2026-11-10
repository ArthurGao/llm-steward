import { setupWorker } from 'msw/browser'
import { setClock } from '@/lib/clock'
import { createHandlers } from './handlers'
import { createStore, NOW } from './store'

/** Starts the in-browser mock of /admin/v1. A reload starts from fresh fixtures. */
export async function startMockApi(): Promise<void> {
  setClock(NOW)
  const worker = setupWorker(...createHandlers(createStore()))
  await worker.start({ onUnhandledFrame: 'bypass', quiet: true, serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` } })
}
