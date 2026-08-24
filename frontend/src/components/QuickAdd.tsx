'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Banner, ErrorNote, ScoreBadge } from '@/components/ui'
import { api } from '@/lib/api'
import type { QuickAddResult } from '@/lib/types'

/**
 * "Paste a job from anywhere" - the honest way to apply to LinkedIn, Indeed and
 * any site that forbids automated fetching. The user pastes text they are
 * already looking at; nothing is scraped. The agent extracts skills/salary/etc,
 * scores it, and drafts a full application.
 */
export function QuickAdd({ onAdded }: { onAdded?: () => void }) {
  const [open, setOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [company, setCompany] = useState('')
  const [title, setTitle] = useState('')
  const [location, setLocation] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<QuickAddResult | null>(null)

  const ready = company.trim() && title.trim() && description.trim().length > 20

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const res = await api.post<QuickAddResult>('/jobs/quick-add', {
        url: url.trim() || 'https://example.com',
        company: company.trim(),
        title: title.trim(),
        location_raw: location.trim(),
        description_text: description.trim(),
        draft: true,
      })
      setResult(res)
      setUrl('')
      setCompany('')
      setTitle('')
      setLocation('')
      setDescription('')
      onAdded?.()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card border-brand/30 bg-brand-soft/40">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Apply to a job from any site</h2>
          <p className="mt-1 max-w-2xl text-xs text-ink-muted">
            Found something on LinkedIn, Indeed or anywhere else? Paste the link and the job text
            here. The agent reads the skills, salary and seniority, scores it against your verified
            profile, and drafts a tailored resume, cover letter and answers - ready for you to
            submit. Nothing is scraped: you paste what you are already looking at.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setOpen((v) => !v)}>
          {open ? 'Close' : 'Paste a job'}
        </button>
      </div>

      {result ? (
        <Banner tone={result.application_id ? 'good' : 'info'}>
          <div className="flex flex-wrap items-center gap-2">
            {result.score !== null ? <ScoreBadge score={result.score} /> : null}
            <span className="font-medium">
              {result.title} at {result.company}
            </span>
          </div>
          <p className="mt-1">{result.message}</p>
          {result.matching_skills.length > 0 ? (
            <p className="mt-1 text-xs">Matched skills: {result.matching_skills.join(', ')}</p>
          ) : null}
          <div className="mt-2 flex flex-wrap gap-3 text-xs">
            {result.application_id ? (
              <Link
                href={`/applications/${result.application_id}`}
                className="font-medium text-brand hover:underline"
              >
                Open the drafted application
              </Link>
            ) : null}
            {result.review_task_id ? (
              <Link href="/reviews" className="font-medium text-brand hover:underline">
                Go to the review queue
              </Link>
            ) : null}
            <Link
              href={`/jobs/${result.job_id}`}
              className="font-medium text-brand hover:underline"
            >
              View the job and its score
            </Link>
          </div>
        </Banner>
      ) : null}

      {open ? (
        <form onSubmit={submit} className="mt-4 grid gap-3 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="qa_company">
              Company *
            </label>
            <input
              id="qa_company"
              className="input"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Stripe"
            />
          </div>
          <div>
            <label className="label" htmlFor="qa_title">
              Job title *
            </label>
            <input
              id="qa_title"
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Senior Backend Engineer"
            />
          </div>
          <div>
            <label className="label" htmlFor="qa_url">
              Job link
            </label>
            <input
              id="qa_url"
              className="input"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.linkedin.com/jobs/view/..."
            />
          </div>
          <div>
            <label className="label" htmlFor="qa_location">
              Location
            </label>
            <input
              id="qa_location"
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="e.g. Remote - US, or Bengaluru, India"
            />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="qa_desc">
              Job description * (paste the whole posting)
            </label>
            <textarea
              id="qa_desc"
              className="input h-40"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Copy the full job description from the page and paste it here. The agent reads skills, salary, seniority, work arrangement and visa sponsorship straight out of this text."
            />
          </div>
          <div className="md:col-span-2">
            <ErrorNote message={error} />
            <button type="submit" className="btn-primary mt-2" disabled={busy || !ready}>
              {busy ? 'Reading, scoring and drafting...' : 'Add and draft my application'}
            </button>
            {!ready ? (
              <p className="mt-1 text-xs text-ink-muted">
                Company, title and a pasted description are required.
              </p>
            ) : null}
          </div>
        </form>
      ) : null}
    </section>
  )
}
