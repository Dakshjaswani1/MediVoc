/** Tiny API helper: keeps the auth token and turns error responses into thrown Errors. */
export const API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

export type Role = 'patient' | 'doctor' | 'admin'

export type User = {
  id: number
  name: string
  email: string
  phone: string
  role: Role
  doctor_id: number | null
}

const TOKEN_KEY = 'medivoc.token'
const USER_KEY = 'medivoc.user'

let authToken: string | null = localStorage.getItem(TOKEN_KEY)

export function savedUser(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw || !authToken) return null
  try {
    return JSON.parse(raw) as User
  } catch {
    return null
  }
}

export function saveSession(token: string, user: User) {
  authToken = token
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  authToken = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function authHeader(): Record<string, string> {
  return authToken ? { Authorization: `Bearer ${authToken}` } : {}
}

type Options = { method?: string; body?: unknown }

export async function api<T = any>(path: string, options: Options = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...authHeader(),
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  if (response.status === 401) {
    clearSession()
    throw new Error('Your session has expired. Please sign in again.')
  }

  const text = await response.text()
  const data = text ? JSON.parse(text) : null

  if (!response.ok) {
    const detail = data?.detail
    // FastAPI validation errors arrive as an array of issues.
    const message = Array.isArray(detail)
      ? detail.map((item: any) => item.msg).join(', ')
      : (detail ?? 'The request could not be completed.')
    throw new Error(message)
  }

  return data as T
}

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function today(): string {
  return new Date().toISOString().slice(0, 10)
}
