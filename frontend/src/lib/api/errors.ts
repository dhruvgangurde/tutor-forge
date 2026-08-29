import { isAxiosError } from 'axios'

/**
 * Extract a user-friendly message from an API error.
 * Backend errors are shaped `{ error: string, detail: string }`
 * (see backend/core/exceptions.py) — prefer `detail` when present.
 */
export function getErrorMessage(
  error: unknown,
  fallback = 'Something went wrong. Please try again.'
): string {
  if (isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.length > 0) return detail
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}
