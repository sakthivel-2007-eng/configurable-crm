/**
 * API keys, webhooks, the outbox and the intake log (M10).
 *
 * The outbox and intake log are read-mostly and change without anyone on this
 * page doing anything — a webhook retries on the worker's clock, not the
 * operator's — so both poll. Everything else invalidates on write in the usual
 * way.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  ApiKey,
  ApiKeyCreated,
  IntakeLogEntry,
  OutboxEvent,
  OutboxStatus,
  Page,
  WebhookCreated,
  WebhookEndpoint,
  WebhookTestResult,
} from '@/api/types'

const keysKey = (workspaceId: string) => ['api-keys', workspaceId] as const
const hooksKey = (workspaceId: string) => ['webhooks', workspaceId] as const
const outboxKey = (workspaceId: string) => ['outbox', workspaceId] as const
const intakeKey = (workspaceId: string) => ['intake-log', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

/** A queued event moves on the worker's clock, so the page has to look again. */
const LIVE_REFRESH_MS = 10_000

// --- API keys ----------------------------------------------------------------

export function useApiKeys(workspaceId: string) {
  return useQuery({
    queryKey: keysKey(workspaceId),
    queryFn: () => api.get<ApiKey[]>(`${base(workspaceId)}/settings/api-keys`),
    enabled: Boolean(workspaceId),
  })
}

export function useCreateApiKey(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; permission_template_id: string }) =>
      api.post<ApiKeyCreated>(`${base(workspaceId)}/settings/api-keys`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: keysKey(workspaceId) }),
  })
}

export function useRevokeApiKey(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (keyId: string) =>
      api.delete<void>(`${base(workspaceId)}/settings/api-keys/${keyId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: keysKey(workspaceId) }),
  })
}

// --- webhooks ----------------------------------------------------------------

export function useWebhooks(workspaceId: string) {
  return useQuery({
    queryKey: hooksKey(workspaceId),
    queryFn: () => api.get<WebhookEndpoint[]>(`${base(workspaceId)}/settings/webhooks`),
    enabled: Boolean(workspaceId),
  })
}

/** The real event list, so the UI cannot offer one the product never emits. */
export function useEventNames(workspaceId: string) {
  return useQuery({
    queryKey: [...hooksKey(workspaceId), 'events'],
    queryFn: () => api.get<string[]>(`${base(workspaceId)}/settings/webhooks/events`),
    enabled: Boolean(workspaceId),
    staleTime: Infinity,
  })
}

export function useCreateWebhook(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      name: string
      url: string
      events: readonly string[]
      permission_template_id: string
    }) => api.post<WebhookCreated>(`${base(workspaceId)}/settings/webhooks`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: hooksKey(workspaceId) }),
  })
}

export function useDeleteWebhook(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (endpointId: string) =>
      api.delete<void>(`${base(workspaceId)}/settings/webhooks/${endpointId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: hooksKey(workspaceId) }),
  })
}

export function useTestWebhook(workspaceId: string) {
  return useMutation({
    mutationFn: (endpointId: string) =>
      api.post<WebhookTestResult>(`${base(workspaceId)}/settings/webhooks/${endpointId}/test`),
  })
}

// --- the outbox --------------------------------------------------------------

export function useOutbox(workspaceId: string, status?: OutboxStatus) {
  return useQuery({
    queryKey: [...outboxKey(workspaceId), status ?? 'all'],
    queryFn: () =>
      api.get<Page<OutboxEvent>>(`${base(workspaceId)}/settings/outbox`, {
        query: { limit: 50, ...(status ? { status } : {}) },
      }),
    enabled: Boolean(workspaceId),
    refetchInterval: LIVE_REFRESH_MS,
  })
}

export function useRetryOutboxEvent(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (eventId: string) =>
      api.post<OutboxEvent>(`${base(workspaceId)}/settings/outbox/${eventId}/retry`),
    onSuccess: () => client.invalidateQueries({ queryKey: outboxKey(workspaceId) }),
  })
}

// --- the intake log ----------------------------------------------------------

export function useIntakeLog(workspaceId: string, rejectedOnly = false) {
  return useQuery({
    queryKey: [...intakeKey(workspaceId), rejectedOnly],
    queryFn: () =>
      api.get<Page<IntakeLogEntry>>(`${base(workspaceId)}/settings/intake-log`, {
        query: { limit: 50, rejected_only: rejectedOnly },
      }),
    enabled: Boolean(workspaceId),
    refetchInterval: LIVE_REFRESH_MS,
  })
}
