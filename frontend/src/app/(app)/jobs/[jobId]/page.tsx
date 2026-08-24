'use client'

import Link from 'next/link'
import { useState } from 'react'

import {
  Banner,
  ErrorNote,
  PolicyBadge,
  ScoreBadge,
  relativeTime,
  salaryText,
  useAsync,
} from '@/components/ui'
import { api } from '@/lib/api'
import type { JobDetail, Match, MatchWithJob, Page } from '@/lib/types'

export default function JobDetailPage({ params }: { params: { jobId: string } }) {
  const { data: job, error } = useAsync<JobDetail>(
    () => api.get<JobDetail>(`/jobs/${params.jobId}`),
    [params.jobId],
  )
  const { data: matches } = useAsync<Page<MatchWithJob>>(
    () => api.get<Page<MatchWithJob>>('/jobs?limit=200'),
    [params.jobId],
  )
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const match: Match | undefined = matches?.items.find((m) => m.job.id === params.jobId)?.match

  async function draft() {
    setBusy(true)
    try {
      const result = await api.post<{ policy: { may_submit: boolean; rationale: string[] } }>(
        '/applications/draft',
        { job_id: params.jobId, include_cover_letter: true },
      )
      setNote(
        result.policy.may_submit
          ? 'Queued for automatic submission.'
          : `Sent to review. ${result.policy.rationale.join(' ')}`,
      )
    } catch (err) {
      setNote((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return <ErrorNote message={error} />
  if (!job) return <p className="text-sm text-ink-muted">Loading...</p>

  return (
    <div className="space-y-5">
      <Link href="/jobs" className="text-xs text-brand hover:underline">
        Back to matched jobs
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">{job.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {job.company} - {job.location_raw || 'location not stated'} - {job.work_arrangement}
          </p>
          <p className="mt-1 text-xs text-ink-muted">
            {job.connector_key} - posted {relativeTime(job.posted_at ?? job.first_seen_at)} -{' '}
            {job.is_direct_employer ? 'direct employer' : 'via aggregator'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {match ? <ScoreBadge score={match.score} /> : null}
          <PolicyBadge policy={job.submission_policy_default} />
          <a
            href={job.apply_url || job.source_url}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary"
          >
            Open posting
          </a>
          <button type="button" className="btn-primary" onClick={draft} disabled={busy}>
            {busy ? 'Drafting...' : 'Draft application'}
          </button>
        </div>
      </header>

      {note ? <Banner tone="info">{note}</Banner> : null}

      <div className="grid gap-5 md:grid-cols-3">
        <section className="card md:col-span-2">
          <h2 className="text-sm font-semibold text-ink">Description</h2>
          <div className="prose-plain mt-3 max-h-[32rem] overflow-y-auto">
            {job.description_text || 'No description captured.'}
          </div>
        </section>

        <div className="space-y-5">
          <section className="card">
            <h2 className="text-sm font-semibold text-ink">Facts</h2>
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-ink-muted">Salary</dt>
                <dd className="text-right">{salaryText(job)}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-muted">Seniority</dt>
                <dd className="capitalize">{job.seniority}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-muted">Employment</dt>
                <dd className="capitalize">{job.employment_type.replace('_', ' ')}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-muted">Sponsorship</dt>
                <dd>
                  {job.visa_sponsorship_mentioned === true
                    ? 'Available'
                    : job.visa_sponsorship_mentioned === false
                      ? 'Not offered'
                      : 'Not stated'}
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-ink-muted">Deadline</dt>
                <dd>{job.deadline_at ? new Date(job.deadline_at).toLocaleDateString() : 'none'}</dd>
              </div>
            </dl>
          </section>

          {match ? (
            <section className="card">
              <h2 className="text-sm font-semibold text-ink">Why this score</h2>
              <div className="prose-plain mt-3">{match.explanation}</div>
            </section>
          ) : null}

          <section className="card">
            <h2 className="text-sm font-semibold text-ink">Skills in the posting</h2>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {job.extracted_skills.length === 0 ? (
                <span className="text-xs text-ink-muted">None recognised.</span>
              ) : (
                job.extracted_skills.map((skill) => (
                  <span
                    key={skill}
                    className={`chip ${
                      match?.matching_skills.includes(skill)
                        ? 'bg-good-soft text-good'
                        : 'bg-slate-100 text-ink-muted'
                    }`}
                  >
                    {skill}
                  </span>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
