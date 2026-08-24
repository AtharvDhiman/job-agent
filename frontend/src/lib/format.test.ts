import { describe, expect, it } from 'vitest'

import { relativeTime, salaryText, toList, toText } from './format'
import { MATCH_DECISION_LABEL, REVIEW_REASON_LABEL } from './labels'

const NOW = Date.parse('2026-08-24T12:00:00Z')

describe('relativeTime', () => {
  it('handles a missing or unparseable value without throwing', () => {
    expect(relativeTime(null)).toBe('-')
    expect(relativeTime(undefined)).toBe('-')
    expect(relativeTime('not a date')).toBe('-')
  })

  it('reports minutes, hours and days', () => {
    expect(relativeTime('2026-08-24T11:59:40Z', NOW)).toBe('just now')
    expect(relativeTime('2026-08-24T11:30:00Z', NOW)).toBe('30m ago')
    expect(relativeTime('2026-08-24T06:00:00Z', NOW)).toBe('6h ago')
    expect(relativeTime('2026-08-20T12:00:00Z', NOW)).toBe('4d ago')
  })
})

describe('salaryText', () => {
  it('says so plainly when nothing is published rather than inventing a range', () => {
    expect(
      salaryText({ salary_min: null, salary_max: null, salary_currency: '', salary_period: '' }),
    ).toBe('not published')
  })

  it('formats a full range', () => {
    expect(
      salaryText({
        salary_min: 150000,
        salary_max: 190000,
        salary_currency: 'USD',
        salary_period: 'year',
      }),
    ).toBe('150,000-190,000 USD / year')
  })

  it('marks a half-open range with a question mark instead of guessing', () => {
    expect(
      salaryText({
        salary_min: 120000,
        salary_max: null,
        salary_currency: 'EUR',
        salary_period: 'year',
      }),
    ).toBe('120,000-? EUR / year')
  })

  it('defaults the period to year when the posting omits it', () => {
    expect(
      salaryText({ salary_min: 100, salary_max: 200, salary_currency: '', salary_period: '' }),
    ).toContain('/ year')
  })
})

describe('list editors', () => {
  it('round-trips a comma separated list', () => {
    expect(toList('python, postgresql , docker')).toEqual(['python', 'postgresql', 'docker'])
    expect(toText(['a', 'b'])).toBe('a, b')
    expect(toText(undefined)).toBe('')
  })

  it('drops empty entries so a trailing comma does not create a blank filter', () => {
    expect(toList('python,,  ,docker,')).toEqual(['python', 'docker'])
  })
})

describe('labels', () => {
  it('covers every hard stop the backend can report', () => {
    // These strings are the ReviewReason values in backend/app/core/enums.py.
    for (const reason of [
      'below_auto_submit_threshold',
      'platform_not_authorized',
      'platform_prohibits_automation',
      'unsupported_platform',
      'captcha_detected',
      'login_required',
      'bot_protection_detected',
      'robots_disallowed',
      'unanswerable_question',
      'free_text_question',
      'missing_verified_fact',
      'fact_guard_flagged',
      'validation_failed',
      'missing_attachment',
      'daily_limit_reached',
      'automation_disabled',
      'submission_error',
      'manual_request',
    ]) {
      expect(REVIEW_REASON_LABEL[reason], `missing label for ${reason}`).toBeTruthy()
    }
  })

  it('covers every match decision', () => {
    for (const decision of [
      'shortlisted',
      'below_threshold',
      'rejected_hard_filter',
      'excluded_company',
      'excluded_keyword',
      'stale_posting',
      'duplicate',
    ]) {
      expect(MATCH_DECISION_LABEL[decision], `missing label for ${decision}`).toBeTruthy()
    }
  })
})
