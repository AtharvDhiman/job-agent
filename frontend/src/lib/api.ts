'use client'

/** Browser-side API client. Always goes through the server proxy. */

export class ApiError extends Error {
  status: number
  details: unknown

  constructor(message: string, status: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`/api/proxy${path}`, { ...init, headers })
  const text = await response.text()
  const data = text ? JSON.parse(text) : null

  if (!response.ok) {
    const detail =
      data?.detail ??
      (Array.isArray(data?.errors)
        ? data.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join('; ')
        : `Request failed (${response.status})`)
    throw new ApiError(detail, response.status, data)
  }
  return data as T
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
  delete: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T,>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
}

export async function login(email: string, password: string) {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(data.detail ?? 'Login failed', response.status, data)
  }
}

export async function registerAccount(email: string, password: string, fullName: string) {
  const response = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, full_name: fullName }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const detail =
      data.detail ??
      (Array.isArray(data.errors)
        ? data.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join('; ')
        : 'Registration failed')
    throw new ApiError(detail, response.status, data)
  }
}

export async function logout() {
  await fetch('/api/auth/logout', { method: 'POST' })
}
