# Architecture

How the AI Job Application Agent is put together, module by module, with the real file
paths. Companion documents: `README.md` for setup and operation, `docs/COMPLIANCE.md`
for the normative policy, `docs/DEPLOYMENT.md` for running it in production.

Measured against this repository: 58 OpenAPI paths, 151 passing backend tests, 18
database tables, 11 registered connectors.

---

## 1. Shape of the system

```
  frontend/            Next.js 14 App Router. Browser holds no token; every call goes
                       through the server-side proxy at /api/proxy/[...path].
        |  httpOnly cookies -> Authorization header, server side
        v
  backend/app/api/     FastAPI. 12 routers under /api/v1 plus /health and /.
        |
        v
  backend/app/services/   All the logic. The API is a thin shell over these.
        |
        v
  backend/app/models/     SQLAlchemy 2.0 ORM, 18 tables, PostgreSQL (SQLite in tests).

  backend/app/workers/    Celery worker and beat. Calls the same services, so the
                          policy gate cannot be sidestepped by running in background.

  backend/app/connectors/ Read-side integrations, each declaring its compliance tier.

  browser-assistant/      Node + Playwright, runs locally and headed on your machine.
                          Talks only to /api/v1/assistant/* with a shared secret.
```

---

## 2. Module-by-module

### Core (`backend/app/core/`)

| File | Responsibility |
|---|---|
| `config.py` | One pydantic-settings `Settings` object built from `.env` plus the environment. Every tunable in the system is here. Validates that `SECRET_KEY` is real outside development, and exposes derived properties `is_production`, `llm_configured`, `storage_root`, `cors_origins`. |
| `enums.py` | The shared vocabulary that appears in the database, the API and the UI: `ComplianceTier`, `SubmissionPolicy`, `WorkArrangement`, `Seniority` (plus `SENIORITY_ORDER`), `EmploymentType`, `MatchDecision`, `ApplicationStatus`, `PipelineStage`, `ReviewReason`, `ReviewStatus`, `DocumentKind`, `FactCategory`, `QuestionType`, `NotificationKind`, `NotificationChannel`, `AuditAction`. |
| `security.py` | Argon2id hashing (`hash_password`, `verify_password`, `needs_rehash`), HS256 JWT mint and decode with a `type` claim, the `Role` enum and `role_allows` ranking, and `constant_time_equals` for the assistant token. |
| `crypto.py` | Fernet envelope encryption with a `MultiFernet` key ring. `enc:v1:` prefix marks ciphertext; unprefixed values pass through so an imported fixture can be re-encrypted rather than crashing every read. Derives a throwaway dev key from `SECRET_KEY` when `ENCRYPTION_KEY` is blank, and refuses to do so in staging or production. `rotate()` re-encrypts under the primary key. |
| `logging.py` | structlog configuration. JSON one-object-per-line in production, coloured console in dev. A `request_id` and `actor_id` contextvar is merged into every line, and a redaction processor blanks password, token, key, secret and phone fields. |
| `ratelimit.py` | Fixed-window counter. Redis when reachable so limits hold across workers, in-process dict otherwise, and it degrades rather than failing a request when Redis errors. |

### Database (`backend/app/db/`)

| File | Responsibility |
|---|---|
| `base.py` | Declarative `Base`, the `UUIDPrimaryKey` and `Timestamps` mixins, and `utcnow()`. |
| `types.py` | Portable column types: `JSONType` (JSONB on PostgreSQL, JSON on SQLite), `GUID` (native UUID or CHAR(36)), and `EncryptedString` / `EncryptedJSON`, which encrypt on bind and decrypt on load. |
| `session.py` | Engine and `SessionLocal`. Pooling settings for PostgreSQL (`pool_size=10`, `max_overflow=20`, `pool_recycle=1800`, pre-ping), SQLite fallbacks for tests, a `PRAGMA foreign_keys=ON` listener, the `get_db` FastAPI dependency and the `session_scope` context manager for workers. |

### Connectors (`backend/app/connectors/`)

| File | Responsibility |
|---|---|
| `base.py` | The contract. `RawJob`, `SourceSpec`, `FetchResult` dataclasses; the abstract `BaseConnector` with its class-level compliance declaration; and `ConnectorRegistry`, which raises `TypeError` unless a connector declares `key`, `display_name`, `compliance_tier` and `submission_policy_default` with the correct enum types. There is no implicit allow. |
| `http.py` | `PoliteClient`. Identifying User-Agent, per-host throttle from `DISCOVERY_PER_HOST_RPS`, robots.txt fetch and cache, ETag conditional GETs, tenacity retries on transport errors only, 401/403 treated as a gate we do not work around, 429 as backoff, and `assert_no_bot_wall` which scans HTML for CAPTCHA and login-wall markers and raises `BlockedByPolicyError`. |
| `greenhouse.py` | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`. |
| `lever.py` | `api.lever.co/v0/postings/{site}?mode=json`, including the `lists` and `additional` blocks and `workplaceType`. |
| `ashby.py` | `api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`, skips unlisted postings, digs salary out of `compensationTiers[].components[]`. |
| `smartrecruiters.py` | `api.smartrecruiters.com/v1/companies/{id}/postings`, paged at 100 up to 10 pages, with an optional per-posting detail fetch for the ad body that is non-fatal on failure. |
| `workable.py` | `apply.workable.com/api/v1/widget/accounts/{sub}?details=true`, published postings only. |
| `feeds.py` | Generic RSS and Atom parsing with `xml.etree`, honouring robots.txt and ETag. Marked `direct_employer = False`. |
| `careers_page.py` | Fetches a page only if robots.txt allows, then extracts **only** schema.org JobPosting JSON-LD via selectolax, recursing through `@graph`, `itemListElement`, `item` and `mainEntity`. A page with no structured data raises `BlockedByPolicyError` rather than being scraped by layout. |
| `partner.py` | Four connectors: LinkedIn and Indeed (both `PARTNER_API` for discovery, both pinned `PROHIBITED` for submission, both refusing to fetch without your own token and refusing to substitute scraping), Adzuna (free developer API, needs your app id and key), and `manual` (`MANUAL_ONLY`, never fetches at all). |
| `__init__.py` | Imports every module for side-effect registration and re-exports the public names. Adding a connector without importing it here means it does not exist. |

### Services (`backend/app/services/`)

| File | Responsibility |
|---|---|
| `discovery.py` | Fetch, normalise, dedupe, upsert, one source at a time through a shared `PoliteClient`. `upsert_job` returns `created`, `updated` or `duplicate`; a duplicate is linked by `canonical_job_id`, and a direct-employer listing takes over as canonical from an aggregator one. Records `last_status`, `last_error`, `consecutive_failures` and `etag` on the subscription. A `BlockedByPolicyError` disables the source permanently and writes a `policy.block` audit entry; five consecutive `ConnectorError`s disable it too. |
| `normalizer.py` | Pure `RawJob -> Job`. Conservative salary extraction (two-step: a candidate range needs a money signal and must land in a plausible band for its period), sponsorship detection that returns `None` unless the posting says so plainly, requirement bullets, skill extraction, and `compute_dedupe_hash(company, title, country)` deliberately coarsened to country level. |
| `taxonomy.py` | Data, not model: skill aliases and canonicalisation, seniority inference, work-arrangement inference, employment-type mapping. Inspectable and deterministic. |
| `locations.py` | `resolve_country`, `resolve_city`, `expand_country_preferences`. |
| `ranking.py` | The scoring model. Hard filters, eight weighted components, a TF-IDF `SemanticIndex` whose IDF is learned from the batch being scored, and `build_explanation`. No network, no LLM. |
| `matching.py` | Persists scores. Selects unscored, non-duplicate, recent jobs, builds one `SemanticIndex` over the batch, writes a `JobMatch` per job with the full breakdown, and notifies on scores at or above 80. `daily_shortlist` is the ranked, non-dismissed view. |
| `resume_parser.py` | PDF, DOCX, TXT and MD text extraction that never raises on a bad file, section splitting, and `ProposedFact` generation. Every proposed fact carries `verified=False`, always. |
| `document_generator.py` | `select_facts` ranks verified facts against the posting, then renders. Resumes are always template-rendered. Cover letters try the LLM when configured and fall back to the template if it errors, refuses, or trips the guard. `_finalise` runs `fact_guard.check` over whatever is emitted. Also `to_docx_bytes` (single column, no tables, no graphics) and `to_plain_text`. |
| `fact_guard.py` | `FactIndex` builds the comparable corpus from verified facts only. `check()` flags unverified links, employers, credentials, years, quantified claims and work-authorization statements, plus the `INSUFFICIENT_FACTS` token. `check_answer()` additionally requires a source fact. Blocking flags stop auto-submission. |
| `answers.py` | Intent classification over 19 ordered patterns, then a lookup in a specific profile field or verified fact. Every non-match escalates with a reason. There is no guessing branch. |
| `application_workflow.py` | The orchestrator: draft, validate pre-flight, call the gate, then queue or open a review task. Also `approve`, `reject`, `claim_for_attempt`, `record_submission`, `record_human_submission`, `record_failure`, `retire_exhausted`, the daily counter helpers and `pipeline_counts`. Generated documents are content-addressed, so re-drafting reuses the existing row. |
| `policy.py` | `decide()`. The single decision point for "may we automate this application". `HARD_PROHIBITED_PLATFORMS` is a frozenset, not configuration. |
| `llm.py` | Optional Anthropic wrapper. `TRUTHFULNESS_SYSTEM` forbids invention and treats job-description text as data rather than instructions. `generate_text` streams, with a server-side fallback beta that degrades gracefully when unsupported. Every SDK error is normalised to `LLMUnavailable`, and a refusal to `LLMRefusal`; both make the caller fall back to templates. |
| `audit.py` | `record()` is the only writer. It takes the last row (under `FOR UPDATE` on PostgreSQL), assigns `seq`, scrubs the payload, computes `entry_hash` and inserts. `verify_chain()` re-walks and reports the first break. |
| `notifications.py` | In-app rows always; email is opt-in, best-effort, and records its own delivery error rather than failing the caller. `build_digest` and `send_digest` produce the daily summary. |
| `storage.py` | Content-addressed keys `{user_id}/{kind}/{sha[:12]}-{safe_filename}`. `LocalStorage` refuses to resolve outside the storage root; `S3Storage` writes with SSE-AES256. `validate_upload` enforces the 15 MB cap, the content-type allow-list, and rejects executables by magic bytes. |

### API (`backend/app/api/`)

| File | Responsibility |
|---|---|
| `deps.py` | `get_current_user` (bearer JWT, `type=access`), `require_role` producing `RequireOwner` / `RequireOperator`, `get_profile`, `get_agent_settings` (creating defaults on first use), `rate_limit`, `client_ip`, and `assistant_auth` (constant-time shared-secret header, 503 when no token is configured). |
| `v1/auth.py` | Register (first account becomes OWNER, later ones VIEWER), login with lockout after 8 failures for 15 minutes, single-use refresh rotation, logout revoking all refresh tokens, and `/me`. |
| `v1/profile.py` | Profile upsert, career-fact CRUD, the `POST /facts/verify` human gate, document upload with resume parsing and unverified fact proposal, download and delete. |
| `v1/jobs.py` | `GET /connectors` (the live register), source subscription CRUD, `POST /discovery/run`, manual job entry, paged and filtered job listing joined to matches, the shortlist, duplicates, match dismissal and rescore. |
| `v1/applications.py` | Draft, list, detail, answer editing, approve (blocked while a required question needs a human), reject, pipeline stage, and attempt history. |
| `v1/reviews.py` | The review queue: list by status and reason, detail, approve and reject, both of which resolve the underlying application. |
| `v1/settings_router.py` | Agent settings, the pause and resume kill-switch, and platform authorizations including the acknowledgement text endpoint. Prohibited platforms are refused with 403. |
| `v1/dashboard.py` | One aggregate call: automation state, counters, pipeline, top matches with skills and risks, rejection reasons grouped by decision, and recent audit activity. |
| `v1/notifications.py` | List, unread count, mark read, digest preview and send. |
| `v1/audit.py` | Read-only listing plus `GET /audit/verify`. There is no route that edits or deletes an entry. |
| `v1/privacy.py` | Export everything as JSON; erase with the literal `DELETE MY DATA` confirmation, deleting rows and files while anonymising audit entries. |
| `v1/assistant.py` | The narrow surface for the local browser assistant: health, `tasks/next` (re-runs the policy gate at hand-out time), attachment download, `tasks/{id}/questions` (the server answers, the assistant never decides), and `tasks/{id}/result` (submitted, aborted or failed, with abort reasons mapped to review reasons). Also ships `GUARD_RULES` telling the client what must abort the run. |
| `main.py` | App assembly, CORS, the request-context middleware that stamps `X-Request-ID` and security headers, validation and `ValueError` handlers, `/health`, `/`, and `_production_preflight()` which refuses to start on a weak secret, a missing encryption key, SQLite, or automation without an assistant token. |

### Workers (`backend/app/workers/`)

`celery_app.py` builds the Celery app (JSON only, `acks_late`, prefetch 1, 15-minute hard
time limit) and the beat schedule. `tasks.py` holds the tasks. Both are described in
section 7.

---

## 3. Data model

18 tables, created by `backend/alembic/versions/0001_initial.py`.

### Entity diagram

```
                                +---------------+
                                |     users     |
                                +---------------+
                                | id (uuid) PK  |
                                | email UNIQUE  |
                                | hashed_pw     |
                                | role          |
                                | is_active     |
                                | locked_until  |
                                +---------------+
                                  |  |  |  |  |
        +-------------------------+  |  |  |  +-------------------------+
        |            +---------------+  |  +-------------+              |
        v            v                  v                v              v
+----------------+ +----------------+ +---------------------+ +------------------+
| refresh_tokens | | agent_settings | |platform_authorizations| |candidate_profiles|
+----------------+ +----------------+ +---------------------+ +------------------+
| jti UNIQUE     | | automation_on  | | platform_key         | | user_id UNIQUE   |
| expires_at     | | auto_min_score | | policy               | | skills, titles   |
| revoked_at     | | daily_limit    | | acknowledgement (enc)| | preferences      |
+----------------+ | job_max_age    | | granted_at/revoked_at| | salary, visa(enc)|
                   | shortlist_min  | +---------------------+ +------------------+
                   +----------------+   UNIQUE(user_id, platform_key)   |
                                                                        |
                                                    +-------------------+
                                                    v
                                            +-----------------+
                                            |   career_facts  |   <-- the only source
                                            +-----------------+       of application
                                            | profile_id FK   |       content
                                            | category        |
                                            | value, org, title|
                                            | highlights[]    |
                                            | verified  <-----+--- the human gate
                                            | source_document |
                                            +-----------------+
                                                    |
                                                    | source_document_id
                                                    v
+---------------------------+            +------------------+
| job_source_subscriptions  |            |    documents     |
+---------------------------+            +------------------+
| user_id FK                |            | user_id FK       |
| connector_key, identifier |            | kind, sha256     |
| enabled, etag             |            | storage_key      |
| last_status, last_error   |            | extracted_text   |
| consecutive_failures      |            | is_primary       |
+---------------------------+            | generated_for_job|
   UNIQUE(user, connector, identifier)   +------------------+
              |                             UNIQUE(user_id, sha256, kind)
              | discovery writes
              v
        +-------------------+                +------------------+
        |       jobs        |<---------------|   job_matches    |
        +-------------------+  job_id        +------------------+
        | connector_key     |                | user_id FK       |
        | compliance_tier   |                | job_id FK        |
        | submission_policy |                | score 0-100      |
        | external_id       |                | decision         |
        | title, company    |                | component_scores |
        | description_text  |                | matching/missing |
        | location, salary  |                | risks[]          |
        | seniority         |                | hard_filter_fails|
        | extracted_skills[]|                | explanation      |
        | dedupe_hash       |                | dismissed_at     |
        | canonical_job_id -+--> self        +------------------+
        +-------------------+                  UNIQUE(user_id, job_id)
     UNIQUE(connector_key, external_id)                |
              |                                        | match_id
              +--------------------+-------------------+
                                   v
                          +------------------+
                          |   applications   |
                          +------------------+
                          | user_id, job_id  |  UNIQUE(user_id, job_id)
                          | status           |
                          | pipeline_stage   |
                          | submission_policy|
                          | fact_guard_flags |
                          | validation_errors|
                          | prefilled_fields |
                          | approved_by/at   |
                          | confirmation (enc)|
                          +------------------+
                           |      |      |      |
      +--------------------+      |      |      +---------------------+
      v                           v      v                            v
+---------------------+ +------------------+ +--------------------+ +--------------+
|application_documents| |application_answers| |submission_attempts | | review_tasks |
+---------------------+ +------------------+ +--------------------+ +--------------+
| document_id FK      | | question_text    | | attempt_number     | | reason       |
| role (resume/cover) | | question_type    | | mode               | | status       |
| attached            | | required, options| | outcome            | | title, detail|
+---------------------+ | answer_value(enc)| | started/finished   | | action_url   |
                        | source_fact_id FK| | guard_findings[]   | | draft_payload|
                        | needs_human      | | filled_fields[]    | | blocking_qs[]|
                        | confidence,reason| | screenshot_doc_id  | | resolved_at  |
                        +------------------+ +--------------------+ +--------------+

  Standalone, user-scoped:
  +----------------+  +------------------+  +--------------------------------+
  |  audit_logs    |  |  notifications   |  |        daily_counters          |
  +----------------+  +------------------+  +--------------------------------+
  | seq UNIQUE     |  | kind, channel    |  | day (YYYY-MM-DD), name, value  |
  | action, object |  | title, body, link|  | UNIQUE(user_id, day, name)     |
  | payload        |  | read_at, sent_at |  +--------------------------------+
  | prev_hash      |  | delivery_error   |
  | entry_hash     |  +------------------+
  +----------------+
   append-only, hash-chained, user_id nullable (SET NULL on erasure)
```

### Table reference

| Table | Model | Important columns | Relationships and constraints |
|---|---|---|---|
| `users` | `models/user.py:User` | `email` (unique), `hashed_password` (Argon2id), `role`, `is_active`, `last_login_at`, `failed_login_count`, `locked_until` | One profile, one settings row, both cascade-delete. |
| `refresh_tokens` | `User`-adjacent, `models/user.py` | `jti` (unique), `expires_at`, `revoked_at`, `user_agent` | Cascade from `users`. Single-use: refresh revokes the presented row. |
| `agent_settings` | `models/user.py:AgentSettings` | `automation_enabled` (default false), `paused_reason`, `auto_submit_min_score`, `daily_application_limit`, `job_max_age_hours`, `discovery_interval_minutes`, `shortlist_min_score`, `notify_channels`, `digest_hour_local`, `timezone` | One per user (`user_id` unique). The per-user kill-switch lives here. |
| `platform_authorizations` | `models/user.py:PlatformAuthorization` | `platform_key`, `policy`, `acknowledgement_text` (**encrypted**), `granted_at`, `revoked_at`, `notes` | `UNIQUE(user_id, platform_key)`. `is_active` is granted and not revoked. |
| `candidate_profiles` | `models/profile.py:CandidateProfile` | `full_name`, `headline`, `contact_email`, `phone` (**encrypted**), `address` (**encrypted**), location fields, `linkedin_url`, `portfolio_urls`, `target_titles`, `skills`, `preferred_countries`, `work_arrangement_preference`, `companies_to_avoid`, `excluded_keywords`, `employment_types`, `seniority_level`, `years_experience`, `min_salary_*`, `willing_to_relocate`, `requires_sponsorship`, `work_authorization` (**encrypted JSON**), `notice_period_days`, `earliest_start_date` | One per user. Owns career facts. Drives every hard filter and component. |
| `career_facts` | `models/profile.py:CareerFact` | `category`, `key`, `value`, `organization`, `title`, `location`, `start_date`, `end_date`, `is_current`, `highlights`, `tags`, `evidence_url`, `source_document_id`, **`verified`** (indexed), `verified_at`, `sensitive` | Cascade from profile. The only permitted source of application content. |
| `documents` | `models/profile.py:Document` | `kind`, `label`, `filename`, `content_type`, `storage_key`, `size_bytes`, `sha256`, `version`, `parent_id`, `extracted_text`, `parsed`, `is_primary`, `generated_for_job_id`, `generation_meta` | `UNIQUE(user_id, sha256, kind)`: identical content reuses the row rather than duplicating. |
| `job_source_subscriptions` | `models/job.py:JobSourceSubscription` | `connector_key`, `identifier`, `display_name`, `enabled`, `config`, `last_run_at`, `last_status`, `last_error`, `consecutive_failures`, `etag`, `jobs_seen` | `UNIQUE(user_id, connector_key, identifier)`. |
| `jobs` | `models/job.py:Job` | `connector_key`, `compliance_tier`, `submission_policy_default`, `external_id`, `source_url`, `apply_url`, `is_direct_employer`, `title(_normalized)`, `company(_normalized)`, `description_text/html`, `location_raw/city/country`, `work_arrangement`, `employment_type`, `seniority`, `salary_min/max/currency/period`, `posted_at`, `deadline_at`, `first_seen_at`, `last_seen_at`, `closed_at`, `extracted_skills`, `requirements`, `visa_sponsorship_mentioned`, `raw`, `dedupe_hash`, `canonical_job_id` | `UNIQUE(connector_key, external_id)`; indexes on dedupe hash, posted_at, and (company, title). Jobs are global, not per user. `canonical_job_id` self-references the surviving record. |
| `job_matches` | `models/job.py:JobMatch` | `score`, `decision`, `component_scores`, `matching_skills`, `missing_skills`, `risks`, `hard_filter_failures`, `explanation`, `semantic_similarity`, `scored_by`, `recommended_resume_id`, `dismissed_at` | `UNIQUE(user_id, job_id)`, index on (user_id, score). Per-user view of a global job. |
| `applications` | `models/application.py:Application` | `status`, `pipeline_stage`, `submission_policy`, `version`, `summary`, `fact_guard_flags`, `validation_errors`, `prefilled_fields`, `approved_by_user_id`, `approved_at`, `submitted_at`, `confirmation_number` (**encrypted**), `submission_receipt`, `screenshot_document_id`, `last_error`, `attempt_count` | `UNIQUE(user_id, job_id)`: one application per job per user; re-drafting bumps `version`. |
| `application_documents` | `models/application.py` | `document_id`, `role` (`resume`, `cover_letter`), `attached` | Join table; pre-flight requires an attached resume. |
| `application_answers` | `models/application.py:ApplicationAnswer` | `question_external_id`, `question_text`, `question_type`, `required`, `options`, `answer_value` (**encrypted**), `source_fact_id`, `confidence`, `needs_human`, `reason` | `source_fact_id` is what makes an answer traceable; auto-submission requires no required answer with `needs_human`. |
| `review_tasks` | `models/application.py:ReviewTask` | `reason` (a `ReviewReason`), `status`, `title`, `detail`, `action_url`, `draft_payload`, `blocking_questions`, `resolved_at`, `resolution_note` | Index on (user_id, status). Always carries the direct apply link and the prefilled draft. |
| `submission_attempts` | `models/application.py:SubmissionAttempt` | `attempt_number`, `mode`, `outcome`, `started_at`, `finished_at`, `guard_findings`, `filled_fields`, `error_message`, `screenshot_document_id`, `assistant_version` | Never deleted. One row per assistant run. |
| `audit_logs` | `models/audit.py:AuditLog` | `seq` (unique, assigned under a row lock), `created_at`, `user_id` (nullable), `actor`, `action`, `object_type`, `object_id`, `outcome`, `request_id`, `ip_address`, `payload`, `prev_hash`, `entry_hash` | Append-only. `actor`, `user_id` and `ip_address` are outside the hash so erasure can scrub them; a PL/pgSQL trigger enforces that everything else is immutable. |
| `notifications` | `models/audit.py:Notification` | `kind`, `channel`, `title`, `body`, `link`, `data`, `read_at`, `sent_at`, `delivery_error` | Index on (user_id, read_at). |
| `daily_counters` | `models/audit.py:DailyCounter` | `day`, `name`, `value`, `frozen` | `UNIQUE(user_id, day, name)`. Backs the daily application limit; incremented under `FOR UPDATE` on PostgreSQL. |

---

## 4. The scoring model

`backend/app/services/ranking.py`. Deterministic, offline, and fully explainable. The
optional LLM pass can rewrite prose; it can never change a number.

### Weights

```python
WEIGHTS: dict[str, int] = {
    "skills": 35,
    "semantic": 20,
    "title": 15,
    "seniority": 10,
    "location": 10,
    "salary": 5,
    "freshness": 3,
    "direct_employer": 2,
}
```

They sum to 100, and the test suite asserts that sum. Each component produces a ratio in
`[0, 1]`; the score is `round(sum(ratio * weight))`, clamped to `[0, 100]`.

| Component | Weight | What it measures | Neutral case |
|---|---|---|---|
| `skills` | 35 | Fraction of the posting's canonicalised `extracted_skills` present in your profile skills. | A posting with no extractable skills scores 0.5 rather than 0, and the missing signal is reported as a risk instead. |
| `semantic` | 20 | TF-IDF cosine between your profile text (headline, target titles, skills, industries, resume text and verified fact text) and the posting. IDF is learned from the batch being scored, so boilerplate is discounted automatically. With one document it degrades to plain TF cosine. | 0.0 when either side is empty. |
| `title` | 15 | Best token overlap between the job title and any of your target titles, as a fraction of the target's tokens. | 0.5 when you list no target titles. |
| `seniority` | 10 | Distance in `SENIORITY_INDEX`. Exact 1.0; one above 0.75 (a stretch role); one below 0.6; more than one above 0.15; more than one below 0.25. | 0.5 when either side is unknown or unparseable. |
| `location` | 10 | Remote and you accept remote 1.0. Same city 1.0, otherwise in an allowed country 0.8. Outside your countries: 0.5 if you will relocate, else 0.2. Remote but you prefer onsite or hybrid 0.4. | 0.5 when the country could not be resolved. |
| `salary` | 5 | No stated minimum 0.6. Top of range at or above 1.25x your minimum 1.0; at or above your minimum 0.8; below it 0.3. | 0.5 when nothing is published or the currency is not comparable. |
| `freshness` | 3 | 1.0 within 24h of `posted_at` (or `first_seen_at`), 0.8 within 48h, 0.6 within 72h, 0.3 beyond. | 0.5 with no date at all. |
| `direct_employer` | 2 | 1.0 for the employer's own board, 0.0 for an aggregator. | n/a |

The `decision` is `shortlisted` when the score reaches `agent_settings.shortlist_min_score`
(default 60) and `below_threshold` otherwise.

### Hard filters

`evaluate_hard_filters()` runs **before** scoring. Any hit means the job is rejected with
score 0 and is never ranked. The complete list, in evaluation order:

| # | Condition | Failure message |
|---|---|---|
| 1 | `job.company_normalized` is in `profile.companies_to_avoid` (normalised) | `Company '<name>' is on your avoid list` |
| 2 | Any `profile.excluded_keywords` entry appears in the folded title plus first 8000 chars of the description | `Contains excluded keyword '<kw>'` |
| 3 | `job.location_country` is outside the expanded `profile.preferred_countries`, **unless** the job is remote and you accept remote | `Location <CC> is outside your preferred countries` |
| 4 | `job.work_arrangement` is known and not in `profile.work_arrangement_preference` | `Work arrangement '<x>' is not one you accept` |
| 5 | `profile.requires_sponsorship` and the posting explicitly says it cannot sponsor (`visa_sponsorship_mentioned is False`) | `Posting states it cannot sponsor and you require sponsorship` |
| 6 | `job.salary_max` is below `profile.min_salary_amount`, only when currency and period are comparable | `Advertised maximum ... is below your minimum of ...` |
| 7 | `job.employment_type` is known and not in `profile.employment_types` | `Employment type '<x>' is not one you accept` |
| 8 | `posted_at` (or `first_seen_at`) is older than `max_age_hours` | `Posted more than <n>h ago` |
| 9 | `job.deadline_at` is in the past | `Application deadline has passed` |
| 10 | `job.closed_at` is set | `Posting is closed` |

The failure text is then mapped to a `MatchDecision` by `_rejection_decision`:
`excluded_company` for the avoid list, `excluded_keyword` for a keyword, `stale_posting`
for age, deadline or closure, and `rejected_hard_filter` for everything else. The full
failure list is stored on the match in `hard_filter_failures`, so a rejection is as
explainable as an acceptance.

### The explanation string

`build_explanation()` assembles a fixed sequence of lines, joined by newlines, and stores
it on `job_matches.explanation`:

1. `Score {score}/100 for {title} at {company}.`
2. `Points: ` then every component sorted by descending contribution, each as
   `name value/weight`, so the arithmetic adds up in front of the reader.
3. Matching skills with a count, or an explicit "none of the posting's skills are on your
   profile".
4. Missing skills with a count, or "none, your profile covers every skill the posting
   names".
5. One line combining seniority (posting versus yours), location and arrangement, and the
   salary phrase (`min-max CUR per period`, or "not published").
6. Work authorization: sponsorship available, cannot sponsor, or not stated.
7. `Risks: ` and the semicolon-joined risk list, when non-empty. Risks come from the
   seniority, location and salary components, plus: no extractable skills, sponsorship
   silence when you need sponsorship, aggregator listing, a deadline within three days,
   and more missing skills than matching ones.
8. `Applies directly with the employer.` or `Routed through a third-party listing.`

When the job was rejected by a hard filter, the explanation is instead
`Rejected before scoring: ` followed by the semicolon-joined failures, and nothing is
scored.

---

## 5. The policy gate

`backend/app/services/policy.decide()` in `backend/app/services/policy.py`. Nothing else
in the codebase may conclude that automation is permitted. Its inputs:

| Input | Source |
|---|---|
| `job` | The `Job` row, for `connector_key` |
| `connector_policy` | `job.submission_policy_default`, copied from the connector at normalisation time |
| `authorization` | The user's `PlatformAuthorization` for this platform, or `None` |
| `agent_settings` | Per-user `automation_enabled`, `paused_reason`, `daily_application_limit`, `auto_submit_min_score` |
| `score` | The `JobMatch` score, or 0 when there is no match |
| `global_enabled` | `settings.automation_global_enabled` |
| `applications_today` | The `daily_counters` value for today |
| `fact_guard_blocked` | Any blocking flag on the generated documents |
| `blocking_questions` | Count of required questions that escalated |
| `validation_errors` | Count of pre-flight failures |

`granted` below means `authorization.policy` when the authorization exists and is active
(granted and not revoked); otherwise `None`.

### Decision table

| # | Condition | `policy` | `granted_policy` | `may_autofill` | `may_submit` | Review reason |
|---|---|---|---|---|---|---|
| 1 | `connector_key` in `{linkedin, indeed}` **or** `connector_policy == prohibited` | `prohibited` | `""` | false | false | `platform_prohibits_automation` |
| 2 | No blockers and `granted == auto_submit` | `auto_submit` | `auto_submit` | true | **true** | none |
| 3 | No blockers and `granted == assisted_autofill` | `assisted_autofill` | `assisted_autofill` | true | false | `manual_request` |
| 4 | Blockers present and `granted` in `{assisted_autofill, auto_submit}` and global on and per-user on and no fact-guard block and no blocking questions | `assisted_autofill` | as granted | true | false | every reason that fired |
| 5 | Blockers present, any other case | `review_required` | as granted, or `""` | false | false | every reason that fired |

Row 1 is a short circuit: it returns immediately, before authorization, the kill-switch,
the limit, the score or content integrity are even consulted. It is unreachable by
configuration because `HARD_PROHIBITED_PLATFORMS` is a frozenset in the module and the
grant endpoint refuses those keys with HTTP 403.

### What counts as a blocker

Each of these appends a `ReviewReason` and a plain-language rationale line:

| Condition | Reason |
|---|---|
| No active authorization for the platform | `platform_not_authorized` |
| Authorization exists but is `review_required` | `platform_not_authorized` |
| `global_enabled` false, or `agent_settings.automation_enabled` false | `automation_disabled` |
| `applications_today >= daily_application_limit` | `daily_limit_reached` |
| `score < auto_submit_min_score` | `below_auto_submit_threshold` |
| `fact_guard_blocked` | `fact_guard_flagged` |
| `blocking_questions > 0` | `unanswerable_question` |
| `validation_errors > 0` | `validation_failed` |

Reasons are de-duplicated and sorted on the way out; the rationale list preserves the
order they fired in and is what the review task shows you.

### The `granted_policy` distinction

`policy` is what may happen to **this** application. `granted_policy` is what **you**
authorized for the platform. Keeping them separate is what lets the UI say "you asked for
auto-submit, but this application scored 71 against your threshold of 85" instead of the
uselessly vague "review required". It is also what
`services/application_workflow.draft_application` uses to tell two superficially similar
outcomes apart: an application whose `granted_policy` is exactly `assisted_autofill` is
set to `queued` **and** given a review task, because you asked for the form to be filled
but not sent and should know it is waiting for your click. Anything else that cannot
submit goes to `needs_review`.

Note one deliberate asymmetry in row 4: `validation_errors` and a low score or a reached
daily limit block **submission** but not **autofill**, while a fact-guard block or an
unanswerable required question stop autofill too. Filling a form you will inspect is safe
when the content is honest; it is not safe when the content might be false.

### Re-evaluation at hand-out time

`backend/app/api/v1/assistant.py:_fresh_policy` calls `decide()` again when the assistant
asks for a task, because settings, authorizations and the kill-switch may have changed
since drafting. If `AUTOMATION_GLOBAL_ENABLED` is false, `tasks/next` returns null without
looking at anything.

The decision is then run through `services/policy.may_hand_out()`, which is the only
place allowed to answer "may the assistant open this page at all". A human-approved
application may be autofilled below the score threshold or past the daily cap, and it
still may not be auto-clicked unless the platform is authorized for `auto_submit`. What
an approval explicitly does **not** do is widen the surface: `HUMAN_APPROVAL_MAY_LIFT`
is an allow-list of review reasons, so a prohibited platform, a platform with no typed
grant, a paused kill-switch, a fact-guard block and an unanswerable required question all
survive an approval and the application is skipped. A new `ReviewReason` is un-liftable
until somebody adds it to that set on purpose.

The application is then claimed with `workflow.claim_for_attempt`, a conditional UPDATE
from `queued`/`approved` to `in_progress`. Two assistants polling the same queue in the
same instant cannot both walk away with it: one changes a row, the other sees rowcount 0
and moves on. `MAX_ATTEMPTS_PER_APPLICATION` (3) bounds how often the same application is
handed out; past that it is retired to `failed` with a review task rather than re-offered
on every poll.

---

## 6. The application state machine

### `ApplicationStatus`

```
                         draft_application()
                                 |
                          [ drafting ]
                                 |
              +------------------+------------------+
              |                  |                  |
   decision.may_submit   granted == assisted   everything else
              |             autofill (may_autofill)  |
              v                  v                   v
        [ queued ]         [ queued ]          [ needs_review ]
                          + review task         + review task
                                                      |
                                            approve() | reject()
                                                      v
                                              [ approved ]   [ cancelled ]
              \                  |                  /
               +-----------------+-----------------+
                                 |
                 claim_for_attempt()  (assistant picks it up)
                                 v
                        [ in_progress ]
                                 |
              +------------------+-------------------+
              |                  |                   |
   record_submission()   record_failure()     record_failure()
                          with guard_findings   without findings
              v                  v                   v
       [ submitted ]    [ blocked_by_policy ]    [ failed ]
                          + review task            + review task
```

| Status | Meaning | Set by | Who can trigger it |
|---|---|---|---|
| `drafting` | Row created, documents and answers being generated | `draft_application` | Operator via `POST /applications/draft`, or the `draft-shortlisted` beat task |
| `needs_review` | The gate said no; a review task is open | `draft_application` | The gate, never a person directly |
| `approved` | A human approved it; it may now be autofilled even below threshold, if `may_hand_out` still agrees | `workflow.approve` | Operator via `POST /applications/{id}/approve` or `POST /reviews/{id}/approve`. Refused while any required answer still needs a human. Only re-queues from `drafting`, `needs_review`, `queued`, `approved` or `failed`: approving a `blocked_by_policy`, `in_progress` or `submitted` application resolves its reviews and leaves the status alone. |
| `queued` | Eligible for the assistant to pick up | `draft_application` | The gate, when `may_submit` or an explicit assisted-autofill grant applies |
| `in_progress` | An attempt is running | `workflow.claim_for_attempt` (conditional UPDATE, so only one claimer wins) | The browser assistant via `GET /assistant/tasks/next` |
| `submitted` | The form was sent; `submitted_at`, confirmation and receipt recorded, daily counter incremented | `workflow.record_submission` (assistant) or `workflow.record_human_submission` (you) | The assistant via `POST /assistant/tasks/{id}/result` with `outcome=submitted`, which requires an attempt that is still open; or the operator via `POST /applications/{id}/mark-submitted` after an assisted-autofill hand-off, recorded as `submitted_by_human` |
| `failed` | The attempt failed for a non-policy reason | `workflow.record_failure` (no guard findings) | The assistant, `outcome=failed` |
| `blocked_by_policy` | The attempt aborted on a hard stop: CAPTCHA, login wall, bot protection, robots, an unanswerable question | `workflow.record_failure` (with guard findings) | The assistant, `outcome=aborted` |
| `cancelled` | You rejected it; pipeline moves to `closed` | `workflow.reject` | Operator via `POST /applications/{id}/reject` or `POST /reviews/{id}/reject` |

Failure is terminal for that attempt: `record_failure` always opens a review task and
never schedules a retry. Re-drafting the same application increments `version`, deletes
the previous documents and answers, and runs the gate again.

### `PipelineStage`

Your own tracking of the conversation with the employer, independent of the automation
status.

| Stage | Set by |
|---|---|
| `saved` | `draft_application`, on creation |
| `applied` | `record_submission` |
| `screening`, `interview`, `offer`, `rejected` | Operator only, via `POST /applications/{id}/stage` |
| `closed` | `workflow.reject`, or the operator |

`workflow.pipeline_counts` aggregates these for the dashboard.

### `ReviewStatus`

`open` on creation; `approved` or `rejected` when you resolve it (approving or rejecting
the underlying application resolves its open tasks too); `expired` after 30 days with no
decision, set by the `expire-stale-reviews` beat task.

---

## 7. Queue and scheduler

`backend/app/workers/celery_app.py`. JSON serialisation only, `task_acks_late=True`,
`worker_prefetch_multiplier=1`, a 900-second hard and 840-second soft time limit, results
expiring after 24 hours, and a `6/m` rate limit annotation on `run_discovery_for_user`.

### Beat schedule

| Beat entry | Task | Cadence | What it does |
|---|---|---|---|
| `discover-and-score` | `app.workers.tasks.discover_all_users` | `DISCOVERY_INTERVAL_MINUTES * 60` seconds (default 10800, i.e. every 3 hours), with `expires` set to the same interval so a backed-up queue does not stack sweeps | Fans out one `run_discovery_for_user` per active user |
| `draft-shortlisted` | `app.workers.tasks.draft_shortlisted_applications` | Every 30 minutes | For each active user, drafts up to 10 not-yet-drafted shortlisted matches, highest score first. Drafting is always safe: it produces documents and, unless every gate passes, a review task. It never submits. |
| `daily-digest` | `app.workers.tasks.send_daily_digests` | `crontab(hour=NOTIFY_DIGEST_HOUR_LOCAL, minute=0)` (default 08:00 in `NOTIFY_TIMEZONE`) | Builds and sends the 24-hour digest notification per user |
| `expire-stale-reviews` | `app.workers.tasks.expire_stale_reviews` | `crontab(hour=3, minute=30)` | Marks review tasks open for more than 30 days as `expired` with an explanatory note |
| `prune-expired-tokens` | `app.workers.tasks.prune_expired_tokens` | `crontab(hour=4, minute=0)` | Deletes `refresh_tokens` rows past their expiry |

### Tasks not on the schedule

| Task | Trigger | Notes |
|---|---|---|
| `run_discovery_for_user` | Fanned out by `discover_all_users`, or called directly | Runs every enabled source for one user, then scores. Retries with exponential backoff capped at 600 seconds, up to 3 times. Returns created, duplicate and blocked counts. |
| `health` | Manual | Returns environment and kill-switch state; useful as a queue liveness probe. |

Every task goes through the same service functions the API uses. There is no worker-only
path around `policy.decide()`.

---

## 8. Adding a connector

Five steps. The registry will reject anything that skips step 2.

### 1. Read the policy first

`docs/COMPLIANCE.md` section 1 defines the tiers. Decide honestly which one applies. If
the vendor does not publish a documented endpoint intended for public consumption, the
answer is `PARTNER_API` (with your own credentials) or `MANUAL_ONLY`, not
`PUBLIC_JOB_API`. If automated applying would violate their terms, the submission default
is `PROHIBITED` and you should also add the key to `HARD_PROHIBITED_PLATFORMS` in
`backend/app/services/policy.py`.

### 2. Write the connector

`backend/app/connectors/examplats.py`:

```python
"""Example ATS public job-board API.

Discovery: PUBLIC_JOB_API. Example ATS publishes an unauthenticated JSON endpoint
so companies can render their own board; reading it is its intended use.
Submission: REVIEW_REQUIRED. Their hosted forms are plain forms with no login,
so they may be authorized, but a CAPTCHA on the page still aborts the run.
"""

from __future__ import annotations

from app.connectors.base import (
    BaseConnector, ConnectorError, FetchResult, RawJob, SourceSpec, registry,
)
from app.core.enums import ComplianceTier, SubmissionPolicy
from app.utils.text import parse_datetime, strip_html

BASE = "https://api.exampleats.com/v1/boards"


@registry.register
class ExampleATSConnector(BaseConnector):
    key = "exampleats"
    display_name = "Example ATS"
    compliance_tier = ComplianceTier.PUBLIC_JOB_API
    submission_policy_default = SubmissionPolicy.REVIEW_REQUIRED
    policy_note = (
        "Documented public board API intended for public consumption. "
        "Applications stay in review until you explicitly authorize the platform; "
        "even then a CAPTCHA or login wall aborts the run."
    )
    #: Names of settings that must be non-empty before this connector may run.
    #: Leave empty for a genuinely public API.
    required_credentials = ()
    #: False when postings are aggregated rather than from the employer's own board.
    direct_employer = True
    identifier_label = "Board slug"
    identifier_help = "The slug in jobs.exampleats.com/<slug>, e.g. 'examplecorp'."

    def fetch(self, spec: SourceSpec, *, etag: str = "") -> FetchResult:
        slug = spec.identifier.strip().strip("/")
        if not slug:
            raise ConnectorError("Example ATS board slug is required")

        # check_robots=False is correct only for a documented JSON API endpoint.
        # For anything HTML, leave the default so robots.txt is honoured.
        payload, new_etag = self.http.get_json(
            f"{BASE}/{slug}/postings", etag=etag, check_robots=False
        )
        if payload is None:               # HTTP 304, nothing changed
            return FetchResult(jobs=[], etag=etag, notes=["not modified"])
        if not isinstance(payload, dict):
            raise ConnectorError(f"Unexpected Example ATS payload for '{slug}'")

        jobs: list[RawJob] = []
        for item in payload.get("postings") or []:
            html_body = item.get("descriptionHtml") or ""
            jobs.append(
                RawJob(
                    external_id=f"{slug}:{item.get('id')}",
                    title=(item.get("title") or "").strip(),
                    company=payload.get("companyName") or spec.display_name or slug,
                    source_url=item.get("url") or "",
                    apply_url=item.get("applyUrl") or item.get("url") or "",
                    description_html=html_body,
                    description_text=strip_html(html_body),
                    location_raw=item.get("location") or "",
                    department=item.get("team") or "",
                    employment_type=item.get("employmentType") or "",
                    posted_at=parse_datetime(item.get("publishedAt")),
                    remote_flag=item.get("isRemote"),
                    is_direct_employer=True,
                    raw={"id": item.get("id"), "board": slug},
                )
            )
        return FetchResult(jobs=jobs, etag=new_etag or etag)
```

Notes that matter:

- Salary, seniority, work arrangement, skills and the dedupe hash are **not** your job.
  `services/normalizer.py` derives all of them. Only set `salary_*` on `RawJob` when the
  API states them explicitly.
- Raise `ConnectorError` for anything recoverable (bad payload, HTTP 5xx). Raise
  `BlockedByPolicyError` for anything you must not retry (missing credentials, robots
  disallow, a bot wall). `PoliteClient` already raises the right one for HTTP-level cases.
- Return `FetchResult(jobs=[], etag=etag, notes=["not modified"])` on a 304 so the run is
  recorded as a success with nothing new.

### 3. Import it for registration

`backend/app/connectors/__init__.py`:

```python
from app.connectors import (  # noqa: F401  (import for side-effect registration)
    ashby,
    careers_page,
    exampleats,      # <-- add here, alphabetically
    feeds,
    greenhouse,
    lever,
    partner,
    smartrecruiters,
    workable,
)
```

A connector that is not imported here does not exist: it will not appear in
`GET /api/v1/connectors`, and `POST /api/v1/sources` will reject its key.

### 4. Add a respx-mocked test

`backend/tests/unit/test_connectors.py`, alongside the existing ones:

```python
import httpx
import respx

from app.connectors import PoliteClient, SourceSpec
from app.connectors.exampleats import BASE, ExampleATSConnector
from app.core.config import settings


@respx.mock
def test_exampleats_maps_a_posting():
    respx.get(f"{BASE}/examplecorp/postings").mock(
        return_value=httpx.Response(
            200,
            json={
                "companyName": "Example Corp",
                "postings": [
                    {
                        "id": "abc123",
                        "title": "Senior Backend Engineer",
                        "url": "https://jobs.exampleats.com/examplecorp/abc123",
                        "applyUrl": "https://jobs.exampleats.com/examplecorp/abc123/apply",
                        "descriptionHtml": "<p>Python and PostgreSQL.</p>",
                        "location": "Remote - US",
                        "publishedAt": "2026-08-20T10:00:00Z",
                        "isRemote": True,
                    }
                ],
            },
            headers={"ETag": "v1"},
        )
    )
    connector = ExampleATSConnector(PoliteClient(), settings=settings)
    result = connector.fetch(SourceSpec(connector_key="exampleats", identifier="examplecorp"))

    assert result.etag == "v1"
    assert len(result.jobs) == 1
    job = result.jobs[0]
    assert job.external_id == "examplecorp:abc123"
    assert job.company == "Example Corp"
    assert job.description_text == "Python and PostgreSQL."
    assert job.remote_flag is True


@respx.mock
def test_exampleats_returns_nothing_on_304():
    respx.get(f"{BASE}/examplecorp/postings").mock(return_value=httpx.Response(304))
    connector = ExampleATSConnector(PoliteClient(), settings=settings)
    result = connector.fetch(
        SourceSpec(connector_key="exampleats", identifier="examplecorp"), etag="v1"
    )
    assert result.jobs == []
    assert result.etag == "v1"
```

The compliance suite (`backend/tests/unit/test_compliance.py`) will then automatically
assert, for your new connector along with every other, that it declares a tier, that it
does not default to automated submission, and that partner connectors stay unavailable
without credentials. Run `pytest -q` and the count should go up, not the failure count.

### 5. Nothing else

There is no separate registry file, no migration and no UI change. `GET /api/v1/connectors`
renders whatever the registry holds, including your `policy_note`, `identifier_label` and
`identifier_help`, and the Sources page uses those strings verbatim to ask the user for
the right identifier.
