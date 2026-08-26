'use client'

import Link from 'next/link'

import { Banner } from '@/components/ui'
import type { DashboardBucket, DashboardEmptyState } from '@/lib/types'

/**
 * The six states a job can be in, in the order it moves through them. Reading
 * left to right is the pipeline: found -> worth applying to -> queued -> waiting
 * on you -> sent -> stopped. The last one carries the exact reason, because
 * "3 failed" without a reason is not actionable.
 */
const BUCKET_ORDER: { key: string; label: string; hint: string; tone: Tone }[] = [
  { key: 'new_jobs_found', label: 'New jobs found', hint: 'Discovered and scored in this window', tone: 'plain' },
  { key: 'high_match', label: 'High match', hint: 'At or above your auto-submit score', tone: 'good' },
  { key: 'queued_for_auto', label: 'Queued to apply', hint: 'Waiting for the browser assistant', tone: 'brand' },
  { key: 'needs_review', label: 'Needs your review', hint: 'Stopped and asking you', tone: 'warn' },
  { key: 'submitted', label: 'Submitted', hint: 'Applications actually sent', tone: 'good' },
  { key: 'failed_or_stopped', label: 'Failed / stopped', hint: 'With the exact reason', tone: 'bad' },
]

type Tone = 'plain' | 'good' | 'brand' | 'warn' | 'bad'

const TONE_CLASS: Record<Tone, string> = {
  plain: 'text-ink',
  good: 'text-good',
  brand: 'text-brand',
  warn: 'text-warn',
  bad: 'text-bad',
}

export function DashboardBuckets({
  buckets,
  emptyState,
}: {
  buckets: Record<string, DashboardBucket>
  emptyState: DashboardEmptyState
}) {
  const failed = buckets.failed_or_stopped
  const failureReasons = failed?.failure_reasons ?? []

  if (emptyState.is_empty) {
    // An empty database is a setup state, not a result. Showing six zeros here
    // would read as "the agent found nothing", which is a different and much
    // more discouraging claim than "the agent has not been given anywhere to look".
    return (
      <Banner tone="info">
        <p className="font-medium">{emptyState.message}</p>
        <p className="mt-1">{emptyState.next_step}</p>
        <ul className="mt-2 space-y-1 text-xs">
          <li>{emptyState.has_resume ? '✓' : '○'} Resume uploaded</li>
          <li>{emptyState.has_verified_facts ? '✓' : '○'} Career facts verified</li>
          <li>{emptyState.has_sources ? '✓' : '○'} At least one job source added</li>
        </ul>
        <div className="mt-3 flex flex-wrap gap-3 text-xs">
          <Link href="/settings" className="font-medium text-brand hover:underline">
            Add a job source
          </Link>
          <Link href="/profile" className="font-medium text-brand hover:underline">
            Upload a resume and verify facts
          </Link>
          <Link href="/jobs" className="font-medium text-brand hover:underline">
            Or paste a job from any site
          </Link>
        </div>
      </Banner>
    )
  }

  return (
    <section className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        {BUCKET_ORDER.map((bucket) => {
          const value = buckets[bucket.key]
          return (
            <Link key={bucket.key} href={value?.link ?? '/jobs'} className="block">
              <div className="card-tight h-full transition hover:bg-surface-raised">
                <div className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                  {bucket.label}
                </div>
                <div
                  className={`mt-1 text-2xl font-semibold tabular-nums ${TONE_CLASS[bucket.tone]}`}
                >
                  {value?.count ?? 0}
                </div>
                <div className="mt-1 text-xs text-ink-muted">{bucket.hint}</div>
              </div>
            </Link>
          )
        })}
      </div>

      {failureReasons.length > 0 ? (
        <div className="card-tight border-bad/30">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-bad">
            Why applications stopped
          </h3>
          <ul className="mt-2 space-y-1">
            {failureReasons.map((reason) => (
              <li key={reason.reason} className="flex items-center justify-between text-sm">
                <span className="text-ink-soft">{reason.label}</span>
                <span className="font-mono tabular-nums text-ink-muted">{reason.count}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-ink-muted">
            Each of these is a deliberate stop, not a crash. Open the review queue to see the
            job, the prefilled draft and a direct link to apply yourself.
          </p>
        </div>
      ) : null}
    </section>
  )
}
