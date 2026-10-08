import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, describe, expect, it } from 'vitest'
import { attachStoredToken, isExpiredSession } from './client'

function requestWith(headers: Record<string, string> = {}): InternalAxiosRequestConfig {
  return { headers: new AxiosHeaders(headers) } as InternalAxiosRequestConfig
}

describe('stored token on requests', () => {
  afterEach(() => localStorage.clear())

  it('attaches the stored token when the caller set none', () => {
    localStorage.setItem('tf_access_token', 'stored-tok')
    expect(attachStoredToken(requestWith()).headers.Authorization).toBe('Bearer stored-tok')
  })

  it('keeps a token the caller set explicitly', () => {
    // E.g. revoking the previous session, or /auth/me with a just-issued token:
    // overwriting it sent the wrong session's token.
    localStorage.setItem('tf_access_token', 'stored-tok')
    const config = attachStoredToken(requestWith({ Authorization: 'Bearer explicit-tok' }))
    expect(config.headers.Authorization).toBe('Bearer explicit-tok')
  })

  it('sends nothing when signed out', () => {
    expect(attachStoredToken(requestWith()).headers.Authorization).toBeUndefined()
  })
})

function at(path: string) {
  window.history.replaceState(null, '', path)
}

describe('401 handling (failed login was never shown)', () => {
  afterEach(() => at('/'))

  it('does not treat a wrong password as an expired session', () => {
    at('/login')
    expect(isExpiredSession(401, '/auth/login')).toBe(false)
    at('/signup')
    expect(isExpiredSession(401, '/auth/register')).toBe(false)
  })

  it('never redirects while already on a sign-in page', () => {
    at('/login')
    expect(isExpiredSession(401, '/auth/me')).toBe(false)
  })

  it('still ends an expired session anywhere else', () => {
    at('/courses')
    expect(isExpiredSession(401, '/courses')).toBe(true)
    expect(isExpiredSession(401, '/auth/me')).toBe(true)
  })

  it('ignores other statuses', () => {
    at('/courses')
    expect(isExpiredSession(403, '/courses')).toBe(false)
    expect(isExpiredSession(undefined, '/courses')).toBe(false)
  })
})
