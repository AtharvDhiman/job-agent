'use client'

import { useState } from 'react'

import { Banner, ErrorNote, PolicyBadge, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Authorization, Connector, SubmissionPolicy } from '@/lib/types'

interface Acknowledgement {
  acknowledgement: string
  note: string
  never_automatable: string[]
}

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
          Every platform starts at review-only. Granting automation is a deliberate, per-platform
          act that you can revoke at any time, and it is ignored entirely while automation is
          paused.
        </p>
      </div>

      {ack?.note ? <Banner tone="info">{ack.note}</Banner> : null}
      {note ? <Banner tone="good">{note}</Banner> : null}
      <ErrorNote message={actionError ?? error} />

      <div className="space-y-3">
        {connectors.map((connector) => {
          const active = grantFor(connector.key)
          const locked = !connector.automation_permitted_for_submission

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
                  {locked ? (
                    <span className="chip bg-bad-soft text-bad">Cannot be enabled</span>
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

              {locked ? (
                <p className="mt-2 text-xs text-bad">
                  Automated applying on this platform is prohibited by its terms, so the app does
                  not offer a control for it. Matched roles from here always become review tasks
                  with a direct link that you open and submit yourself.
                </p>
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
