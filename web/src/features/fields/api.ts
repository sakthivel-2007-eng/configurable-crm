/**
 * Field-definition queries and mutations (M2).
 *
 * `useFieldTypes` is the important one: the frontend asks the backend which
 * field types exist and how to draw each. Nothing in `src/` enumerates the 13
 * types, so adding a type is a backend change plus a renderer — never an edit
 * to a TypeScript union that would silently drift from the server's registry.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { FieldOption, FieldTypeSpec, IndexedField, LeadField } from '@/api/types'

const fieldsKey = (workspaceId: string) => ['lead-fields', workspaceId] as const
const typesKey = (workspaceId: string) => ['field-types', workspaceId] as const
const indexedKey = (workspaceId: string) => ['indexed-fields', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}/settings`
}

/** The 13-type lead registry, straight from the backend. */
export function useFieldTypes(workspaceId: string) {
  return useQuery({
    queryKey: typesKey(workspaceId),
    // The registry is a product constant: it cannot change without a deploy,
    // so it is worth caching for the session rather than per screen.
    staleTime: Infinity,
    queryFn: () => api.get<FieldTypeSpec[]>(`${base(workspaceId)}/field-types`),
  })
}

/** The 8-type action registry, used by the custom-action form builder. */
export function useActionFieldTypes(workspaceId: string) {
  return useQuery({
    queryKey: ['action-field-types', workspaceId],
    staleTime: Infinity,
    queryFn: () => api.get<FieldTypeSpec[]>(`${base(workspaceId)}/action-field-types`),
  })
}

export function useLeadFields(workspaceId: string, options?: { includeHidden?: boolean }) {
  const includeHidden = options?.includeHidden ?? false
  return useQuery({
    queryKey: [...fieldsKey(workspaceId), includeHidden] as const,
    queryFn: () =>
      api.get<LeadField[]>(`${base(workspaceId)}/lead-fields`, {
        query: { include_hidden: includeHidden },
      }),
  })
}

export function useIndexedFields(workspaceId: string) {
  return useQuery({
    queryKey: indexedKey(workspaceId),
    queryFn: () => api.get<IndexedField[]>(`${base(workspaceId)}/indexed-fields`),
  })
}

/** Invalidates every field-shaped cache — an option edit moves the field too. */
function useFieldMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: fieldsKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: indexedKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] }),
      ])
    },
  })
}

export interface CreateFieldInput {
  readonly label: string
  readonly field_type: string
  readonly description?: string | null
  readonly is_required?: boolean
  readonly show_in_import?: boolean
  readonly show_in_quick_add?: boolean
  readonly lock_after_create?: boolean
  readonly can_use_variable?: boolean
  readonly config?: Record<string, unknown>
}

export function useCreateField(workspaceId: string) {
  return useFieldMutation(workspaceId, (input: CreateFieldInput) =>
    api.post<LeadField>(`${base(workspaceId)}/lead-fields`, input),
  )
}

export function useUpdateField(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, ...body }: { fieldId: string } & Partial<CreateFieldInput>) =>
      api.patch<LeadField>(`${base(workspaceId)}/lead-fields/${fieldId}`, body),
  )
}

/** Hide or unhide. Fields are never deleted — values are stored under the key. */
export function useSetFieldHidden(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, hidden }: { fieldId: string; hidden: boolean }) =>
      api.post<LeadField>(
        `${base(workspaceId)}/lead-fields/${fieldId}/${hidden ? 'hide' : 'unhide'}`,
      ),
  )
}

export function useAddOption(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({
      fieldId,
      ...body
    }: {
      fieldId: string
      label: string
      color?: string | null
      parent_option_id?: string | null
    }) => api.post<FieldOption>(`${base(workspaceId)}/lead-fields/${fieldId}/options`, body),
  )
}

/** "Add multiple" — the drawer's bulk-paste box, one option per line. */
export function useAddOptionsBulk(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, labels }: { fieldId: string; labels: string[] }) =>
      api.post<FieldOption[]>(`${base(workspaceId)}/lead-fields/${fieldId}/options/bulk`, {
        labels,
      }),
  )
}

/** "Copy options" — clone another field's option set, tree and all. */
export function useCopyOptions(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, sourceFieldId }: { fieldId: string; sourceFieldId: string }) =>
      api.post<FieldOption[]>(
        `${base(workspaceId)}/lead-fields/${fieldId}/options/copy-from/${sourceFieldId}`,
      ),
  )
}

export function useUpdateOption(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({
      fieldId,
      optionId,
      ...body
    }: {
      fieldId: string
      optionId: string
      label?: string
      color?: string | null
    }) =>
      api.patch<FieldOption>(
        `${base(workspaceId)}/lead-fields/${fieldId}/options/${optionId}`,
        body,
      ),
  )
}

/** Archives rather than deletes — leads already carrying the option keep it. */
export function useArchiveOption(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, optionId }: { fieldId: string; optionId: string }) =>
      api.delete<FieldOption>(`${base(workspaceId)}/lead-fields/${fieldId}/options/${optionId}`),
  )
}

export function useReorderOptions(workspaceId: string) {
  return useFieldMutation(
    workspaceId,
    ({ fieldId, orderedIds }: { fieldId: string; orderedIds: string[] }) =>
      api.patch<FieldOption[]>(`${base(workspaceId)}/lead-fields/${fieldId}/options/reorder`, {
        ordered_ids: orderedIds,
      }),
  )
}

export function useSetIdentityField(workspaceId: string) {
  return useFieldMutation(workspaceId, ({ fieldId }: { fieldId: string }) =>
    api.put<{ identity_field_id: string }>(`${base(workspaceId)}/identity-field`, {
      field_id: fieldId,
    }),
  )
}

export function useSetPrimaryFields(workspaceId: string) {
  return useFieldMutation(workspaceId, ({ h1, h2 }: { h1: string; h2: string | null }) =>
    api.put<Record<string, string | null>>(`${base(workspaceId)}/primary-fields`, {
      h1_field_id: h1,
      h2_field_id: h2,
    }),
  )
}

export function useDeclareIndexed(workspaceId: string) {
  return useFieldMutation(workspaceId, ({ fieldId }: { fieldId: string }) =>
    api.post<IndexedField>(`${base(workspaceId)}/indexed-fields`, { field_id: fieldId }),
  )
}

export function useUndeclareIndexed(workspaceId: string) {
  return useFieldMutation(workspaceId, ({ fieldId }: { fieldId: string }) =>
    api.delete<{ index_name: string }>(`${base(workspaceId)}/indexed-fields/${fieldId}`),
  )
}
