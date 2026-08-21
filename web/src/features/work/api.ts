/**
 * Tasks, labels, bulk edit, undo and imports (M7).
 *
 * Two invalidation rules run through all of it, and both exist because M7's
 * writes touch more than the thing you asked about:
 *
 * - A **task on a lead** appends to that lead's timeline, so completing one
 *   has to invalidate the timeline as well as the task list.
 * - A **bulk edit, an undo or an import** rewrites many leads at once, so all
 *   of them invalidate the lead search, the edit report and the timeline —
 *   anything less leaves the screen showing pre-change data and reads as the
 *   write having failed.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type {
  Changeset,
  DuplicateGroup,
  ImportableField,
  ImportJob,
  ImportJobKind,
  Label,
  Page,
  Task,
  TaskBucket,
  TaskCounts,
  UndoPreview,
  UndoResult,
} from '@/api/types'

const tasksKey = (workspaceId: string) => ['tasks', workspaceId] as const
const labelsKey = (workspaceId: string) => ['labels', workspaceId] as const
const importsKey = (workspaceId: string) => ['imports', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

/** Everything a lead-wide write invalidates. Named once so no caller forgets. */
function leadWideKeys(workspaceId: string) {
  return [
    ['lead-search', workspaceId],
    ['leads', workspaceId],
    ['changesets', workspaceId],
    ['lead-actions', workspaceId],
  ]
}

// --- tasks -------------------------------------------------------------------

export function useTasks(
  workspaceId: string,
  options: { bucket?: TaskBucket; leadId?: string } = {},
) {
  return useQuery({
    queryKey: [...tasksKey(workspaceId), options] as const,
    queryFn: () =>
      api.get<Page<Task>>(`${base(workspaceId)}/tasks`, {
        query: {
          limit: 100,
          ...(options.bucket ? { bucket: options.bucket } : {}),
          ...(options.leadId ? { lead_id: options.leadId } : {}),
        },
      }),
  })
}

export function useTaskCounts(workspaceId: string) {
  return useQuery({
    queryKey: [...tasksKey(workspaceId), 'counts'] as const,
    queryFn: () => api.get<TaskCounts>(`${base(workspaceId)}/tasks/counts`),
  })
}

export function useLeadTasks(workspaceId: string, leadId: string | null) {
  return useQuery({
    queryKey: [...tasksKey(workspaceId), 'lead', leadId] as const,
    enabled: leadId !== null,
    queryFn: () => api.get<Task[]>(`${base(workspaceId)}/leads/${leadId as string}/tasks`),
  })
}

function useTaskMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      // The timeline too: a task on a lead is an entry in its audit trail.
      await Promise.all([
        client.invalidateQueries({ queryKey: tasksKey(workspaceId) }),
        client.invalidateQueries({ queryKey: ['lead-actions', workspaceId] }),
      ])
    },
  })
}

export function useCreateTask(workspaceId: string) {
  return useTaskMutation(workspaceId, (body: Record<string, unknown>) =>
    api.post<Task>(`${base(workspaceId)}/tasks`, body),
  )
}

export function useUpdateTask(workspaceId: string) {
  return useTaskMutation(workspaceId, ({ id, ...body }: { id: string } & Record<string, unknown>) =>
    api.patch<Task>(`${base(workspaceId)}/tasks/${id}`, body),
  )
}

export function useCompleteTask(workspaceId: string) {
  return useTaskMutation(workspaceId, (id: string) =>
    api.post<Task>(`${base(workspaceId)}/tasks/${id}/complete`, {}),
  )
}

export function useReopenTask(workspaceId: string) {
  return useTaskMutation(workspaceId, (id: string) =>
    api.post<Task>(`${base(workspaceId)}/tasks/${id}/reopen`, {}),
  )
}

// --- labels ------------------------------------------------------------------

export function useLabels(workspaceId: string) {
  return useQuery({
    queryKey: labelsKey(workspaceId),
    queryFn: () => api.get<Label[]>(`${base(workspaceId)}/labels`),
  })
}

export function useLeadLabels(workspaceId: string, leadId: string | null) {
  return useQuery({
    queryKey: [...labelsKey(workspaceId), 'lead', leadId] as const,
    enabled: leadId !== null,
    queryFn: () => api.get<Label[]>(`${base(workspaceId)}/leads/${leadId as string}/labels`),
  })
}

function useLabelMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => client.invalidateQueries({ queryKey: labelsKey(workspaceId) }),
  })
}

export function useCreateLabel(workspaceId: string) {
  return useLabelMutation(workspaceId, (body: { name: string; color?: string | null }) =>
    api.post<Label>(`${base(workspaceId)}/labels`, body),
  )
}

export function useArchiveLabel(workspaceId: string) {
  return useLabelMutation(workspaceId, (id: string) =>
    api.delete<Label>(`${base(workspaceId)}/labels/${id}`),
  )
}

export function useAttachLabel(workspaceId: string) {
  return useLabelMutation(workspaceId, ({ leadId, labelId }: { leadId: string; labelId: string }) =>
    api.post<Label[]>(`${base(workspaceId)}/leads/${leadId}/labels/${labelId}`, {}),
  )
}

export function useDetachLabel(workspaceId: string) {
  return useLabelMutation(workspaceId, ({ leadId, labelId }: { leadId: string; labelId: string }) =>
    api.delete<Label[]>(`${base(workspaceId)}/leads/${leadId}/labels/${labelId}`),
  )
}

// --- bulk edit and undo ------------------------------------------------------

function useLeadWideMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await Promise.all(
        leadWideKeys(workspaceId).map((queryKey) => client.invalidateQueries({ queryKey })),
      )
    },
  })
}

export function useBulkUpdate(workspaceId: string) {
  return useLeadWideMutation(workspaceId, (body: Record<string, unknown>) =>
    api.post<{ changeset_id: string; summary: string; leads_updated: number }>(
      `${base(workspaceId)}/leads/bulk`,
      body,
    ),
  )
}

export function useChangesets(
  workspaceId: string,
  filters: { source?: string; actor_id?: string; undone?: boolean } = {},
) {
  return useQuery({
    queryKey: ['changesets', workspaceId, filters] as const,
    queryFn: () =>
      api.get<Page<Changeset>>(`${base(workspaceId)}/changesets`, {
        query: { limit: 50, ...filters },
      }),
  })
}

/**
 * What undoing this changeset would do.
 *
 * A POST because the server treats it as an operation, but it writes nothing —
 * safe to call as often as the dialog needs.
 */
export function useUndoPreview(workspaceId: string, changesetId: string | null) {
  return useQuery({
    queryKey: ['undo-preview', workspaceId, changesetId] as const,
    enabled: changesetId !== null,
    queryFn: () =>
      api.post<UndoPreview>(
        `${base(workspaceId)}/changesets/${changesetId as string}/preview-undo`,
        {},
      ),
  })
}

export function useUndo(workspaceId: string) {
  return useLeadWideMutation(
    workspaceId,
    ({ changesetId, skipConflicts }: { changesetId: string; skipConflicts: boolean }) =>
      api.post<UndoResult>(`${base(workspaceId)}/changesets/${changesetId}/undo`, {
        skip_conflicts: skipConflicts,
      }),
  )
}

// --- imports and export ------------------------------------------------------

export function useImportableFields(workspaceId: string) {
  return useQuery({
    queryKey: ['importable-fields', workspaceId],
    queryFn: () => api.get<ImportableField[]>(`${base(workspaceId)}/imports/fields`),
  })
}

export function useImports(workspaceId: string) {
  return useQuery({
    queryKey: importsKey(workspaceId),
    queryFn: () =>
      api.get<Page<ImportJob>>(`${base(workspaceId)}/imports`, { query: { limit: 20 } }),
  })
}

/**
 * Upload — step one of four.
 *
 * Sent as multipart rather than base64 JSON: a 15MB spreadsheet grows by a
 * third in base64, and the server has to parse the header row anyway.
 */
export function useUploadImport(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ file, kind }: { file: File; kind: ImportJobKind }) => {
      const form = new FormData()
      form.append('file', file)
      // `formData`, not `body`: the client sets `Content-Type: application/json`
      // whenever a body is present, and a multipart request needs the browser
      // to set it with the boundary instead.
      return api.post<ImportJob>(`${base(workspaceId)}/imports`, undefined, {
        query: { kind },
        formData: form,
      })
    },
    onSuccess: () => client.invalidateQueries({ queryKey: importsKey(workspaceId) }),
  })
}

export function useSetImportMapping(workspaceId: string) {
  return useMutation({
    mutationFn: ({
      jobId,
      mapping,
      options,
    }: {
      jobId: string
      mapping: Record<string, string>
      options: Record<string, unknown>
    }) => api.put<ImportJob>(`${base(workspaceId)}/imports/${jobId}/mapping`, { mapping, options }),
  })
}

export function usePreviewImport(workspaceId: string) {
  return useMutation({
    mutationFn: (jobId: string) =>
      api.post<ImportJob>(`${base(workspaceId)}/imports/${jobId}/preview`, {}),
  })
}

export function useCommitImport(workspaceId: string) {
  return useLeadWideMutation(workspaceId, (jobId: string) =>
    api.post<ImportJob>(`${base(workspaceId)}/imports/${jobId}/commit`, {}),
  )
}

export function useDuplicates(workspaceId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['duplicates', workspaceId],
    enabled,
    queryFn: () => api.get<DuplicateGroup[]>(`${base(workspaceId)}/leads/duplicates`),
  })
}

export function useMergeLeads(workspaceId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { primary_id: string; merge_ids: string[] }) =>
      api.post<{ primary_id: string; merged_ids: string[]; fields_filled: string[] }>(
        `${base(workspaceId)}/leads/merge`,
        body,
      ),
    onSuccess: async () => {
      await Promise.all([
        ...leadWideKeys(workspaceId).map((queryKey) => client.invalidateQueries({ queryKey })),
        client.invalidateQueries({ queryKey: ['duplicates', workspaceId] }),
      ])
    },
  })
}
