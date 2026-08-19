import { Link, Navigate, Outlet, useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/context'

/**
 * The authenticated shell.
 *
 * Guards two things in one place: you must be signed in, and you must have
 * chosen a workspace. Every tenant route hangs off this, so no page has to
 * check either for itself.
 */
export function AppLayout() {
  const { status, user, activeMembership, activeWorkspaceId, memberships, logout } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div className="text-muted-foreground flex min-h-screen items-center justify-center text-sm">
        Loading…
      </div>
    )
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (!activeWorkspaceId || !activeMembership) {
    return <Navigate to="/workspaces" replace />
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between gap-4 border-b px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="font-semibold">{activeMembership.workspace.name}</span>
          <nav className="flex items-center gap-1">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/members">Team</Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/status">System</Link>
            </Button>
          </nav>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-muted-foreground text-sm">{user?.email}</span>
          {memberships.length > 1 ? (
            <Button variant="outline" size="sm" asChild>
              <Link to="/workspaces">Switch workspace</Link>
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      </header>

      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
