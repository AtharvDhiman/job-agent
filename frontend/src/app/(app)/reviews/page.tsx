'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Empty, ErrorNote, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import { REVIEW_REASON_LABEL } from '@/lib/labels'
import type { Page, ReviewTask } from '@/lib/types'

export default function ReviewsPage() {
  const [status, setStatus] = useState('open')
  const { data, error, loading, reload } = useAsync<Page<ReviewTask>>(
    () => api.get<Page<ReviewTask>>(`/reviews?status=${status}&limit=100`),
    [status],
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function resolve(task: ReviewTask, action: 'approve' | 'reject') {
    setBusy(task.id)
    setNote(null)
    try {
      await api.post(`/reviews/${task.id}/${action}`, {
        note: action === 'approve' ? 'Approved from the review queue' : 'Rejected by me',
      })
      reload()
    } catch (err) {
      setNote((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Needs review</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Everything the agent refused to do on its own, with a direct link and the draft it
            prepared.
          </p>
        </div>
        <select
          className="input w-auto"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Review status"
        >
          <option value="open">Open</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired">Expired</option>
          <option value="all">All</option>
        </select>
      </header>

      <ErrorNote message={note ?? error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading...</p> : null}

      {data && data.items.length === 0 ? (
        <Empty
          title="Nothing waiting on you"
          hint="Review tasks appear whenever the agent hits a stop condition."
        />
      ) : null}

      <div className="space-y-3">
        {data?.items.map((task) => (
          <article key={task.id} className="card">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="font-medium text-ink">{task.title}</h2>
                <p className="mt-0.5 text-xs text-ink-muted">
                  {REVIEW_REASON_LABEL[task.reason] ?? task.reason} - {relativeTime(task.created_at)}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {task.action_url ? (
                  <a
                    href={task.action_url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn-secondary"
                  >
                    Open and apply yourself
                  </a>
                ) : null}
                {task.application_id ? (
                  <Link
                    href={`/applications/${task.application_id}`}
                    className="btn-secondary"
                  >
                    Open draft
                  </Link>
                ) : null}
                {task.status === 'open' ? (
                  <>
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={busy === task.id}
                      onClick={() => resolve(task, 'approve')}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="btn-ghost"
                      disabled={busy === task.id}
                      onClick={() => resolve(task, 'reject')}
                    >
                      Reject
                    </button>
                  </>
                ) : (
                  <span className="chip bg-slate-100 text-ink-muted">{task.status}</span>
                )}
              </div>
            </div>

            {task.detail ? <p className="mt-3 text-sm text-ink-soft">{task.detail}</p> : null}

            {task.blocking_questions.length > 0 ? (
              <div className="mt-3 rounded-md border border-warn/30 bg-warn-soft p-3">
                <p className="text-xs font-medium text-warn">
                  Needs your answer before this can be submitted:
                </p>
                <ul className="mt-1.5 space-y-1 text-xs text-warn">
                  {task.blocking_questions.map((q, index) => (
                    <li key={index}>
                      - {q.question ?? q.text ?? JSON.stringify(q)}
                      {q.reason ? ` (${q.reason})` : ''}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  )
}
