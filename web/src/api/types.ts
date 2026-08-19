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
