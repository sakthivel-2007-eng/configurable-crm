/**
 * Permission template queries and mutations (M4).
 *
 * The matrix endpoints return the whole matrix after every write, so the screen
 * never recomputes counts or rollups locally — the badge and the data it
 * describes come from the same response and cannot disagree.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  CapabilitySchema,
  FieldMatrix,
  LeadViewGroup,
  PermissionTemplateDetail,
} from '@/api/types'

const templatesKey = (workspaceId: string) => ['permission-templates', workspaceId] as const
const matrixKey = (workspaceId: string, templateId: string) =>
  ['field-matrix', workspaceId, templateId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}/settings/permission-templates`
}

export function usePermissionTemplates(workspaceId: string) {
  return useQuery({
    queryKey: templatesKey(workspaceId),
    queryFn: () => api.get<PermissionTemplateDetail[]>(base(workspaceId)),
  })
}

/** The 10 Access and 3 View groups, with proposed contents flagged. */
export function useCapabilitySchema(workspaceId: string) {
  return useQuery({
    queryKey: ['capability-schema', workspaceId],
    staleTime: Infinity,
    queryFn: () => api.get<CapabilitySchema>(`${base(workspaceId)}/capability-schema`),
  })
}

export function usePermissionTemplate(workspaceId: string, templateId: string) {
  return useQuery({
    queryKey: [...templatesKey(workspaceId), templateId] as const,
    enabled: templateId !== '',
    queryFn: () => api.get<PermissionTemplateDetail>(`${base(workspaceId)}/${templateId}`),
  })
}

export function useFieldMatrix(workspaceId: string, templateId: string) {
  return useQuery({
    queryKey: matrixKey(workspaceId, templateId),
    enabled: templateId !== '',
    queryFn: () => api.get<FieldMatrix>(`${base(workspaceId)}/${templateId}/field-grants`),
  })
}

export function useLeadView(workspaceId: string, templateId: string) {
  return useQuery({
    queryKey: ['lead-view', workspaceId, templateId] as const,
    enabled: templateId !== '',
    queryFn: () =>
      api.get<{ layout: LeadViewGroup[] }>(`${base(workspaceId)}/${templateId}/lead-view`),
  })
}

function usePermissionMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: templatesKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: ['field-matrix', workspaceId] }),
        // A grant change alters what the current caller may read, so their own
        // resolved permissions are stale too.
        queryClient.invalidateQueries({ queryKey: ['permissions', workspaceId] }),
      ])
    },
  })
}

export function useCreateTemplate(workspaceId: string) {
  return usePermissionMutation(workspaceId, (body: { name: string }) =>
    api.post<PermissionTemplateDetail>(base(workspaceId), body),
  )
}

export function useUpdateCapabilities(workspaceId: string) {
  return usePermissionMutation(
    workspaceId,
    ({ templateId, capabilities }: { templateId: string; capabilities: Record<string, unknown> }) =>
      api.patch<PermissionTemplateDetail>(`${base(workspaceId)}/${templateId}`, { capabilities }),
  )
}

export interface GrantRowInput {
  readonly field_id: string
  readonly view: boolean
  readonly edit: boolean
  readonly import: boolean
  readonly export: boolean
}

export function useSetGrants(workspaceId: string) {
  return usePermissionMutation(
    workspaceId,
    ({ templateId, grants }: { templateId: string; grants: GrantRowInput[] }) =>
      api.put<FieldMatrix>(`${base(workspaceId)}/${templateId}/field-grants`, { grants }),
  )
}

/** The column select-all from §6.4. */
export function useBulkSetGrant(workspaceId: string) {
  return usePermissionMutation(
    workspaceId,
    ({ templateId, grant, value }: { templateId: string; grant: string; value: boolean }) =>
      api.put<FieldMatrix>(`${base(workspaceId)}/${templateId}/field-grants/bulk`, {
        grant,
        value,
      }),
  )
}

export function useSetLeadView(workspaceId: string) {
  return usePermissionMutation(
    workspaceId,
    ({ templateId, layout }: { templateId: string; layout: LeadViewGroup[] }) =>
      api.put<{ layout: LeadViewGroup[] }>(`${base(workspaceId)}/${templateId}/lead-view`, {
        layout,
      }),
  )
}
