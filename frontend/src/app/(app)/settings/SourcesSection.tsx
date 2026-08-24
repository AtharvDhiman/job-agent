'use client'

import { useState } from 'react'

import { Banner, Empty, ErrorNote, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { CatalogEntry, Connector, FoundBoard, Source } from '@/lib/types'

interface DiscoveryResult {
  total_created: number
  total_updated: number
  total_duplicates: number
  blocked: { connector_key: string; identifier: string; error: string }[]
}

export function SourcesSection({ connectors }: { connectors: Connector[] }) {
  const { data, error, reload } = useAsync<Source[]>(() => api.get<Source[]>('/sources'))
  const [connectorKey, setConnectorKey] = useState('')
  const [identifier, setIdentifier] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [runResult, setRunResult] = useState<DiscoveryResult | null>(null)

  // Only connectors that may legitimately be polled appear here. A manual-only
  // connector has no automated discovery path at all.
  const addable = connectors.filter(
    (c) => c.automation_permitted_for_discovery && c.compliance_tier !== 'manual_only',
  )
  const chosen = addable.find((c) => c.key === connectorKey)

  async function addSource(event: React.FormEvent) {
    event.preventDefault()
    if (!connectorKey || !identifier.trim()) return
    setBusy(true)
    setActionError(null)
    try {
      await api.post('/sources', {
        connector_key: connectorKey,
        identifier: identifier.trim(),
        display_name: displayName.trim(),
        config: {},
        enabled: true,
      })
      setIdentifier('')
      setDisplayName('')
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggleEnabled(source: Source) {
    setActionError(null)
    try {
      await api.patch(`/sources/${source.id}`, {
        connector_key: source.connector_key,
        identifier: source.identifier,
        display_name: source.display_name,
        config: {},
        enabled: !source.enabled,
      })
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  async function remove(id: string) {
    setActionError(null)
    try {
      await api.delete(`/sources/${id}`)
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  async function runDiscovery() {
    setBusy(true)
    setActionError(null)
    setRunResult(null)
    try {
      setRunResult(await api.post<DiscoveryResult>('/discovery/run'))
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Job sources</h2>
          <p className="mt-1 text-xs text-ink-muted">
            Each source is one board to poll. Only connectors with an explicit compliance tier
            can be added, and a source that gets blocked by policy is disabled rather than
            retried.
          </p>
        </div>
        <button type="button" className="btn-secondary" onClick={runDiscovery} disabled={busy}>
          {busy ? 'Searching...' : 'Run discovery now'}
        </button>
      </div>

      {runResult ? (
        <Banner tone={runResult.blocked.length > 0 ? 'warn' : 'good'}>
          <p>
            {runResult.total_created} new, {runResult.total_updated} refreshed,{' '}
            {runResult.total_duplicates} duplicates collapsed.
          </p>
          {runResult.blocked.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {runResult.blocked.map((b, index) => (
                <li key={index}>
                  <strong>{b.connector_key}</strong> ({b.identifier}) was blocked by policy and
                  disabled: {b.error}
                </li>
              ))}
            </ul>
          ) : null}
        </Banner>
      ) : null}

      <ErrorNote message={actionError ?? error} />

      <FinderBlock onAdded={reload} />

      <CatalogBlock onAdded={reload} />

      <form onSubmit={addSource} className="grid gap-3 rounded-md border border-slate-200 p-4 md:grid-cols-4">
        <div>
          <label className="label" htmlFor="source_connector">
            Platform
          </label>
          <select
            id="source_connector"
            className="input"
            value={connectorKey}
            onChange={(e) => setConnectorKey(e.target.value)}
          >
            <option value="">Choose a platform</option>
            {addable.map((c) => (
              <option key={c.key} value={c.key} disabled={!c.available}>
                {c.display_name}
                {c.available ? '' : ' (credentials required)'}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="source_identifier">
            {chosen?.identifier_label ?? 'Identifier'}
          </label>
          <input
            id="source_identifier"
            className="input"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            disabled={!chosen || !chosen.available}
          />
        </div>
        <div>
          <label className="label" htmlFor="source_name">
            Display name
          </label>
          <input
            id="source_name"
            className="input"
            placeholder="Optional"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div className="flex items-end">
          <button
            type="submit"
            className="btn-primary w-full"
            disabled={busy || !chosen || !chosen.available || !identifier.trim()}
          >
            Add source
          </button>
        </div>

        {chosen ? (
          <div className="md:col-span-4">
            <p className="text-xs text-ink-muted">{chosen.identifier_help}</p>
            <p className="mt-1 text-xs text-ink-soft">
              <strong>Policy:</strong> {chosen.policy_note}
            </p>
            {!chosen.available ? (
              <p className="mt-1 text-xs text-bad">{chosen.unavailable_reason}</p>
            ) : null}
          </div>
        ) : null}
      </form>

      {data && data.length === 0 ? (
        <Empty
          title="No sources configured"
          hint="Add a Greenhouse board token, a Lever site name or an Ashby board to start."
        />
      ) : null}

      {data && data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Identifier</th>
                <th>Status</th>
                <th>Last run</th>
                <th>Seen</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((source) => (
                <tr key={source.id}>
                  <td className="text-xs text-ink-muted">{source.connector_key}</td>
                  <td>
                    <div className="text-sm text-ink">{source.identifier}</div>
                    {source.display_name ? (
                      <div className="text-xs text-ink-muted">{source.display_name}</div>
                    ) : null}
                  </td>
                  <td>
                    <span
                      className={`chip ${
                        source.last_status === 'ok'
                          ? 'bg-good-soft text-good'
                          : source.last_status === 'blocked_by_policy'
                            ? 'bg-bad-soft text-bad'
                            : source.last_status === 'error'
                              ? 'bg-warn-soft text-warn'
                              : 'bg-slate-100 text-ink-muted'
                      }`}
                    >
                      {source.last_status.replace(/_/g, ' ')}
                    </span>
                    {source.last_error ? (
                      <div className="mt-1 max-w-xs text-xs text-bad">{source.last_error}</div>
                    ) : null}
                    {source.consecutive_failures > 0 ? (
                      <div className="text-xs text-warn">
                        {source.consecutive_failures} consecutive failure(s)
                      </div>
                    ) : null}
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {relativeTime(source.last_run_at)}
                  </td>
                  <td className="text-xs tabular-nums text-ink-muted">{source.jobs_seen}</td>
                  <td className="whitespace-nowrap">
                    <button
                      type="button"
                      className="btn-secondary px-2 py-1 text-xs"
                      onClick={() => toggleEnabled(source)}
                    >
                      {source.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => remove(source.id)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

interface FindResult {
  company: string
  candidates: FoundBoard[]
}

function FinderBlock({ onAdded }: { onAdded: () => void }) {
  const [company, setCompany] = useState('')
  const [busy, setBusy] = useState(false)
  const [blockError, setBlockError] = useState<string | null>(null)
  const [result, setResult] = useState<FindResult | null>(null)

  async function probe(name: string) {
    if (!name.trim()) return
    setBusy(true)
    setBlockError(null)
    try {
      setResult(await api.post<FindResult>('/sources/find', { company: name.trim() }))
    } catch (err) {
      setBlockError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function add(candidate: FoundBoard) {
    setBlockError(null)
    try {
      await api.post('/sources', {
        connector_key: candidate.connector_key,
        identifier: candidate.identifier,
        display_name: candidate.display_name,
      })
      onAdded()
      // Re-probe so the row flips to "added" without the user searching again.
      await probe(result?.company ?? company)
    } catch (err) {
      setBlockError((err as Error).message)
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-slate-200 p-4">
      <div>
        <h3 className="text-xs font-semibold text-ink">Find a company&apos;s board</h3>
        <p className="mt-1 text-xs text-ink-muted">
          Type a company name and the agent probes the public job-board APIs it is allowed to
          use for a matching board.
        </p>
      </div>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          probe(company)
        }}
      >
        <input
          className="input flex-1"
          placeholder="Company name"
          value={company}
          onChange={(e) => setCompany(e.target.value)}
        />
        <button type="submit" className="btn-secondary" disabled={busy || !company.trim()}>
          {busy ? 'Searching...' : 'Find board'}
        </button>
      </form>

      <ErrorNote message={blockError} />

      {busy ? <p className="text-xs text-ink-muted">Probing the public job-board APIs...</p> : null}

      {!busy && result && result.candidates.length === 0 ? (
        <Empty
          title="No public board found under that name."
          hint="The company may use a different slug or an ATS we do not integrate - you can still add jobs manually."
        />
      ) : null}

      {!busy && result && result.candidates.length > 0 ? (
        <ul className="divide-y divide-slate-100">
          {result.candidates.map((candidate) => (
            <li
              key={`${candidate.connector_key}-${candidate.identifier}`}
              className="flex flex-wrap items-center gap-2 py-2"
            >
              <span className="text-sm text-ink">{candidate.display_name}</span>
              <span className="chip bg-slate-100 text-ink-muted">{candidate.connector_key}</span>
              <span className="text-xs tabular-nums text-ink-muted">
                {candidate.job_count} jobs
              </span>
              <a
                href={candidate.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-brand underline"
              >
                View board
              </a>
              <button
                type="button"
                className="btn-secondary ml-auto px-2 py-1 text-xs"
                disabled={candidate.already_added}
                onClick={() => add(candidate)}
              >
                {candidate.already_added ? 'Added' : 'Add'}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function CatalogBlock({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false)
  const { data, error, reload } = useAsync<CatalogEntry[]>(() =>
    api.get<CatalogEntry[]>('/sources/catalog'),
  )
  const [blockError, setBlockError] = useState<string | null>(null)

  async function add(entry: CatalogEntry) {
    setBlockError(null)
    try {
      await api.post('/sources', {
        connector_key: entry.connector_key,
        identifier: entry.identifier,
        display_name: entry.display_name,
      })
      onAdded()
      reload()
    } catch (err) {
      setBlockError((err as Error).message)
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-xs font-semibold text-ink">Curated catalog</h3>
          <p className="mt-1 text-xs text-ink-muted">
            Ready-made boards and feeds that are compliant to poll, one click to add.
          </p>
        </div>
        <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setOpen(!open)}>
          {open ? 'Hide catalog' : 'Show catalog'}
        </button>
      </div>

      {open ? (
        <>
          <ErrorNote message={blockError ?? error} />
          {data && data.length === 0 ? <Empty title="The catalog is empty" /> : null}
          {data && data.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {data.map((entry) => (
                <li
                  key={`${entry.connector_key}-${entry.identifier}`}
                  className="flex flex-wrap items-start gap-2 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <span className="text-sm text-ink">{entry.display_name}</span>
                    <p className="text-xs text-ink-muted">{entry.note}</p>
                    <p className="text-xs text-ink-muted">{entry.compliance_note}</p>
                    {!entry.available && entry.requires_credentials.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {entry.requires_credentials.map((cred) => (
                          <span key={cred} className="chip bg-warn-soft text-warn">
                            {cred}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="btn-secondary px-2 py-1 text-xs"
                    disabled={entry.already_added || !entry.available}
                    onClick={() => add(entry)}
                  >
                    {entry.already_added ? 'Added' : 'Add'}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
