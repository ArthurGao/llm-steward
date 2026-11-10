import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('a viewer reads the queue but cannot approve', async ({ page }) => {
  await signIn(page, 'viewer')
  await page.goto('/acme-support/review?selected=cand_01jc4q')
  const approve = page.getByRole('button', { name: /^Approve/ })
  await expect(approve).toHaveAttribute('aria-disabled', 'true')
  await approve.hover()
  await expect(page.getByRole('tooltip')).toContainText('Reviewing needs the reviewer or owner role')
  await page.keyboard.press('a')
  await expect(page.getByRole('list', { name: 'Lifecycle' }).locator('[aria-current="step"]')).toContainText('Review')
})
