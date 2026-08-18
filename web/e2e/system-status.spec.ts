import { expect, test, type Page } from '@playwright/test'

/**
 * Smoke test: the app boots, calls /health, and renders the result.
 *
 * The API is stubbed so the test stays hermetic — `docker compose up` plus a
 * green /health is verified separately, against the real stack.
 */

const HEALTH_ROUTE = '**/api/v1/health'

function healthPayload(overrides: {
  status: 'ok' | 'degraded'
  redisStatus: 'ok' | 'error'
  redisError: string | null
}) {
  return {
    status: overrides.status,
    service: 'Configurable CRM API',
    version: '0.1.0',
    environment: 'test',
    checks: {
      database: { status: 'ok', latency_ms: 1.5, error: null },
      redis: {
        status: overrides.redisStatus,
        latency_ms: 0.9,
        error: overrides.redisError,
      },
      object_storage: { status: 'ok', latency_ms: 4.2, error: null },
    },
  }
}

async function stubHealth(page: Page, statusCode: number, body: unknown) {
  await page.route(HEALTH_ROUTE, async (route) => {
    await route.fulfill({
      status: statusCode,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
}

test('renders a green report when every backing service is healthy', async ({ page }) => {
  await stubHealth(page, 200, healthPayload({ status: 'ok', redisStatus: 'ok', redisError: null }))

  await page.goto('/')

  await expect(page.getByTestId('overall-status')).toHaveText('All systems operational')
  await expect(page.getByTestId('service-identity')).toContainText('Configurable CRM API')

  for (const check of ['database', 'redis', 'object_storage']) {
    await expect(page.getByTestId(`check-${check}`).getByTestId('check-status')).toHaveText('ok')
  }
})

test('renders a degraded report and the failure detail', async ({ page }) => {
  await stubHealth(
    page,
    503,
    healthPayload({
      status: 'degraded',
      redisStatus: 'error',
      redisError: 'ConnectionError: connection refused',
    }),
  )

  await page.goto('/')

  await expect(page.getByTestId('overall-status')).toHaveText('Degraded')
  await expect(page.getByTestId('check-redis').getByTestId('check-status')).toHaveText('error')
  await expect(page.getByTestId('check-redis').getByTestId('check-error')).toContainText(
    'connection refused',
  )
})
