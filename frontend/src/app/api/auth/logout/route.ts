import { NextResponse } from 'next/server'

import { API_BASE, REFRESH_COOKIE, clearSessionCookies } from '@/lib/session'
import { cookies } from 'next/headers'

export async function POST() {
  const refresh = cookies().get(REFRESH_COOKIE)?.value
  if (refresh) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: 'no-store',
    }).catch(() => null)
  }
  return clearSessionCookies(NextResponse.json({ ok: true }))
}
