import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { clearSession } from '@/auth/session'
import { setClock } from '@/lib/clock'
import { NOW } from '@/mocks/store'
import { server } from './render'

beforeAll(() => {
  setClock(NOW)
  server.listen({ onUnhandledFrame: 'error' })
})
afterEach(() => {
  cleanup()
  clearSession()
})
afterAll(() => server.close())

// jsdom lacks these browser APIs used by Radix and the router.
window.scrollTo = () => {}
Element.prototype.scrollIntoView ??= () => {}
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}
