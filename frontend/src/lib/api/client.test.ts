import { afterEach, describe, expect, it } from 'vitest'
import { isExpiredSession } from './client'

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
