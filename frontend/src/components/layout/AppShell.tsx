import { Outlet } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'
import { BottomTabBar } from './BottomTabBar'
import { STUDENT_NAV, TEACHER_NAV } from './navItems'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

/**
 * Authenticated application shell. Teachers get a full-height sidebar,
 * students a top bar (design mockups 2-5). At <=768px both shrink to the
 * wordmark and the links move to a bottom tab bar (CSS decides which shows). Every authenticated route renders
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
        <BottomTabBar items={TEACHER_NAV} />
      </div>
    )
  }

  return (
    <div className="page-layout app-shell app-shell--topbar">
      <TopBar />
      <main className="main-content">
        <Outlet />
      </main>
      <BottomTabBar items={STUDENT_NAV} />
    </div>
  )
}
