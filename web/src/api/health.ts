import { apiGet } from '@/api/client'

export type ComponentStatus = 'ok' | 'error'
export type OverallStatus = 'ok' | 'degraded'

export interface ComponentHealth {
  readonly status: ComponentStatus
  readonly latency_ms: number | null
  readonly error: string | null
}

export interface HealthChecks {
  readonly database: ComponentHealth
  readonly redis: ComponentHealth
  readonly object_storage: ComponentHealth
}

export interface HealthResponse {
  readonly status: OverallStatus
  readonly service: string
  readonly version: string
  readonly environment: string
  readonly checks: HealthChecks
}

/** 503 carries a full report, so it is a result to render, not an error to throw. */
export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiGet<HealthResponse>('/health', {
    acceptStatuses: [503],
    ...(signal ? { signal } : {}),
  })
}

export const healthQueryKey = ['health'] as const
