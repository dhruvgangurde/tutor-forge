import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../../store/AuthContext'

interface NavItem {
  to: string
  label: string
  icon: string
}

const TEACHER_NAV: NavItem[] = [
  { to: '/courses', label: 'Courses', icon: '📚' },
  { to: '/grading', label: 'Grading', icon: '🎓' },
]

const STUDENT_NAV: NavItem[] = [
  { to: '/tutor', label: 'Tutor', icon: '🧑‍🏫' },
  { to: '/assessments', label: 'Assessments', icon: '📝' },
]

/** Role-aware navigation sidebar. Uses the existing `.sidebar` CSS system (index.css). */
export function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const navItems = user?.role === 'teacher' ? TEACHER_NAV : STUDENT_NAV

  function handleLogout() {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        Tutor<span>Forge</span>
      </div>

      <nav className="sidebar-nav">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <span aria-hidden="true">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <button type="button" className="nav-link nav-link-logout" onClick={handleLogout}>
          <span aria-hidden="true">🚪</span>
          <span>Log out</span>
        </button>
      </div>
    </aside>
  )
}
