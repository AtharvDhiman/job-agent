/**
 * Server-side proxy to the backend API.
 *
 * The browser never holds a token: it calls /api/proxy/<path>, this handler
 * reads the httpOnly cookie, attaches the Authorization header, and on a 401
 * silently rotates the refresh token once before giving up.
 */
import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'

import { isSafePath } from '@/lib/path-safety'
import { ACCESS_COOKIE, API_BASE, REFRESH_COOKIE, setSessionCookies } from '@/lib/session'

const PASSTHROUGH_HEADERS = ['content-type', 'content-disposition', 'cache-control']

/**
 * A path segment is only safe if it stays a single segment.
 *
 * Next decodes catch-all segments before handing them over, so `%2e%2e` arrives
 * here as `..` and `%2f` as `/`. Joining those straight back into a URL let a
 * caller walk out of API_BASE -- `/api/proxy/..%2f..%2fopenapi.json` resolved to
 * the backend root instead of `/api/v1/...`, turning the proxy into a way to
 * reach parts of the API the prefix was meant to fence off. Anything that is not
 * a plain segment is refused rather than normalised.
 */

async function refreshTokens(): Promise<{
  access_token: string
  refresh_token: string
  expires_in: number
} | null> {
  const refresh = cookies().get(REFRESH_COOKIE)?.value
  if (!refresh) return null
  const upstream = await fetch(`${API_BASE}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
    cache: 'no-store',
  }).catch(() => null)
  if (!upstream || !upstream.ok) return null
  return upstream.json()
}

async function forward(request: Request, path: string[], token: string | undefined) {
  const url = new URL(request.url)
  const target = `${API_BASE}/${path.join('/')}${url.search}`
  const headers = new Headers()
  const contentType = request.headers.get('content-type')
  if (contentType) headers.set('content-type', contentType)
  headers.set('accept', request.headers.get('accept') ?? 'application/json')
  if (token) headers.set('authorization', `Bearer ${token}`)

  const body =
    request.method === 'GET' || request.method === 'HEAD'
      ? undefined
      : await request.arrayBuffer()

  return fetch(target, { method: request.method, headers, body, cache: 'no-store' })
}

async function handle(request: Request, context: { params: { path: string[] } }) {
  const path = context.params.path ?? []
  if (!isSafePath(path)) {
    return NextResponse.json({ detail: 'Invalid API path.' }, { status: 400 })
  }
  let token = cookies().get(ACCESS_COOKIE)?.value

  let upstream = await forward(request.clone(), path, token).catch(() => null)
  if (!upstream) {
    return NextResponse.json(
      { detail: `Could not reach the API at ${API_BASE}.` },
      { status: 503 },
    )
  }

  // Exactly one refresh attempt, and exactly one retry after it. There is no
  // loop here: a second 401 is returned to the caller as-is.
  let rotated: Awaited<ReturnType<typeof refreshTokens>> = null
  if (upstream.status === 401) {
    rotated = await refreshTokens()
    if (rotated) {
      token = rotated.access_token
      const retry = await forward(request.clone(), path, token).catch(() => null)
      if (retry) upstream = retry
    }
  }

  const buffer = await upstream.arrayBuffer()
  const responseHeaders = new Headers()
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name)
    if (value) responseHeaders.set(name, value)
  }
  const response = new NextResponse(buffer, {
    status: upstream.status,
    headers: responseHeaders,
  })
  return rotated ? setSessionCookies(response as NextResponse, rotated) : response
}

export const GET = handle
export const POST = handle
export const PATCH = handle
export const PUT = handle
export const DELETE = handle
