import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { queryClient, shouldRetry } from './queryClient'

function apiError(status: number) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: {} },
  })
}

describe('query retry policy (frontend audit #12)', () => {
  it('never retries a 4xx', () => {
    for (const status of [400, 401, 403, 404, 409, 422, 429]) {
      expect(shouldRetry(0, apiError(status))).toBe(false)
    }
  })

  it('retries a 5xx or a network failure once', () => {
    expect(shouldRetry(0, apiError(503))).toBe(true)
    expect(shouldRetry(1, apiError(503))).toBe(false)
    const offline = Object.assign(new Error('Network Error'), { isAxiosError: true })
    expect(shouldRetry(0, offline)).toBe(true)
    expect(shouldRetry(1, offline)).toBe(false)
  })

  it('is the default for every query', () => {
    expect(queryClient.getDefaultOptions().queries?.retry).toBe(shouldRetry)
  })

  it('makes exactly one request for a 404', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: shouldRetry, retryDelay: 0 } } })
    const fetcher = vi.fn().mockRejectedValue(apiError(404))
    await expect(client.fetchQuery({ queryKey: ['missing'], queryFn: fetcher, retry: shouldRetry, retryDelay: 0 })).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('makes two requests for a 500', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: shouldRetry, retryDelay: 0 } } })
    const fetcher = vi.fn().mockRejectedValue(apiError(500))
    await expect(client.fetchQuery({ queryKey: ['flaky'], queryFn: fetcher, retry: shouldRetry, retryDelay: 0 })).rejects.toThrow()
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
