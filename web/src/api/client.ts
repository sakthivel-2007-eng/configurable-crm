/**
 * Minimal fetch wrapper.
 *
 * M1 replaces the hand-written response types with a client generated from the
 * API's OpenAPI schema; the transport below stays.
 */

const DEFAULT_API_BASE_URL = 'http://localhost:8000'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
export const API_V1_URL = `${API_BASE_URL}/api/v1`

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface RequestOptions {
  /** Statuses whose JSON body is a valid result rather than a failure. */
  readonly acceptStatuses?: readonly number[]
  readonly signal?: AbortSignal
}

export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { acceptStatuses = [], signal } = options

  let response: Response
  try {
    response = await fetch(`${API_V1_URL}${path}`, {
      headers: { Accept: 'application/json' },
      ...(signal ? { signal } : {}),
    })
  } catch (cause) {
    throw new ApiError(0, cause instanceof Error ? cause.message : 'Network request failed')
  }

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    throw new ApiError(response.status, `Request to ${path} failed with ${response.status}`)
  }

  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(response.status, `Response from ${path} was not valid JSON`)
  }
}
