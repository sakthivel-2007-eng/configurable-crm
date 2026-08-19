import { createBrowserRouter, Navigate } from 'react-router-dom'

import { AppLayout } from '@/routes/AppLayout'
import { LoginPage } from '@/routes/LoginPage'
import { MembersPage } from '@/routes/MembersPage'
import { RequireAuth } from '@/routes/RequireAuth'
import { SystemStatusPage } from '@/routes/SystemStatusPage'
import { WorkspacePickerPage } from '@/routes/WorkspacePickerPage'

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    // Signed in, but no workspace chosen yet — the picker sits between the two
    // guards, so it must not require an active workspace itself.
    path: '/workspaces',
    element: (
      <RequireAuth>
        <WorkspacePickerPage />
      </RequireAuth>
    ),
  },
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/members" replace /> },
      { path: 'members', element: <MembersPage /> },
      { path: 'status', element: <SystemStatusPage /> },
    ],
  },
])
