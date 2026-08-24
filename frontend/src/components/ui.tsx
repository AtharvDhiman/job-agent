'use client'

import Link from 'next/link'
import { type ReactNode, useEffect, useState } from 'react'

import { relativeTime, salaryText } from '@/lib/format'

export function ScoreBadge({ score }: { score: number }) {
  const tone =
    score >= 85
      ? 'bg-good-soft text-good'
      : score >= 60
        ? 'bg-brand-soft text-brand'
        : 'bg-slate-100 text-ink-muted'
  return (
    <span className={`chip ${tone} font-mono tabular-nums`} title={`Match score ${score}/100`}>
      {score}
    </span>
  )
}

const POLICY_TONE: Record<string, string> = {
  prohibited: 'bg-bad-soft text-bad',
  review_required: 'bg-warn-soft text-warn',
  assisted_autofill: 'bg-brand-soft text-brand',
  auto_submit: 'bg-good-soft text-good',
}

const POLICY_LABEL: Record<string, string> = {
  prohibited: 'Never automated',
  review_required: 'Review required',
  assisted_autofill: 'Assisted autofill',
  auto_submit: 'Auto-submit',
}

export function PolicyBadge({ policy }: { policy: string }) {
  return (
    <span className={`chip ${POLICY_TONE[policy] ?? 'bg-slate-100 text-ink-muted'}`}>
      {POLICY_LABEL[policy] ?? policy}
    </span>
  )
}

const STATUS_TONE: Record<string, string> = {
  submitted: 'bg-good-soft text-good',
  approved: 'bg-good-soft text-good',
  queued: 'bg-brand-soft text-brand',
  in_progress: 'bg-brand-soft text-brand',
  needs_review: 'bg-warn-soft text-warn',
  drafting: 'bg-slate-100 text-ink-muted',
  failed: 'bg-bad-soft text-bad',
  blocked_by_policy: 'bg-bad-soft text-bad',
  cancelled: 'bg-slate-100 text-ink-muted',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`chip ${STATUS_TONE[status] ?? 'bg-slate-100 text-ink-muted'}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

export function StatCard({
  label,
  value,
  hint,
  href,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: string
  href?: string
  tone?: 'default' | 'warn' | 'good' | 'bad'
}) {
  const toneClass =
    tone === 'warn'
      ? 'text-warn'
      : tone === 'good'
        ? 'text-good'
        : tone === 'bad'
          ? 'text-bad'
          : 'text-ink'
  const inner = (
    <div className="card-tight h-full">
      <div className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {hint ? <div className="mt-1 text-xs text-ink-muted">{hint}</div> : null}
    </div>
  )
  return href ? (
    <Link href={href} className="block transition hover:opacity-80">
      {inner}
    </Link>
  ) : (
    inner
  )
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center">
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint ? <p className="mt-1 text-xs text-ink-muted">{hint}</p> : null}
    </div>
  )
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="rounded-md border border-bad/30 bg-bad-soft px-3 py-2 text-sm text-bad">
      {message}
    </div>
  )
}

export function Banner({
  tone,
  children,
}: {
  tone: 'warn' | 'good' | 'bad' | 'info'
  children: ReactNode
}) {
  const map = {
    warn: 'border-warn/30 bg-warn-soft text-warn',
    good: 'border-good/30 bg-good-soft text-good',
    bad: 'border-bad/30 bg-bad-soft text-bad',
    info: 'border-brand/30 bg-brand-soft text-brand',
  }
  return <div className={`rounded-md border px-4 py-3 text-sm ${map[tone]}`}>{children}</div>
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    loader()
      .then((value) => {
        if (!cancelled) {
          setData(value)
          setError(null)
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload: () => setNonce((n) => n + 1) }
}

// Re-exported so pages can keep importing presentation helpers from one place,
// while the implementations stay React-free and unit testable in lib/format.ts.
export { relativeTime, salaryText }
