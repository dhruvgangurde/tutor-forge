import axios from 'axios'

// API base URL (F2):
//  - Dev: defaults to '/api', which Vite proxies to http://localhost:8000
//    (see vite.config.ts) so the app runs as a single origin.
//  - Production: set VITE_API_BASE_URL at build time to the deployed backend
//    origin (e.g. https://tutorforge-backend.onrender.com). On a static host
//    (Vercel) there is no dev proxy, so an absolute URL is required.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { 'Content-Type': 'application/json' },
})

// Inject JWT from localStorage on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('tf_access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// A 401 from these is a wrong email or password, not an expired session.
const CREDENTIAL_PATHS = ['/auth/login', '/auth/register']
const SIGN_IN_PAGES = ['/login', '/signup']

/**
 * True when a 401 means "your session is over": clear it and go to /login.
 *
 * Not for the login and signup requests themselves, and not while already on
 * a sign-in page: there the redirect reloaded the page, so "Invalid email or
 * password" was never seen.
 */
export function isExpiredSession(status: number | undefined, url: string | undefined): boolean {
  if (status !== 401) return false
  if (CREDENTIAL_PATHS.some((p) => (url ?? '').endsWith(p))) return false
  return !SIGN_IN_PAGES.includes(window.location.pathname)
}

// On 401 from an expired session: clear credentials and redirect to /login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (isExpiredSession(error.response?.status, error.config?.url)) {
      localStorage.removeItem('tf_access_token')
      localStorage.removeItem('tf_user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api
