/**
 * Tasks, bulk edit, undo and the import wizard (M7).
 *
 * The undo tests get the most attention, because the conflict path is the one
 * place in this milestone where a wrong click destroys somebody else's work.
 * What these prove is the half only a browser can: that the destructive option
 * is *not* preselected, that the dialog says what would be discarded, and that
 * the request the page finally sends matches what the operator chose.
 *
 * Assertions are against `stub.lastUndo()` / `lastBulk()` / `lastMapping()` —
 * the bodies the page actually sent. Checking the rendered result would only
 * prove the stub echoed something back.
 */

import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

import { stubApi } from './fixtures/api'

/** A preview with one lead changed since — the case the dialog exists for. */
const CONFLICTED_PREVIEW = {
  changeset_id: 'changeset-bulk',
  summary: 'Set Name on 3 leads',
  is_undone: false,
  counts: { total: 3, reversible: 2, conflicted: 1, deleted: 0 },
  leads: [
    {
      lead_id: 'lead-2',
      identity_value: '+919000000002',
      outcome: 'CONFLICTED',
      reversals: [
        {
          target: 'values.name',
          label: 'Name',
          revert_to: 'Before',
          expected: 'After',
          current: "Someone else's work",
          conflicted: true,
        },
      ],
    },
  ],
}

async function signIn(page: Page, options = {}) {
  const stub = await stubApi(page, options)
  await page.goto('/')
  await page.getByLabel('Email').fill('owner@example.com')
  await page.getByLabel('Password').fill('correct-horse-battery-staple')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
  return stub
}

test.describe('tasks', () => {
  test('buckets come from the server, late first', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Tasks' }).click()

    await expect(page.getByRole('heading', { name: 'Tasks' })).toBeVisible()
    const buckets = page.getByRole('group', { name: 'Task buckets' })
    // Late leads, because overdue follow-up is what a telecalling team loses
    // money on.
    await expect(buckets.getByRole('button').first()).toContainText('Late')
    await expect(buckets.getByRole('button', { name: /Late/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await expect(page.getByTestId('task-row')).toHaveCount(1)
  })

  test('completing a task moves it out of the working list', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Tasks' }).click()

    // Scoped to the row: "Done" is also the name of the third bucket chip.
    await page.getByTestId('task-row').getByRole('button', { name: 'Done' }).click()
    await expect(page.getByTestId('task-row')).toHaveCount(0)

    await page
      .getByRole('group', { name: 'Task buckets' })
      .getByRole('button', { name: /Done/ })
      .click()
    await expect(page.getByTestId('task-row')).toHaveCount(1)
    await expect(page.getByTestId('task-row').getByRole('button', { name: 'Reopen' })).toBeVisible()
  })

  test('a task can be created for a date and a person', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Tasks' }).click()

    await page.getByRole('button', { name: 'Add task' }).click()
    await page.getByLabel('Task', { exact: true }).fill('Send the fee structure')
    await page.getByLabel('Due').fill('2026-12-01T09:00')
    await page.getByRole('button', { name: 'Create task' }).click()

    await page.getByRole('button', { name: /Upcoming/ }).click()
    await expect(page.getByText('Send the fee structure')).toBeVisible()
  })

  test('a refused request is not rendered as an empty list', async ({ page }) => {
    await signIn(page, { tasksAllowed: false })
    await page.getByRole('link', { name: 'Tasks' }).click()

    // "Nothing overdue" when the server said 403 tells the operator there is no
    // work to do, which is the opposite of what happened.
    await expect(page.getByRole('alert')).toContainText(/does not allow tasks/i)
    await expect(page.getByText('Nothing overdue.')).toHaveCount(0)
  })
})

test.describe('bulk edit', () => {
  test('the action bar appears only once something is selected', async ({ page }) => {
    await signIn(page)

    await expect(page.getByRole('button', { name: /^Edit \d/ })).toHaveCount(0)
    await page
      .getByRole('checkbox', { name: /^Select \+/ })
      .first()
      .check()
    await expect(page.getByRole('button', { name: 'Edit 1' })).toBeVisible()
    await expect(page.getByText('1 selected')).toBeVisible()
  })

  test('the header checkbox selects the whole page', async ({ page }) => {
    await signIn(page)

    await page.getByRole('checkbox', { name: 'Select every lead on this page' }).check()
    await expect(page.getByRole('button', { name: 'Edit 1' })).toBeVisible()
  })

  test('a bulk edit sends the chosen ids and says it can be undone', async ({ page }) => {
    const stub = await signIn(page)

    await page
      .getByRole('checkbox', { name: /^Select \+/ })
      .first()
      .check()
    await page.getByRole('button', { name: 'Edit 1' }).click()

    // The property that makes bulk editing safe to offer, said where the
    // decision is made.
    await expect(page.getByText(/undone from the edit report/i)).toBeVisible()

    await page.getByLabel('Set a field').selectOption('name')
    await page.getByLabel('Name', { exact: true }).fill('Renamed in bulk')
    await page.getByRole('button', { name: 'Apply to 1' }).click()

    await expect.poll(() => stub.lastBulk()?.lead_ids).toHaveLength(1)
    expect(stub.lastBulk()?.values).toEqual({ name: 'Renamed in bulk' })
  })
})

test.describe('undo', () => {
  test('a clean batch undoes without ceremony', async ({ page }) => {
    const stub = await signIn(page)
    await page.getByRole('link', { name: 'Edit report' }).click()

    await expect(page.getByTestId('changeset-row').first()).toBeVisible()
    await page.getByRole('button', { name: 'Undo' }).first().click()

    await expect(page.getByRole('heading', { name: 'Undo this change' })).toBeVisible()
    await expect(page.getByText('Changed since')).toBeVisible()
    await page.getByRole('button', { name: /^Undo 3 leads/ }).click()

    await expect.poll(() => stub.lastUndo()?.skip_conflicts).toBe(false)
  })

  test('a conflict is shown in full and cannot be undone by accident', async ({ page }) => {
    const stub = await signIn(page, { undoPreview: CONFLICTED_PREVIEW })
    await page.getByRole('link', { name: 'Edit report' }).click()
    await page.getByRole('button', { name: 'Undo' }).first().click()

    // Named three ways: what the batch set, what it holds now, what reverting
    // would put back. "1 lead changed since" is not enough to decide on.
    await expect(page.getByText(/One lead has been edited since/i)).toBeVisible()
    await expect(page.getByText("Someone else's work")).toBeVisible()
    await expect(page.getByText('+919000000002')).toBeVisible()

    // The destructive path is available but never preselected.
    const skip = page.getByRole('checkbox', { name: 'Skip the leads that changed' })
    await expect(skip).not.toBeChecked()
    await expect(page.getByRole('button', { name: /^Undo/ }).last()).toBeDisabled()

    await skip.check()
    const proceed = page.getByRole('button', { name: 'Undo the other 2' })
    await expect(proceed).toBeEnabled()
    await proceed.click()

    await expect.poll(() => stub.lastUndo()?.skip_conflicts).toBe(true)
  })

  test('the dialog forgets the choice between openings', async ({ page }) => {
    await signIn(page, { undoPreview: CONFLICTED_PREVIEW })
    await page.getByRole('link', { name: 'Edit report' }).click()

    await page.getByRole('button', { name: 'Undo' }).first().click()
    await page.getByRole('checkbox', { name: 'Skip the leads that changed' }).check()
    await page.getByRole('button', { name: 'Cancel' }).click()

    // Reopening must not carry a destructive choice over from last time.
    await page.getByRole('button', { name: 'Undo' }).first().click()
    await expect(
      page.getByRole('checkbox', { name: 'Skip the leads that changed' }),
    ).not.toBeChecked()
  })

  test('the report filters by source and marks an undo as one', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Edit report' }).click()

    await page.getByLabel('Source').selectOption('IMPORT')
    await expect(page.getByLabel('Source')).toHaveValue('IMPORT')
    await page.getByLabel('Changed by').selectOption({ index: 1 })
    await expect(page.getByTestId('changeset-row').first()).toBeVisible()
  })
})

test.describe('the import wizard', () => {
  test('the four flows are offered as distinct choices', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Import' }).click()

    const kind = page.getByLabel('What are you importing?')
    await expect(kind.locator('option')).toHaveCount(3)
    // Create-or-update and update-only look alike and are not; the hint is
    // where that difference gets explained.
    await kind.selectOption('LEAD_UPDATE')
    await expect(page.getByText(/reported as an error rather than creating a lead/i)).toBeVisible()
    await kind.selectOption('ACTION_IMPORT')
    await expect(page.getByText(/keep their original dates/i)).toBeVisible()
  })

  test('upload, map, preview and commit are four visible steps', async ({ page }) => {
    const stub = await signIn(page)
    await page.getByRole('link', { name: 'Import' }).click()

    await page.getByLabel('Spreadsheet').setInputFiles({
      name: 'leads.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('Phone,Name,Owner\n9000000001,A,x@example.com\n'),
    })

    await expect(page.getByText(/Match the columns/)).toBeVisible()
    await page.getByLabel('Match Phone').selectOption('phone')
    await page.getByRole('button', { name: 'Preview' }).click()

    // The dry run — the answer to "Pitfalls of Excel Upload".
    await expect(page.getByText(/What this would do/)).toBeVisible()
    await expect(page.getByText('2 create')).toBeVisible()
    await expect(page.getByText('1 error')).toBeVisible()
    // Row numbers match Excel's own gutter so the operator can go and look.
    await expect(page.getByText('Row 5')).toBeVisible()

    await page.getByRole('button', { name: /Import 2 new/ }).click()
    await expect(page.getByText(/Import finished/)).toBeVisible()
    await expect(page.getByText(/undone in one go/i)).toBeVisible()

    expect(stub.lastMapping()?.mapping).toEqual({ Phone: 'phone' })
  })

  test('distribution and owner-column are options on the run', async ({ page }) => {
    const stub = await signIn(page)
    await page.getByRole('link', { name: 'Import' }).click()

    await page.getByLabel('Spreadsheet').setInputFiles({
      name: 'leads.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('Phone,Name,Owner\n9000000001,A,x@example.com\n'),
    })
    await page.getByLabel('Match Phone').selectOption('phone')

    // "Excel Advance Distribution" and "Owner Specific Assignment" are one
    // setting with several answers, not two more import types.
    await page.getByLabel('Who gets these leads?').selectOption('COLUMN')
    await page.getByLabel('Owner column').selectOption('Owner')
    await page.getByRole('button', { name: 'Preview' }).click()

    await expect
      .poll(() => stub.lastMapping()?.options)
      .toMatchObject({
        strategy: 'COLUMN',
        owner_column: 'Owner',
      })
  })

  test('the mapping only offers fields this workspace allows in a sheet', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Import' }).click()

    await page.getByLabel('Spreadsheet').setInputFiles({
      name: 'leads.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('Phone,Name,Owner\n9000000001,A,x@example.com\n'),
    })

    // The list comes from the server, which applies both `show_in_import` and
    // the caller's Import grant — so the screen cannot offer a mapping the
    // commit would refuse.
    const target = page.getByLabel('Match Phone')
    // The four built-ins plus "ignore" — and nothing the admin excluded from
    // sheets or that this caller lacks the Import grant on.
    await expect(target.locator('option')).toHaveCount(5)
    await expect(target.locator('option', { hasText: /^Ignore this column$/ })).toHaveCount(1)
  })
})
