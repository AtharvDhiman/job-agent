'use client'

import { useEffect, useState } from 'react'

import { Banner, ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { AgentSettings } from '@/lib/types'

export function AutomationSection() {
  const { data, error, reload } = useAsync<AgentSettings>(() => api.get<AgentSettings>('/settings'))
  const [form, setForm] = useState<Partial<AgentSettings>>({})
  const [saved, setSaved] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  function set<K extends keyof AgentSettings>(key: K, value: AgentSettings[K]) {
    setForm((current) => ({ ...current, [key]: value }))
    setSaved(false)
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setActionError(null)
    try {
      await api.patch<AgentSettings>('/settings', {
        auto_submit_min_score: form.auto_submit_min_score,
        daily_application_limit: form.daily_application_limit,
        job_max_age_hours: form.job_max_age_hours,
        discovery_interval_minutes: form.discovery_interval_minutes,
        shortlist_min_score: form.shortlist_min_score,
        digest_hour_local: form.digest_hour_local,
        timezone: form.timezone,
        notify_channels: form.notify_channels,
      })
      setSaved(true)
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggle() {
    if (!data) return
    setBusy(true)
    setActionError(null)
    try {
      if (data.automation_enabled) {
        await api.post('/settings/pause', { reason: 'Paused from Settings' })
      } else {
        await api.post('/settings/resume')
      }
      reload()
    } catch (err) {
      setActionError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="text-sm text-ink-muted">Loading settings...</p>

  const channels = form.notify_channels ?? {}

  return (
    <section className="card space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">Automation</h2>
          <p className="mt-1 text-xs text-ink-muted">
            Pausing takes effect immediately and stops every drafting and submission path,
            including background workers. Resuming requires the owner role.
          </p>
        </div>
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          className={data.automation_enabled ? 'btn-danger' : 'btn-primary'}
        >
          {data.automation_enabled ? 'Pause all automation' : 'Resume automation'}
        </button>
      </div>

      {data.automation_enabled ? (
        <Banner tone="good">Automation is running.</Banner>
      ) : (
        <Banner tone="warn">
          Automation is paused{data.paused_reason ? `: ${data.paused_reason}` : '.'}
        </Banner>
      )}

      <form onSubmit={save} className="grid gap-4 md:grid-cols-3">
        <div>
          <label className="label" htmlFor="auto_submit_min_score">
            Auto-submit minimum score
          </label>
          <input
            id="auto_submit_min_score"
            type="number"
            min={0}
            max={100}
            className="input"
            value={form.auto_submit_min_score ?? 85}
            onChange={(e) => set('auto_submit_min_score', Number(e.target.value))}
          />
          <p className="mt-1 text-xs text-ink-muted">
            Below this, everything is queued for your review.
          </p>
        </div>
        <div>
          <label className="label" htmlFor="shortlist_min_score">
            Shortlist minimum score
          </label>
          <input
            id="shortlist_min_score"
            type="number"
            min={0}
            max={100}
            className="input"
            value={form.shortlist_min_score ?? 60}
            onChange={(e) => set('shortlist_min_score', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label" htmlFor="daily_application_limit">
            Daily application limit
          </label>
          <input
            id="daily_application_limit"
            type="number"
            min={0}
            max={200}
            className="input"
            value={form.daily_application_limit ?? 10}
            onChange={(e) => set('daily_application_limit', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label" htmlFor="job_max_age_hours">
            Only jobs posted within (hours)
          </label>
          <select
            id="job_max_age_hours"
            className="input"
            value={form.job_max_age_hours ?? 48}
            onChange={(e) => set('job_max_age_hours', Number(e.target.value))}
          >
            <option value={24}>24</option>
            <option value={48}>48</option>
            <option value={72}>72</option>
            <option value={168}>168 (one week)</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="discovery_interval_minutes">
            Search every (minutes)
          </label>
          <input
            id="discovery_interval_minutes"
            type="number"
            min={15}
            max={1440}
            className="input"
            value={form.discovery_interval_minutes ?? 180}
            onChange={(e) => set('discovery_interval_minutes', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label" htmlFor="digest_hour_local">
            Daily digest hour
          </label>
          <input
            id="digest_hour_local"
            type="number"
            min={0}
            max={23}
            className="input"
            value={form.digest_hour_local ?? 8}
            onChange={(e) => set('digest_hour_local', Number(e.target.value))}
          />
        </div>
        <div>
          <label className="label" htmlFor="settings_timezone">
            Time zone
          </label>
          <input
            id="settings_timezone"
            className="input"
            value={form.timezone ?? 'UTC'}
            onChange={(e) => set('timezone', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <span className="label">Notifications</span>
          <div className="flex gap-4">
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={Boolean(channels.in_app)}
                onChange={(e) => set('notify_channels', { ...channels, in_app: e.target.checked })}
              />
              In-app
            </label>
            <label className="flex items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={Boolean(channels.email)}
                onChange={(e) => set('notify_channels', { ...channels, email: e.target.checked })}
              />
              Email
            </label>
          </div>
          <p className="mt-1 text-xs text-ink-muted">
            Email also needs SMTP configured and NOTIFY_EMAIL_ENABLED=true on the server.
          </p>
        </div>

        <div className="md:col-span-3">
          {saved ? <Banner tone="good">Settings saved.</Banner> : null}
          <ErrorNote message={actionError} />
          <button type="submit" className="btn-primary mt-3" disabled={busy}>
            {busy ? 'Saving...' : 'Save settings'}
          </button>
        </div>
      </form>
    </section>
  )
}
