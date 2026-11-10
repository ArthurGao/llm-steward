import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'

async function boot() {
  // Mock mode is the default (and what the Vercel demo builds); VITE_API_MODE=live talks to a real backend.
  if (import.meta.env.VITE_API_MODE !== 'live') {
    const { startMockApi } = await import('./mocks/browser')
    await startMockApi()
  }
  const root = document.getElementById('root')
  if (!root) throw new Error('#root missing from index.html')
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void boot()
