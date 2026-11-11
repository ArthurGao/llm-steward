import { MODE } from './session'

// The Admin has no roles (decided 2026-11-10): whoever holds the admin token may do everything
// it offers, and the backend enforces the governance rules. Controls the backend has no route for
// yet say so.
export const NOT_IN_DEMO = MODE === 'mock' ? 'Not available in the demo' : 'Not available in this version'
