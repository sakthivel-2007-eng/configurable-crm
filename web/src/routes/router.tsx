import { createBrowserRouter, Navigate } from 'react-router-dom'

import { AppLayout } from '@/routes/AppLayout'
import { CustomActionsPage } from '@/routes/CustomActionsPage'
import { FieldSettingsPage } from '@/routes/FieldSettingsPage'
import { LeadsPage } from '@/routes/LeadsPage'
import { LoginPage } from '@/routes/LoginPage'
import { MembersPage } from '@/routes/MembersPage'
import { PermissionsPage } from '@/routes/PermissionsPage'
import { PipelineSettingsPage } from '@/routes/PipelineSettingsPage'
import { RequireAuth } from '@/routes/RequireAuth'
import { SystemStatusPage } from '@/routes/SystemStatusPage'
import { TemplatesPage } from '@/routes/TemplatesPage'
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
      // Leads is the landing page now that there is something to land on.
      { index: true, element: <Navigate to="/leads" replace /> },
      { path: 'leads', element: <LeadsPage /> },
      { path: 'templates', element: <TemplatesPage /> },
      { path: 'members', element: <MembersPage /> },
      { path: 'settings/fields', element: <FieldSettingsPage /> },
      { path: 'settings/pipeline', element: <PipelineSettingsPage /> },
      { path: 'settings/custom-actions', element: <CustomActionsPage /> },
      { path: 'settings/permissions', element: <PermissionsPage /> },
      { path: 'status', element: <SystemStatusPage /> },
    ],
  },
])
