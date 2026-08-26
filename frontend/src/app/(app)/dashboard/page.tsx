'use client'

import Link from 'next/link'
import { useState } from 'react'

import {
  Banner,
  Empty,
  ErrorNote,
  ScoreBadge,
  StatCard,
  relativeTime,
  useAsync,
} from '@/components/ui'
import { DashboardBuckets } from '@/components/DashboardBuckets'
import { api } from '@/lib/api'
import { MATCH_DECISION_LABEL } from '@/lib/labels'
import type { Dashboard } from '@/lib/types'

export default function DashboardPage() {
  const [hours, setHours] = useState(24)
  const { data, error, loading, reload } = useAsync<Dashboard>(
    () => api.get<Dashboard>(`/dashboard?hours=${hours}`),
    [hours],
  )
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<string | null>(null)

  async function runDiscovery() {
    setRunning(true)
    setRunResult(null)
    try {
      const result = await api.post<{
        total_created: number
        total_updated: number
        total_duplicates: number
        blocked: { connector_key: string; error: string }[]
      }>('/discovery/run')
      const blocked = result.blocked.length
        ? ` ${result.blocked.length} source(s) blocked by policy.`
        : ''
      setRunResult(
        `${result.total_created} new, ${result.total_updated} refreshed, ` +
          `${result.total_duplicates} duplicates collapsed.${blocked}`,
      )
      reload()
    } catch (err) {
      setRunResult((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  if (loading && !data) return <p className="text-sm text-ink-muted">Loading dashboard...</p>
  if (error) return <ErrorNote message={error} />
  if (!data) return null

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Activity over the last {hours} hours. Document generation is running in{' '}
            <strong>{data.llm_mode === 'claude' ? 'Claude' : 'deterministic'}</strong> mode.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input w-auto"
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            aria-label="Time window"
          >
            <option value={24}>Last 24 hours</option>
            <option value={72}>Last 3 days</option>
            <option value={168}>Last 7 days</option>
          </select>
          <button type="button" onClick={runDiscovery} disabled={running} className="btn-secondary">
            {running ? 'Searching...' : 'Run discovery now'}
          </button>
        </div>
      </header>

      {!data.global_automation_enabled ? (
        <Banner tone="info">
          Global automation is off. The agent will discover, score and draft, but will never submit
          anything.
        </Banner>
      ) : !data.automation_enabled ? (
        <Banner tone="warn">
          Automation is paused{data.paused_reason ? `: ${data.paused_reason}` : '.'} Nothing will be
          submitted until you resume it.
        </Banner>
      ) : (
        <Banner tone="good">
          Automation is running. Auto-submit needs a score of {data.auto_submit_min_score} or above
          on an explicitly authorized platform. {data.applications_today} of{' '}
          {data.daily_application_limit} applications used today.
        </Banner>
      )}

      {runResult ? <Banner tone="info">{runResult}</Banner> : null}

      <DashboardBuckets buckets={data.buckets} emptyState={data.empty_state} />

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="New matches" value={data.new_matches} href="/jobs" />
        <StatCard
          label="Shortlisted"
          value={data.shortlisted}
          href="/jobs?min_score=60"
          tone="good"
        />
        <StatCard
          label="Awaiting review"
          value={data.awaiting_review}
          href="/reviews"
          tone={data.awaiting_review > 0 ? 'warn' : 'default'}
        />
        <StatCard
          label="Auto-submitted"
          value={data.auto_submitted}
          href="/applications?status=submitted"
        />
        <StatCard label="Rejected / skipped" value={data.rejected_or_skipped} href="/jobs" />
      </section>

      <section className="card">
        <h2 className="text-sm font-semibold text-ink">Application pipeline</h2>
        <div className="mt-3 grid grid-cols-3 gap-3 md:grid-cols-7">
          {Object.entries(data.pipeline).map(([stage, count]) => (
            <Link
              key={stage}
              href={`/applications?stage=${stage}`}
              className="rounded-md border border-slate-200 px-3 py-2 transition hover:bg-surface-raised"
            >
              <div className="text-xs capitalize text-ink-muted">{stage}</div>
              <div className="text-lg font-semibold tabular-nums">{count}</div>
            </Link>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">Today&apos;s shortlist</h2>
          <Link href="/jobs" className="text-xs text-brand hover:underline">
            See all matched jobs
          </Link>
        </div>
        {data.top_matches.length === 0 ? (
          <Empty
            title="No shortlisted jobs yet"
            hint="Add a job source under Settings, then run discovery."
          />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Score</th>
                <th>Role</th>
                <th>Where</th>
                <th>Matching</th>
                <th>Missing</th>
                <th>Posted</th>
              </tr>
            </thead>
            <tbody>
              {data.top_matches.map((m) => (
                <tr key={m.match_id}>
                  <td>
                    <ScoreBadge score={m.score} />
                  </td>
                  <td>
                    <Link
                      href={`/jobs/${m.job_id}`}
                      className="font-medium text-brand hover:underline"
                    >
                      {m.title}
                    </Link>
                    <div className="text-xs text-ink-muted">
                      {m.company}
                      {m.direct ? '' : ' (via aggregator)'}
                    </div>
                  </td>
                  <td className="text-xs text-ink-muted">
                    {m.location || 'not stated'}
                    <div>{m.connector}</div>
                  </td>
                  <td className="text-xs text-good">{m.matching_skills.join(', ') || '-'}</td>
                  <td className="text-xs text-warn">{m.missing_skills.join(', ') || '-'}</td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {relativeTime(m.posted_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section className="card">
          <h2 className="text-sm font-semibold text-ink">Why jobs were skipped</h2>
          {data.rejection_reasons.length === 0 ? (
            <p className="mt-2 text-sm text-ink-muted">Nothing skipped in this window.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.rejection_reasons.map((r) => (
                <li key={r.decision} className="flex items-center justify-between text-sm">
                  <span className="text-ink-soft">{MATCH_DECISION_LABEL[r.decision] ?? r.decision}</span>
                  <span className="font-mono tabular-nums text-ink-muted">{r.count}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Recent activity</h2>
            <Link href="/audit" className="text-xs text-brand hover:underline">
              Full audit log
            </Link>
          </div>
          {data.recent_activity.length === 0 ? (
            <p className="text-sm text-ink-muted">No activity yet.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {data.recent_activity.slice(0, 12).map((entry) => (
                <li key={entry.seq} className="flex justify-between gap-3">
                  <span className="font-mono text-ink-soft">{entry.action}</span>
                  <span className="whitespace-nowrap text-ink-muted">{relativeTime(entry.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
