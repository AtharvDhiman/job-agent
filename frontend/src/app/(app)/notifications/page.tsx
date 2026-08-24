'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Empty, ErrorNote, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Notification, Page } from '@/lib/types'

const KIND_TONE: Record<string, string> = {
  high_match_job: 'bg-good-soft text-good',
  review_required: 'bg-warn-soft text-warn',
  submission_succeeded: 'bg-good-soft text-good',
  submission_failed: 'bg-bad-soft text-bad',
  employer_reply: 'bg-brand-soft text-brand',
  daily_digest: 'bg-slate-100 text-ink-muted',
  system_alert: 'bg-warn-soft text-warn',
}

export default function NotificationsPage() {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const { data, error, loading, reload } = useAsync<Page<Notification>>(
    () => api.get<Page<Notification>>(`/notifications?limit=100&unread_only=${unreadOnly}`),
    [unreadOnly],
  )
  const [busy, setBusy] = useState(false)

  async function markAllRead() {
    setBusy(true)
    try {
      await api.post('/notifications/read')
      reload()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Notifications</h1>
          <p className="mt-1 text-sm text-ink-muted">
            High-match jobs, applications needing review, submissions and the daily digest.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.target.checked)}
            />
            Unread only
          </label>
          <button type="button" className="btn-secondary" onClick={markAllRead} disabled={busy}>
            Mark all read
          </button>
        </div>
      </header>

      <ErrorNote message={error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading...</p> : null}
      {data && data.items.length === 0 ? <Empty title="Nothing here yet" /> : null}

      <div className="space-y-2">
        {data?.items.map((item) => (
          <article
            key={item.id}
            className={`card-tight ${item.read_at ? '' : 'border-l-4 border-l-brand'}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`chip ${KIND_TONE[item.kind] ?? 'bg-slate-100 text-ink-muted'}`}>
                    {item.kind.replace(/_/g, ' ')}
                  </span>
                  <h2 className="text-sm font-medium text-ink">{item.title}</h2>
                </div>
                {item.body ? <div className="prose-plain mt-2">{item.body}</div> : null}
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {item.link ? (
                  <Link href={item.link} className="text-xs text-brand hover:underline">
                    Open
                  </Link>
                ) : null}
                <span className="whitespace-nowrap text-xs text-ink-muted">
                  {relativeTime(item.created_at)}
                </span>
              </div>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}
