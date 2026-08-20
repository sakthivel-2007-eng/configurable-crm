/**
 * Saved filters, layouts and filtered search (M6).
 *
 * Search is a POST, not a GET with a query string: the DSL is a nested
 * document, and URL-encoding one would be unreadable in a log, would hit length
 * limits on a real filter, and would put a customer's values in a URL — which
 * is the one place they must never go.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  FilterNode,
  FilterStats,
  Lead,
  LeadSearchRequest,
  Page,
  SavedFilter,
  SavedFilterVisibility,
  TableLayout,
} from '@/api/types'

const filtersKey = (workspaceId: string) => ['saved-filters', workspaceId] as const
const layoutKey = (workspaceId: string, filterId: string | null) =>
  ['table-layout', workspaceId, filterId ?? 'default'] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

/**
 * The lead list, filtered.
 *
 * `keepPreviousData` matters more here than it looks: without it, paging or
 * changing a sort blanks the table for the length of a round trip, and on a
 * 50,000-lead workspace that reads as the filter having broken.
 */
export function useLeadSearch(workspaceId: string, request: LeadSearchRequest) {
  return useQuery({
    queryKey: ['lead-search', workspaceId, request] as const,
    placeholderData: (previous) => previous,
    queryFn: () => api.post<Page<Lead>>(`${base(workspaceId)}/leads/search`, request),
  })
}

export function useSavedFilters(workspaceId: string) {
  return useQuery({
    queryKey: filtersKey(workspaceId),
    queryFn: () => api.get<SavedFilter[]>(`${base(workspaceId)}/filters`),
  })
}

export function useFilterStats(workspaceId: string, filterId: string | null) {
  return useQuery({
    queryKey: ['filter-stats', workspaceId, filterId] as const,
    enabled: filterId !== null,
    queryFn: () => api.get<FilterStats>(`${base(workspaceId)}/filters/${filterId as string}/stats`),
  })
}

export function useCreateFilter(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      name: string
      description?: string | null
      definition: FilterNode
      visibility: SavedFilterVisibility
      template_id?: string | null
    }) => api.post<SavedFilter>(`${base(workspaceId)}/filters`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: filtersKey(workspaceId) }),
  })
}

export function useUpdateFilter(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<SavedFilter>(`${base(workspaceId)}/filters/${id}`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: filtersKey(workspaceId) }),
  })
}

export function useDuplicateFilter(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) =>
      api.post<SavedFilter>(`${base(workspaceId)}/filters/${id}/duplicate`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: filtersKey(workspaceId) }),
  })
}

export function useArchiveFilter(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<SavedFilter>(`${base(workspaceId)}/filters/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: filtersKey(workspaceId) }),
  })
}

export function useReorderFilters(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (filter_ids: readonly string[]) =>
      api.patch<SavedFilter[]>(`${base(workspaceId)}/filters/reorder`, { filter_ids }),
    onSuccess: () => client.invalidateQueries({ queryKey: filtersKey(workspaceId) }),
  })
}

export function useTableLayout(workspaceId: string, filterId: string | null) {
  return useQuery({
    queryKey: layoutKey(workspaceId, filterId),
    queryFn: () =>
      api.get<TableLayout | null>(`${base(workspaceId)}/layouts`, {
        query: filterId ? { filter_id: filterId } : {},
      }),
  })
}

export function useSaveLayout(workspaceId: string, filterId: string | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: {
      columns: readonly string[]
      column_widths?: Record<string, number>
      sort_key?: string | null
      sort_desc?: boolean
    }) =>
      api.put<TableLayout>(`${base(workspaceId)}/layouts`, body, {
        query: filterId ? { filter_id: filterId } : {},
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: layoutKey(workspaceId, filterId) }),
  })
}
