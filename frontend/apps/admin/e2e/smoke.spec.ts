import { expect, test } from '@playwright/test'
import { signIn, watchErrors } from './helpers'

const PAGES: [path: string, heading: string][] = [
  ['dashboard', 'Dashboard'],
  ['memories', 'Memories'],
  ['users', 'Users'],
  ['kb', 'Knowledge Base'],
  ['review', 'Review Queue'],
  ['playground', 'Retrieval Playground'],
  ['activity', 'Activity'],
  ['jobs', 'Jobs'],
  ['providers', 'Providers'],
  ['settings', 'Settings'],
]

test('the sign-in page renders', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to llm-steward' })).toBeVisible()
  expect(errors).toEqual([])
})

test('every page renders its heading, the demo marker and no console errors', async ({ page }) => {
  const errors = watchErrors(page)
  await signIn(page)
  for (const [path, heading] of PAGES) {
    await page.goto(`/acme-support/${path}`)
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await expect(page.getByText(/^Demo data/).filter({ visible: true })).toHaveCount(1)
    const width = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(width, `${path} scrolls sideways`).toBeLessThanOrEqual(0)
  }
  expect(errors).toEqual([])
})
