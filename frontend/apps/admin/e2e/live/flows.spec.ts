import { expect, test, type Page } from '@playwright/test'
import { ADMIN_TOKEN, BACKEND } from '../../playwright.live.config'

// Design §11 P6 acceptance against a real llm-steward (native engines, fake LLM): the reviewer's
// decision is what verifies knowledge, and the outbox worker then pushes it to the engine.

async function connect(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Connect to llm-steward' })).toBeVisible()
  await page.getByLabel('Backend URL').fill(BACKEND)
  await page.getByLabel('Admin token').fill(ADMIN_TOKEN)
  await page.getByRole('button', { name: 'Connect' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
  await expect(page).toHaveURL(/\/acme\/dashboard$/)
}

const nav = (page: Page, name: string | RegExp) => page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name }).click()

test('a wrong admin token is refused and not kept', async ({ page }) => {
  await page.goto('/connect')
  await page.getByLabel('Backend URL').fill(BACKEND)
  await page.getByLabel('Admin token').fill('wrong-token-of-a-plausible-length-000000')
  await page.getByRole('button', { name: 'Connect' }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.goto('/acme/dashboard')
  await expect(page.getByRole('heading', { name: 'Connect to llm-steward' })).toBeVisible()
})

test('review a high-risk candidate, see it verified and synced to the engine', async ({ page }) => {
  await connect(page)
  await nav(page, /^Review/)
  await page.getByRole('button', { name: /Refund approval threshold/ }).click()
  await expect(page.getByRole('heading', { name: /Refunds over \$500 require approval/ })).toBeVisible()
  await page.getByRole('button', { name: /^Approve/ }).click()

  const outcome = page.getByRole('status')
  await expect(outcome).toContainText(/KB-\w{6} is now v1, verified\./)
  const key = (await outcome.getByRole('link').textContent())!.split(' ')[0]!
  await outcome.getByRole('link').click()
  await expect(page.getByText('Refunds over $500 require approval from a second support lead.').first()).toBeVisible()

  // The outbox worker pushes it to the native engine shortly after the decision.
  await nav(page, 'Providers')
  await page.getByLabel('Show synced and removed').check()
  await expect(async () => {
    await page.reload()
    await page.getByLabel('Show synced and removed').check()
    await expect(page.getByRole('row', { name: new RegExp(key) })).toContainText('Synced', { timeout: 1000 })
  }).toPass({ timeout: 20_000 })
})

test('find a gap in the Playground and add the answer to the KB through review', async ({ page }) => {
  await connect(page)
  await nav(page, 'Playground')
  await page.getByLabel('Question').fill('data retention period')
  await page.getByRole('button', { name: 'Retrieve' }).click()
  // The engine returns its nearest neighbours however weak (no relevance floor yet), so this is
  // not logged as a gap: the operator judges that none of the hits answers it.
  await expect(page.getByRole('heading', { name: 'Do these answer it?' })).toBeVisible()
  await expect(page.getByText(/Refund approval threshold|Invoice delivery/).first()).toBeVisible()
  await page.getByRole('button', { name: 'Add the missing answer to the KB' }).click()
  await page.getByLabel('Title').fill('Data retention')
  await page.getByLabel('Answer').fill('Conversation data is kept for 30 days, then deleted.')
  await page.getByRole('button', { name: 'Send to review' }).click()
  await page.getByRole('status').getByRole('link').click()

  await expect(page.getByRole('heading', { name: 'Conversation data is kept for 30 days, then deleted.' })).toBeVisible()
  await page.keyboard.press('a')
  await expect(page.getByRole('status')).toContainText(/KB-\w{6} is now v1, verified\./)

  // Once the worker has pushed it to the engine (asynchronously, rule 2), the same question finds it.
  await nav(page, 'Playground')
  await page.getByLabel('Question').fill('data retention period')
  await expect(async () => {
    await page.getByRole('button', { name: 'Retrieve' }).click()
    await expect(page.getByText('Data retention: Conversation data is kept for 30 days, then deleted.').first()).toBeVisible({ timeout: 1000 })
  }).toPass({ timeout: 20_000 })
})
