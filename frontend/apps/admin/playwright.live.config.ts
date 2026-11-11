import { defineConfig, devices } from '@playwright/test'

// The P6 acceptance flows against a real llm-steward (design §11): native providers on a fresh
// SQLite file, the outbox worker running, fake embedder and LLM (backend/tests/e2e/admin_server.py),
// and the Admin built in live mode. 8602 / 8612 are registered for this run.
export const ADMIN_TOKEN = 'e2e-admin-token-not-a-secret-0123456789'
export const BACKEND = 'http://127.0.0.1:8602'
const admin = 'http://127.0.0.1:8612'

export default defineConfig({
  testDir: './e2e/live',
  workers: 1, // the flows share one backend and change its data
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...devices['Desktop Chrome'], baseURL: admin, viewport: { width: 1440, height: 900 }, trace: 'retain-on-failure' },
  webServer: [
    {
      command: `uv run python -m tests.e2e.admin_server --port 8602 --token ${ADMIN_TOKEN} --origin ${admin}`,
      cwd: '../../../backend',
      url: `${BACKEND}/health`,
      reuseExistingServer: false, // fresh data every run
      timeout: 120_000,
    },
    {
      command: `vite build --outDir dist-live --emptyOutDir && vite preview --outDir dist-live --port 8612 --strictPort`,
      env: { VITE_API_MODE: 'live', VITE_API_BASE_URL: BACKEND },
      url: admin,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
