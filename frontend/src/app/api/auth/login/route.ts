import { NextResponse } from 'next/server'

import { API_BASE, setSessionCookies } from '@/lib/session'

export async function POST(request: Request) {
  const body = await request.json()
  const upstream = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  }).catch(() => null)

  if (!upstream) {
    return NextResponse.json(
      { detail: `Could not reach the API at ${API_BASE}. Is the backend running?` },
      { status: 503 },
    )
  }

  const data = await upstream.json().catch(() => ({}))
  if (!upstream.ok) {
    return NextResponse.json(data, { status: upstream.status })
  }
  return setSessionCookies(NextResponse.json({ ok: true }), data)
}
