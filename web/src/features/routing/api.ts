/**
 * Sales groups, assignment rules, distribution and schedules (M8).
 *
 * One invalidation rule worth stating: **changing a rule changes where future
 * leads land, not where existing ones are**, so rule writes invalidate the rule
 * list and nothing else. Distribution is the opposite — it rewrites assignees
 * in bulk, so it invalidates everything a lead-wide write does, including the
 * edit report that now holds its undo handle.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  AssignmentPreview,
  AssignmentRule,
  AssignmentStrategy,
  DistributionResult,
  RecurringOccurrence,
  SalesGroup,
  SalesGroupMember,
  ScheduledReport,
} from '@/api/types'

const groupsKey = (workspaceId: string) => ['sales-groups', workspaceId] as const
const rulesKey = (workspaceId: string) => ['assignment-rules', workspaceId] as const
const schedulesKey = (workspaceId: string) => ['scheduled-reports', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

// --- sales groups ------------------------------------------------------------

export function useSalesGroups(workspaceId: string, includeArchived = false) {
  return useQuery({
    queryKey: [...groupsKey(workspaceId), includeArchived],
    queryFn: () =>
      api.get<SalesGroup[]>(`${base(workspaceId)}/settings/sales-groups`, {
        query: { include_archived: includeArchived },
      }),
    enabled: Boolean(workspaceId),
  })
}

export function useGroupMembers(workspaceId: string, groupId: string | null) {
  return useQuery({
    queryKey: [...groupsKey(workspaceId), groupId, 'members'],
    queryFn: () =>
      api.get<SalesGroupMember[]>(`${base(workspaceId)}/settings/sales-groups/${groupId}/members`),
    enabled: Boolean(workspaceId && groupId),
  })
}

export function useCreateGroup(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) =>
      api.post<SalesGroup>(`${base(workspaceId)}/settings/sales-groups`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: groupsKey(workspaceId) }),
  })
}

export function useArchiveGroup(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (groupId: string) =>
      api.delete<void>(`${base(workspaceId)}/settings/sales-groups/${groupId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: groupsKey(workspaceId) }),
  })
}

export function useSetGroupMembers(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({
      groupId,
      members,
    }: {
      groupId: string
      members: ReadonlyArray<{ membership_id: string; weight: number }>
    }) =>
      api.put<SalesGroupMember[]>(
        `${base(workspaceId)}/settings/sales-groups/${groupId}/members`,
        members,
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: groupsKey(workspaceId) }),
  })
}

// --- assignment rules --------------------------------------------------------

export function useAssignmentRules(workspaceId: string) {
  return useQuery({
    queryKey: rulesKey(workspaceId),
    queryFn: () => api.get<AssignmentRule[]>(`${base(workspaceId)}/settings/assignment-rules`),
    enabled: Boolean(workspaceId),
  })
}

export interface RuleWrite {
  readonly name: string
  readonly strategy: AssignmentStrategy
  readonly config: Record<string, unknown>
  readonly conditions?: Record<string, unknown>
  readonly skip_unavailable?: boolean
  readonly is_active?: boolean
}

export function useCreateRule(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: RuleWrite) =>
      api.post<AssignmentRule>(`${base(workspaceId)}/settings/assignment-rules`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: rulesKey(workspaceId) }),
  })
}

export function useUpdateRule(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ ruleId, ...body }: { ruleId: string } & Partial<RuleWrite>) =>
      api.patch<AssignmentRule>(`${base(workspaceId)}/settings/assignment-rules/${ruleId}`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: rulesKey(workspaceId) }),
  })
}

export function useDeleteRule(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (ruleId: string) =>
      api.delete<void>(`${base(workspaceId)}/settings/assignment-rules/${ruleId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: rulesKey(workspaceId) }),
  })
}

export function useReorderRules(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (order: readonly string[]) =>
      api.patch<AssignmentRule[]>(`${base(workspaceId)}/settings/assignment-rules/reorder`, {
        order,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: rulesKey(workspaceId) }),
  })
}

export function usePreviewAssignment(workspaceId: string) {
  return useMutation({
    mutationFn: (leadId: string) =>
      api.post<AssignmentPreview>(
        `${base(workspaceId)}/settings/assignment-rules/preview`,
        undefined,
        { query: { lead_id: leadId } },
      ),
  })
}

// --- distribution ------------------------------------------------------------

export function useDistribute(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      lead_ids: readonly string[]
      strategy: AssignmentStrategy
      config: Record<string, unknown>
      skip_unavailable?: boolean
    }) => api.post<DistributionResult>(`${base(workspaceId)}/leads/distribute`, body),
    onSuccess: () => {
      // A redistribution rewrites assignees in bulk and leaves an undo handle,
      // so the list, the timelines and the edit report are all now stale.
      for (const key of [
        ['lead-search', workspaceId],
        ['leads', workspaceId],
        ['changesets', workspaceId],
        ['lead-actions', workspaceId],
      ]) {
        void client.invalidateQueries({ queryKey: key })
      }
    },
  })
}

// --- scheduled reports -------------------------------------------------------

export function useScheduledReports(workspaceId: string) {
  return useQuery({
    queryKey: schedulesKey(workspaceId),
    queryFn: () => api.get<ScheduledReport[]>(`${base(workspaceId)}/scheduled-reports`),
    enabled: Boolean(workspaceId),
  })
}

export function useCreateScheduledReport(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      name: string
      report_type: string
      cron: string
      recipients: readonly string[]
    }) => api.post<ScheduledReport>(`${base(workspaceId)}/scheduled-reports`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: schedulesKey(workspaceId) }),
  })
}

export function useDeleteScheduledReport(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (reportId: string) =>
      api.delete<void>(`${base(workspaceId)}/scheduled-reports/${reportId}`),
    onSuccess: () => client.invalidateQueries({ queryKey: schedulesKey(workspaceId) }),
  })
}

export function useRunScheduledReportNow(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (reportId: string) =>
      api.post<ScheduledReport>(`${base(workspaceId)}/scheduled-reports/${reportId}/run-now`),
    onSuccess: () => client.invalidateQueries({ queryKey: schedulesKey(workspaceId) }),
  })
}

// --- recurring dates ---------------------------------------------------------

export function useRecurringOccurrences(
  workspaceId: string,
  options: { fieldKey: string | null; from: string; to: string },
) {
  return useQuery({
    queryKey: ['recurring-occurrences', workspaceId, options],
    queryFn: () =>
      api.get<RecurringOccurrence[]>(`${base(workspaceId)}/recurring-dates/occurrences`, {
        // `enabled` guarantees a key here; the assertion keeps the query
        // param type honest rather than widening it to accept null.
        query: { field_key: options.fieldKey as string, from: options.from, to: options.to },
      }),
    enabled: Boolean(workspaceId && options.fieldKey),
  })
}
