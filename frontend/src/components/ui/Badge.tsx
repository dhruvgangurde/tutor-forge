import { ReactNode } from 'react'

export type BadgeVariant = 'success' | 'warning' | 'danger' | 'info' | 'muted'

interface BadgeProps {
  variant: BadgeVariant
  children: ReactNode
}

/** Wraps the existing `.badge badge-{variant}` CSS classes (index.css). */
export function Badge({ variant, children }: BadgeProps) {
  return <span className={`badge badge-${variant}`}>{children}</span>
}
