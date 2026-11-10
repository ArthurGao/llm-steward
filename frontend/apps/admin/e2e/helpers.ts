import { expect, type Page } from '@playwright/test'

export async function signIn(page: Page, role: 'viewer' | 'reviewer' | 'owner' = 'reviewer') {
  await page.goto('/login')
  await page.getByLabel('Role').selectOption(role)
  await page.getByRole('button', { name: 'Sign in' }).click()
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
