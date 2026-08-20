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

// --- M2-M5 fixture data -------------------------------------------------------
//
// The registry entries carry the *real* renderer contracts, composites
// included, because these tests exist to prove the frontend draws whatever the
// server declares — a simplified stub would let a hardcoded type list pass.

export const FIELD_TYPES = [
  {
    key: 'TEXT',
    label: 'Text',
    description: 'Names, addresses, free text',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq', 'contains'],
    renderer: { widget: 'text', multiline: false },
    config_schema: {},
  },
  {
    key: 'DROPDOWN',
    label: 'Dropdown',
    description: 'One of a predefined list',
    storage: 'scalar',
    uses_options: true,
    operators: ['eq', 'in'],
    renderer: { widget: 'select', multiple: false },
    config_schema: {},
  },
  {
    key: 'TAGS',
    label: 'Tags',
    description: 'Many of a predefined list',
    storage: 'list',
    uses_options: true,
    operators: ['has_any'],
    renderer: { widget: 'select', multiple: true },
    config_schema: {},
  },
  {
    key: 'EMAIL',
    label: 'Email',
    description: 'Email addresses',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'email', inputMode: 'email' },
    config_schema: {},
  },
  {
    key: 'PHONE',
    label: 'Phone',
    description: 'Contact numbers',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'phone', inputMode: 'tel', normalisedToE164: true },
    config_schema: {},
  },
  {
    key: 'CHECKBOX',
    label: 'Checkbox',
    description: 'Yes/no, true/false',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'checkbox' },
    config_schema: {},
  },
  {
    key: 'DATE',
    label: 'Date',
    description: 'Calendar dates',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq', 'between'],
    renderer: { widget: 'date' },
    config_schema: {},
  },
  {
    key: 'MONEY',
    label: 'Money',
    description: 'Currency amounts',
    storage: 'object',
    uses_options: false,
    operators: ['gt'],
    renderer: { widget: 'money', amountKey: 'amount', currencyKey: 'currency' },
    config_schema: {},
  },
  {
    key: 'NUMBER',
    label: 'Number',
    description: 'Numeric values',
    storage: 'scalar',
    uses_options: false,
    operators: ['gt'],
    renderer: { widget: 'number' },
    config_schema: {},
  },
  {
    key: 'WEBSITE',
    label: 'Website',
    description: 'URLs',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'url', inputMode: 'url' },
    config_schema: {},
  },
  {
    key: 'DEPENDENT_DROPDOWN',
    label: 'Dependent dropdown',
    description: 'Cascading — Country to State, Category to Subcategory',
    storage: 'object',
    uses_options: true,
    operators: ['eq'],
    renderer: {
      widget: 'cascader',
      valueKey: 'value',
      parentKey: 'parent',
      optionsAreTree: true,
    },
    config_schema: {},
  },
  {
    key: 'RECURRING_DATE',
    label: 'Recurring date',
    description: 'Repeating events — birthdays, renewals',
    storage: 'object',
    uses_options: false,
    operators: ['between'],
    renderer: {
      widget: 'recurring-date',
      startKey: 'start',
      frequencyKey: 'frequency',
      intervalKey: 'interval',
      derivedKey: 'next',
      frequencies: ['YEARLY', 'MONTHLY', 'WEEKLY', 'DAILY'],
    },
    config_schema: {},
  },
  {
    key: 'LOCATION',
    label: 'Location',
    description: 'City/state/landmark or GPS coordinates',
    storage: 'object',
    uses_options: false,
    operators: ['contains'],
    renderer: {
      widget: 'location',
      textKeys: ['line1', 'line2', 'city', 'state', 'postal_code', 'country'],
      latKey: 'lat',
      lngKey: 'lng',
    },
    config_schema: {},
  },
]

export const ACTION_FIELD_TYPES = [
  ...FIELD_TYPES.filter((entry) =>
    ['TEXT', 'NUMBER', 'DATE', 'DROPDOWN', 'TAGS'].includes(entry.key),
  ),
  {
    key: 'USER',
    label: 'User',
    description: 'A picker over workspace members',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'member-select' },
    config_schema: {},
  },
  {
    key: 'FILE',
    label: 'File',
    description: 'An upload stored in object storage',
    storage: 'object',
    uses_options: false,
    operators: ['is_empty'],
    renderer: { widget: 'file' },
    config_schema: {},
  },
  {
    key: 'MEDIA_LINK',
    label: 'Media link',
    description: 'A link to an audio or video asset',
    storage: 'scalar',
    uses_options: false,
    operators: ['eq'],
    renderer: { widget: 'media-link' },
    config_schema: {},
  },
]

export function leadField(overrides: Record<string, unknown> = {}) {
  return {
    id: 'field-1',
    key: 'name',
    label: 'Name',
    field_type: 'TEXT',
    description: null,
    is_builtin: true,
    is_hidden: false,
    is_required: false,
    sort_order: 0,
    field_group: null,
    show_in_import: true,
    show_in_quick_add: true,
    lock_after_create: false,
    can_use_variable: false,
    config: {},
    options: [] as Record<string, unknown>[],
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

/** The four built-ins every workspace is provisioned with. */
export const BUILTIN_FIELDS = [
  leadField(),
  leadField({ id: 'field-2', key: 'phone', label: 'Phone', field_type: 'PHONE' }),
  leadField({
    id: 'field-3',
    key: 'email',
    label: 'Email',
    field_type: 'EMAIL',
    show_in_quick_add: false,
  }),
  leadField({
    id: 'field-4',
    key: 'alternate_phone',
    label: 'Alternate Phone',
    field_type: 'PHONE',
    show_in_quick_add: false,
  }),
]

export const STAGES = {
  initial: {
    id: 'stage-1',
    kind: 'INITIAL',
    label: 'New',
    color: '#6b7280',
    sort_order: 0,
    is_archived: false,
  },
  active: [
    {
      id: 'stage-2',
      kind: 'ACTIVE',
      label: 'Contacted',
      color: '#3b82f6',
      sort_order: 1,
      is_archived: false,
    },
  ],
  won: {
    id: 'stage-3',
    kind: 'WON',
    label: 'Won',
    color: '#22c55e',
    sort_order: 2,
    is_archived: false,
  },
  lost: {
    id: 'stage-4',
    kind: 'LOST',
    label: 'Lost',
    color: '#ef4444',
    sort_order: 3,
    is_archived: false,
  },
  archived: [] as Record<string, unknown>[],
}

export const LOST_REASONS = [
  { id: 'reason-1', label: 'Not interested', sort_order: 0, is_default: false, is_archived: false },
  { id: 'reason-2', label: 'Budget', sort_order: 1, is_default: false, is_archived: false },
  { id: 'reason-3', label: 'Unknown', sort_order: 2, is_default: true, is_archived: false },
]

export const DISPOSITIONS = [
  {
    id: 'disposition-1',
    label: 'Connected',
    is_default: true,
    is_system: true,
    is_archived: false,
    sort_order: 0,
  },
  {
    id: 'disposition-2',
    label: 'No Answer',
    is_default: false,
    is_system: true,
    is_archived: false,
    sort_order: 1,
  },
  {
    id: 'disposition-3',
    label: 'Left Voicemail',
    is_default: false,
    is_system: false,
    is_archived: false,
    sort_order: 2,
  },
]

export const CUSTOM_ACTIONS = [
  {
    id: 'action-type-1',
    code: 1001,
    name: 'Demo Given',
    icon: null,
    score: 50,
    direction: 'OUTBOUND',
    description: null,
    allow_predated: false,
    is_archived: false,
    fields: [
      {
        id: 'action-field-1',
        action_type_id: 'action-type-1',
        key: 'notes',
        label: 'Notes',
        field_type: 'TEXT',
        description: null,
        is_required: true,
        is_hidden: false,
        sort_order: 0,
        config: {},
        options: [] as Record<string, unknown>[],
      },
    ],
    created_at: '2026-08-01T00:00:00Z',
  },
]

export const PREFERENCES = {
  default_country_code: '91',
  timezone: 'Asia/Kolkata',
  currency: 'INR',
  connected_call_min_seconds: 1,
  session_timeout_minutes: null,
  leaderboard_metrics: { stage: true, rating: false },
  features: { campaign: true, custom_actions: true, sales_group: false },
}

export const CAPABILITY_SCHEMA = {
  access: [
    { key: 'leads', proposed: false, capabilities: ['admin_access', 'add_or_update', 'actions'] },
    { key: 'team', proposed: true, capabilities: ['view_members', 'invite_members'] },
    { key: 'reports', proposed: true, capabilities: ['view_reports'] },
  ],
  view: [
    { key: 'lead', proposed: true, capabilities: ['show_timeline', 'show_score'] },
    { key: 'dashboard', proposed: true, capabilities: ['show_personal_dashboard'] },
    { key: 'leads_table', proposed: true, capabilities: ['show_all_leads'] },
  ],
}

export interface StubLead {
  id: string
  identity_value: string
  primary: { h1: unknown; h2: unknown }
  stage_id: string | null
  lost_reason_id: string | null
  assignee_id: string | null
  rating: number | null
  score: number
  values: Record<string, unknown>
  labels: Record<string, unknown>
  last_action_at: string | null
  created_at: string
}

export function lead(overrides: Record<string, unknown> = {}): StubLead {
  return {
    id: 'lead-1',
    identity_value: '+919876543210',
    primary: { h1: 'Ada Lead', h2: '+919876543210' },
    stage_id: 'stage-1',
    lost_reason_id: null,
    assignee_id: null,
    rating: null,
    score: 0,
    values: { name: 'Ada Lead', phone: '+919876543210', email: 'ada@example.com' },
    labels: {},
    last_action_at: null,
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

export function action(overrides: Record<string, unknown> = {}) {
  return {
    id: `action-${Math.random().toString(16).slice(2)}`,
    lead_id: 'lead-1',
    changeset_id: 'changeset-1',
    kind: 'LEAD_CREATED',
    action_type_id: null,
    actor_id: null,
    payload: {},
    body: null,
    score_applied: 0,
    is_pinned: false,
    performed_at: '2026-08-01T10:00:00Z',
    created_at: '2026-08-01T10:00:00Z',
    ...overrides,
  }
}

/**
 * A timeline showing the M5 guarantee: three field changes sharing one
 * changeset id, which is what makes them undoable as a unit.
 */
export const TIMELINE = [
  action({
    id: 'action-1',
    kind: 'FIELD_CHANGE',
    changeset_id: 'changeset-2',
    payload: { field_key: 'name', label: 'Name', old: 'Ada', new: 'Ada Lead' },
  }),
  action({
    id: 'action-2',
    kind: 'FIELD_CHANGE',
    changeset_id: 'changeset-2',
    payload: { field_key: 'email', label: 'Email', old: null, new: 'ada@example.com' },
  }),
  action({
    id: 'action-3',
    kind: 'STAGE_CHANGE',
    changeset_id: 'changeset-2',
    payload: { old_stage_id: 'stage-1', new_stage_id: 'stage-2', lost_reason_id: null },
  }),
  action({ id: 'action-4', kind: 'LEAD_CREATED', changeset_id: 'changeset-1' }),
]

export const CHANGESETS = [
  {
    id: 'changeset-2',
    source: 'SINGLE_EDIT',
    actor_id: null,
    summary: 'Updated lead +919876543210',
    lead_count: 1,
    is_undone: false,
    created_at: '2026-08-01T10:05:00Z',
  },
]

export interface StubMessageTemplate {
  id: string
  channel: string
  name: string
  subject: string | null
  body: string
  owner_id: string | null
  template_id: string | null
  visibility: string
}

export const MESSAGE_TEMPLATES: StubMessageTemplate[] = [
  {
    id: 'template-1',
    channel: 'WHATSAPP',
    name: 'Intro',
    subject: null,
    body: 'Hi {{name}}, we will call you on {{phone}}. Ref: {{secret_code}}',
    owner_id: null,
    template_id: null,
    visibility: 'shared',
  },
]

export function fieldMatrix() {
  return {
    fields: BUILTIN_FIELDS.map((field, index) => ({
      field_id: field.id,
      key: field.key,
      label: field.label,
      field_type: field.field_type,
      is_hidden: false,
      view: true,
      edit: index !== 3,
      import: true,
      export: false,
    })),
    columns: {
      view: { count: 4, total: 4, rollup: 'Full' },
      edit: { count: 3, total: 4, rollup: 'Partial' },
      import: { count: 4, total: 4, rollup: 'Full' },
      // The observed default: exporting is off unless someone turns it on.
      export: { count: 0, total: 4, rollup: 'None' },
    },
  }
}

export function workspaceDetail(id: string) {
  return {
    id,
    name: 'Acme Sales',
    slug: 'acme-sales',
    default_country_code: '91',
    timezone: 'Asia/Kolkata',
    currency: 'INR',
    connected_call_min_seconds: 1,
    session_timeout_minutes: null,
    leaderboard_metrics: {},
    features: PREFERENCES.features,
    seat_limit: 3,
    seats_used: 2,
    is_active: true,
    identity_field_id: 'field-2',
    primary_field_1_id: 'field-1',
    primary_field_2_id: 'field-2',
  }
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
  /** The workspace's lead schema. Defaults to the four built-ins. */
  readonly fields?: ReturnType<typeof leadField>[]
  /** Leads the list endpoint returns. */
  readonly leads?: ReturnType<typeof lead>[]
  /** When false, custom-action endpoints answer 403 feature_disabled. */
  readonly customActionsEnabled?: boolean
}

export interface StubHandle {
  /** Every request path the app made, in order. */
  readonly requests: string[]
  /** Forces the next N authenticated calls to answer 401. */
  expireAccessToken: () => void
  readonly refreshCount: () => number
  /**
   * The body of the last `POST /leads/search`.
   *
   * Lets a spec assert the *document the builder produced* — which is the only
   * way to check that a history predicate was encoded correctly without
   * reaching into component state or showing the user JSON.
   */
  readonly lastSearch: () => LastSearch | null
}

export interface LastSearch {
  readonly filter?: {
    readonly type?: string
    readonly op?: string
    readonly children?: readonly Record<string, unknown>[]
  } | null
  readonly q?: string | null
  readonly sort?: string
  readonly columns?: readonly string[] | null
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
  let currentFields = [...(options.fields ?? BUILTIN_FIELDS)]
  let currentLeads = [...(options.leads ?? [lead()])]
  let savedFilters: Record<string, unknown>[] = []
  let currentLayout: Record<string, unknown> | null = null
  //: The last search body the page sent, so a spec can assert the *shape* of
  //: the filter the builder produced without reading the component's state.
  let lastSearchBody: LastSearch | null = null
  let currentTemplates = [...MESSAGE_TEMPLATES]
  const customActionsEnabled = options.customActionsEnabled ?? true

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

    // --- M2: the field registry and schema -------------------------------
    //
    // The registry stub carries the real renderer contracts, including the
    // three composites, because the point of these tests is that the frontend
    // draws whatever the *server* declares rather than a hardcoded list.
    if (path.endsWith('/settings/field-types')) {
      return json(route, 200, FIELD_TYPES)
    }

    if (path.endsWith('/settings/action-field-types')) {
      return json(route, 200, ACTION_FIELD_TYPES)
    }

    if (path.endsWith('/settings/lead-fields') && method === 'GET') {
      return json(route, 200, currentFields)
    }

    if (path.endsWith('/settings/lead-fields') && method === 'POST') {
      const body = route.request().postDataJSON() as { label: string; field_type: string }
      const created = leadField({
        id: `field-${currentFields.length + 1}`,
        key: body.label.toLowerCase().replace(/[^a-z0-9]+/g, '_'),
        label: body.label,
        field_type: body.field_type,
        is_builtin: false,
      })
      currentFields = [...currentFields, created]
      return json(route, 201, created)
    }

    const optionsMatch = /\/settings\/lead-fields\/([^/]+)\/options$/.exec(path)
    if (optionsMatch && method === 'POST') {
      const body = route.request().postDataJSON() as {
        label: string
        color: string | null
        parent_option_id: string | null
      }
      const option = {
        id: `option-${Date.now()}`,
        code: body.label.toLowerCase().replace(/[^a-z0-9]+/g, '_'),
        label: body.label,
        color: body.color,
        sort_order: 0,
        is_archived: false,
        parent_option_id: body.parent_option_id,
      }
      currentFields = currentFields.map((field) =>
        field.id === optionsMatch[1] ? { ...field, options: [...field.options, option] } : field,
      )
      return json(route, 201, option)
    }

    if (path.endsWith('/settings/indexed-fields') && method === 'GET') {
      return json(route, 200, [])
    }

    if (path.endsWith('/settings/identity-field') || path.endsWith('/settings/primary-fields')) {
      return json(route, 200, { identity_field_id: 'field-2', backfill: 'pending' })
    }

    // --- M3: pipeline and taxonomy ---------------------------------------
    if (path.endsWith('/settings/stages') && method === 'GET') {
      return json(route, 200, STAGES)
    }

    if (path.endsWith('/settings/stages') && method === 'POST') {
      return apiError(route, 409, 'stage_cardinality')
    }

    if (path.endsWith('/settings/lost-reasons') && method === 'GET') {
      return json(route, 200, LOST_REASONS)
    }

    if (path.endsWith('/settings/call-dispositions') && method === 'GET') {
      return json(route, 200, DISPOSITIONS)
    }

    const renameDispositionMatch = /\/settings\/call-dispositions\/([^/]+)$/.exec(path)
    if (renameDispositionMatch && method === 'PATCH') {
      const target = DISPOSITIONS.find((entry) => entry.id === renameDispositionMatch[1])
      if (target?.is_system) {
        return apiError(route, 403, 'system_disposition')
      }
      return json(route, 200, target)
    }

    if (path.endsWith('/settings/custom-actions') && method === 'GET') {
      if (customActionsEnabled) {
        return json(route, 200, CUSTOM_ACTIONS)
      }
      return apiError(route, 403, 'feature_disabled')
    }

    if (path.endsWith('/settings/preferences')) {
      return json(route, 200, PREFERENCES)
    }

    // --- M4: the field matrix --------------------------------------------
    if (path.endsWith('/settings/permission-templates/capability-schema')) {
      return json(route, 200, CAPABILITY_SCHEMA)
    }

    const matrixMatch = /\/settings\/permission-templates\/([^/]+)\/field-grants$/.exec(path)
    if (matrixMatch) {
      return json(route, 200, fieldMatrix())
    }

    const leadViewMatch = /\/settings\/permission-templates\/([^/]+)\/lead-view$/.exec(path)
    if (leadViewMatch) {
      return json(route, 200, { layout: [] })
    }

    const templateDetailMatch = /\/settings\/permission-templates\/([^/]+)$/.exec(path)
    if (templateDetailMatch) {
      const isRoot = templateDetailMatch[1] === TEMPLATE_ROOT
      if (isRoot && method !== 'GET') {
        return apiError(route, 403, 'template_readonly')
      }
      return json(route, 200, {
        id: templateDetailMatch[1],
        name: isRoot ? 'Root' : 'Caller',
        is_system: true,
        is_readonly: isRoot,
        capabilities: { leads: { admin_access: isRoot } },
      })
    }

    // --- M6: filtered search, saved filters, layouts ----------------------
    // The stub does not evaluate the DSL — that is the backend's job and it has
    // its own tests. What these specs check is that the builder *sends* a
    // well-formed document and renders what comes back.
    if (path.endsWith('/leads/search') && method === 'POST') {
      const body = route.request().postDataJSON() as LastSearch
      lastSearchBody = body
      const filtered =
        body.filter && Array.isArray(body.filter.children) && body.filter.children.length > 0
          ? currentLeads.slice(0, 1)
          : currentLeads
      return json(route, 200, {
        items: filtered,
        total: filtered.length,
        limit: 25,
        offset: 0,
      })
    }

    if (path.endsWith('/filters') && method === 'GET') {
      return json(route, 200, savedFilters)
    }

    if (path.endsWith('/filters') && method === 'POST') {
      const body = route.request().postDataJSON() as {
        name?: string
        definition?: unknown
        visibility?: string
        template_id?: string | null
      }
      const created = {
        id: `filter-${savedFilters.length + 1}`,
        name: body.name ?? 'Untitled',
        description: null,
        definition: body.definition,
        visibility: body.visibility ?? 'PERSONAL',
        template_id: body.template_id ?? null,
        owner_membership_id: 'membership-1',
        sort_order: savedFilters.length,
        is_archived: false,
        created_at: '2026-08-21T09:00:00Z',
      }
      savedFilters = [...savedFilters, created]
      return json(route, 201, created)
    }

    const filterIdMatch = /\/filters\/([^/]+)$/.exec(path)
    if (filterIdMatch && method === 'DELETE') {
      savedFilters = savedFilters.filter((entry) => entry.id !== filterIdMatch[1])
      return json(route, 200, { ...savedFilters[0], is_archived: true })
    }

    if (path.endsWith('/layouts') && method === 'GET') {
      return json(route, 200, currentLayout)
    }

    if (path.endsWith('/layouts') && method === 'PUT') {
      const body = route.request().postDataJSON() as { columns?: string[] }
      currentLayout = {
        id: 'layout-1',
        filter_id: null,
        columns: body.columns ?? [],
        column_widths: {},
        sort_key: null,
        sort_desc: true,
      }
      return json(route, 200, currentLayout)
    }

    // --- M5: leads, timeline, templates -----------------------------------
    if (path.endsWith('/leads') && method === 'GET') {
      return json(route, 200, {
        items: currentLeads,
        total: currentLeads.length,
        limit: 50,
        offset: 0,
      })
    }

    if (path.endsWith('/leads') && method === 'POST') {
      const body = route.request().postDataJSON() as { values: Record<string, unknown> }
      // The identity field is Phone for this workspace, so an absent value is
      // the 422 the real API answers rather than a lead with no identifier.
      const phone = typeof body.values.phone === 'string' ? body.values.phone : ''
      if (!phone) {
        return apiError(route, 422, 'identity_required', { field: 'phone' })
      }
      const created = lead({
        id: `lead-${currentLeads.length + 1}`,
        identity_value: `+91${phone}`,
        primary: { h1: body.values.name ?? null, h2: `+91${phone}` },
        values: body.values,
      })
      currentLeads = [...currentLeads, created]
      return json(route, 201, created)
    }

    const actionsMatch = /\/leads\/([^/]+)\/actions$/.exec(path)
    if (actionsMatch) {
      return json(route, 200, { items: TIMELINE, total: TIMELINE.length, limit: 100, offset: 0 })
    }

    const noteMatch = /\/leads\/([^/]+)\/notes$/.exec(path)
    if (noteMatch) {
      return json(route, 201, action({ kind: 'NOTE', body: 'Added from the overlay' }))
    }

    const leadPatchMatch = /\/leads\/([^/]+)$/.exec(path)
    if (leadPatchMatch && method === 'PATCH') {
      const body = route.request().postDataJSON() as { values?: Record<string, unknown> }
      // Model the write filter: a field the caller cannot edit is refused by
      // name, never silently dropped.
      if (body.values && 'locked_note' in body.values) {
        return apiError(route, 403, 'field_not_editable', { fields: ['locked_note'] })
      }
      currentLeads = currentLeads.map((entry) =>
        entry.id === leadPatchMatch[1]
          ? { ...entry, values: { ...entry.values, ...(body.values ?? {}) } }
          : entry,
      )
      return json(
        route,
        200,
        currentLeads.find((entry) => entry.id === leadPatchMatch[1]),
      )
    }

    if (path.endsWith('/templates') && method === 'GET') {
      return json(route, 200, currentTemplates)
    }

    if (path.endsWith('/templates') && method === 'POST') {
      const body = route.request().postDataJSON() as {
        channel: string
        name: string
        body: string
        subject: string | null
      }
      if (body.channel === 'EMAIL' && !body.subject) {
        return apiError(route, 422, 'subject_required')
      }
      const created = {
        id: `template-${currentTemplates.length + 1}`,
        channel: body.channel,
        name: body.name,
        subject: body.subject,
        body: body.body,
        owner_id: OWNER_MEMBERSHIP,
        template_id: null,
        visibility: 'personal',
      }
      currentTemplates = [...currentTemplates, created]
      return json(route, 201, created)
    }

    const renderMatch = /\/templates\/([^/]+)\/render$/.exec(path)
    if (renderMatch) {
      // The unresolved list is the security-relevant half: a placeholder for a
      // field the sender cannot view comes back here rather than resolving.
      return json(route, 200, {
        id: renderMatch[1],
        channel: 'WHATSAPP',
        subject: null,
        body: 'Hi Ada Lead, we will call you on +919876543210. Ref: ',
        unresolved: ['secret_code'],
      })
    }

    if (path.endsWith('/changesets')) {
      return json(route, 200, { items: CHANGESETS, total: CHANGESETS.length, limit: 50, offset: 0 })
    }

    const workspaceMatch = /^\/workspaces\/([^/]+)$/.exec(path)
    if (workspaceMatch && method === 'GET') {
      return json(route, 200, workspaceDetail(workspaceMatch[1] ?? WORKSPACE_A))
    }

    return json(route, 404, { detail: { code: 'not_found', message: 'Not found' } })
  })

  return {
    requests,
    expireAccessToken: () => {
      accessTokenExpired = true
    },
    refreshCount: () => refreshCount,
    lastSearch: () => lastSearchBody,
  }
}
