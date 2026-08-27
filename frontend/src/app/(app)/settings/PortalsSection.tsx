'use client'

import { Empty, ErrorNote, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { PortalState, PortalStatus } from '@/lib/types'

/**
 * What each portal can do and what is stopping it right now.
 *
 * The two facts kept deliberately apart everywhere else in this codebase stay
 * apart here too: "their terms forbid automated applying" (blocked) is a
 * different sentence from "we have not built an adapter for this form"
 * (discovery only). Collapsing them would tell the user the wrong thing about
 * why LinkedIn behaves as it does.
 */
const STATUS_META: Record<
  PortalStatus,
  { label: string; chip: string; blurb: string }
> = {
  ready: {
    label: 'Ready',
    chip: 'bg-good-soft text-good',
    blurb: 'Authorized and switched on. A job from this portal would be submitted.',
  },
  authorized: {
    label: 'Authorized',
    chip: 'bg-brand-soft text-brand',
    blurb: 'You granted automation, but something below still stops a submit.',
  },
  discovery_only: {
    label: 'Discovery only',
    chip: 'bg-slate-100 text-ink-muted',
    blurb: 'Jobs are found and ranked. Applying stays a review task you complete.',
  },
  blocked: {
    label: 'Never automated',
    chip: 'bg-bad-soft text-bad',
    blurb: 'Their terms forbid automated applying. This can never be enabled; the blockers below say what is still possible.',
  },
  unsupported: {
    label: 'Manual only',
    chip: 'bg-slate-100 text-ink-muted',
    blurb: 'Nothing is fetched automatically. You add jobs here yourself.',
  },
}

export function PortalsSection() {
  const { data, error, loading } = useAsync<PortalState[]>(() => api.get<PortalState[]>('/portals'))

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">Portal readiness</h2>
        <p className="mt-1 text-xs text-ink-muted">
          Every portal the agent knows about, closest to working first. Discovery and submission
          are separate capabilities: a portal can be excellent at finding jobs and still never
          submit one.
        </p>
      </div>

      <ErrorNote message={error} />
      {loading && !data ? <p className="text-sm text-ink-muted">Loading portals...</p> : null}
      {data && data.length === 0 ? <Empty title="No portals registered" /> : null}

      <div className="space-y-2">
        {data?.map((portal) => {
          const meta = STATUS_META[portal.status]
          return (
            <article key={portal.key} className="rounded-md border border-slate-200 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium text-ink">{portal.display_name}</h3>
                    <span className={`chip ${meta.chip}`}>{meta.label}</span>
                    {portal.browser_submission_supported ? (
                      <span className="chip bg-brand-soft text-brand">auto-submit capable</span>
                    ) : null}
                    {portal.granted_policy ? (
                      <span className="chip bg-slate-100 text-ink-muted">
                        granted: {portal.granted_policy.replace(/_/g, ' ')}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs text-ink-muted">{meta.blurb}</p>
                </div>

                <dl className="shrink-0 text-right text-xs text-ink-muted">
                  <div>
                    <dt className="inline">Sources: </dt>
                    <dd className="inline tabular-nums">
                      {portal.enabled_source_count} of {portal.source_count} enabled
                    </dd>
                  </div>
                  <div>
                    <dt className="inline">Jobs fetched: </dt>
                    <dd className="inline tabular-nums">{portal.jobs_seen}</dd>
                  </div>
                  <div>
                    <dt className="inline">Last run: </dt>
                    <dd className="inline">{relativeTime(portal.last_run_at)}</dd>
                  </div>
                  {portal.error_count > 0 ? (
                    <div className="text-bad">
                      {portal.error_count} source(s) errored on the last run
                    </div>
                  ) : null}
                </dl>
              </div>

              {portal.credentials_required.length > 0 ? (
                <p className="mt-2 text-xs text-ink-muted">
                  Needs your own credentials: {portal.credentials_required.join(', ')}
                  {portal.credentials_present ? ' (present)' : ' (not set)'}
                </p>
              ) : null}

              {portal.blockers.length > 0 ? (
                <>
                  {/*
                   * `status` is about SUBMITTING; some blockers are only about
                   * DISCOVERY. A portal with no source is genuinely ready to
                   * submit a job you paste in yourself, so saying it is blocked
                   * would be wrong -- but so would listing a blocker under a
                   * heading claiming nothing is wrong. Name which half applies.
                   */}
                  <p className="mt-2 text-xs font-medium text-ink-soft">
                    {portal.status === 'ready'
                      ? 'Submitting works. These only stop it finding jobs on its own:'
                      : 'What is stopping a submit right now:'}
                  </p>
                  <ul className="mt-1 space-y-0.5">
                    {portal.blockers.map((blocker) => (
                      <li key={blocker} className="text-xs text-warn">
                        - {blocker}
                      </li>
                    ))}
                  </ul>
                </>
              ) : portal.status === 'ready' ? (
                <p className="mt-2 text-xs text-good">
                  Nothing is blocking this portal. Start the local browser assistant and it will
                  work through the queue.
                </p>
              ) : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}
