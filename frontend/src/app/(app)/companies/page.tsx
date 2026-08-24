'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Empty, ErrorNote, ScoreBadge, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Company, Page } from '@/lib/types'

const SORTS = [
  { value: 'jobs', label: 'Most jobs' },
  { value: 'recent', label: 'Most recently posted' },
  { value: 'score', label: 'Best match' },
  { value: 'name', label: 'Name (A-Z)' },
]

const ARRANGEMENT_TONE: Record<string, string> = {
  remote: 'bg-good-soft text-good',
  hybrid: 'bg-brand-soft text-brand',
  onsite: 'bg-slate-100 text-ink-muted',
}

export default function CompaniesPage() {
  const [q, setQ] = useState('')
  const [country, setCountry] = useState('')
  const [postedWithin, setPostedWithin] = useState('')
  const [sort, setSort] = useState('jobs')
  const [scoredOnly, setScoredOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const limit = 50

  const query = new URLSearchParams({ sort, limit: String(limit), offset: String(offset) })
  if (q) query.set('q', q)
  if (country) query.set('country', country.toUpperCase())
  if (postedWithin) query.set('posted_within_hours', postedWithin)
  if (scoredOnly) query.set('scored_only', 'true')

  const { data, error, loading } = useAsync<Page<Company>>(
    () => api.get<Page<Company>>(`/companies?${query.toString()}`),
    [q, country, postedWithin, sort, scoredOnly, offset],
  )

  function change<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value)
      setOffset(0)
    }
  }

  const totalJobs = (data?.items ?? []).reduce((sum, c) => sum + c.job_count, 0)

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold text-ink">Companies</h1>
        <p className="mt-1 text-sm text-ink-muted">
          Every employer that has posted a job the agent ingested. A role found on more than one
          board is counted once, under the employer&apos;s own listing.
        </p>
      </header>

      <div className="card-tight grid gap-3 md:grid-cols-5">
        <input
          className="input md:col-span-2"
          placeholder="Search company name"
          value={q}
          onChange={(e) => change(setQ)(e.target.value)}
        />
        <input
          className="input"
          placeholder="Country code, e.g. US"
          maxLength={2}
          value={country}
          onChange={(e) => change(setCountry)(e.target.value)}
        />
        <select
          className="input"
          value={postedWithin}
          onChange={(e) => change(setPostedWithin)(e.target.value)}
          aria-label="Posted within"
        >
          <option value="">Any age</option>
          <option value="24">Posted in 24h</option>
          <option value="48">Posted in 48h</option>
          <option value="72">Posted in 72h</option>
          <option value="168">Posted in a week</option>
        </select>
        <select
          className="input"
          value={sort}
          onChange={(e) => change(setSort)(e.target.value)}
          aria-label="Sort"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-sm text-ink-soft md:col-span-2">
          <input
            type="checkbox"
            checked={scoredOnly}
            onChange={(e) => change(setScoredOnly)(e.target.checked)}
          />
          Only companies with a job scored against my profile
        </label>
      </div>

      <ErrorNote message={error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading companies...</p> : null}

      {data && data.items.length === 0 ? (
        <Empty
          title="No companies yet"
          hint="Add a job source under Settings and run discovery, or add a job by hand."
        />
      ) : null}

      {data && data.items.length > 0 ? (
        <>
          <p className="text-xs text-ink-muted">
            {data.total} {data.total === 1 ? 'company' : 'companies'}, {totalJobs} job
            {totalJobs === 1 ? '' : 's'} on this page.
          </p>

          <div className="card overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Jobs</th>
                  <th>Where</th>
                  <th>Arrangement</th>
                  <th>Source</th>
                  <th>Best match</th>
                  <th>Latest posting</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((company) => (
                  <tr key={company.company_normalized}>
                    <td className="max-w-xs">
                      <Link
                        href={`/jobs?q=${encodeURIComponent(company.company)}`}
                        className="font-medium text-brand hover:underline"
                      >
                        {company.company}
                      </Link>
                      {company.applied_count > 0 ? (
                        <div className="mt-1">
                          <span className="chip bg-good-soft text-good">
                            {company.applied_count} application
                            {company.applied_count === 1 ? '' : 's'}
                          </span>
                        </div>
                      ) : null}
                    </td>
                    <td className="tabular-nums">
                      <span className="font-medium">{company.job_count}</span>
                      {company.open_job_count !== company.job_count ? (
                        <div className="text-xs text-ink-muted">
                          {company.open_job_count} open
                        </div>
                      ) : null}
                    </td>
                    <td className="text-xs text-ink-muted">
                      {company.countries.length > 0 ? company.countries.join(', ') : 'not stated'}
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {company.work_arrangements.length === 0 ? (
                          <span className="text-xs text-ink-muted">-</span>
                        ) : (
                          company.work_arrangements.map((arrangement) => (
                            <span
                              key={arrangement}
                              className={`chip ${
                                ARRANGEMENT_TONE[arrangement] ?? 'bg-slate-100 text-ink-muted'
                              }`}
                            >
                              {arrangement}
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="text-xs text-ink-muted">
                      {company.connectors.join(', ')}
                      <div>
                        {company.direct_employer ? 'direct employer' : 'via aggregator'}
                      </div>
                    </td>
                    <td>
                      {company.best_score !== null ? (
                        <ScoreBadge score={company.best_score} />
                      ) : (
                        <span className="text-xs text-ink-muted">not scored</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap text-xs text-ink-muted">
                      {relativeTime(company.latest_posted_at ?? company.first_seen_at)}
                    </td>
                    <td className="whitespace-nowrap">
                      <Link
                        href={`/jobs?q=${encodeURIComponent(company.company)}`}
                        className="btn-secondary px-2 py-1 text-xs"
                      >
                        View jobs
                      </Link>
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
        </>
      ) : null}
    </div>
  )
}
