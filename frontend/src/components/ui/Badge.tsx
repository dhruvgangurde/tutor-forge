import { ReactNode } from 'react'
import { AlertIcon, CheckIcon, CircleIcon, ClockIcon, InfoIcon } from './icons'

export type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'muted'

interface BadgeProps {
  variant: BadgeVariant
  children: ReactNode
}

// Every chip carries an icon as well as its word: status never rests on colour
// alone (design brief). Neutral (muted) chips get a small hollow dot.
const ICONS: Record<BadgeVariant, (p: { size?: number }) => JSX.Element> = {
  success: CheckIcon,
  warning: ClockIcon,
  danger: AlertIcon,
  info: InfoIcon,
  muted: CircleIcon,
}

/** Status chip: icon plus word, tinted by meaning. Uses `.badge badge-{variant}` (index.css). */
export function Badge({ variant, children }: BadgeProps) {
  const Icon = ICONS[variant]
  return (
    <span className={`badge badge-${variant}`}>
      <Icon size={13} />
      <span className="badge-text">{children}</span>
    </span>
  )
}
