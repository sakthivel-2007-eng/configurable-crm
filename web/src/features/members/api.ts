/**
 * Member administration queries and mutations.
 *
 * Every path is built from the active workspace id, so there is no call here
 * that could address another tenant even if a component passed a stray uuid —
 * the server would answer 404 for it anyway, but the client should not be the
 * thing that tries.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, apiRequest } from '@/api/client'
import type {
  AvailabilityStatus,
  BulkUploadReport,
  DeactivateResponse,
  MemberDetail,
  Page,
  SeatUsage,
} from '@/api/types'

const membersKey = (workspaceId: string) => ['members', workspaceId] as const
const seatsKey = (workspaceId: string) => ['seats', workspaceId] as const

function base(workspaceId: string): string {
  return `/workspaces/${workspaceId}`
}

export function useMembers(workspaceId: string) {
  return useQuery({
    queryKey: membersKey(workspaceId),
    queryFn: () =>
      api.get<Page<MemberDetail>>(`${base(workspaceId)}/members`, { query: { limit: 100 } }),
  })
}

export function useSeatUsage(workspaceId: string) {
  return useQuery({
    queryKey: seatsKey(workspaceId),
    queryFn: () => api.get<SeatUsage>(`${base(workspaceId)}/members/seats`),
  })
}

/** Invalidates both members and seats — a licence change moves both. */
function useMemberMutation<TVariables, TResult>(
  workspaceId: string,
  mutationFn: (variables: TVariables) => Promise<TResult>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: seatsKey(workspaceId) }),
      ])
    },
  })
}

export function useInviteMember(workspaceId: string) {
  return useMemberMutation(
    workspaceId,
    (input: {
      email: string
      full_name: string
      template_id: string
      manager_id: string | null
      grant_license: boolean
    }) => api.post<MemberDetail>(`${base(workspaceId)}/members`, input),
  )
}

export function useSetAvailability(workspaceId: string) {
  return useMemberMutation(
    workspaceId,
    (input: { membershipId: string; status: AvailabilityStatus; note: string | null }) =>
      api.put<MemberDetail>(`${base(workspaceId)}/members/${input.membershipId}/availability`, {
        status: input.status,
        note: input.note,
      }),
  )
}

export function useToggleLicense(workspaceId: string) {
  return useMemberMutation(workspaceId, (input: { membershipId: string; grant: boolean }) =>
    input.grant
      ? api.post<MemberDetail>(`${base(workspaceId)}/members/${input.membershipId}/license`)
      : api.delete<MemberDetail>(`${base(workspaceId)}/members/${input.membershipId}/license`),
  )
}

export function useDeactivateMember(workspaceId: string) {
  return useMemberMutation(
    workspaceId,
    (input: { membershipId: string; reassignTo: string | null }) =>
      api.post<DeactivateResponse>(
        `${base(workspaceId)}/members/${input.membershipId}/deactivate`,
        {
          reassign_to_membership_id: input.reassignTo,
        },
      ),
  )
}

export function useReactivateMember(workspaceId: string) {
  return useMemberMutation(workspaceId, (membershipId: string) =>
    api.post<MemberDetail>(`${base(workspaceId)}/members/${membershipId}/reactivate`),
  )
}

export function useBulkUpload(workspaceId: string) {
  return useMemberMutation(workspaceId, (input: { file: File; dryRun: boolean }) => {
    const formData = new FormData()
    formData.append('file', input.file)
    return apiRequest<BulkUploadReport>(`${base(workspaceId)}/members/bulk-upload`, {
      method: 'POST',
      formData,
      query: { dry_run: input.dryRun },
    })
  })
}
