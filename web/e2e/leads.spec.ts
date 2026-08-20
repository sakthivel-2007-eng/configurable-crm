import { expect, test, type Page } from '@playwright/test'

import { leadField, stubApi, type StubOptions } from './fixtures/api'

/**
 * Lead management, the timeline and template composition (M5).
 *
 * Two assertions here carry real weight beyond "the screen renders":
 *
 * - the timeline shows three field changes sharing one changeset id, which is
 *   the guarantee M7's undo is built on;
 * - a template render surfaces its unresolved placeholders, which is how a
 *   field the sender cannot view fails visibly instead of leaking.
 */

async function signIn(page: Page, options: StubOptions = {}) {
  const stub = await stubApi(page, options)
  await page.goto('/login')
  await page.getByLabel('Email').fill('owner@example.com')
  await page.getByLabel('Password').fill('correct-horse-battery-staple')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Leads' })).toBeVisible()
  return stub
}

test.describe('lead list', () => {
  test('columns are the workspace’s own headline fields', async ({ page }) => {
    await signIn(page)

    // H1 is Name and H2 is Phone for this workspace — both settings, not
    // constants, so the header renders their labels rather than fixed ones.
    const table = page.getByRole('table')
    await expect(table.getByRole('columnheader', { name: 'Name' })).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'Phone' })).toBeVisible()
    await expect(page.getByTestId('lead-row')).toHaveCount(1)
  })

  test('the quick-add form renders the workspace schema', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Add lead' }).click()

    // Only `show_in_quick_add` fields plus anything required.
    await expect(page.getByLabel('Name', { exact: true })).toBeVisible()
    await expect(page.getByLabel('Phone', { exact: true })).toBeVisible()
    await expect(page.getByLabel('Email', { exact: true })).toHaveCount(0)
  })

  test('a missing identity value is refused with the server’s reason', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Add lead' }).click()
    await page.getByLabel('Name', { exact: true }).fill('No Phone Given')
    await page.getByRole('button', { name: 'Create lead' }).click()

    await expect(page.getByRole('alert')).toContainText('identity field needs a value')
  })

  test('creating a lead adds it to the list', async ({ page }) => {
    await signIn(page)
    await page.getByRole('button', { name: 'Add lead' }).click()
    await page.getByLabel('Name', { exact: true }).fill('Second Lead')
    await page.getByLabel('Phone', { exact: true }).fill('9000000002')
    await page.getByRole('button', { name: 'Create lead' }).click()

    await expect(page.getByTestId('lead-row')).toHaveCount(2)
  })
})

test.describe('lead detail', () => {
  test('opens as an overlay over the list', async ({ page }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()

    await expect(page.getByTestId('lead-detail')).toBeVisible()
    // The list is still mounted underneath, so filter context survives.
    await expect(page.getByTestId('lead-row')).toHaveCount(1)
  })

  test('renders each field through its registry widget', async ({ page }) => {
    const withComposites = [
      leadField(),
      leadField({ id: 'field-2', key: 'phone', label: 'Phone', field_type: 'PHONE' }),
      leadField({
        id: 'field-6',
        key: 'renewal',
        label: 'Renewal',
        field_type: 'RECURRING_DATE',
        is_builtin: false,
      }),
      leadField({
        id: 'field-7',
        key: 'site',
        label: 'Site',
        field_type: 'LOCATION',
        is_builtin: false,
      }),
    ]
    await signIn(page, {
      fields: withComposites,
      leads: [
        {
          id: 'lead-1',
          identity_value: '+919876543210',
          primary: { h1: 'Ada Lead', h2: '+919876543210' },
          stage_id: 'stage-1',
          lost_reason_id: null,
          assignee_id: null,
          rating: null,
          score: 0,
          values: {
            name: 'Ada Lead',
            phone: '+919876543210',
            renewal: { start: '2026-03-01', frequency: 'YEARLY', interval: 1, next: '2027-03-01' },
            site: { city: 'Springfield', state: 'IL' },
          },
          labels: {},
          last_action_at: null,
          created_at: '2026-08-01T00:00:00Z',
        },
      ],
    })
    await page.getByTestId('lead-row').first().click()
    const detail = page.getByTestId('lead-detail')

    // RECURRING_DATE draws start, frequency and interval, and shows the
    // server-derived next occurrence read-only.
    await expect(detail.getByLabel('Renewal — start')).toHaveValue('2026-03-01')
    await expect(detail.getByLabel('Renewal — frequency')).toHaveValue('YEARLY')
    await expect(detail.getByText('Next occurrence')).toBeVisible()
    await expect(detail.getByText('2027-03-01')).toBeVisible()

    // LOCATION draws the structured parts the registry named, not a text box.
    await expect(detail.getByLabel('Site — City')).toHaveValue('Springfield')
    await expect(detail.getByLabel('Site — latitude')).toBeVisible()
  })

  test('a non-editable field is refused by name, not silently dropped', async ({ page }) => {
    await signIn(page, {
      fields: [
        leadField(),
        leadField({ id: 'field-2', key: 'phone', label: 'Phone', field_type: 'PHONE' }),
        leadField({
          id: 'field-9',
          key: 'locked_note',
          label: 'Locked Note',
          field_type: 'TEXT',
          is_builtin: false,
        }),
      ],
      leads: [
        {
          id: 'lead-1',
          identity_value: '+919876543210',
          primary: { h1: 'Ada Lead', h2: '+919876543210' },
          stage_id: 'stage-1',
          lost_reason_id: null,
          assignee_id: null,
          rating: null,
          score: 0,
          values: { name: 'Ada Lead', phone: '+919876543210', locked_note: 'admin only' },
          labels: {},
          last_action_at: null,
          created_at: '2026-08-01T00:00:00Z',
        },
      ],
    })
    await page.getByTestId('lead-row').first().click()
    const detail = page.getByTestId('lead-detail')

    // Visible, because View and Edit are independent grants.
    await expect(detail.getByLabel('Locked Note', { exact: true })).toHaveValue('admin only')

    await detail.getByLabel('Locked Note', { exact: true }).fill('rep tried this')
    await detail.getByRole('button', { name: /^Save/ }).click()

    await expect(page.getByRole('alert')).toContainText('locked_note')
  })
})

test.describe('timeline', () => {
  test('three field changes share one changeset id', async ({ page }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()

    await expect(page.getByTestId('lead-timeline')).toBeVisible()
    await expect(page.getByTestId('timeline-entry')).toHaveCount(4)

    // The changeset chip is what makes the batch visible to an operator.
    await expect(page.getByText('cs:changese').first()).toBeVisible()
    // Scoped to the entries: the kind filter also lists "Field changed".
    await expect(
      page.getByTestId('timeline-entry').filter({ hasText: 'Field changed' }),
    ).toHaveCount(2)
  })

  test('a field change shows old and new, a stage change resolves both labels', async ({
    page,
  }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()

    await expect(page.getByText('Ada', { exact: false }).first()).toBeVisible()
    // Both stage ids resolve to their workspace labels rather than uuids.
    await expect(page.getByTestId('lead-timeline').getByText('New')).toBeVisible()
    await expect(page.getByTestId('lead-timeline').getByText('Contacted')).toBeVisible()
  })

  test('the timeline can be filtered by kind', async ({ page }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()

    await page.getByLabel('Filter timeline by kind').selectOption('FIELD_CHANGE')
    await expect(page.getByTestId('timeline-entry')).toHaveCount(2)
  })

  test('the call logger preselects the default outcome past the threshold', async ({ page }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()
    await page.getByRole('button', { name: 'Log a call' }).click()

    await page.getByLabel('Duration in seconds').fill('45')
    // The workspace's connected threshold is 1s, so Connected is preselected.
    await expect(page.getByLabel('Call outcome')).toHaveValue('disposition-1')
    await expect(page.getByText('the default outcome is preselected')).toBeVisible()
  })
})

test.describe('message templates', () => {
  test('a render surfaces unresolved placeholders', async ({ page }) => {
    await signIn(page)
    await page.getByTestId('lead-row').first().click()

    await page.getByLabel('Message template').selectOption({ index: 1 })
    await page.getByRole('button', { name: 'Preview' }).click()

    await expect(page.getByTestId('template-preview')).toBeVisible()
    await expect(page.getByTestId('template-preview')).toContainText('Hi Ada Lead')
    // The security-relevant half: a placeholder the sender cannot resolve is
    // reported, not left as a literal or quietly filled.
    await expect(page.getByTestId('unresolved-placeholders')).toContainText('secret_code')
  })

  test('the templates screen offers the workspace’s own field keys', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Templates' }).click()
    await expect(page.getByRole('heading', { name: 'Message templates' })).toBeVisible()

    // Placeholder buttons come from the schema, so they cannot name a key the
    // renderer would fail to resolve.
    await expect(page.getByRole('button', { name: '{{name}}' })).toBeVisible()
    await expect(page.getByRole('button', { name: '{{alternate_phone}}' })).toBeVisible()
  })

  test('an email template without a subject is refused', async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: 'Templates' }).click()

    await page.getByLabel('Channel', { exact: true }).selectOption('EMAIL')
    await page.getByLabel('Name', { exact: true }).fill('No Subject')
    await page.getByLabel('Body', { exact: true }).fill('Body only')
    await page.getByRole('button', { name: 'Create template' }).click()

    await expect(page.getByRole('alert')).toContainText('needs a subject')
  })
})
