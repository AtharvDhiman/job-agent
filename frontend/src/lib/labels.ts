/**
 * Human-readable labels for backend enum values.
 *
 * These live outside the app/ tree because a Next.js App Router page module may
 * only export `default` plus the framework's route config keys.
 */

/** ReviewReason -> plain English. Keep in sync with backend/app/core/enums.py. */
export const REVIEW_REASON_LABEL: Record<string, string> = {
  below_auto_submit_threshold: 'Score below your auto-submit threshold',
  platform_not_authorized: 'Platform not authorized for automation',
  platform_prohibits_automation: 'Platform prohibits automated applying',
  unsupported_platform: 'Unsupported platform',
  captcha_detected: 'CAPTCHA on the page',
  login_required: 'The site requires an account',
  bot_protection_detected: 'Bot protection detected',
  robots_disallowed: 'robots.txt disallows this path',
  unanswerable_question: 'A required question needs your answer',
  free_text_question: 'Free-text question needs your words',
  missing_verified_fact: 'A verified fact is missing',
  fact_guard_flagged: 'Generated text contained an unverifiable claim',
  validation_failed: 'Pre-flight validation failed',
  missing_attachment: 'A required attachment is missing',
  daily_limit_reached: 'Daily application limit reached',
  automation_disabled: 'Automation is paused',
  submission_error: 'Submission failed',
  manual_request: 'Waiting on you',
}

/** MatchDecision -> plain English, for the "why jobs were skipped" panel. */
export const MATCH_DECISION_LABEL: Record<string, string> = {
  shortlisted: 'Shortlisted',
  below_threshold: 'Scored below your shortlist threshold',
  rejected_hard_filter: 'Failed a hard filter',
  excluded_company: 'Company on your avoid list',
  excluded_keyword: 'Contained an excluded keyword',
  stale_posting: 'Older than your posting window',
  duplicate: 'Duplicate of another listing',
}
