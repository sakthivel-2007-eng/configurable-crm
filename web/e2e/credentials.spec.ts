/**
 * The front door (M11).
 *
 * The workspace model is invite-only, so setting a password from an emailed
 * link is how every account in this product begins — there is no registration
 * form anywhere. These cover the two properties a browser can check that the
 * API tests cannot:
 *
 * - the reset request page says the same thing whatever address it is given,
 *   so the page does not undo what the server carefully refuses to reveal
 * - a successful set does **not** log you in, because a forwarded email would
 *   otherwise sign the wrong person in
 */

import { expect, test } from '@playwright/test'

import { stubApi } from './fixtures/api'

test.describe('setting a password', () => {
  test('a link with no token says so instead of failing later', async ({ page }) => {
    await stubApi(page)
    await page.goto('/set-password')
    await expect(page.getByText(/link is incomplete/i)).toBeVisible()
    await expect(page.getByRole('link', { name: /sign in/i })).toBeVisible()
  })

  test('the token travels from the URL to the request body', async ({ page }) => {
    const stub = await stubApi(page)
    await page.goto('/set-password?token=emailed-token-abc')

    await page.getByLabel('New password').fill('a-perfectly-fine-password')
    await page.getByLabel('Confirm').fill('a-perfectly-fine-password')
    await page.getByRole('button', { name: 'Set password' }).click()

    await expect.poll(() => stub.lastSetPassword()?.['token']).toBe('emailed-token-abc')
    await expect
      .poll(() => stub.lastSetPassword()?.['new_password'])
      .toBe('a-perfectly-fine-password')
  })

  test('success does not sign you in', async ({ page }) => {
    await stubApi(page)
    await page.goto('/set-password?token=emailed-token-abc')
    await page.getByLabel('New password').fill('a-perfectly-fine-password')
    await page.getByLabel('Confirm').fill('a-perfectly-fine-password')
    await page.getByRole('button', { name: 'Set password' }).click()

    // A forwarded email would otherwise log the wrong person in.
    await expect(page.getByText('Password set')).toBeVisible()
    await expect(page.getByRole('link', { name: /go to sign in/i })).toBeVisible()
    await expect(page).not.toHaveURL(/\/leads/)
  })

  test('mismatched fields are caught before a round trip', async ({ page }) => {
    const stub = await stubApi(page)
    await page.goto('/set-password?token=emailed-token-abc')

    await page.getByLabel('New password').fill('a-perfectly-fine-password')
    await page.getByLabel('Confirm').fill('a-different-password-here')

    await expect(page.getByText(/do not match/i)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Set password' })).toBeDisabled()
    expect(stub.lastSetPassword()).toBeNull()
  })

  test('a short password is refused with the shortfall named', async ({ page }) => {
    await stubApi(page)
    await page.goto('/set-password?token=emailed-token-abc')
    await page.getByLabel('New password').fill('short')
    await expect(page.getByText(/7 more to go/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Set password' })).toBeDisabled()
  })

  test('an expired link reports what the server said', async ({ page }) => {
    await stubApi(page, { setPasswordResult: 'invalid_token' })
    await page.goto('/set-password?token=stale-token')

    await page.getByLabel('New password').fill('a-perfectly-fine-password')
    await page.getByLabel('Confirm').fill('a-perfectly-fine-password')
    await page.getByRole('button', { name: 'Set password' }).click()

    await expect(page.getByRole('alert')).toBeVisible()
  })
})

test.describe('asking for a link', () => {
  test('the login page offers a way in', async ({ page }) => {
    await stubApi(page)
    await page.goto('/')
    // Without this the reset flow exists and nobody can reach it — and an
    // invitee whose link expired has no other route back.
    await page.getByRole('link', { name: /forgotten your password/i }).click()
    await expect(page.getByText(/reset your password/i)).toBeVisible()
  })

  test('the confirmation reveals nothing about the address', async ({ page }) => {
    const stub = await stubApi(page)
    await page.goto('/forgot-password')

    await page.getByLabel('Email').fill('nobody-at-all@example.com')
    await page.getByRole('button', { name: 'Send a link' }).click()

    // The same words a real account would get. A page that said "no such user"
    // would hand back precisely what the server refuses to.
    await expect(page.getByText(/if that address has an account/i)).toBeVisible()
    await expect.poll(() => stub.lastResetRequest()?.['email']).toBe('nobody-at-all@example.com')
  })
})
