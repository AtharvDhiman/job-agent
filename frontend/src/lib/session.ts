/**
 * Server-side session handling.
 *
 * Tokens live in httpOnly cookies and are never exposed to client JavaScript.
 * Every browser request to the API goes through /api/proxy, which reads the
 * cookie server-side and attaches the Authorization header.
 */
import { cookies } from 'next/headers'
import type { NextResponse } from 'next/server'

export const ACCESS_COOKIE = 'ja_access'
export const REFRESH_COOKIE = 'ja_refresh'

export const API_BASE =
  process.env.API_INTERNAL_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  'http://localhost:8000/api/v1'

const isProd = process.env.NODE_ENV === 'production'

const baseCookie = {
  httpOnly: true,
  secure: isProd,
  sameSite: 'lax' as const,
  path: '/',
}

export function setSessionCookies(
  response: NextResponse,
  tokens: { access_token: string; refresh_token: string; expires_in: number },
) {
  response.cookies.set(ACCESS_COOKIE, tokens.access_token, {
    ...baseCookie,
    maxAge: tokens.expires_in,
  })
  response.cookies.set(REFRESH_COOKIE, tokens.refresh_token, {
    ...baseCookie,
    maxAge: 60 * 60 * 24 * 14,
  })
  return response
}

export function clearSessionCookies(response: NextResponse) {
  response.cookies.set(ACCESS_COOKIE, '', { ...baseCookie, maxAge: 0 })
  response.cookies.set(REFRESH_COOKIE, '', { ...baseCookie, maxAge: 0 })
  return response
}

export function readAccessToken(): string | undefined {
  return cookies().get(ACCESS_COOKIE)?.value
}

export function readRefreshToken(): string | undefined {
  return cookies().get(REFRESH_COOKIE)?.value
}

/** Server component / route handler fetch against the backend. */
export async function apiFetch<T>(
  path: string,
  init: RequestInit & { token?: string } = {},
): Promise<{ ok: boolean; status: number; data: T | null; error?: string }> {
  const token = init.token ?? readAccessToken()
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      cache: 'no-store',
    })
    const text = await response.text()
    const data = text ? (JSON.parse(text) as T) : null
    if (!response.ok) {
      const detail =
        (data as { detail?: string } | null)?.detail ?? `Request failed (${response.status})`
      return { ok: false, status: response.status, data, error: detail }
    }
    return { ok: true, status: response.status, data }
  } catch (error) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: `Could not reach the API at ${API_BASE}. Is the backend running? (${String(error)})`,
    }
  }
}
