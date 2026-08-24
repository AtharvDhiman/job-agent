/**
 * Pure formatting helpers.
 *
 * Deliberately free of React and Next imports so they can be unit tested in a
 * plain Node environment.
 */

export function relativeTime(value: string | null | undefined, now: number = Date.now()): string {
  if (!value) return '-'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '-'
  const minutes = Math.round((now - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export interface SalaryLike {
  salary_min: number | null
  salary_max: number | null
  salary_currency: string
  salary_period: string
}

export function salaryText(job: SalaryLike): string {
  if (job.salary_min === null && job.salary_max === null) return 'not published'
  const fmt = (n: number | null) => (n === null ? '?' : n.toLocaleString('en-US'))
  const period = job.salary_period || 'year'
  const currency = job.salary_currency ? `${job.salary_currency} ` : ''
  return `${fmt(job.salary_min)}-${fmt(job.salary_max)} ${currency}/ ${period}`.replace('  ', ' ')
}

/** Comma-separated text <-> string[], used by the profile list editors. */
export function toList(text: string): string[] {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function toText(values: string[] | undefined | null): string {
  return (values ?? []).join(', ')
}
