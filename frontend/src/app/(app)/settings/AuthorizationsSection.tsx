'use client'

import Link from 'next/link'
import { useState } from 'react'

import { Banner, ErrorNote, PolicyBadge, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Authorization, Connector, SubmissionPolicy } from '@/lib/types'

interface Acknowledgement {
  acknowledgement: string
  note: string
  never_automatable: string[]
}

/** "Greenhouse (job boards)" -> "Greenhouse". */
function shortName(connector: Connector): string {
  return connector.display_name.split(' (')[0] ?? connector.display_name
}

const sentenceList = (names: string[]) =>
  new Intl.ListFormat('en', { style: 'long', type: 'conjunction' }).format(names)

export function AuthorizationsSection({ connectors }: { connectors: Connector[] }) {
  const { data: grants, error, reload } = useAsync<Authorization[]>(
    () => api.get<Authorization[]>('/settings/authorizations'),
  )
  const { data: ack } = useAsync<Acknowledgement>(
    () => api.get<Acknowledgement>('/settings/authorizations/acknowledgement'),
  )

  const [openKey, setOpenKey] = useState<string | null>(null)
  const [policy, setPolicy] = useState<SubmissionPolicy>('assisted_autofill')
  const [typed, setTyped] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const grantFor = (key: string) => grants?.find((g) => g.platform_key === key)
  const phrase = ack?.acknowledgement ?? ''
  const matches = phrase.length > 0 && typed.trim() === phrase

  // Derived from the API, never hardcoded: the backend registry is the only
  // place that decides which platforms have a supported browser workflow.
  const supportedNames = connectors
    .filter((c) => c.automation_permitted_for_submission)
    .map((c) => shortName(c))

  function openForm(key: string) {
    setOpenKey(openKey === key ? null : key)
    setPolicy('assisted_autofill')
    setTyped('')
    setNotes('')
    setActionError(null)
  }

  async function grant(platformKey: string) {
    setBusy(true)
    setActionError(null)
    try {
      await api.post('/settings/authorizations', {
        platform_key: platformKey,
        policy,
        acknowledgement: typed.trim(),
        notes,
      })
      setNote(`Authorized ${platformKey} for ${policy.replace(/_/g, ' ')}.`)
      setOpenKey(null)
      setTyped('')
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function revoke(platformKey: string) {
    setBusy(true)
    setActionError(null)
    try {
      await api.delete(`/settings/authorizations/${platformKey}`)
      setNote(`Revoked ${platformKey}. It is back to review-only.`)
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">Platform automation authorizations</h2>
        <p className="mt-1 text-xs text-ink-muted">
          {supportedNames.length > 0
            ? `${sentenceList(supportedNames)} can use the visible local browser workflow after your
               explicit authorization. Every other source stays discovery-and-review only: it is
               still searched, ranked and drafted for you, but you submit it yourself. You can
               revoke any authorization at any time.`
            : `No platform in this build has a supported browser workflow, so every source is
               discovery-and-review only.`}
        </p>
      </div>

      {ack?.note ? <Banner tone="info">{ack.note}</Banner> : null}
      {note ? <Banner tone="good">{note}</Banner> : null}
      <ErrorNote message={actionError ?? error} />

      <div className="space-y-3">
        {connectors.map((connector) => {
          const active = grantFor(connector.key)
          // Two independent reasons automation is unavailable, and the user is
          // owed the right one. "Their terms forbid it" is permanent and is
          // about them; "no supported workflow" is about this app.
          const prohibited = connector.submission_policy_default === 'prohibited'
          const unsupported = !prohibited && !connector.browser_submission_supported
          const locked = prohibited || unsupported
          const name = shortName(connector)

          return (
            <div key={connector.key} className="rounded-md border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium text-ink">{connector.display_name}</h3>
                    <span className="chip bg-slate-100 text-ink-muted">
                      {connector.compliance_tier.replace(/_/g, ' ')}
                    </span>
                    <PolicyBadge
                      policy={active?.is_active ? active.policy : connector.submission_policy_default}
                    />
                  </div>
                  <p className="mt-1.5 max-w-3xl text-xs text-ink-muted">{connector.policy_note}</p>
                  {connector.required_credentials.length > 0 ? (
                    <p className="mt-1 text-xs text-ink-muted">
                      Requires your own credentials: {connector.required_credentials.join(', ')}
                    </p>
                  ) : null}
                </div>

                <div className="shrink-0">
                  {prohibited ? (
                    <span className="chip bg-bad-soft text-bad">Blocked by their terms</span>
                  ) : unsupported ? (
                    <span className="chip bg-slate-100 text-ink-muted">
                      Discovery and review only
                    </span>
                  ) : active?.is_active ? (
                    <button
                      type="button"
                      className="btn-danger"
                      disabled={busy}
                      onClick={() => revoke(connector.key)}
                    >
                      Revoke
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => openForm(connector.key)}
                    >
                      {openKey === connector.key ? 'Cancel' : 'Authorize automation'}
                    </button>
                  )}
                </div>
              </div>

              {prohibited ? (
                <div className="mt-2 rounded-md border border-slate-200 bg-surface-raised p-3">
                  <p className="text-xs text-ink-soft">
                    {name} forbids automated access in its terms, so this app never touches it
                    programmatically - that protects your account from being banned. You can still
                    apply to jobs you find there:
                  </p>
                  <ol className="mt-1.5 space-y-0.5 text-xs text-ink-soft">
                    <li>1. Search on {name} yourself and open a job.</li>
                    <li>2. Copy its link and the job description.</li>
                    <li>
                      3. Paste them into{' '}
                      <Link href="/jobs" className="font-medium text-brand hover:underline">
                        Apply to a job from any site
                      </Link>{' '}
                      - the agent drafts your whole application.
                    </li>
                  </ol>
                </div>
              ) : unsupported ? (
                <div className="mt-2 rounded-md border border-slate-200 bg-surface-raised p-3">
                  <p className="text-xs text-ink-soft">
                    Nothing here is off limits - this app simply has no browser workflow for {name}
                    application forms, so it will not open one. {name} jobs are still discovered,
                    ranked, and drafted for you, and each match becomes a review task with a direct
                    link:
                  </p>
                  <ol className="mt-1.5 space-y-0.5 text-xs text-ink-soft">
                    <li>
                      1. Open the match from{' '}
                      <Link href="/reviews" className="font-medium text-brand hover:underline">
                        Needs review
                      </Link>
                      .
                    </li>
                    <li>2. Copy the resume, cover letter and answers the agent prepared.</li>
                    <li>3. Submit it yourself on the employer&apos;s site.</li>
                  </ol>
                </div>
              ) : null}

              {openKey === connector.key && !locked ? (
                <div className="mt-4 space-y-3 border-t border-slate-200 pt-4">
                  <div className="grid gap-3 md:grid-cols-2">
                    <div>
                      <label className="label" htmlFor={`policy_${connector.key}`}>
                        What may the assistant do?
                      </label>
                      <select
                        id={`policy_${connector.key}`}
                        className="input"
                        value={policy}
                        onChange={(e) => setPolicy(e.target.value as SubmissionPolicy)}
                      >
                        <option value="assisted_autofill">
                          Assisted autofill - fill the form, stop at submit
                        </option>
                        <option value="auto_submit">
                          Auto-submit - fill the form and click submit
                        </option>
                      </select>
                    </div>
                    <div>
                      <label className="label" htmlFor={`notes_${connector.key}`}>
                        Notes (optional)
                      </label>
                      <input
                        id={`notes_${connector.key}`}
                        className="input"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                      />
                    </div>
                  </div>

                  <div className="rounded-md bg-surface-raised p-3 text-xs text-ink-soft">
                    <p>
                      <strong>Assisted autofill</strong> opens a visible browser on your own
                      machine, fills the form from your verified facts, and stops at the submit
                      button so you click it.
                    </p>
                    <p className="mt-1">
                      <strong>Auto-submit</strong> clicks submit as well, and only when the match
                      score meets your threshold, the daily limit has room, every answer traces
                      to a verified fact, and no CAPTCHA, login wall or bot check is present. Any
                      one of those failing sends the job to your review queue instead.
                    </p>
                  </div>

                  <div>
                    <span className="label">
                      Type this exactly to confirm you have read {connector.display_name}&apos;s
                      terms:
                    </span>
                    <code className="mt-1 block rounded bg-surface-raised px-3 py-2 text-xs text-ink">
                      {phrase || 'Loading the acknowledgement...'}
                    </code>
                    <input
                      className="input mt-2"
                      value={typed}
                      onChange={(e) => setTyped(e.target.value)}
                      placeholder="Type the sentence above"
                      aria-label="Acknowledgement"
                    />
                  </div>

                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busy || !matches}
                    onClick={() => grant(connector.key)}
                  >
                    {busy ? 'Authorizing...' : `Authorize ${connector.display_name}`}
                  </button>
                </div>
              ) : null}

              {active?.is_active && active.granted_at ? (
                <p className="mt-2 text-xs text-ink-muted">
                  Granted {new Date(active.granted_at).toLocaleString()}
                  {active.notes ? ` - ${active.notes}` : ''}
                </p>
              ) : null}
            </div>
          )
        })}
      </div>
    </section>
  )
}
