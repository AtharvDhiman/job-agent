'use client'

import { useState } from 'react'

import { Banner, Empty, ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { CareerFact } from '@/lib/types'

const CATEGORIES = [
  'employment',
  'education',
  'certification',
  'skill',
  'project',
  'achievement',
  'language',
  'work_authorization',
  'compensation',
  'reference',
  'link',
  'personal',
  'screening_answer',
]

const EMPTY_FACT = {
  category: 'employment',
  key: '',
  value: '',
  organization: '',
  title: '',
  location: '',
  start_date: '',
  end_date: '',
  is_current: false,
  highlights: '',
  tags: '',
  evidence_url: '',
  sensitive: false,
}

export function FactsSection() {
  const [filter, setFilter] = useState<'all' | 'verified' | 'unverified'>('all')
  const query =
    filter === 'all' ? '/facts' : `/facts?verified=${filter === 'verified' ? 'true' : 'false'}`
  const { data, error, reload } = useAsync<CareerFact[]>(
    () => api.get<CareerFact[]>(query),
    [filter],
  )

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [draft, setDraft] = useState({ ...EMPTY_FACT })

  function toggle(id: string) {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }

  async function setVerified(verified: boolean) {
    if (selected.size === 0) return
    setBusy(true)
    setActionError(null)
    try {
      await api.post('/facts/verify', { fact_ids: [...selected], verified })
      setNote(`${selected.size} fact(s) marked ${verified ? 'verified' : 'unverified'}.`)
      setSelected(new Set())
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setActionError(null)
    try {
      await api.delete(`/facts/${id}`)
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  async function addFact(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setActionError(null)
    try {
      await api.post('/facts', {
        category: draft.category,
        key: draft.key,
        value: draft.value,
        organization: draft.organization,
        title: draft.title,
        location: draft.location,
        start_date: draft.start_date || null,
        end_date: draft.end_date || null,
        is_current: draft.is_current,
        highlights: draft.highlights
          .split('\n')
          .map((h) => h.trim())
          .filter(Boolean),
        tags: draft.tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        evidence_url: draft.evidence_url,
        sensitive: draft.sensitive,
      })
      setDraft({ ...EMPTY_FACT })
      setShowAdd(false)
      setNote('Fact added. It is UNVERIFIED until you confirm it.')
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const unverifiedCount = (data ?? []).filter((f) => !f.verified).length

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Verified career facts</h2>
          <p className="mt-1 text-xs text-ink-muted">
            This is the only source the agent may draw on. An unverified fact can never appear in
            a resume, a cover letter or a screening answer. Editing a fact resets its
            verification, because the text you confirmed is no longer the text on file.
          </p>
        </div>
        <div className="flex gap-2">
          <select
            className="input w-auto"
            value={filter}
            onChange={(e) => setFilter(e.target.value as typeof filter)}
            aria-label="Fact filter"
          >
            <option value="all">All facts</option>
            <option value="verified">Verified only</option>
            <option value="unverified">Unverified only</option>
          </select>
          <button type="button" className="btn-secondary" onClick={() => setShowAdd(!showAdd)}>
            {showAdd ? 'Cancel' : 'Add a fact by hand'}
          </button>
        </div>
      </div>

      {unverifiedCount > 0 && filter === 'all' ? (
        <Banner tone="warn">
          {unverifiedCount} fact(s) are unverified and will be ignored by the agent. Select them
          and mark them verified once you have checked every word is true.
        </Banner>
      ) : null}

      {note ? <Banner tone="good">{note}</Banner> : null}
      <ErrorNote message={actionError ?? error} />

      {showAdd ? (
        <form onSubmit={addFact} className="rounded-md border border-slate-200 p-4">
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="fact_category">
                Category
              </label>
              <select
                id="fact_category"
                className="input"
                value={draft.category}
                onChange={(e) => setDraft({ ...draft, category: e.target.value })}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="fact_title">
                Title
              </label>
              <input
                id="fact_title"
                className="input"
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </div>
            <div>
              <label className="label" htmlFor="fact_org">
                Organization
              </label>
              <input
                id="fact_org"
                className="input"
                value={draft.organization}
                onChange={(e) => setDraft({ ...draft, organization: e.target.value })}
              />
            </div>
            <div className="md:col-span-3">
              <label className="label" htmlFor="fact_value">
                Value
              </label>
              <input
                id="fact_value"
                className="input"
                placeholder="The fact, in your own words"
                value={draft.value}
                onChange={(e) => setDraft({ ...draft, value: e.target.value })}
              />
            </div>
            <div>
              <label className="label" htmlFor="fact_start">
                Start date
              </label>
              <input
                id="fact_start"
                type="date"
                className="input"
                value={draft.start_date}
                onChange={(e) => setDraft({ ...draft, start_date: e.target.value })}
              />
            </div>
            <div>
              <label className="label" htmlFor="fact_end">
                End date
              </label>
              <input
                id="fact_end"
                type="date"
                className="input"
                value={draft.end_date}
                onChange={(e) => setDraft({ ...draft, end_date: e.target.value })}
                disabled={draft.is_current}
              />
            </div>
            <div className="flex items-end gap-4">
              <label className="flex items-center gap-2 text-sm text-ink-soft">
                <input
                  type="checkbox"
                  checked={draft.is_current}
                  onChange={(e) => setDraft({ ...draft, is_current: e.target.checked })}
                />
                Current
              </label>
              <label className="flex items-center gap-2 text-sm text-ink-soft">
                <input
                  type="checkbox"
                  checked={draft.sensitive}
                  onChange={(e) => setDraft({ ...draft, sensitive: e.target.checked })}
                />
                Sensitive
              </label>
            </div>
            <div className="md:col-span-3">
              <label className="label" htmlFor="fact_highlights">
                Highlights (one per line)
              </label>
              <textarea
                id="fact_highlights"
                className="input h-24"
                placeholder="A real achievement, in your own words"
                value={draft.highlights}
                onChange={(e) => setDraft({ ...draft, highlights: e.target.value })}
              />
            </div>
            <div className="md:col-span-2">
              <label className="label" htmlFor="fact_tags">
                Tags (comma separated)
              </label>
              <input
                id="fact_tags"
                className="input"
                value={draft.tags}
                onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
              />
            </div>
            <div>
              <label className="label" htmlFor="fact_evidence">
                Evidence URL
              </label>
              <input
                id="fact_evidence"
                className="input"
                value={draft.evidence_url}
                onChange={(e) => setDraft({ ...draft, evidence_url: e.target.value })}
              />
            </div>
          </div>
          <button type="submit" className="btn-primary mt-3" disabled={busy}>
            Add as unverified
          </button>
        </form>
      ) : null}

      {data && data.length === 0 ? (
        <Empty
          title="No career facts yet"
          hint="Upload a resume below to have facts proposed, or add them by hand."
        />
      ) : null}

      {data && data.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-muted">{selected.size} selected</span>
            <button
              type="button"
              className="btn-primary px-2 py-1 text-xs"
              disabled={busy || selected.size === 0}
              onClick={() => setVerified(true)}
            >
              Mark verified
            </button>
            <button
              type="button"
              className="btn-secondary px-2 py-1 text-xs"
              disabled={busy || selected.size === 0}
              onClick={() => setVerified(false)}
            >
              Mark unverified
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-8" />
                  <th>Category</th>
                  <th>Fact</th>
                  <th>Dates</th>
                  <th>Verified</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.map((fact) => (
                  <tr key={fact.id} className={fact.verified ? '' : 'bg-warn-soft/40'}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(fact.id)}
                        onChange={() => toggle(fact.id)}
                        aria-label={`Select ${fact.value || fact.title}`}
                      />
                    </td>
                    <td className="whitespace-nowrap text-xs capitalize text-ink-muted">
                      {fact.category.replace(/_/g, ' ')}
                    </td>
                    <td className="max-w-md">
                      <div className="text-sm text-ink">
                        {fact.title || fact.value || fact.key}
                        {fact.organization ? (
                          <span className="text-ink-muted"> at {fact.organization}</span>
                        ) : null}
                      </div>
                      {fact.highlights.length > 0 ? (
                        <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
                          {fact.highlights.map((h, i) => (
                            <li key={i}>- {h}</li>
                          ))}
                        </ul>
                      ) : null}
                      {fact.evidence_url ? (
                        <a
                          href={fact.evidence_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-brand hover:underline"
                        >
                          {fact.evidence_url}
                        </a>
                      ) : null}
                    </td>
                    <td className="whitespace-nowrap text-xs text-ink-muted">
                      {fact.start_date ?? ''}
                      {fact.is_current ? ' - present' : fact.end_date ? ` - ${fact.end_date}` : ''}
                    </td>
                    <td>
                      {fact.verified ? (
                        <span className="chip bg-good-soft text-good">verified</span>
                      ) : (
                        <span className="chip bg-warn-soft text-warn">unverified</span>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs"
                        onClick={() => remove(fact.id)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  )
}
