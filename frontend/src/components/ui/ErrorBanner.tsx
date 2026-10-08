import { AlertIcon } from './icons'

interface ErrorBannerProps {
  /** Already user-facing: callers pass the output of getErrorMessage. */
  message: string
  /**
   * Shown as a "Try again" button only when the caller can genuinely retry
   * (design/STATES-AND-CHAT.md). Omit it everywhere else.
   */
  onRetry?: () => void
  retryLabel?: string
}

/**
 * Inline error card: a contained soft-red card next to the thing that failed,
 * with an icon and plain words. Keeps the `.error-banner` class and
 * role="alert" that tests and assistive tech rely on.
 */
export function ErrorBanner({ message, onRetry, retryLabel = 'Try again' }: ErrorBannerProps) {
  return (
    <div className="error-banner" role="alert">
      <AlertIcon size={18} className="error-banner-icon" />
      <p className="error-banner-message">{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-secondary btn-sm error-banner-retry" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  )
}
