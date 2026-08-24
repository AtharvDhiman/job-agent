export type SubmissionPolicy =
  | 'prohibited'
  | 'review_required'
  | 'assisted_autofill'
  | 'auto_submit'

export type ComplianceTier =
  | 'public_job_api'
  | 'partner_api'
  | 'public_feed'
  | 'careers_page'
  | 'manual_only'

export interface Connector {
  key: string
  display_name: string
  compliance_tier: ComplianceTier
  submission_policy_default: SubmissionPolicy
  automation_permitted_for_discovery: boolean
  automation_permitted_for_submission: boolean
  requires_user_review_by_default: boolean
  policy_note: string
  required_credentials: string[]
  available: boolean
  unavailable_reason: string
  direct_employer: boolean
  identifier_label: string
  identifier_help: string
}

export interface Job {
  id: string
  connector_key: string
  compliance_tier: ComplianceTier
  submission_policy_default: SubmissionPolicy
  source_url: string
  apply_url: string
  is_direct_employer: boolean
  title: string
  company: string
  department: string
  location_raw: string
  location_country: string
  work_arrangement: string
  employment_type: string
  seniority: string
  salary_min: number | null
  salary_max: number | null
  salary_currency: string
  salary_period: string
  posted_at: string | null
  deadline_at: string | null
  first_seen_at: string
  extracted_skills: string[]
  visa_sponsorship_mentioned: boolean | null
  canonical_job_id: string | null
}

export interface JobDetail extends Job {
  description_text: string
  requirements: string[]
}

export interface Match {
  id: string
  job_id: string
  score: number
  decision: string
  component_scores: Record<string, number>
  matching_skills: string[]
  missing_skills: string[]
  risks: string[]
  hard_filter_failures: string[]
  explanation: string
  semantic_similarity: number
  scored_by: string
  dismissed_at: string | null
  created_at: string
}

export interface MatchWithJob {
  match: Match
  job: Job
}

export interface Company {
  company: string
  company_normalized: string
  job_count: number
  open_job_count: number
  latest_posted_at: string | null
  first_seen_at: string | null
  connectors: string[]
  countries: string[]
  work_arrangements: string[]
  direct_employer: boolean
  best_score: number | null
  scored_job_count: number
  applied_count: number
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface Application {
  id: string
  job_id: string
  status: string
  pipeline_stage: string
  submission_policy: SubmissionPolicy
  version: number
  summary: string
  fact_guard_flags: { kind: string; span: string; reason: string; severity: string }[]
  validation_errors: string[]
  prefilled_fields: Record<string, string>
  approved_at: string | null
  submitted_at: string | null
  confirmation_number: string | null
  last_error: string
  attempt_count: number
  created_at: string
  updated_at: string
}

export interface Answer {
  id: string
  question_external_id: string
  question_text: string
  question_type: string
  required: boolean
  options: string[]
  answer_value: string | null
  source_fact_id: string | null
  confidence: number
  needs_human: boolean
  reason: string
}

export interface ApplicationDetail extends Application {
  job: Job
  answers: Answer[]
  documents: { id: string; document_id: string; role: string; attached: boolean }[]
}

export interface SubmissionAttempt {
  id: string
  attempt_number: number
  mode: string
  outcome: string
  started_at: string | null
  finished_at: string | null
  guard_findings: Record<string, unknown>[]
  filled_fields: Record<string, unknown>[]
  error_message: string
  assistant_version: string
}

export interface ReviewTask {
  id: string
  application_id: string | null
  job_id: string | null
  reason: string
  status: string
  title: string
  detail: string
  action_url: string
  draft_payload: Record<string, unknown>
  blocking_questions: { question?: string; reason?: string; text?: string }[]
  resolved_at: string | null
  resolution_note: string
  created_at: string
}

export interface CareerFact {
  id: string
  category: string
  key: string
  value: string
  organization: string
  title: string
  location: string
  start_date: string | null
  end_date: string | null
  is_current: boolean
  highlights: string[]
  tags: string[]
  evidence_url: string
  verified: boolean
  verified_at: string | null
  sensitive: boolean
  created_at: string
}

export interface Document {
  id: string
  kind: string
  label: string
  filename: string
  content_type: string
  size_bytes: number
  sha256: string
  version: number
  is_primary: boolean
  generated_for_job_id: string | null
  generation_meta: Record<string, unknown>
  created_at: string
}

export interface Profile {
  id: string
  full_name: string
  headline: string
  contact_email: string
  phone: string | null
  location_city: string
  location_region: string
  location_country: string
  timezone: string
  linkedin_url: string
  portfolio_urls: string[]
  target_titles: string[]
  skills: string[]
  preferred_countries: string[]
  preferred_timezones: string[]
  work_arrangement_preference: string[]
  industries_priority: string[]
  companies_to_avoid: string[]
  excluded_keywords: string[]
  employment_types: string[]
  seniority_level: string
  years_experience: number | null
  min_salary_amount: number | null
  min_salary_currency: string
  salary_period: string
  willing_to_relocate: boolean
  requires_sponsorship: boolean | null
  notice_period_days: number | null
  earliest_start_date: string | null
}

export interface AgentSettings {
  id: string
  automation_enabled: boolean
  paused_reason: string
  auto_submit_min_score: number
  daily_application_limit: number
  job_max_age_hours: number
  discovery_interval_minutes: number
  shortlist_min_score: number
  notify_channels: Record<string, boolean>
  digest_hour_local: number
  timezone: string
}

export interface Authorization {
  id: string
  platform_key: string
  policy: SubmissionPolicy
  granted_at: string | null
  revoked_at: string | null
  notes: string
  is_active: boolean
}

export interface Source {
  id: string
  connector_key: string
  identifier: string
  display_name: string
  enabled: boolean
  last_run_at: string | null
  last_status: string
  last_error: string
  consecutive_failures: number
  jobs_seen: number
}

export interface Dashboard {
  automation_enabled: boolean
  global_automation_enabled: boolean
  paused_reason: string
  applications_today: number
  daily_application_limit: number
  auto_submit_min_score: number
  new_matches: number
  shortlisted: number
  awaiting_review: number
  auto_submitted: number
  rejected_or_skipped: number
  pipeline: Record<string, number>
  unread_notifications: number
  llm_mode: string
  top_matches: {
    match_id: string
    job_id: string
    score: number
    title: string
    company: string
    location: string
    connector: string
    posted_at: string | null
    direct: boolean
    matching_skills: string[]
    missing_skills: string[]
    risks: string[]
  }[]
  rejection_reasons: { decision: string; count: number }[]
  recent_activity: {
    seq: number
    at: string
    actor: string
    action: string
    object_type: string
    object_id: string
    outcome: string
  }[]
}

export interface AuditEntry {
  id: string
  seq: number
  created_at: string
  actor: string
  action: string
  object_type: string
  object_id: string
  outcome: string
  request_id: string
  payload: Record<string, unknown>
  prev_hash: string
  entry_hash: string
}

export interface Notification {
  id: string
  kind: string
  channel: string
  title: string
  body: string
  link: string
  read_at: string | null
  created_at: string
}

export interface AutopilotStatus {
  automation_enabled: boolean
  global_automation_enabled: boolean
  authorized_platforms: string[]
  verified_fact_count: number
  unverified_fact_count: number
  enabled_source_count: number
  resume_uploaded: boolean
  applications_today: number
  daily_application_limit: number
  queued_application_count: number
  next_steps: string[]
}

export interface AutopilotRun {
  discovery: {
    sources_run: number
    created: number
    updated: number
    duplicates: number
    blocked: { connector_key: string; identifier: string; error: string }[]
  }
  scoring: { scored: number; shortlisted: number; rejected: number }
  drafting: { drafted: number; queued_for_auto_submit: number; sent_to_review: number }
  gates: Omit<AutopilotStatus, 'next_steps'>
  next_steps: string[]
}

export interface FoundBoard {
  connector_key: string
  identifier: string
  display_name: string
  url: string
  job_count: number
  probed_slug: string
  already_added: boolean
}

export interface CatalogEntry {
  connector_key: string
  identifier: string
  display_name: string
  note: string
  compliance_note: string
  requires_credentials: string[]
  already_added: boolean
  available: boolean
}
