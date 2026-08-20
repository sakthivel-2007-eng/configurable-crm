/**
 * API response types.
 *
 * Hand-written for M1. The plan is to generate these from the API's OpenAPI
 * schema once the surface stabilises — until then, `test:contract` in the E2E
 * suite is what keeps them honest against the live schema.
 *
 * Field names stay `snake_case` to match the wire format exactly. Converting
 * at the boundary reads nicer but means every mismatch becomes a silent
 * `undefined` instead of a type error.
 */

export type AvailabilityStatus = 'WORKING' | 'ON_LEAVE' | 'INACTIVE'

export interface UserSummary {
  readonly id: string
  readonly email: string
  readonly full_name: string
  readonly is_active: boolean
}

export interface WorkspaceSummary {
  readonly id: string
  readonly name: string
  readonly slug: string
  readonly timezone: string
  readonly currency: string
  readonly default_country_code: string
}

export interface MembershipSummary {
  readonly id: string
  readonly workspace: WorkspaceSummary
  readonly template_id: string
  readonly template_name: string
  readonly is_active: boolean
  readonly has_license: boolean
  readonly availability: AvailabilityStatus
  readonly manager_id: string | null
}

export interface TokenResponse {
  readonly access_token: string
  readonly refresh_token: string
  readonly token_type: string
  readonly expires_in: number
  readonly user: UserSummary
  readonly memberships: readonly MembershipSummary[]
}

export interface MeResponse {
  readonly user: UserSummary
  readonly memberships: readonly MembershipSummary[]
}

export interface ResolvedPermissions {
  readonly workspace_id: string
  readonly membership_id: string
  readonly template_id: string
  readonly template_name: string
  readonly capabilities: Record<string, Record<string, boolean>>
  readonly visible_membership_ids: readonly string[]
  readonly sees_all_members: boolean
  /** Populated from M4. Present and empty until then. */
  readonly field_grants: Record<string, readonly string[]>
}

export interface WorkspaceDetail {
  readonly id: string
  readonly name: string
  readonly slug: string
  readonly default_country_code: string
  readonly timezone: string
  readonly currency: string
  readonly connected_call_min_seconds: number
  readonly session_timeout_minutes: number | null
  readonly leaderboard_metrics: Record<string, unknown>
  readonly features: Record<string, unknown>
  readonly seat_limit: number
  readonly seats_used: number
  readonly is_active: boolean
  readonly identity_field_id: string | null
  readonly primary_field_1_id: string | null
  readonly primary_field_2_id: string | null
}

export interface MemberDetail {
  readonly id: string
  readonly workspace_id: string
  readonly user: UserSummary
  readonly template_id: string
  readonly template_name: string
  readonly manager_id: string | null
  readonly is_active: boolean
  readonly has_license: boolean
  readonly availability: AvailabilityStatus
  readonly created_at: string
}

export interface Page<T> {
  readonly items: readonly T[]
  readonly total: number
  readonly limit: number
  readonly offset: number
}

export interface SeatUsage {
  readonly seats_used: number
  readonly seat_limit: number
}

export interface AvailabilityLogEntry {
  readonly id: string
  readonly membership_id: string
  readonly status: AvailabilityStatus
  readonly note: string | null
  readonly changed_by_id: string | null
  readonly changed_at: string
}

export interface HierarchyNode {
  readonly member: MemberDetail
  readonly reports: readonly HierarchyNode[]
}

export interface DeactivateResponse {
  readonly member: MemberDetail
  readonly leads_reassigned: number
}

export interface BulkUploadRow {
  readonly row_number: number
  readonly email: string | null
  readonly full_name: string | null
  readonly template_name: string | null
  readonly manager_email: string | null
  readonly status: 'created' | 'skipped' | 'error'
  readonly message: string | null
}

export interface BulkUploadReport {
  readonly dry_run: boolean
  readonly total_rows: number
  readonly created: number
  readonly skipped: number
  readonly errored: number
  readonly rows: readonly BulkUploadRow[]
}

// --- M2: the field definition engine -----------------------------------------

/**
 * One entry of the lead or action field-type registry.
 *
 * Fetched from `/settings/field-types`; never hardcoded here. The frontend
 * knows how to draw a `widget`, which is a product concept, and knows nothing
 * about which *types* exist — that list is the backend's to declare, so adding
 * one is a backend change plus a renderer, never an edit to a TypeScript union.
 */
export interface FieldTypeSpec {
  readonly key: string
  readonly label: string
  readonly description: string
  readonly storage: 'scalar' | 'list' | 'object'
  readonly uses_options: boolean
  readonly operators: readonly string[]
  readonly renderer: FieldRendererContract
  readonly config_schema: Record<string, unknown>
}

/** What the backend tells the frontend about drawing a type. */
export interface FieldRendererContract {
  readonly widget: string
  readonly multiline?: boolean
  readonly multiple?: boolean
  readonly inputMode?: string
  readonly optionsAreTree?: boolean
  readonly valueKey?: string
  readonly parentKey?: string
  readonly startKey?: string
  readonly frequencyKey?: string
  readonly intervalKey?: string
  readonly derivedKey?: string
  readonly frequencies?: readonly string[]
  readonly textKeys?: readonly string[]
  readonly latKey?: string
  readonly lngKey?: string
  readonly amountKey?: string
  readonly currencyKey?: string
}

export interface FieldOption {
  readonly id: string
  readonly code: string
  readonly label: string
  readonly color: string | null
  readonly sort_order: number
  readonly is_archived: boolean
  readonly parent_option_id: string | null
}

export interface LeadField {
  readonly id: string
  readonly key: string
  readonly label: string
  readonly field_type: string
  readonly description: string | null
  readonly is_builtin: boolean
  readonly is_hidden: boolean
  readonly is_required: boolean
  readonly sort_order: number
  readonly field_group: string | null
  readonly show_in_import: boolean
  readonly show_in_quick_add: boolean
  readonly lock_after_create: boolean
  readonly can_use_variable: boolean
  readonly config: Record<string, unknown>
  readonly options: readonly FieldOption[]
  readonly created_at: string
  readonly updated_at: string
}

export interface IndexedField {
  readonly id: string
  readonly field_id: string
  readonly index_name: string
  readonly status: string
  readonly last_error: string | null
  readonly created_at: string
}

// --- M3: pipeline and taxonomy ------------------------------------------------

export type StageKind = 'INITIAL' | 'ACTIVE' | 'WON' | 'LOST'

export interface Stage {
  readonly id: string
  readonly kind: StageKind
  readonly label: string
  readonly color: string
  readonly sort_order: number
  readonly is_archived: boolean
}

/** Grouped, not flat — the settings screen is a three-column pipeline. */
export interface StagePipeline {
  readonly initial: Stage | null
  readonly active: readonly Stage[]
  readonly won: Stage | null
  readonly lost: Stage | null
  readonly archived: readonly Stage[]
}

export interface LostReason {
  readonly id: string
  readonly label: string
  readonly sort_order: number
  readonly is_default: boolean
  readonly is_archived: boolean
}

export interface CallDisposition {
  readonly id: string
  readonly label: string
  readonly is_default: boolean
  readonly is_system: boolean
  readonly is_archived: boolean
  readonly sort_order: number
}

export interface ActionFieldDef {
  readonly id: string
  readonly action_type_id: string
  readonly key: string
  readonly label: string
  readonly field_type: string
  readonly description: string | null
  readonly is_required: boolean
  readonly is_hidden: boolean
  readonly sort_order: number
  readonly config: Record<string, unknown>
  readonly options: readonly {
    readonly id: string
    readonly code: string
    readonly label: string
    readonly color: string | null
  }[]
}

export interface CustomActionType {
  readonly id: string
  readonly code: number
  readonly name: string
  readonly icon: string | null
  readonly score: number
  readonly direction: 'INBOUND' | 'OUTBOUND' | 'INFORMATION'
  readonly description: string | null
  readonly allow_predated: boolean
  readonly is_archived: boolean
  readonly fields: readonly ActionFieldDef[]
  readonly created_at: string
}

export interface WorkspacePreferences {
  readonly default_country_code: string
  readonly timezone: string
  readonly currency: string
  readonly connected_call_min_seconds: number
  readonly session_timeout_minutes: number | null
  readonly leaderboard_metrics: Record<string, unknown>
  readonly features: Record<string, boolean>
}

// --- M4: permissions ----------------------------------------------------------

export interface CapabilityGroupSchema {
  readonly key: string
  /** True when this codebase proposed the contents rather than observing them. */
  readonly proposed: boolean
  readonly capabilities: readonly string[]
}

export interface CapabilitySchema {
  readonly access: readonly CapabilityGroupSchema[]
  readonly view: readonly CapabilityGroupSchema[]
}

/**
 * The capability blob.
 *
 * Deliberately `unknown` at the leaf: the ten Access groups are flat
 * `{capability: boolean}` maps, but `view` nests one level deeper into three
 * sub-groups. A single flat type would describe one shape and lie about the
 * other, so the accessors in the permissions screen narrow it instead.
 */
export type TemplateCapabilities = Record<string, unknown>

export interface PermissionTemplateDetail {
  readonly id: string
  readonly name: string
  readonly is_system: boolean
  readonly is_readonly: boolean
  readonly capabilities: TemplateCapabilities
}

export interface FieldGrantRow {
  readonly field_id: string
  readonly key: string
  readonly label: string
  readonly field_type: string
  readonly is_hidden: boolean
  readonly view: boolean
  readonly edit: boolean
  readonly import: boolean
  readonly export: boolean
}

export interface FieldGrantColumn {
  readonly count: number
  readonly total: number
  readonly rollup: 'Full' | 'Partial' | 'None'
}

export interface FieldMatrix {
  readonly fields: readonly FieldGrantRow[]
  readonly columns: Record<string, FieldGrantColumn>
}

export interface LeadViewGroup {
  readonly label: string
  readonly collapsed: boolean
  readonly field_ids: readonly string[]
}

// --- M5: leads, actions, templates --------------------------------------------

/** Option metadata for a stored code, so an archived option still renders. */
export interface OptionLabel {
  readonly code: string
  readonly label: string
  readonly color: string | null
  readonly archived: boolean
}

export interface Lead {
  readonly id: string
  readonly identity_value: string
  readonly primary: { readonly h1: unknown; readonly h2: unknown }
  readonly stage_id: string | null
  readonly lost_reason_id: string | null
  readonly assignee_id: string | null
  readonly rating: number | null
  readonly score: number
  /** View-granted fields only. A field absent here is one the caller cannot see. */
  readonly values: Record<string, unknown>
  readonly labels: Record<string, OptionLabel | readonly OptionLabel[]>
  readonly last_action_at: string | null
  readonly created_at: string
}

export type ActionKind =
  | 'LEAD_CREATED'
  | 'FIELD_CHANGE'
  | 'STAGE_CHANGE'
  | 'ASSIGNMENT_CHANGE'
  | 'RATING_CHANGE'
  | 'NOTE'
  | 'CALL_LOGGED'
  | 'WHATSAPP_SENT'
  | 'EMAIL_SENT'
  | 'SMS_SENT'
  | 'TASK_CREATED'
  | 'TASK_COMPLETED'
  | 'CUSTOM'

export interface LeadAction {
  readonly id: string
  readonly lead_id: string
  readonly changeset_id: string | null
  readonly kind: ActionKind
  readonly action_type_id: string | null
  readonly actor_id: string | null
  readonly payload: Record<string, unknown>
  readonly body: string | null
  readonly score_applied: number
  readonly is_pinned: boolean
  readonly performed_at: string
  readonly created_at: string
}

export interface Changeset {
  readonly id: string
  readonly source: string
  readonly actor_id: string | null
  readonly summary: string
  readonly lead_count: number
  readonly is_undone: boolean
  readonly created_at: string
}

export type TemplateChannel = 'WHATSAPP' | 'SMS' | 'EMAIL'

export interface MessageTemplate {
  readonly id: string
  readonly channel: TemplateChannel
  readonly name: string
  readonly subject: string | null
  readonly body: string
  readonly owner_id: string | null
  readonly template_id: string | null
  readonly visibility: 'personal' | 'shared' | 'role'
}

export interface RenderedTemplate {
  readonly id: string
  readonly channel: TemplateChannel
  readonly subject: string | null
  readonly body: string
  /** Placeholders that resolved to nothing — including fields the sender cannot view. */
  readonly unresolved: readonly string[]
}
