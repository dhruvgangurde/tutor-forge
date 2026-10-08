/**
 * Loading placeholders shaped like the final content (design/STATES-AND-CHAT.md):
 * pale blocks instead of a full-page spinner. The shimmer is static under
 * prefers-reduced-motion (index.css). One polite status line announces the
 * load to screen readers; the blocks themselves are hidden from them.
 */

interface SkeletonProps {
  /** What is loading, for screen readers, e.g. "Loading assessments". */
  label: string
}

function Announce({ label }: SkeletonProps) {
  return (
    <span className="sr-only" role="status">
      {label}
    </span>
  )
}

/** A single pale block; width/height accept any CSS length. */
export function SkeletonBlock({ width = '100%', height = '1rem' }: { width?: string; height?: string }) {
  return <span className="skeleton" style={{ width, height }} aria-hidden="true" />
}

/** Placeholder card grid, for lists rendered as cards. */
export function SkeletonCards({ label, count = 3 }: SkeletonProps & { count?: number }) {
  return (
    <div className="skeleton-cards">
      <Announce label={label} />
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card skeleton-card" aria-hidden="true">
          <SkeletonBlock width="60%" height="1.4rem" />
          <SkeletonBlock width="85%" />
          <SkeletonBlock width="40%" />
          <SkeletonBlock width="7.5rem" height="2.5rem" />
        </div>
      ))}
    </div>
  )
}

/** Placeholder table rows, for lists rendered as tables. */
export function SkeletonRows({ label, rows = 4, columns = 4 }: SkeletonProps & { rows?: number; columns?: number }) {
  return (
    <div className="skeleton-rows">
      <Announce label={label} />
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="skeleton-row" aria-hidden="true">
          {Array.from({ length: columns }, (_, c) => (
            <SkeletonBlock key={c} width={c === 0 ? '70%' : '45%'} />
          ))}
        </div>
      ))}
    </div>
  )
}
