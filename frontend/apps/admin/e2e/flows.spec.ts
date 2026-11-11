import { expect, test } from '@playwright/test'
import { openDemo } from './helpers'

// Design §11 P6 acceptance, on mock data.

test('review a high-risk candidate and see it verified and queued for sync', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: /^Review/ }).click()
  await page.getByRole('button', { name: /Refund approval threshold/ }).click()
  await expect(page.getByRole('heading', { name: /Refunds over \$500 require approval/ })).toBeVisible()
  await page.getByRole('button', { name: /^Approve/ }).click()

  const outcome = page.getByRole('status')
  await expect(outcome).toContainText('KB-0142 is now v4, verified · sync pending')
  await outcome.getByRole('link').click()
  await expect(page.getByText('Key: KB-0142 v4')).toBeVisible()
  await expect(page.getByText('Refunds over $500 require approval from a second support lead before they are issued.').first()).toBeVisible()

  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Providers' }).click()
  const row = page.getByRole('row', { name: /KB-0142 Refund approval threshold/ })
  await expect(row).toContainText('Pending')
})

test('find a gap in the Playground and add it to the KB through review', async ({ page }) => {
  await openDemo(page)
  await page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: 'Playground' }).click()
  await page.getByLabel('Question').fill('data retention period')
  await page.getByRole('button', { name: 'Retrieve' }).click()
  await expect(page.getByRole('heading', { name: 'No confident answer' })).toBeVisible()
  await page.getByRole('button', { name: 'Add the missing answer to the KB' }).click()
  await page.getByLabel('Title').fill('Data retention')
  await page.getByLabel('Answer').fill('Conversation data is kept for 30 days, then deleted.')
  await page.getByRole('button', { name: 'Send to review' }).click()
  await page.getByRole('status').getByRole('link').click()

  await expect(page.getByRole('heading', { name: 'Conversation data is kept for 30 days, then deleted.' })).toBeVisible()
  await page.keyboard.press('a')
  await expect(page.getByRole('status')).toContainText(/KB-\d+ is now v1, verified/)
})
