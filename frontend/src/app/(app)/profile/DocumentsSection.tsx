'use client'

import { useRef, useState } from 'react'

import { Banner, Empty, ErrorNote, relativeTime, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Document } from '@/lib/types'

const KINDS = [
  { value: 'resume_source', label: 'Resume' },
  { value: 'cover_letter_source', label: 'Cover letter' },
  { value: 'certification', label: 'Certification' },
  { value: 'portfolio', label: 'Portfolio' },
  { value: 'transcript', label: 'Transcript' },
  { value: 'other', label: 'Other' },
]

interface UploadResult {
  document: Document
  proposed_fact_count: number
  warnings: string[]
  // Profile fields the server filled in from the resume; empty when nothing was blank.
  auto_configured: Record<string, unknown>
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function DocumentsSection({ onFactsChanged }: { onFactsChanged?: () => void }) {
  const { data, error, reload } = useAsync<Document[]>(() => api.get<Document[]>('/documents'))
  const fileRef = useRef<HTMLInputElement>(null)
  const [kind, setKind] = useState('resume_source')
  const [label, setLabel] = useState('')
  const [isPrimary, setIsPrimary] = useState(true)
  const [proposeFacts, setProposeFacts] = useState(true)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  async function upload(event: React.FormEvent) {
    event.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setUploadError('Choose a file first.')
      return
    }
    setBusy(true)
    setUploadError(null)
    setResult(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('kind', kind)
      form.append('label', label)
      form.append('is_primary', String(isPrimary))
      form.append('propose_facts', String(proposeFacts))
      const uploaded = await api.upload<UploadResult>('/documents', form)
      setResult(uploaded)
      setLabel('')
      if (fileRef.current) fileRef.current.value = ''
      reload()
      onFactsChanged?.()
    } catch (err) {
      setUploadError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setUploadError(null)
    try {
      await api.delete(`/documents/${id}`)
      reload()
    } catch (err) {
      setUploadError((err as Error).message)
    }
  }

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-ink">Documents</h2>
        <p className="mt-1 text-xs text-ink-muted">
          Upload a resume and the parser will propose career facts from it. Every proposed fact
          is stored UNVERIFIED: nothing is used until you confirm it above. PDF, DOCX, TXT and
          MD are supported, up to 15 MB.
        </p>
      </div>

      <form onSubmit={upload} className="grid gap-3 md:grid-cols-4">
        <div className="md:col-span-2">
          <label className="label" htmlFor="doc_file">
            File
          </label>
          <input
            id="doc_file"
            ref={fileRef}
            type="file"
            className="input"
            accept=".pdf,.docx,.txt,.md,application/pdf,text/plain,text/markdown"
          />
        </div>
        <div>
          <label className="label" htmlFor="doc_kind">
            Kind
          </label>
          <select
            id="doc_kind"
            className="input"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="doc_label">
            Label
          </label>
          <input
            id="doc_label"
            className="input"
            placeholder="Optional"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-4 md:col-span-3">
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={isPrimary}
              onChange={(e) => setIsPrimary(e.target.checked)}
            />
            Make this the primary document of its kind
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={proposeFacts}
              onChange={(e) => setProposeFacts(e.target.checked)}
            />
            Propose career facts from it
          </label>
        </div>
        <div className="flex items-end">
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </form>

      {result ? (
        <Banner tone="info">
          <p className="font-medium">
            Uploaded {result.document.filename}
            {result.proposed_fact_count > 0
              ? `, ${result.proposed_fact_count} fact(s) proposed.`
              : '.'}
          </p>
          {result.warnings.length > 0 ? (
            <ul className="mt-1.5 space-y-1">
              {result.warnings.map((warning, index) => (
                <li key={index}>- {warning}</li>
              ))}
            </ul>
          ) : null}
        </Banner>
      ) : null}

      {result && Object.keys(result.auto_configured).length > 0 ? (
        <Banner tone="info">
          Auto-filled from your resume:{' '}
          {Object.keys(result.auto_configured)
            .map((field) => field.replace(/_/g, ' '))
            .join(', ')}
          . Review them under Identity and preferences - nothing you already entered was
          overwritten.
        </Banner>
      ) : null}

      <ErrorNote message={uploadError ?? error} />

      {data && data.length === 0 ? <Empty title="No documents uploaded yet" /> : null}

      {data && data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>File</th>
                <th>Size</th>
                <th>Version</th>
                <th>Added</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((doc) => (
                <tr key={doc.id}>
                  <td className="whitespace-nowrap text-xs capitalize text-ink-muted">
                    {doc.kind.replace(/_/g, ' ')}
                    {doc.is_primary ? (
                      <span className="chip ml-1 bg-brand-soft text-brand">primary</span>
                    ) : null}
                  </td>
                  <td className="max-w-xs">
                    <span className="text-sm text-ink">{doc.label || doc.filename}</span>
                    <div className="text-xs text-ink-muted">{doc.filename}</div>
                  </td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {formatBytes(doc.size_bytes)}
                  </td>
                  <td className="text-xs tabular-nums text-ink-muted">v{doc.version}</td>
                  <td className="whitespace-nowrap text-xs text-ink-muted">
                    {relativeTime(doc.created_at)}
                  </td>
                  <td className="whitespace-nowrap">
                    <a
                      href={`/api/proxy/documents/${doc.id}/content`}
                      target="_blank"
                      rel="noreferrer"
                      className="btn-secondary px-2 py-1 text-xs"
                    >
                      Download
                    </a>
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => remove(doc.id)}
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
