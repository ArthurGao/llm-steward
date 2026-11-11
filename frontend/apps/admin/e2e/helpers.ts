import { expect, type Page } from '@playwright/test'

/** The demo has no sign-in: / opens the dashboard of the demo namespace. */
export async function openDemo(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
}

/** Collects console errors and uncaught exceptions for the page's lifetime. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(String(e)))
  return errors
}
