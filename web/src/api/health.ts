import { ApiError, API_V1_URL } from '@/api/client'

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

/**
 * 503 carries a full report, so it is a result to render, not an error to throw.
 *
 * Fetched directly rather than through `apiRequest`, which treats any non-2xx
 * as a failure — correct everywhere else, wrong for the one endpoint whose
 * unhealthy response is the thing we want to display.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  let response: Response
  try {
    response = await fetch(`${API_V1_URL}/health`, {
      headers: { Accept: 'application/json' },
      ...(signal ? { signal } : {}),
    })
  } catch (cause) {
    throw new ApiError(
      0,
      'network_error',
      cause instanceof Error ? cause.message : 'Network request failed',
    )
  }

  if (!response.ok && response.status !== 503) {
    throw new ApiError(response.status, 'unknown_error', `Health check failed`)
  }

  try {
    return (await response.json()) as HealthResponse
  } catch {
    throw new ApiError(response.status, 'invalid_response', 'Health response was not valid JSON')
  }
}

export const healthQueryKey = ['health'] as const
