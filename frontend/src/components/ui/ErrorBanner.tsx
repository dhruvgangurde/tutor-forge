interface ErrorBannerProps {
  message: string
}

/** Wraps the existing `.error-banner` CSS class (index.css), matching LoginPage's inline usage. */
export function ErrorBanner({ message }: ErrorBannerProps) {
  return (
    <div className="error-banner" role="alert">
      {message}
    </div>
  )
}
