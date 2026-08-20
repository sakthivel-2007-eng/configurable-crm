import { expect, test, type Page } from '@playwright/test'

import { memberDetail, REP_MEMBERSHIP, stubApi, type StubOptions } from './fixtures/api'

async function signIn(page: Page, options: StubOptions = {}) {
  const stub = await stubApi(page, options)
  await page.goto('/login')
  await page.getByLabel('Email').fill('owner@example.com')
  await page.getByLabel('Password').fill('correct-horse-battery-staple')
  await page.getByRole('button', { name: 'Sign in' }).click()
  // Leads is the landing page; members admin is a nav click away.
  await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
  await page.getByRole('link', { name: 'Team' }).click()
  await expect(page.getByRole('heading', { name: 'Team' })).toBeVisible()
  return stub
}

test.describe('members admin', () => {
  test('lists members with their template, availability and seat', async ({ page }) => {
    await signIn(page)

    const table = page.getByRole('table')
    await expect(table.getByRole('cell', { name: 'Ada Owner', exact: false })).toBeVisible()
    await expect(table.getByRole('cell', { name: 'Rey Rep', exact: false })).toBeVisible()
    await expect(page.getByText('2 of 3 licensed seats in use')).toBeVisible()
  })

  test('a seat can be revoked and reassigned', async ({ page }) => {
    await signIn(page)

    await page.getByRole('button', { name: 'Revoke seat' }).click()

    await expect(page.getByText('1 of 3 licensed seats in use')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Assign seat' })).toBeVisible()
  })

  test('availability does not offer INACTIVE', async ({ page }) => {
    // Setting INACTIVE is deactivation, which has to reassign leads first.
    // Offering it here would be the orphaned-pipeline bug with extra steps.
    await signIn(page)

    const picker = page.getByLabel('Availability for Rey Rep')
    await expect(picker).toBeVisible()
    await expect(picker.locator('option')).toHaveText(['Working', 'On leave'])
  })

  test('deactivating a member holding no leads takes one click', async ({ page }) => {
    await signIn(page, { openLeadCount: 0 })

    await page.getByRole('button', { name: 'Deactivate' }).first().click()
    await page.getByRole('button', { name: 'Deactivate', exact: true }).last().click()

    await expect(page.getByRole('button', { name: 'Reactivate' })).toBeVisible()
  })

  test('deactivating a member with open leads demands a reassignment target', async ({ page }) => {
    // The rule this milestone exists to protect: never orphan a pipeline.
    await signIn(page, { openLeadCount: 42 })

    await page.getByRole('button', { name: 'Deactivate' }).first().click()
    await page.getByRole('button', { name: 'Deactivate', exact: true }).last().click()

    await expect(page.getByText('42')).toBeVisible()
    await expect(page.getByLabel('Reassign leads to')).toBeVisible()

    // The confirm stays disabled until someone is chosen.
    const confirm = page.getByRole('button', { name: 'Deactivate', exact: true }).last()
    await expect(confirm).toBeDisabled()

    await page.getByLabel('Reassign leads to').selectOption({ label: 'Ada Owner (Root)' })
    await expect(confirm).toBeEnabled()
    await confirm.click()

    await expect(page.getByRole('button', { name: 'Reactivate' })).toBeVisible()
  })

  test('the seat limit surfaces as a readable error, not a raw 409', async ({ page }) => {
    await signIn(page, {
      members: [
        memberDetail({ id: 'bbbbbbbb-0000-0000-0000-000000000001', has_license: true }),
        memberDetail({ id: REP_MEMBERSHIP, has_license: true }),
        memberDetail({
          id: 'bbbbbbbb-0000-0000-0000-000000000003',
          has_license: true,
          user: {
            id: 'cccccccc-0000-0000-0000-000000000003',
            email: 'third@example.com',
            full_name: 'Tam Third',
            is_active: true,
          },
        }),
        memberDetail({
          id: 'bbbbbbbb-0000-0000-0000-000000000004',
          has_license: false,
          user: {
            id: 'cccccccc-0000-0000-0000-000000000004',
            email: 'fourth@example.com',
            full_name: 'Fen Fourth',
            is_active: true,
          },
        }),
      ],
    })

    await page.getByRole('button', { name: 'Assign seat' }).click()
    await expect(page.getByRole('alert')).toContainText('seats are in use')
  })

  test('the invite dialog offers the workspace’s own templates', async ({ page }) => {
    // Never a hardcoded role list — a customer who renamed or added templates
    // must see exactly what they configured.
    await signIn(page)

    await page.getByRole('button', { name: 'Invite member' }).click()
    await expect(page.getByLabel('Permission template').locator('option')).toHaveText([
      'Select a template…',
      'Root',
      'Caller',
    ])
  })
})
