/**
 * The filter builder, column picker and saved filters (M6).
 *
 * M6's definition of done names two filters and requires that both work
 * **through the builder**:
 *
 *   "status went from HOT to Lost in the last 7 days"
 *   "no outgoing call in 14 days"
 *
 * The backend suite proves those return the right leads. What these specs prove
 * is the half only a browser can: that a user can *express* them from labelled
 * controls, and that the document the page sends is the one they described.
 *
 * The assertion is against `stub.lastSearch()` — the body the page actually
 * posted. Checking the rendered rows would only prove the stub echoed
 * something; checking the request proves the builder encoded the rule.
 */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { stubApi } from './fixtures/api'

async function signIn(page: Page) {
  const stub = await stubApi(page)
  await page.goto('/')
  await page.getByLabel('Email').fill('owner@example.com')
  await page.getByLabel('Password').fill('correct-horse-battery-staple')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
  return stub
}

async function openBuilder(page: Page) {
  await page.getByRole('button', { name: /^Filters/ }).click()
}

test.describe('the filter builder', () => {
  test('history predicates are first-class rules, not an advanced mode', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)

    // Four of the ten filters the audit observed query the timeline. They sit
    // beside the field rule, at the same level, with no JSON in sight.
    for (const label of [
      '+ Field',
      '+ Has not done',
      '+ Has done',
      '+ Stage moved',
      '+ Reassigned',
    ]) {
      await expect(page.getByRole('button', { name: label })).toBeVisible()
    }
    await expect(page.locator('textarea')).toHaveCount(0)
  })

  test('builds “no outgoing call in 14 days” from dropdowns', async ({ page }) => {
    const stub = await signIn(page)
    await openBuilder(page)

    await page.getByRole('button', { name: '+ Has not done' }).click()

    // The rule arrives pre-filled with exactly this question, because it is the
    // one a telecalling team asks every morning.
    await expect(page.getByLabel('Action')).toHaveValue('CALL_LOGGED')
    await expect(page.getByLabel('Call direction')).toHaveValue('OUTGOING')
    await expect(page.getByLabel('Time window')).toHaveValue('relative')
    await expect(page.getByLabel('Number of days')).toHaveValue('14')

    await expect
      .poll(() => stub.lastSearch()?.filter?.children?.[0])
      .toMatchObject({
        type: 'action_not_performed',
        action_kind: 'CALL_LOGGED',
        payload_match: { direction: 'OUTGOING' },
        within: { last_days: 14 },
      })
  })

  test('builds a stage transition within a window', async ({ page }) => {
    const stub = await signIn(page)
    await openBuilder(page)

    await page.getByRole('button', { name: '+ Stage moved' }).click()
    // Stage options are the workspace's own — the picker is populated from the
    // pipeline endpoint, so nothing here names a stage the product invented.
    await page.getByLabel('From stage').selectOption({ index: 1 })
    await page.getByLabel('To stage').selectOption({ index: 2 })
    await page.getByLabel('Number of days').fill('7')

    await expect
      .poll(() => stub.lastSearch()?.filter?.children?.[0])
      .toMatchObject({
        type: 'status_changed',
        within: { last_days: 7 },
      })
    const rule = stub.lastSearch()?.filter?.children?.[0] as Record<string, unknown>
    expect(rule.from_stage_id).toBeTruthy()
    expect(rule.to_stage_id).toBeTruthy()
    expect(rule.from_stage_id).not.toEqual(rule.to_stage_id)
  })

  test('a field rule offers only the operators its type supports', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)
    await page.getByRole('button', { name: '+ Field' }).click()

    // Name is TEXT, so it offers textual operators and no numeric comparison.
    const operators = page.getByLabel('Operator')
    await expect(operators.locator('option', { hasText: 'contains' })).toHaveCount(1)
    await expect(operators.locator('option', { hasText: 'is greater than' })).toHaveCount(0)
  })

  test('switching field keeps a shared operator and drops one the new type lacks', async ({
    page,
  }) => {
    await signIn(page)
    await openBuilder(page)
    await page.getByRole('button', { name: '+ Field' }).click()

    // Shared: both types offer `eq`, so a half-built rule survives the switch
    // rather than resetting under the user.
    await page.getByLabel('Operator').selectOption('eq')
    await page.getByLabel('Field').selectOption('phone')
    await expect(page.getByLabel('Operator')).toHaveValue('eq')

    // Not shared: this registry gives PHONE `eq` alone, so `contains` cannot
    // survive — and the rule falls back to something the type does support
    // instead of sending one the server would refuse.
    await page.getByLabel('Field').selectOption('name')
    await page.getByLabel('Operator').selectOption('contains')
    await page.getByLabel('Field').selectOption('phone')
    await expect(page.getByLabel('Operator')).toHaveValue('eq')
  })

  test('groups nest, and an empty filter matches everything', async ({ page }) => {
    const stub = await signIn(page)
    await openBuilder(page)

    await page.getByRole('button', { name: '+ Group' }).click()
    await expect(page.getByRole('button', { name: 'Remove group' })).toHaveCount(1)
    await expect(page.getByLabel('Match')).toHaveCount(2)

    await page.getByRole('button', { name: 'Clear' }).click()
    // Cleared means "no filter", not "a filter matching nothing".
    await expect.poll(() => stub.lastSearch()?.filter ?? null).toBeNull()
  })

  test('a rule can be removed again', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)

    await page.getByRole('button', { name: '+ Has not done' }).click()
    await expect(page.getByLabel('Action')).toHaveCount(1)
    await page.getByRole('button', { name: 'Remove rule' }).click()
    await expect(page.getByLabel('Action')).toHaveCount(0)
  })
})

test.describe('saved filters', () => {
  test('a filter can be saved and appears in the picker', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)
    await page.getByRole('button', { name: '+ Has not done' }).click()

    await page.getByRole('button', { name: 'Save as filter' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Needs a call today')
    await page.getByLabel('Who can see it').selectOption('SHARED')
    await page.getByRole('button', { name: 'Save filter' }).click()

    await expect(page.getByLabel('Saved filter')).toContainText('Needs a call today')
  })

  test('the save dialog explains that sharing a filter is not sharing data', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)
    await page.getByRole('button', { name: '+ Field' }).click()
    await page.getByRole('button', { name: 'Save as filter' }).click()

    // The property the whole visibility model rests on, said out loud where the
    // decision is being made.
    await expect(
      page.getByText(/only the leads and columns their own permissions allow/i),
    ).toBeVisible()
  })

  test('a role-scoped filter cannot be saved without naming a template', async ({ page }) => {
    await signIn(page)
    await openBuilder(page)
    await page.getByRole('button', { name: '+ Field' }).click()
    await page.getByRole('button', { name: 'Save as filter' }).click()

    await page.getByLabel('Name', { exact: true }).fill('Caller worklist')
    await page.getByLabel('Who can see it').selectOption('ROLE')
    await expect(page.getByRole('button', { name: 'Save filter' })).toBeDisabled()

    await page.getByLabel('Permission template').selectOption({ index: 1 })
    await expect(page.getByRole('button', { name: 'Save filter' })).toBeEnabled()
  })
})

test.describe('columns', () => {
  test('the picker offers built-ins and the workspace’s own fields', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Columns' }).click()

    await expect(page.getByRole('heading', { name: 'Columns' })).toBeVisible()
    // Grouped, because "Stage" and "Guardian Name" are different kinds of
    // thing: one the product owns, one an admin invented.
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByText('Built in')).toBeVisible()
    await expect(dialog.getByText('Fields', { exact: true })).toBeVisible()
    await expect(page.getByLabel('Email', { exact: true })).toBeVisible()
  })

  test('choosing a field column adds it to the grid', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Columns' }).click()

    await page.getByLabel('Email', { exact: true }).check()
    await page.getByRole('button', { name: 'Save columns' }).click()

    await expect(page.getByRole('table').getByRole('columnheader', { name: 'Email' })).toBeVisible()
  })

  test('chosen columns reorder from the keyboard, not only by dragging', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Columns' }).click()

    const chosen = page.getByRole('listitem')
    await expect(chosen.first()).toContainText('Identifier')
    await page.getByRole('button', { name: 'Move Stage up' }).click()
    await expect(chosen.first()).toContainText('Stage')
  })
})

test.describe('sorting', () => {
  test('a built-in column sorts, an unindexed field does not offer to', async ({ page }) => {
    await signIn(page)

    // Sorting is restricted to built-in columns plus declared indexed fields,
    // so an unindexed field's header is inert rather than offering an action
    // the server would answer 400 to.
    const table = page.getByRole('table')
    await expect(table.getByRole('button', { name: 'Identifier' })).toBeVisible()

    await page.getByRole('button', { name: 'Columns' }).click()
    await page.getByLabel('Email', { exact: true }).check()
    await page.getByRole('button', { name: 'Save columns' }).click()

    await expect(table.getByRole('columnheader', { name: 'Email' })).toBeVisible()
    await expect(table.getByRole('button', { name: 'Email' })).toHaveCount(0)
  })

  test('clicking a sortable header asks the server for that order', async ({ page }) => {
    const stub = await signIn(page)

    await page.getByRole('table').getByRole('button', { name: 'Identifier' }).click()
    await expect.poll(() => stub.lastSearch()?.sort).toBe('identity_value')

    await page.getByRole('table').getByRole('button', { name: 'Identifier' }).click()
    await expect.poll(() => stub.lastSearch()?.sort).toBe('-identity_value')
  })
})
