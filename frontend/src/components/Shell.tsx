'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { api, logout } from '@/lib/api'
import type { AgentSettings } from '@/lib/types'

const NAV = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/jobs', label: 'Matched jobs' },
  { href: '/companies', label: 'Companies' },
  { href: '/reviews', label: 'Needs review' },
  { href: '/applications', label: 'Applications' },
  { href: '/notifications', label: 'Notifications' },
  { href: '/profile', label: 'Profile & facts' },
  { href: '/settings', label: 'Settings' },
  { href: '/audit', label: 'Audit log' },
]

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [settings, setSettings] = useState<AgentSettings | null>(null)
  const [unread, setUnread] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, n] = await Promise.all([
        api.get<AgentSettings>('/settings'),
        api.get<{ unread: number }>('/notifications/unread-count'),
      ])
      setSettings(s)
      setUnread(n.unread)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh, pathname])

  async function toggleAutomation() {
    if (!settings) return
    setBusy(true)
    try {
      const next = settings.automation_enabled
        ? await api.post<AgentSettings>('/settings/pause', {
            reason: 'Paused from the dashboard',
          })
        : await api.post<AgentSettings>('/settings/resume')
      setSettings(next)
      setError(null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    await logout()
    router.push('/login')
    router.refresh()
  }

  const running = settings?.automation_enabled ?? false

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-surface">
        <div className="border-b border-slate-200 px-5 py-4">
          <div className="text-sm font-semibold text-ink">Job Application Agent</div>
          <div className="mt-0.5 text-xs text-ink-muted">Review-first by default</div>
        </div>

        <nav className="flex-1 space-y-0.5 p-3">
          {NAV.map((item) => {
            const active = pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center justify-between rounded-md px-3 py-2 text-sm transition ${
                  active
                    ? 'bg-brand-soft font-medium text-brand'
                    : 'text-ink-soft hover:bg-surface-raised'
                }`}
              >
                <span>{item.label}</span>
                {item.href === '/reviews' && unread > 0 ? (
                  <span className="chip bg-warn-soft text-warn">{unread}</span>
                ) : null}
              </Link>
            )
          })}
        </nav>

        <div className="space-y-2 border-t border-slate-200 p-3">
          <button
            type="button"
            onClick={toggleAutomation}
            disabled={busy || !settings}
            className={running ? 'btn-danger w-full' : 'btn-primary w-full'}
            title={
              running
                ? 'Stop all automated drafting and submission immediately'
                : 'Allow the agent to draft and, where authorized, submit'
            }
          >
            {running ? 'Pause all automation' : 'Resume automation'}
          </button>
          <div className="px-1 text-xs text-ink-muted">
            {running
              ? 'Automation is running.'
              : `Paused${settings?.paused_reason ? `: ${settings.paused_reason}` : '.'}`}
          </div>
          <button type="button" onClick={signOut} className="btn-ghost w-full">
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-x-auto">
        {error ? (
          <div className="border-b border-bad/30 bg-bad-soft px-8 py-2 text-sm text-bad">
            {error}
          </div>
        ) : null}
        <div className="mx-auto max-w-6xl px-8 py-8">{children}</div>
      </main>
    </div>
  )
}
