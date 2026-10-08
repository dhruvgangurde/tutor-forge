import { NavLink } from 'react-router-dom'
import { LogOutIcon } from '../ui/icons'
import { type NavItem, useLogout } from './navItems'

/**
 * Phone and small-tablet navigation (<=768px; hidden above that by CSS, where
 * the top bar or sidebar carries the same links). The role's existing sections
 * plus Log out as a fourth control, so signing out is never hidden in a menu.
 *
 * The selected tab is marked by shape as well as colour: an accent bar above
 * it, a filled pill behind the icon and a heavier label; NavLink also sets
 * aria-current="page".
 */
export function BottomTabBar({ items }: { items: NavItem[] }) {
  const handleLogout = useLogout()

  return (
    <nav className="tabbar" aria-label="Main">
      <ul className="tabbar-list">
        {items.map(({ to, label, Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              className={({ isActive }) => `tabbar-item${isActive ? ' active' : ''}`}
            >
              <span className="tabbar-icon" aria-hidden="true">
                <Icon size={22} />
              </span>
              <span className="tabbar-label">{label}</span>
            </NavLink>
          </li>
        ))}
        <li>
          <button type="button" className="tabbar-item" onClick={handleLogout}>
            <span className="tabbar-icon" aria-hidden="true">
              <LogOutIcon size={22} />
            </span>
            <span className="tabbar-label">Log out</span>
          </button>
        </li>
      </ul>
    </nav>
  )
}
