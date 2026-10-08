import { ReactNode } from 'react'

interface EmptyStateProps {
  /** One line saying what is empty, why, and what to do. */
  label: string
  /** Optional short heading above the line. */
  title?: string
  /** Optional single action (a button or link). */
  action?: ReactNode
  /**
   * @deprecated Kept so existing callers compile; no longer rendered. Empty
   * states are quiet text in the page layout, without an illustration
   * (design/STATES-AND-CHAT.md).
   */
  icon?: string
}

/**
 * Quiet empty state: optional title, one line, optional action. Sits in the
 * normal page flow. The `.placeholder-label` class is kept for the line.
 */
export function EmptyState({ label, title, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {title && <h2 className="empty-state-title">{title}</h2>}
      <p className="placeholder-label empty-state-label">{label}</p>
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  )
}
