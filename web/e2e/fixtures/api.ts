import type { Page, Route } from '@playwright/test'

/**
 * A stubbed API, so the frontend E2E suite stays hermetic.
 *
 * The real backend is covered by 123 pytest tests including the cross-workspace
 * isolation matrix; what these tests need to prove is *frontend* behaviour —
 * that the refresh interceptor retries once, that a 409 drives the
 * reassignment step, that a licence error surfaces its own copy.
 *
 * The stub therefore models the API's contract, not its implementation: same
 * status codes, same `{detail: {code, message}}` shape.
 */

export const WORKSPACE_A = '11111111-1111-1111-1111-111111111111'
export const WORKSPACE_B = '22222222-2222-2222-2222-222222222222'

const TEMPLATE_ROOT = 'aaaaaaaa-0000-0000-0000-000000000001'
const TEMPLATE_CALLER = 'aaaaaaaa-0000-0000-0000-000000000004'

export const OWNER_MEMBERSHIP = 'bbbbbbbb-0000-0000-0000-000000000001'
export const REP_MEMBERSHIP = 'bbbbbbbb-0000-0000-0000-000000000002'

function workspaceSummary(id: string, name: string) {
  return {
    id,
    name,
    slug: name.toLowerCase().replace(/\s+/g, '-'),
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    default_country_code: '91',
  }
}

const OWNER_USER = {
  id: 'cccccccc-0000-0000-0000-000000000001',
  email: 'owner@example.com',
  full_name: 'Ada Owner',
  is_active: true,
}

const REP_USER = {
  id: 'cccccccc-0000-0000-0000-000000000002',
  email: 'rep@example.com',
  full_name: 'Rey Rep',
  is_active: true,
}

export function membershipSummary(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: OWNER_MEMBERSHIP,
    workspace: workspaceSummary(WORKSPACE_A, 'Acme Sales'),
    template_id: TEMPLATE_ROOT,
    template_name: 'Root',
    is_active: true,
    has_license: true,
    availability: 'WORKING',
    manager_id: null,
    ...overrides,
  }
}

export function memberDetail(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: REP_MEMBERSHIP,
    workspace_id: WORKSPACE_A,
    user: REP_USER,
    template_id: TEMPLATE_CALLER,
    template_name: 'Caller',
    manager_id: null,
    is_active: true,
    has_license: true,
    availability: 'WORKING',
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

function ownerDetail() {
  return memberDetail({
    id: OWNER_MEMBERSHIP,
    user: OWNER_USER,
    template_id: TEMPLATE_ROOT,
    template_name: 'Root',
  })
}

export interface StubOptions {
  /** Login outcome. `ok` issues tokens; anything else is the error code. */
  readonly loginResult?: 'ok' | 'no_license' | 'member_inactive' | 'invalid_credentials'
  /** Workspaces the signed-in user belongs to. */
  readonly memberships?: ReturnType<typeof membershipSummary>[]
  /** Members returned by the list endpoint. */
  readonly members?: ReturnType<typeof memberDetail>[]
  /** Open leads the rep holds; drives the 409 on deactivate. */
  readonly openLeadCount?: number
}

export interface StubHandle {
  /** Every request path the app made, in order. */
  readonly requests: string[]
  /** Forces the next N authenticated calls to answer 401. */
  expireAccessToken: () => void
  readonly refreshCount: () => number
}

const ERROR_MESSAGES: Record<string, string> = {
  no_license: 'No licensed membership is available for this account',
  member_inactive: 'All of your memberships have been deactivated',
  invalid_credentials: 'Email or password is incorrect',
}

export async function stubApi(page: Page, options: StubOptions = {}): Promise<StubHandle> {
  const {
    loginResult = 'ok',
    memberships = [membershipSummary()],
    members = [ownerDetail(), memberDetail()],
    openLeadCount = 0,
  } = options

  const requests: string[] = []
  let accessTokenExpired = false
  let refreshCount = 0
  let currentMembers = [...members]

  async function json(route: Route, status: number, body: unknown) {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  }

  async function apiError(route: Route, status: number, code: string, extra = {}) {
    await json(route, status, {
      detail: { code, message: ERROR_MESSAGES[code] ?? code, ...extra },
    })
  }

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace('/api/v1', '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)

    // --- auth ------------------------------------------------------------
    if (path === '/auth/login') {
      if (loginResult !== 'ok') {
        const status = loginResult === 'invalid_credentials' ? 401 : 403
        return apiError(route, status, loginResult)
      }
      return json(route, 200, {
        access_token: 'access-1',
        refresh_token: 'refresh-1',
        token_type: 'bearer',
        expires_in: 1800,
        user: OWNER_USER,
        memberships,
      })
    }

    if (path === '/auth/refresh') {
      refreshCount += 1
      accessTokenExpired = false
      return json(route, 200, {
        access_token: `access-${refreshCount + 1}`,
        refresh_token: `refresh-${refreshCount + 1}`,
        token_type: 'bearer',
        expires_in: 1800,
        user: OWNER_USER,
        memberships,
      })
    }

    if (path === '/auth/logout') {
      return route.fulfill({ status: 204, body: '' })
    }

    // Everything below needs a live access token.
    if (accessTokenExpired) {
      return apiError(route, 401, 'invalid_token')
    }

    if (path === '/me') {
      return json(route, 200, { user: OWNER_USER, memberships })
    }

    if (path === '/me/permissions') {
      return json(route, 200, {
        workspace_id: url.searchParams.get('workspace_id'),
        membership_id: OWNER_MEMBERSHIP,
        template_id: TEMPLATE_ROOT,
        template_name: 'Root',
        capabilities: { leads: { admin_access: true }, permissions: { admin_access: true } },
        visible_membership_ids: currentMembers.map((m) => m.id),
        sees_all_members: true,
        field_grants: {},
      })
    }

    // --- tenant ----------------------------------------------------------
    if (path.endsWith('/settings/permission-templates')) {
      return json(route, 200, [
        { id: TEMPLATE_ROOT, name: 'Root', is_system: true, is_readonly: true },
        { id: TEMPLATE_CALLER, name: 'Caller', is_system: true, is_readonly: false },
      ])
    }

    if (path.endsWith('/members/seats')) {
      return json(route, 200, {
        seats_used: currentMembers.filter((m) => m.has_license).length,
        seat_limit: 3,
      })
    }

    if (path.endsWith('/members') && method === 'GET') {
      return json(route, 200, {
        items: currentMembers,
        total: currentMembers.length,
        limit: 100,
        offset: 0,
      })
    }

    const deactivateMatch = /\/members\/([^/]+)\/deactivate$/.exec(path)
    if (deactivateMatch) {
      const body = route.request().postDataJSON() as {
        reassign_to_membership_id: string | null
      }
      if (openLeadCount > 0 && !body.reassign_to_membership_id) {
        return apiError(route, 409, 'reassignment_required', {
          open_lead_count: openLeadCount,
        })
      }
      currentMembers = currentMembers.map((m) =>
        m.id === deactivateMatch[1]
          ? { ...m, is_active: false, has_license: false, availability: 'INACTIVE' }
          : m,
      )
      return json(route, 200, {
        member: currentMembers.find((m) => m.id === deactivateMatch[1]),
        leads_reassigned: openLeadCount,
      })
    }

    const licenseMatch = /\/members\/([^/]+)\/license$/.exec(path)
    if (licenseMatch) {
      if (method === 'POST' && currentMembers.filter((m) => m.has_license).length >= 3) {
        return apiError(route, 409, 'seat_limit_reached', { seat_limit: 3, seats_used: 3 })
      }
      currentMembers = currentMembers.map((m) =>
        m.id === licenseMatch[1] ? { ...m, has_license: method === 'POST' } : m,
      )
      return json(
        route,
        200,
        currentMembers.find((m) => m.id === licenseMatch[1]),
      )
    }

    return json(route, 404, { detail: { code: 'not_found', message: 'Not found' } })
  })

  return {
    requests,
    expireAccessToken: () => {
      accessTokenExpired = true
    },
    refreshCount: () => refreshCount,
  }
}
