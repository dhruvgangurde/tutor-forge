import { useNavigate } from 'react-router-dom'
import type { ComponentType } from 'react'
import { useAuth } from '../../store/AuthContext'
import { BookIcon, ChartIcon, ChatIcon, CheckSquareIcon, ClipboardIcon } from '../ui/icons'

export interface NavItem {
  to: string
  label: string
  Icon: ComponentType<{ size?: number }>
}

// Routes and labels are unchanged; only the order puts each role's landing
// page first.
export const TEACHER_NAV: NavItem[] = [
  { to: '/courses', label: 'Courses', Icon: BookIcon },
  { to: '/grading', label: 'Grading', Icon: CheckSquareIcon },
]

export const STUDENT_NAV: NavItem[] = [
  { to: '/assessments', label: 'Assessments', Icon: ClipboardIcon },
  { to: '/tutor', label: 'Tutor', Icon: ChatIcon },
  { to: '/progress', label: 'Progress', Icon: ChartIcon },
]

/** Log out, then go to /login (unchanged behaviour, shared by both shells). */
export function useLogout() {
  const { logout } = useAuth()
  const navigate = useNavigate()
  return () => {
    logout()
    navigate('/login', { replace: true })
  }
}
