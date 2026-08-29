interface SpinnerProps {
  label?: string
}

/** Wraps the existing `.spinner` CSS class (index.css). Optional label rendered below it. */
export function Spinner({ label }: SpinnerProps) {
  return (
    <div className="spinner-wrap" role="status" aria-live="polite">
      <div className="spinner" />
      {label && <span className="spinner-label">{label}</span>}
    </div>
  )
}
