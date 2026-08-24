'use client'

import { useEffect, useState } from 'react'

import { Banner, ErrorNote, useAsync } from '@/components/ui'
import { api } from '@/lib/api'
import type { Profile } from '@/lib/types'

const ARRANGEMENTS = ['remote', 'hybrid', 'onsite'] as const
const SENIORITY = [
  'unknown',
  'intern',
  'entry',
  'junior',
  'mid',
  'senior',
  'staff',
  'lead',
  'principal',
  'manager',
  'director',
  'executive',
]
const EMPLOYMENT_TYPES = ['full_time', 'part_time', 'contract', 'internship', 'temporary']

/** Comma-separated text <-> string[] so lists stay easy to edit by hand. */
function toText(values: string[] | undefined): string {
  return (values ?? []).join(', ')
}

function toList(text: string): string[] {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

type ListField =
  | 'portfolio_urls'
  | 'target_titles'
  | 'skills'
  | 'preferred_countries'
  | 'preferred_timezones'
  | 'industries_priority'
  | 'companies_to_avoid'
  | 'excluded_keywords'

const LIST_FIELDS: { key: ListField; label: string; hint: string }[] = [
  {
    key: 'target_titles',
    label: 'Target job titles',
    hint: 'Drives the title component of the match score.',
  },
  {
    key: 'skills',
    label: 'Skills',
    hint: 'Compared against the skills extracted from each posting.',
  },
  {
    key: 'preferred_countries',
    label: 'Preferred countries',
    hint: 'ISO codes or names. Regions like EMEA, EU, APAC and LATAM expand. Blank means anywhere. HARD FILTER.',
  },
  {
    key: 'preferred_timezones',
    label: 'Preferred time zones',
    hint: 'For example UTC+1, America/New_York. Recorded on your profile and shown alongside remote roles.',
  },
  {
    key: 'industries_priority',
    label: 'Industries to prioritise',
    hint: 'Used for semantic weighting only.',
  },
  {
    key: 'companies_to_avoid',
    label: 'Companies to avoid',
    hint: 'Matched on a normalised company name. HARD FILTER.',
  },
  {
    key: 'excluded_keywords',
    label: 'Keywords to exclude',
    hint: 'If any appears in the title or description the job is rejected. HARD FILTER.',
  },
  {
    key: 'portfolio_urls',
    label: 'Portfolio and profile links',
    hint: 'Absolute https URLs. The generator can only echo these, never invent one.',
  },
]

export function ProfileForm() {
  const { data, error, reload } = useAsync<Profile>(() => api.get<Profile>('/profile'))
  const [form, setForm] = useState<Partial<Profile>>({})
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (data) setForm(data)
  }, [data])

  function set<K extends keyof Profile>(key: K, value: Profile[K]) {
    setForm((current) => ({ ...current, [key]: value }))
    setSaved(false)
  }

  function toggleArrangement(value: string) {
    const current = form.work_arrangement_preference ?? []
    set(
      'work_arrangement_preference',
      current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
    )
  }

  function toggleEmployment(value: string) {
    const current = form.employment_types ?? []
    set(
      'employment_types',
      current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
    )
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setSaveError(null)
    try {
      const { id, ...payload } = form as Profile
      void id
      await api.put<Profile>('/profile', payload)
      setSaved(true)
      reload()
    } catch (err) {
      setSaveError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (error) return <ErrorNote message={error} />
  if (!data) return <p className="text-sm text-ink-muted">Loading profile...</p>

  return (
    <form onSubmit={save} className="card space-y-5">
      <div>
        <h2 className="text-sm font-semibold text-ink">Identity and preferences</h2>
        <p className="mt-1 text-xs text-ink-muted">
          These fields drive the hard filters and are the only source for contact answers on an
          application form. Work authorization and salary history are never inferred: if a field
          is blank, the agent asks you rather than guessing.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="label" htmlFor="full_name">
            Full name
          </label>
          <input
            id="full_name"
            className="input"
            value={form.full_name ?? ''}
            onChange={(e) => set('full_name', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="contact_email">
            Contact email
          </label>
          <input
            id="contact_email"
            type="email"
            className="input"
            value={form.contact_email ?? ''}
            onChange={(e) => set('contact_email', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label" htmlFor="headline">
            Headline
          </label>
          <input
            id="headline"
            className="input"
            placeholder="One line describing what you do"
            value={form.headline ?? ''}
            onChange={(e) => set('headline', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="phone">
            Phone
          </label>
          <input
            id="phone"
            className="input"
            value={form.phone ?? ''}
            onChange={(e) => set('phone', e.target.value)}
          />
          <p className="mt-1 text-xs text-ink-muted">Encrypted at rest.</p>
        </div>
        <div>
          <label className="label" htmlFor="linkedin_url">
            LinkedIn URL
          </label>
          <input
            id="linkedin_url"
            className="input"
            placeholder="https://www.linkedin.com/in/..."
            value={form.linkedin_url ?? ''}
            onChange={(e) => set('linkedin_url', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="location_city">
            City
          </label>
          <input
            id="location_city"
            className="input"
            value={form.location_city ?? ''}
            onChange={(e) => set('location_city', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="location_region">
            Region or state
          </label>
          <input
            id="location_region"
            className="input"
            value={form.location_region ?? ''}
            onChange={(e) => set('location_region', e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="location_country">
            Country code
          </label>
          <input
            id="location_country"
            className="input"
            maxLength={2}
            placeholder="US"
            value={form.location_country ?? ''}
            onChange={(e) => set('location_country', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label className="label" htmlFor="timezone">
            Time zone
          </label>
          <input
            id="timezone"
            className="input"
            placeholder="UTC"
            value={form.timezone ?? ''}
            onChange={(e) => set('timezone', e.target.value)}
          />
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {LIST_FIELDS.map((field) => (
          <div key={field.key} className={field.key === 'portfolio_urls' ? 'md:col-span-2' : ''}>
            <label className="label" htmlFor={field.key}>
              {field.label}
            </label>
            <input
              id={field.key}
              className="input"
              placeholder="Comma separated"
              value={toText(form[field.key] as string[] | undefined)}
              onChange={(e) => set(field.key, toList(e.target.value) as Profile[ListField])}
            />
            <p className="mt-1 text-xs text-ink-muted">{field.hint}</p>
          </div>
        ))}
      </div>

      <div>
        <span className="label">Work arrangement you accept (hard filter)</span>
        <div className="flex flex-wrap gap-4">
          {ARRANGEMENTS.map((value) => (
            <label key={value} className="flex items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={(form.work_arrangement_preference ?? []).includes(value)}
                onChange={() => toggleArrangement(value)}
              />
              <span className="capitalize">{value}</span>
            </label>
          ))}
        </div>
      </div>

      <div>
        <span className="label">Employment types you accept (hard filter)</span>
        <div className="flex flex-wrap gap-4">
          {EMPLOYMENT_TYPES.map((value) => (
            <label key={value} className="flex items-center gap-2 text-sm text-ink-soft">
              <input
                type="checkbox"
                checked={(form.employment_types ?? []).includes(value)}
                onChange={() => toggleEmployment(value)}
              />
              <span className="capitalize">{value.replace(/_/g, ' ')}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <label className="label" htmlFor="seniority_level">
            Experience level
          </label>
          <select
            id="seniority_level"
            className="input"
            value={form.seniority_level ?? 'unknown'}
            onChange={(e) => set('seniority_level', e.target.value)}
          >
            {SENIORITY.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="years_experience">
            Years of experience
          </label>
          <input
            id="years_experience"
            type="number"
            min={0}
            max={70}
            step="0.5"
            className="input"
            value={form.years_experience ?? ''}
            onChange={(e) =>
              set('years_experience', e.target.value === '' ? null : Number(e.target.value))
            }
          />
          <p className="mt-1 text-xs text-ink-muted">Never estimated for you.</p>
        </div>
        <div>
          <label className="label" htmlFor="notice_period_days">
            Notice period (days)
          </label>
          <input
            id="notice_period_days"
            type="number"
            min={0}
            max={365}
            className="input"
            value={form.notice_period_days ?? ''}
            onChange={(e) =>
              set('notice_period_days', e.target.value === '' ? null : Number(e.target.value))
            }
          />
        </div>
        <div>
          <label className="label" htmlFor="min_salary_amount">
            Minimum salary (hard filter)
          </label>
          <input
            id="min_salary_amount"
            type="number"
            min={0}
            className="input"
            value={form.min_salary_amount ?? ''}
            onChange={(e) =>
              set('min_salary_amount', e.target.value === '' ? null : Number(e.target.value))
            }
          />
        </div>
        <div>
          <label className="label" htmlFor="min_salary_currency">
            Currency
          </label>
          <input
            id="min_salary_currency"
            className="input"
            maxLength={3}
            value={form.min_salary_currency ?? ''}
            onChange={(e) => set('min_salary_currency', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label className="label" htmlFor="salary_period">
            Period
          </label>
          <select
            id="salary_period"
            className="input"
            value={form.salary_period ?? 'year'}
            onChange={(e) => set('salary_period', e.target.value)}
          >
            <option value="year">year</option>
            <option value="month">month</option>
            <option value="day">day</option>
            <option value="hour">hour</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="requires_sponsorship">
            Do you require visa sponsorship?
          </label>
          <select
            id="requires_sponsorship"
            className="input"
            value={
              form.requires_sponsorship === null || form.requires_sponsorship === undefined
                ? ''
                : String(form.requires_sponsorship)
            }
            onChange={(e) =>
              set(
                'requires_sponsorship',
                e.target.value === '' ? null : e.target.value === 'true',
              )
            }
          >
            <option value="">Not recorded (the agent will ask you)</option>
            <option value="true">Yes, I require sponsorship</option>
            <option value="false">No, I do not</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="earliest_start_date">
            Earliest start date
          </label>
          <input
            id="earliest_start_date"
            type="date"
            className="input"
            value={form.earliest_start_date ?? ''}
            onChange={(e) => set('earliest_start_date', e.target.value || null)}
          />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-ink-soft">
            <input
              type="checkbox"
              checked={form.willing_to_relocate ?? false}
              onChange={(e) => set('willing_to_relocate', e.target.checked)}
            />
            Willing to relocate
          </label>
        </div>
      </div>

      {saved ? <Banner tone="good">Profile saved. Re-score your matches to apply it.</Banner> : null}
      <ErrorNote message={saveError} />

      <div className="flex gap-2">
        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? 'Saving...' : 'Save profile'}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={async () => {
            setBusy(true)
            try {
              const result = await api.post<{ scored: number; shortlisted: number }>(
                '/matches/rescore',
              )
              setSaveError(null)
              setSaved(false)
              alert(`Re-scored ${result.scored} job(s), ${result.shortlisted} shortlisted.`)
            } catch (err) {
              setSaveError((err as Error).message)
            } finally {
              setBusy(false)
            }
          }}
          disabled={busy}
        >
          Re-score all jobs
        </button>
      </div>
    </form>
  )
}
