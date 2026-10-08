import { Outlet } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

/**
 * Authenticated application shell. Teachers get a full-height sidebar,
 * students a top bar (design mockups 2-5). Every authenticated route renders
 * inside via <Outlet/> (see App.tsx) -- feature pages must NOT render their
 * own shell wrapper.
 */
export function AppShell() {
  const { user } = useAuth()

  if (user?.role === 'teacher') {
    return (
      <div className="page-layout app-shell app-shell--sidebar">
        <Sidebar />
        <main className="main-content">
          <Outlet />
        </main>
      </div>
    )
  }

  return (
    <div className="page-layout app-shell app-shell--topbar">
      <TopBar />
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
