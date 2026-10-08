import { QueryClient } from '@tanstack/react-query'
import { isAxiosError } from 'axios'

/**
 * Retry a failed query once -- unless the server answered with a 4xx.
 *
 * A 4xx (a missing course, a session that is not yours, a malformed id) gives
 * the same answer however often it is asked, so retrying it only doubled the
 * network calls and kept the page on a spinner for the retry delay before the
 * not-found page appeared. Network failures and 5xx can be transient and are
 * still retried once.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (isAxiosError(error)) {
    const status = error.response?.status
    if (status !== undefined && status >= 400 && status < 500) return false
  }
  return failureCount < 1
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,   // 30 seconds
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
  },
})
