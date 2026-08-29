import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'

/**
 * Authenticated application shell: sidebar + main content area.
 * Every authenticated route renders inside this via <Outlet/> (see App.tsx) —
 * feature pages must NOT re-render their own .page-layout/.main-content wrapper.
 */
export function AppShell() {
  return (
    <div className="page-layout">
      <Sidebar />
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
