'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { Banner, ErrorNote } from '@/components/ui'
import { api, logout } from '@/lib/api'

const CONFIRMATION = 'DELETE MY DATA'

interface EraseResult {
  erased: boolean
  rows_deleted: Record<string, number>
  files_removed: number
  audit_entries_anonymised: number
  note: string
}

export function DataSection() {
  const router = useRouter()
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<EraseResult | null>(null)

  async function erase() {
    setBusy(true)
    setError(null)
    try {
      const response = await api.post<EraseResult>('/privacy/erase', { confirmation: typed })
      setResult(response)
      await logout()
      setTimeout(() => {
        router.push('/login')
        router.refresh()
      }, 4000)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">Your data</h2>
        <p className="mt-1 text-xs text-ink-muted">
          Export gives you everything held about you as a single JSON file, including the audit
          trail. Erasure is irreversible.
        </p>
      </div>

      <a
        href="/api/proxy/privacy/export"
        className="btn-secondary inline-flex"
        target="_blank"
        rel="noreferrer"
      >
        Export everything as JSON
      </a>

      <div className="rounded-md border border-bad/30 bg-bad-soft p-4">
        <h3 className="text-sm font-medium text-bad">Danger zone</h3>
        <p className="mt-1 text-xs text-bad">
          This permanently deletes your profile, career facts, documents, stored files, matches,
          applications, review tasks and notifications, and disables your account. Audit entries
          are anonymised rather than deleted so the tamper-evident hash chain stays verifiable:
          the record that something happened survives, the record of who did it does not.
        </p>

        {result ? (
          <Banner tone="good">
            <p className="font-medium">Erased.</p>
            <p className="mt-1">
              {result.files_removed} file(s) removed, {result.audit_entries_anonymised} audit
              entries anonymised. Signing you out.
            </p>
          </Banner>
        ) : (
          <>
            <label className="label mt-3" htmlFor="erase_confirm">
              Type <code>{CONFIRMATION}</code> to confirm
            </label>
            <input
              id="erase_confirm"
              className="input max-w-sm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
            <ErrorNote message={error} />
            <button
              type="button"
              className="btn-danger mt-3"
              disabled={busy || typed !== CONFIRMATION}
              onClick={erase}
            >
              {busy ? 'Erasing...' : 'Permanently erase my data'}
            </button>
          </>
        )}
      </div>
    </section>
  )
}
