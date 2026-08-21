/**
 * Reports and dashboards (M9).
 *
 * Reports are read-only aggregates that change only when the underlying leads
 * do, so they cache normally rather than polling — unlike M10's outbox, which
 * moves on a worker's clock.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  Bucket,
  Dashboard,
  DashboardWidget,
  FollowUpCounts,
  LeaderboardRow,
  WidgetSpec,
} from '@/api/types'

const dashboardsKey = (workspaceId: string) => ['dashboards', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

export interface Range {
  readonly from?: string
  readonly to?: string
}

function rangeQuery(range: Range): Record<string, string> {
  return {
    ...(range.from ? { from: range.from } : {}),
    ...(range.to ? { to: range.to } : {}),
  }
}

/** Current state — an overdue follow-up is overdue today, whatever range the
 *  operator is looking at, and burying it in a date filter is how it gets
 *  missed. */
export function useFollowUps(workspaceId: string) {
  return useQuery({
    queryKey: ['follow-ups', workspaceId],
    queryFn: () => api.get<FollowUpCounts>(`${base(workspaceId)}/dashboard/follow-ups`),
    enabled: Boolean(workspaceId),
  })
}

/** A pipeline is a current-state view, so this takes no range — see the
 *  endpoint's own note for why windowing it would change what it means. */
export function useLeadsByStage(workspaceId: string) {
  return useQuery({
    queryKey: ['leads-by-stage', workspaceId],
    queryFn: () => api.get<Bucket[]>(`${base(workspaceId)}/dashboard/leads-by-stage`),
    enabled: Boolean(workspaceId),
  })
}

export function useFunnel(workspaceId: string) {
  return useQuery({
    queryKey: ['funnel', workspaceId],
    queryFn: () => api.get<Bucket[]>(`${base(workspaceId)}/reports/funnel`),
    enabled: Boolean(workspaceId),
  })
}

export function useBreakdown(workspaceId: string, fieldKey: string | null, range: Range = {}) {
  return useQuery({
    queryKey: ['breakdown', workspaceId, fieldKey, range],
    queryFn: () =>
      api.get<Bucket[]>(`${base(workspaceId)}/reports/breakdown`, {
        query: { field_key: fieldKey as string, ...rangeQuery(range) },
      }),
    enabled: Boolean(workspaceId && fieldKey),
  })
}

export function useActivity(workspaceId: string, range: Range = {}) {
  return useQuery({
    queryKey: ['activity', workspaceId, range],
    queryFn: () =>
      api.get<Record<string, number>>(`${base(workspaceId)}/reports/activity`, {
        query: rangeQuery(range),
      }),
    enabled: Boolean(workspaceId),
  })
}

export function useLeaderboard(workspaceId: string, range: Range = {}) {
  return useQuery({
    queryKey: ['leaderboard', workspaceId, range],
    queryFn: () =>
      api.get<LeaderboardRow[]>(`${base(workspaceId)}/reports/leaderboard`, {
        query: rangeQuery(range),
      }),
    enabled: Boolean(workspaceId),
  })
}

export function useWidgetCatalogue(workspaceId: string) {
  return useQuery({
    queryKey: ['widgets', workspaceId],
    queryFn: () => api.get<WidgetSpec[]>(`${base(workspaceId)}/dashboards/widgets`),
    enabled: Boolean(workspaceId),
    staleTime: Infinity,
  })
}

export function useDashboards(workspaceId: string) {
  return useQuery({
    queryKey: dashboardsKey(workspaceId),
    queryFn: () => api.get<Dashboard[]>(`${base(workspaceId)}/dashboards`),
    enabled: Boolean(workspaceId),
  })
}

export function useCreateDashboard(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      name: string
      layout: readonly DashboardWidget[]
      shared?: boolean
      template_id?: string | null
    }) => api.post<Dashboard>(`${base(workspaceId)}/dashboards`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: dashboardsKey(workspaceId) }),
  })
}

export function useUpdateDashboard(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      dashboardId,
      ...body
    }: {
      dashboardId: string
      name?: string
      layout?: readonly DashboardWidget[]
      template_id?: string | null
    }) => api.patch<Dashboard>(`${base(workspaceId)}/dashboards/${dashboardId}`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: dashboardsKey(workspaceId) }),
  })
}

export function useArchiveDashboard(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (dashboardId: string) =>
      api.delete<void>(`${base(workspaceId)}/dashboards/${dashboardId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: dashboardsKey(workspaceId) }),
  })
}
