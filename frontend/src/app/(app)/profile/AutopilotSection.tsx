'use client'

import { useState } from 'react'

import { Banner, ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { AgentSettings, AutopilotRun, AutopilotStatus } from '@/lib/types'

export function AutopilotSection() {
  // Settings are fetched alongside status only for auto_submit_min_score, so the
  // "everything is set" copy can state the real threshold instead of a vague one.
  const { data, error, loading, reload } = useAsync(() =>
    Promise.all([api.get<AutopilotStatus>('/autopilot/status'), api.get<AgentSettings>('/settings')]),
  )
  const [busy, setBusy] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [runResult, setRunResult] = useState<AutopilotRun | null>(null)

  const status = data ? data[0] : null
  const settings = data ? data[1] : null

  async function runPipeline() {
    setBusy(true)
    setRunError(null)
    setRunResult(null)
    try {
      setRunResult(await api.post<AutopilotRun>('/autopilot/run', { include_drafting: true }))
      reload()
    } catch (err) {
      setRunError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const running = status ? status.automation_enabled && status.global_automation_enabled : false

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Hands-off from here</h2>
          <p className="mt-1 text-xs text-ink-muted">
            Facts need one confirmation pass per upload because the agent never attests facts
            about you; after that the loop is automatic on your authorized platforms and
            everything else lands in the review queue with a direct link.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={runPipeline} disabled={busy || loading}>
          {busy ? 'Running...' : 'Run the whole pipeline now'}
        </button>
      </div>

      <ErrorNote message={runError ?? error} />

      {runResult ? (
        <>
          <Banner tone="good">
            Discovery: {runResult.discovery.created} new, {runResult.discovery.updated} refreshed,{' '}
            {runResult.discovery.duplicates} duplicates. Scoring: {runResult.scoring.scored}{' '}
            scored, {runResult.scoring.shortlisted} shortlisted. Drafting:{' '}
            {runResult.drafting.drafted} drafted - {runResult.drafting.queued_for_auto_submit}{' '}
            queued for auto-submit, {runResult.drafting.sent_to_review} sent to review.
          </Banner>
          {runResult.discovery.blocked.length > 0 ? (
            <Banner tone="warn">
              <ul className="space-y-1">
                {runResult.discovery.blocked.map((b, index) => (
                  <li key={index}>
                    <strong>{b.connector_key}</strong> ({b.identifier}) was blocked: {b.error}
                  </li>
                ))}
              </ul>
            </Banner>
          ) : null}
        </>
      ) : null}

      {status ? (
        <ul className="space-y-2">
          <GateRow done={status.resume_uploaded} label="Resume uploaded" />
          <GateRow done={status.verified_fact_count > 0} label="Facts verified">
            {status.verified_fact_count} verified
            {status.unverified_fact_count > 0 ? (
              <span className="ml-2 text-warn">
                {status.unverified_fact_count} still unverified - unverified facts are never
                used in an application
              </span>
            ) : null}
          </GateRow>
          <GateRow done={status.enabled_source_count > 0} label="At least one source enabled">
            {status.enabled_source_count} enabled
          </GateRow>
          <GateRow done={running} label="Automation running">
            {!status.global_automation_enabled
              ? 'The server-level switch is off: set AUTOMATION_GLOBAL_ENABLED=true in the backend environment and restart it.'
              : status.automation_enabled
                ? 'On schedule'
                : 'Paused in settings'}
          </GateRow>
          <GateRow
            done={status.authorized_platforms.length > 0}
            label="Platform authorized for auto-submit"
          >
            {status.authorized_platforms.length > 0
              ? status.authorized_platforms.join(', ')
              : 'none - every drafted application goes to your review queue'}
          </GateRow>
          {status.queued_application_count > 0 ? (
            <GateRow done={false} label="Local browser assistant">
              {status.queued_application_count} application(s) queued. Start the local browser
              assistant: it performs the actual submissions on your machine.
            </GateRow>
          ) : null}
        </ul>
      ) : null}

      {status && status.next_steps.length > 0 ? (
        <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-soft">
          {status.next_steps.map((step, index) => (
            <li key={index}>{step}</li>
          ))}
        </ol>
      ) : null}

      {status && settings && status.next_steps.length === 0 ? (
        <Banner tone="good">
          Everything is set. The agent searches on schedule, drafts every shortlisted job,
          auto-submits scores of {settings.auto_submit_min_score}+ on your authorized platforms,
          and queues the rest for review. Today: {status.applications_today} of{' '}
          {status.daily_application_limit} daily applications used.
        </Banner>
      ) : null}
    </section>
  )
}

function GateRow({
  done,
  label,
  children,
}: {
  done: boolean
  label: string
  children?: React.ReactNode
}) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <span
        className={`chip mt-0.5 shrink-0 ${done ? 'bg-good-soft text-good' : 'bg-slate-100 text-ink-muted'}`}
      >
        {done ? 'done' : 'pending'}
      </span>
      <span>
        <span className="font-medium text-ink">{label}</span>
        {children ? <span className="ml-2 text-xs text-ink-muted">{children}</span> : null}
      </span>
    </li>
  )
}
