import { NavLink } from 'react-router-dom'
import { LogOutIcon } from '../ui/icons'
import { STUDENT_NAV, useLogout } from './navItems'

/**
 * Student navigation: a top bar with the wordmark, the three student
 * sections (selected = ink text and a thin accent underline) and Log out.
 */
export function TopBar() {
  const handleLogout = useLogout()

  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="wordmark topbar-wordmark">TutorForge</div>

        <nav className="topbar-nav" aria-label="Main">
          {STUDENT_NAV.map(({ to, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `topbar-link${isActive ? ' active' : ''}`}
            >
              {label}
            </NavLink>
          ))}
        </nav>

        <button type="button" className="topbar-logout" onClick={handleLogout}>
          <LogOutIcon size={16} />
          <span>Log out</span>
        </button>
      </div>
    </header>
  )
}
