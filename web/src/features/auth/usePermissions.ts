/**
 * Resolved permissions for the active workspace.
 *
 * The frontend builds its UI from `/me/permissions` rather than inferring
 * anything from the template name — "Manager" is a label a workspace admin can
 * rename or repurpose, so branching on it would break the moment a customer
 * did. Branch on capabilities.
 *
 * Note this only hides UI. Every capability is enforced server-side too; a
 * hidden button is a courtesy, not a control.
 */

import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { ResolvedPermissions } from '@/api/types'
import { useAuth } from '@/features/auth/context'

export function usePermissions() {
  const { activeWorkspaceId } = useAuth()

  const query = useQuery({
    queryKey: ['permissions', activeWorkspaceId],
    enabled: activeWorkspaceId !== null,
    queryFn: () =>
      api.get<ResolvedPermissions>('/me/permissions', {
        query: { workspace_id: activeWorkspaceId as string },
      }),
  })

  const capabilities = query.data?.capabilities ?? {}

  const can = (group: string, name: string): boolean => capabilities[group]?.[name] === true

  return {
    ...query,
    can,
    isAdmin: can('leads', 'admin_access') || can('permissions', 'admin_access'),
  }
}
