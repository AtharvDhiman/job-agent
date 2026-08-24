'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Empty, ErrorNote, PolicyBadge, StatusBadge, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Application, Page } from '@/lib/types'

const STAGES = ['', 'saved', 'applied', 'screening', 'interview', 'offer', 'rejected', 'closed']
const STATUSES = [
  '',
  'drafting',
  'needs_review',
  'approved',
  'queued',
  'in_progress',
  'submitted',
  'failed',
  'blocked_by_policy',
  'cancelled',
]

export default function ApplicationsPage() {
  const [stage, setStage] = useState('')
  const [status, setStatus] = useState('')

  const query = new URLSearchParams({ limit: '100' })
  if (stage) query.set('stage', stage)
  if (status) query.set('status', status)

  const { data, error, loading } = useAsync<Page<Application>>(
    () => api.get<Page<Application>>(`/applications?${query.toString()}`),
    [stage, status],
  )

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Applications</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Every draft and submission, with its confirmation number and attempt history.
          </p>
        </div>
        <div className="flex gap-2">
          <select
            className="input w-auto"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Status"
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s ? s.replace(/_/g, ' ') : 'Any status'}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            value={stage}
            onChange={(e) => setStage(e.target.value)}
            aria-label="Pipeline stage"
          >
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {s || 'Any stage'}
              </option>
            ))}
          </select>
        </div>
      </header>

      <ErrorNote message={error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading...</p> : null}
      {data && data.items.length === 0 ? (
        <Empty title="No applications yet" hint="Draft one from a matched job." />
      ) : null}

      {data && data.items.length > 0 ? (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Summary</th>
                <th>Policy</th>
                <th>Stage</th>
                <th>Confirmation</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((application) => (
                <tr key={application.id}>
                  <td>
                    <StatusBadge status={application.status} />
                  </td>
                  <td className="max-w-lg">
                    <Link
                      href={`/applications/${application.id}`}
                      className="font-medium text-brand hover:underline"
                    >
                      {application.summary.split('\n')[0] || 'Application'}
                    </Link>
                    {application.fact_guard_flags.some((f) => f.severity === 'block') ? (
                      <div className="mt-1 text-xs text-bad">
                        Fact guard blocked this draft: a claim could not be traced to a verified
                        fact.
                      </div>
                    ) : null}
                    {application.last_error ? (
                      <div className="mt-1 text-xs text-bad">{application.last_error}</div>
                    ) : null}
                  </td>
                  <td>
                    <PolicyBadge policy={application.submission_policy} />
                  </td>
                  <td className="text-xs capitalize text-ink-muted">{application.pipeline_stage}</td>
                  <td className="font-mono text-xs text-ink-muted">
                    {application.confirmation_number ?? '-'}
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {relativeTime(application.updated_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
