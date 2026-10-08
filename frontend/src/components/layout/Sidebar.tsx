import { NavLink } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'
import { LogOutIcon } from '../ui/icons'
import { TEACHER_NAV, useLogout } from './navItems'

/**
 * Teacher navigation: a full-height sidebar with the wordmark, the two teacher
 * sections and the signed-in account with Log out at the bottom.
 */
export function Sidebar() {
  const { user } = useAuth()
  const handleLogout = useLogout()

  return (
    <aside className="sidebar">
      <div className="sidebar-inner">
        <div className="wordmark sidebar-wordmark">TutorForge</div>

        <nav className="sidebar-nav" aria-label="Main">
          {TEACHER_NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <Icon size={18} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          {user?.email && <p className="sidebar-account">{user.email}</p>}
          <button type="button" className="nav-link nav-link-logout" onClick={handleLogout}>
            <LogOutIcon size={18} />
            <span>Log out</span>
          </button>
        </div>
      </div>
    </aside>
  )
}
