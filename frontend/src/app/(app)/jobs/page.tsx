'use client'

import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'

import {
  Empty,
  ErrorNote,
  PolicyBadge,
  ScoreBadge,
  relativeTime,
  salaryText,
  useAsync,
} from '@/components/ui'
import { api } from '@/lib/api'
import type { MatchWithJob, Page } from '@/lib/types'

const DECISIONS = [
  { value: '', label: 'Any outcome' },
  { value: 'shortlisted', label: 'Shortlisted' },
  { value: 'below_threshold', label: 'Below threshold' },
  { value: 'rejected_hard_filter', label: 'Failed a hard filter' },
  { value: 'excluded_company', label: 'Excluded company' },
  { value: 'excluded_keyword', label: 'Excluded keyword' },
  { value: 'stale_posting', label: 'Too old' },
]

function JobsList() {
  // Deep links from the Companies page arrive as ?q=<company>, so the search
  // box starts pre-filled rather than showing every job with the filter hidden.
  const searchParams = useSearchParams()
  const [q, setQ] = useState(searchParams.get('q') ?? '')
  const [minScore, setMinScore] = useState(searchParams.get('min_score') ?? '')
  const [decision, setDecision] = useState('')
  const [arrangement, setArrangement] = useState('')
  const [postedWithin, setPostedWithin] = useState('')
  const [directOnly, setDirectOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const limit = 25

  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (q) query.set('q', q)
  if (minScore) query.set('min_score', minScore)
  if (decision) query.set('decision', decision)
  if (arrangement) query.set('work_arrangement', arrangement)
  if (postedWithin) query.set('posted_within_hours', postedWithin)
  if (directOnly) query.set('direct_only', 'true')

  const { data, error, loading, reload } = useAsync<Page<MatchWithJob>>(
    () => api.get<Page<MatchWithJob>>(`/jobs?${query.toString()}`),
    [q, minScore, decision, arrangement, postedWithin, directOnly, offset],
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function draft(jobId: string) {
    setBusy(jobId)
    setNote(null)
    try {
      const result = await api.post<{
        application: { id: string; status: string }
        policy: { may_submit: boolean; review_reasons: string[]; rationale: string[] }
        review_task_id: string | null
      }>('/applications/draft', { job_id: jobId, include_cover_letter: true })
      setNote(
        result.policy.may_submit
          ? 'Queued for automatic submission: every gate passed.'
          : `Sent to review. ${result.policy.rationale.join(' ')}`,
      )
      reload()
    } catch (err) {
      setNote((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  async function dismiss(matchId: string) {
    await api.post(`/matches/${matchId}/dismiss`)
    reload()
  }

  function onFilterChange<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value)
      setOffset(0)
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-ink">Matched jobs</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Every job the agent scored, with the reasoning behind the number.
        </p>
      </header>

      <div className="card-tight grid gap-3 md:grid-cols-6">
        <input
          className="input md:col-span-2"
          placeholder="Search title or company"
          value={q}
          onChange={(e) => onFilterChange(setQ)(e.target.value)}
        />
        <select
          className="input"
          value={decision}
          onChange={(e) => onFilterChange(setDecision)(e.target.value)}
          aria-label="Outcome"
        >
          {DECISIONS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
        <select
          className="input"
          value={minScore}
          onChange={(e) => onFilterChange(setMinScore)(e.target.value)}
          aria-label="Minimum score"
        >
          <option value="">Any score</option>
          <option value="60">60+</option>
          <option value="75">75+</option>
          <option value="85">85+</option>
        </select>
        <select
          className="input"
          value={arrangement}
          onChange={(e) => onFilterChange(setArrangement)(e.target.value)}
          aria-label="Work arrangement"
        >
          <option value="">Any arrangement</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">On-site</option>
        </select>
        <select
          className="input"
          value={postedWithin}
          onChange={(e) => onFilterChange(setPostedWithin)(e.target.value)}
          aria-label="Posted within"
        >
          <option value="">Any age</option>
          <option value="24">Last 24h</option>
          <option value="48">Last 48h</option>
          <option value="72">Last 72h</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-ink-soft md:col-span-2">
          <input
            type="checkbox"
            checked={directOnly}
            onChange={(e) => onFilterChange(setDirectOnly)(e.target.checked)}
          />
          Direct employer applications only
        </label>
      </div>

      {note ? (
        <div className="rounded-md border border-brand/30 bg-brand-soft px-3 py-2 text-sm text-brand">
          {note}
        </div>
      ) : null}
      <ErrorNote message={error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading...</p> : null}
      {data && data.items.length === 0 ? (
        <Empty title="No jobs match these filters" hint="Try widening the score or age filter." />
      ) : null}

      {data && data.items.length > 0 ? (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Score</th>
                <th>Role</th>
                <th>Location</th>
                <th>Salary</th>
                <th>Source</th>
                <th>Posted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map(({ match, job }) => (
                <tr key={match.id}>
                  <td>
                    <ScoreBadge score={match.score} />
                  </td>
                  <td className="max-w-sm">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="font-medium text-brand hover:underline"
                    >
                      {job.title}
                    </Link>
                    <div className="text-xs text-ink-muted">{job.company}</div>
                    {match.hard_filter_failures.length > 0 ? (
                      <div className="mt-1 text-xs text-bad">{match.hard_filter_failures[0]}</div>
                    ) : (
                      <div className="mt-1 text-xs text-ink-muted">
                        {match.matching_skills.slice(0, 5).join(', ')}
                      </div>
                    )}
                  </td>
                  <td className="text-xs text-ink-muted">
                    {job.location_raw || 'not stated'}
                    <div className="capitalize">{job.work_arrangement}</div>
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">{salaryText(job)}</td>
                  <td className="text-xs">
                    <div className="text-ink-muted">{job.connector_key}</div>
                    <PolicyBadge policy={job.submission_policy_default} />
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {relativeTime(job.posted_at ?? job.first_seen_at)}
                  </td>
                  <td className="whitespace-nowrap">
                    <div className="flex gap-1">
                      <button
                        type="button"
                        className="btn-secondary px-2 py-1 text-xs"
                        disabled={busy === job.id}
                        onClick={() => draft(job.id)}
                      >
                        {busy === job.id ? '...' : 'Draft'}
                      </button>
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs"
                        onClick={() => dismiss(match.id)}
                      >
                        Dismiss
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="mt-3 flex items-center justify-between text-xs text-ink-muted">
            <span>
              {offset + 1}-{Math.min(offset + limit, data.total)} of {data.total}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary px-2 py-1 text-xs"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn-secondary px-2 py-1 text-xs"
                disabled={offset + limit >= data.total}
                onClick={() => setOffset(offset + limit)}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function JobsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-muted">Loading...</p>}>
      <JobsList />
    </Suspense>
  )
}
