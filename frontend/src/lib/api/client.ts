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

// On 401: clear credentials and redirect to /login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('tf_access_token')
      localStorage.removeItem('tf_user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default api
