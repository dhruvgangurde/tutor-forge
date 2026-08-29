import { ReactNode } from 'react'

interface EmptyStateProps {
  icon: string
  label: string
  action?: ReactNode
}

/**
 * Generalizes the `.placeholder-page` markup previously duplicated inline in
 * every feature page (index.css). Used both for legitimate empty states
 * ("no courses yet") and, until a feature phase lands, as a stand-in.
 */
export function EmptyState({ icon, label, action }: EmptyStateProps) {
  return (
    <div className="placeholder-page">
      <div className="placeholder-icon">{icon}</div>
      <p className="placeholder-label">{label}</p>
      {action}
    </div>
  )
}
