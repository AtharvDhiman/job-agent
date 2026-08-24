'use client'

import { useState } from 'react'

import { Banner, Empty, ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { AuditEntry, Page } from '@/lib/types'

interface ChainResult {
  valid: boolean
  checked: number
  broken_at_seq: number | null
  detail: string
}

export default function AuditPage() {
  const [action, setAction] = useState('')
  const [objectType, setObjectType] = useState('')
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (action) query.set('action', action)
  if (objectType) query.set('object_type', objectType)

  const { data, error, loading } = useAsync<Page<AuditEntry>>(
    () => api.get<Page<AuditEntry>>(`/audit?${query.toString()}`),
    [action, objectType, limit, offset],
  )

  const [chain, setChain] = useState<ChainResult | null>(null)
  const [chainError, setChainError] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)

  async function verify() {
    setVerifying(true)
    setChainError(null)
    try {
      setChain(await api.get<ChainResult>('/audit/verify'))
    } catch (err) {
      setChainError((err as Error).message)
    } finally {
      setVerifying(false)
    }
  }

  function toggle(seq: number) {
    const next = new Set(expanded)
    if (next.has(seq)) next.delete(seq)
    else next.add(seq)
    setExpanded(next)
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">Audit log</h1>
          <p className="mt-1 max-w-3xl text-sm text-ink-muted">
            Append-only and hash-chained: each entry stores the hash of the one before it, so any
            retroactive edit breaks the chain and shows up here. No route in the application can
            update or delete an entry, and the database enforces it with a trigger. The actor and
            IP fields are deliberately excluded from the chain so erasure can scrub them without
            destroying verifiability.
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={verify} disabled={verifying}>
          {verifying ? 'Verifying...' : 'Verify chain'}
        </button>
      </header>

      {chain ? (
        chain.valid ? (
          <Banner tone="good">
            Chain intact. {chain.checked} entries verified end to end.
          </Banner>
        ) : (
          <Banner tone="bad">
            Chain broken at sequence {chain.broken_at_seq}: {chain.detail}
          </Banner>
        )
      ) : null}
      <ErrorNote message={chainError ?? error} />

      <div className="card-tight grid gap-3 md:grid-cols-4">
        <input
          className="input"
          placeholder="Filter by action, e.g. application.submitted"
          value={action}
          onChange={(e) => {
            setAction(e.target.value)
            setOffset(0)
          }}
        />
        <input
          className="input"
          placeholder="Filter by object type, e.g. application"
          value={objectType}
          onChange={(e) => {
            setObjectType(e.target.value)
            setOffset(0)
          }}
        />
        <select
          className="input"
          value={limit}
          onChange={(e) => {
            setLimit(Number(e.target.value))
            setOffset(0)
          }}
          aria-label="Page size"
        >
          <option value={50}>50 per page</option>
          <option value={100}>100 per page</option>
          <option value={250}>250 per page</option>
        </select>
      </div>

      {loading && !data ? <p className="text-sm text-ink-muted">Loading...</p> : null}
      {data && data.items.length === 0 ? <Empty title="No audit entries match" /> : null}

      {data && data.items.length > 0 ? (
        <div className="card overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Seq</th>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Object</th>
                <th>Outcome</th>
                <th>Hash</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((entry) => (
                <tr key={entry.id}>
                  <td className="tabular-nums text-xs text-ink-muted">{entry.seq}</td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {new Date(entry.created_at).toLocaleString()}
                  </td>
                  <td className="max-w-[12rem] truncate text-xs text-ink-muted">{entry.actor}</td>
                  <td className="max-w-lg">
                    <button
                      type="button"
                      className="font-mono text-xs text-brand hover:underline"
                      onClick={() => toggle(entry.seq)}
                    >
                      {entry.action}
                    </button>
                    {expanded.has(entry.seq) ? (
                      <pre className="prose-plain mt-1 max-h-64 overflow-auto rounded bg-surface-raised p-2">
                        {JSON.stringify(entry.payload, null, 2)}
                      </pre>
                    ) : null}
                  </td>
                  <td className="text-xs text-ink-muted">
                    {entry.object_type}
                    {entry.object_id ? (
                      <div className="max-w-[10rem] truncate font-mono">{entry.object_id}</div>
                    ) : null}
                  </td>
                  <td>
                    <span
                      className={`chip ${
                        entry.outcome === 'ok'
                          ? 'bg-good-soft text-good'
                          : entry.outcome === 'blocked'
                            ? 'bg-bad-soft text-bad'
                            : 'bg-warn-soft text-warn'
                      }`}
                    >
                      {entry.outcome}
                    </span>
                  </td>
                  <td>
                    <span
                      className="font-mono text-xs text-ink-muted"
                      title={`entry ${entry.entry_hash}\nprev  ${entry.prev_hash}`}
                    >
                      {entry.entry_hash.slice(0, 10)}
                    </span>
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
      ) : null}
    </div>
  )
}
