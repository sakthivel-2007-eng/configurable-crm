import { createBrowserRouter } from 'react-router-dom'

import { SystemStatusPage } from '@/routes/SystemStatusPage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <SystemStatusPage />,
  },
])
