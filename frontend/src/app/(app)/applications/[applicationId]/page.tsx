'use client'

import Link from 'next/link'
import { useState } from 'react'

import {
  Banner,
  ErrorNote,
  PolicyBadge,
  StatusBadge,
  relativeTime,
  useAsync,
} from '@/components/ui'
import { api } from '@/lib/api'
import type { ApplicationDetail, SubmissionAttempt } from '@/lib/types'

const PIPELINE = ['saved', 'applied', 'screening', 'interview', 'offer', 'rejected', 'closed']

export default function ApplicationDetailPage({
  params,
}: {
  params: { applicationId: string }
}) {
  const { data, error, reload } = useAsync<ApplicationDetail>(
    () => api.get<ApplicationDetail>(`/applications/${params.applicationId}`),
    [params.applicationId],
  )
  const { data: attempts, reload: reloadAttempts } = useAsync<SubmissionAttempt[]>(
    () => api.get<SubmissionAttempt[]>(`/applications/${params.applicationId}/attempts`),
    [params.applicationId],
  )

  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [note, setNote] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function saveAnswers() {
    const payload = Object.entries(drafts)
      .filter(([, value]) => value.trim() !== '')
      .map(([answer_id, answer_value]) => ({ answer_id, answer_value }))
    if (payload.length === 0) return
    setBusy(true)
    setActionError(null)
    try {
      await api.patch(`/applications/${params.applicationId}/answers`, payload)
      setDrafts({})
      setNote(`Saved ${payload.length} answer(s).`)
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function act(action: 'approve' | 'reject') {
    setBusy(true)
    setNote(null)
    setActionError(null)
    try {
      await api.post(`/applications/${params.applicationId}/${action}`, { note: '' })
      setNote(action === 'approve' ? 'Approved for submission.' : 'Rejected.')
      reload()
      reloadAttempts()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function setStage(stage: string) {
    setActionError(null)
    try {
      await api.post(`/applications/${params.applicationId}/stage`, {
        pipeline_stage: stage,
        note: '',
      })
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="text-sm text-ink-muted">Loading...</p>

  const blocking = data.answers.filter((a) => a.needs_human)
  const requiredBlocking = blocking.filter((a) => a.required)
  const guardBlocks = data.fact_guard_flags.filter((f) => f.severity === 'block')
  const guardWarnings = data.fact_guard_flags.filter((f) => f.severity !== 'block')

  return (
    <div className="space-y-5">
      <Link href="/applications" className="text-xs text-brand hover:underline">
        Back to applications
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">{data.job.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">
            {data.job.company} - {data.job.location_raw || 'location not stated'}
          </p>
          <p className="mt-0.5 text-xs text-ink-muted">
            Version {data.version} - {data.attempt_count} attempt(s) - updated{' '}
            {relativeTime(data.updated_at)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={data.status} />
          <PolicyBadge policy={data.submission_policy} />
          <select
            className="input w-auto"
            value={data.pipeline_stage}
            onChange={(e) => setStage(e.target.value)}
            aria-label="Pipeline stage"
          >
            {PIPELINE.map((stage) => (
              <option key={stage} value={stage}>
                {stage}
              </option>
            ))}
          </select>
        </div>
      </header>

      {note ? <Banner tone="good">{note}</Banner> : null}
      <ErrorNote message={actionError} />

      {guardBlocks.length > 0 ? (
        <Banner tone="bad">
          <p className="font-medium">
            The fact guard blocked this draft, so it will not be auto-submitted. Each item below
            is a claim that could not be traced back to a verified career fact.
          </p>
          <ul className="mt-2 space-y-1">
            {guardBlocks.map((flag, index) => (
              <li key={index}>
                <code className="rounded bg-white/50 px-1">{flag.span}</code> - {flag.reason}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs">
            Fix this by verifying the missing fact under Profile, or by editing the document so
            it only says what you have confirmed.
          </p>
        </Banner>
      ) : null}

      {guardWarnings.length > 0 ? (
        <Banner tone="warn">
          <p className="font-medium">Fact guard warnings (non-blocking):</p>
          <ul className="mt-1.5 space-y-1">
            {guardWarnings.map((flag, index) => (
              <li key={index}>
                <code>{flag.span}</code> - {flag.reason}
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}

      {data.validation_errors.length > 0 ? (
        <Banner tone="warn">
          <p className="font-medium">Pre-flight validation found problems:</p>
          <ul className="mt-1.5 space-y-1">
            {data.validation_errors.map((message) => (
              <li key={message}>- {message}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <section className="card">
        <h2 className="text-sm font-semibold text-ink">Application summary</h2>
        <div className="prose-plain mt-2">{data.summary}</div>
      </section>

      <section className="card">
        <h2 className="text-sm font-semibold text-ink">Generated documents</h2>
        {data.documents.length === 0 ? (
          <p className="mt-2 text-sm text-ink-muted">No documents attached.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100">
            {data.documents.map((doc) => (
              <li key={doc.id} className="flex items-center justify-between py-2">
                <span className="text-sm capitalize text-ink-soft">
                  {doc.role.replace(/_/g, ' ')}
                  {doc.attached ? '' : ' (not attached)'}
                </span>
                <a
                  href={`/api/proxy/documents/${doc.document_id}/content`}
                  className="text-xs text-brand hover:underline"
                  target="_blank"
                  rel="noreferrer"
                >
                  Download
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink">Screening answers</h2>
          {blocking.length > 0 ? (
            <span className="chip bg-warn-soft text-warn">
              {blocking.length} need you ({requiredBlocking.length} required)
            </span>
          ) : (
            <span className="chip bg-good-soft text-good">
              Every answer traces to a verified fact
            </span>
          )}
        </div>

        <p className="mt-2 text-xs text-ink-muted">
          The agent only answers a question when the answer can be read straight off an explicit
          profile field or a verified career fact. Anything else is left for you.
        </p>

        <div className="mt-3 overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Question</th>
                <th>Answer</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {data.answers.map((answer) => (
                <tr key={answer.id}>
                  <td className="max-w-sm">
                    {answer.question_text}
                    {answer.required ? <span className="text-bad"> *</span> : null}
                    <div className="text-xs text-ink-muted">{answer.question_type}</div>
                  </td>
                  <td className="max-w-sm">
                    {answer.needs_human ? (
                      <div>
                        {answer.options.length > 0 ? (
                          <select
                            className="input"
                            value={drafts[answer.id] ?? ''}
                            onChange={(e) =>
                              setDrafts({ ...drafts, [answer.id]: e.target.value })
                            }
                          >
                            <option value="">Choose an answer</option>
                            {answer.options.map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            className="input"
                            placeholder="Your answer"
                            value={drafts[answer.id] ?? ''}
                            onChange={(e) =>
                              setDrafts({ ...drafts, [answer.id]: e.target.value })
                            }
                          />
                        )}
                        <p className="mt-1 text-xs text-warn">{answer.reason}</p>
                      </div>
                    ) : (
                      <span className="text-ink-soft">{answer.answer_value}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {answer.needs_human
                      ? 'not answered'
                      : answer.source_fact_id
                        ? 'verified fact'
                        : `profile field (${answer.confidence}%)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {blocking.length > 0 ? (
          <button type="button" className="btn-primary mt-3" onClick={saveAnswers} disabled={busy}>
            {busy ? 'Saving...' : 'Save my answers'}
          </button>
        ) : null}
      </section>

      <section className="card">
        <h2 className="text-sm font-semibold text-ink">Attempt history</h2>
        <p className="mt-1 text-xs text-ink-muted">
          An aborted attempt means the browser assistant hit a hard stop - a CAPTCHA, a login
          wall, bot protection, or a question it could not answer truthfully. It stops there and
          never retries on its own; the work moves to your review queue instead.
        </p>
        {!attempts || attempts.length === 0 ? (
          <p className="mt-3 text-sm text-ink-muted">No submission attempts yet.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Mode</th>
                  <th>Outcome</th>
                  <th>Started</th>
                  <th>Finished</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {attempts.map((attempt) => (
                  <tr key={attempt.id}>
                    <td className="tabular-nums">{attempt.attempt_number}</td>
                    <td className="text-xs capitalize">{attempt.mode.replace(/_/g, ' ')}</td>
                    <td>
                      <StatusBadge status={attempt.outcome} />
                    </td>
                    <td className="whitespace-nowrap text-xs text-ink-muted">
                      {relativeTime(attempt.started_at)}
                    </td>
                    <td className="whitespace-nowrap text-xs text-ink-muted">
                      {relativeTime(attempt.finished_at)}
                    </td>
                    <td className="max-w-md">
                      {attempt.error_message ? (
                        <p className="text-xs text-bad">{attempt.error_message}</p>
                      ) : null}
                      {attempt.guard_findings.length > 0 ? (
                        <pre className="prose-plain mt-1 max-h-32 overflow-y-auto">
                          {JSON.stringify(attempt.guard_findings, null, 2)}
                        </pre>
                      ) : null}
                      {attempt.assistant_version ? (
                        <p className="mt-1 text-xs text-ink-muted">
                          assistant {attempt.assistant_version}
                        </p>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {data.confirmation_number ? (
        <Banner tone="good">
          Submitted{data.submitted_at ? ` ${relativeTime(data.submitted_at)}` : ''}. Confirmation
          number: <code>{data.confirmation_number}</code>
        </Banner>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={() => act('approve')}
          disabled={busy || data.status === 'submitted'}
          title={
            requiredBlocking.length > 0
              ? 'Answer the required questions first'
              : 'Approve this application for submission'
          }
        >
          Approve for submission
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => act('reject')}
          disabled={busy || data.status === 'submitted'}
        >
          Reject
        </button>
        <a
          href={data.job.apply_url || data.job.source_url}
          target="_blank"
          rel="noreferrer"
          className="btn-secondary"
        >
          Open the posting and apply yourself
        </a>
      </div>
    </div>
  )
}
