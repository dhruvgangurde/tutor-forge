import React, { createContext, useContext, useState, useCallback } from 'react'
import api from '../lib/api/client'

interface User {
  id: string
  email: string
  role: 'teacher' | 'student'
}

interface AuthContextValue {
  accessToken: string | null
  user: User | null
  login: (token: string, user: User) => void
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

const TOKEN_KEY = 'tf_access_token'
const USER_KEY = 'tf_user'

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(
    () => localStorage.getItem(TOKEN_KEY)
  )
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  })

  const login = useCallback((token: string, u: User) => {
    // Signing in while another session's token is still stored used to just
    // overwrite it, leaving that token valid until it expired. Revoke it now,
    // best-effort and without waiting. This only runs after a login has
    // succeeded, so a failed attempt never ends the session in place.
    const previous = localStorage.getItem(TOKEN_KEY)
    if (previous && previous !== token) {
      void api
        .post('/auth/logout', {}, { headers: { Authorization: `Bearer ${previous}` } })
        .catch(() => {})
    }
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(u))
    setAccessToken(token)
    setUser(u)
  }, [])

  const logout = useCallback(() => {
    // End the session on the server too: /auth/logout revokes this access
    // token, so a copy of it (another tab, a leaked token) stops working now
    // rather than at expiry. Fire-and-forget -- logging out locally must never
    // wait on, or fail because of, the network. The header is passed
    // explicitly because the request interceptor runs after the token is
    // removed from storage below.
    const token = localStorage.getItem(TOKEN_KEY)
    if (token) {
      void api
        .post('/auth/logout', {}, { headers: { Authorization: `Bearer ${token}` } })
        .catch(() => {})
    }
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    setAccessToken(null)
    setUser(null)
  }, [])

  const value: AuthContextValue = {
    accessToken,
    user,
    login,
    logout,
    isAuthenticated: !!accessToken && !!user,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
