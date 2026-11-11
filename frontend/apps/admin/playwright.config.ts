import { defineConfig, devices } from '@playwright/test'

// E2E runs against the production build in mock mode. 8611 is registered for it
// (8610 is the dev server). PLAYWRIGHT_BASE_URL points the smoke tests at a deployment.
const base = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:8611'

export default defineConfig({
  testDir: './e2e',
  testIgnore: 'live/**', // playwright.live.config.ts runs these against a real backend
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: base, trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } }, testMatch: /smoke/ },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : { command: 'pnpm build && pnpm preview --port 8611', url: base, reuseExistingServer: !process.env.CI, timeout: 120_000 },
})
