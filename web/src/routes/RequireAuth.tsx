import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'

import { useAuth } from '@/features/auth/context'

/**
 * Requires a session but not a chosen workspace.
 *
 * `AppLayout` requires both. The workspace picker sits between the two, so it
 * needs this weaker guard — using the layout's would bounce it back to itself.
 */
export function RequireAuth({ children }: { readonly children: ReactNode }) {
  const { status } = useAuth()

  if (status === 'loading') {
    return (
      <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
        Loading…
      </div>
    )
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
