# Build phases: what exists, and what is still yours to do

The project was specified as five build phases. This document maps each phase onto what is
actually in this repository, with file paths as evidence, and then states plainly what a
real deployment still needs **from you**. Nothing below is aspirational: if a thing is not
in the tree, it says so.

Measured on this repository at the time of writing:

| Measure | Value |
|---|---|
| Backend tests passing | **151** (104 unit, 47 integration) |
| OpenAPI paths | **58** (56 under `/api/v1`, plus `/` and `/health`) |
| Database tables | **18**, one Alembic revision |
| Registered connectors | **11** |
| LLM required to run | **No.** The whole suite runs with `ANTHROPIC_API_KEY` empty |

---

## Phase 1 - Foundation: configuration, identity, data model, audit

**Status: complete.**

| Deliverable | Evidence |
|---|---|
| Single settings object, everything from the environment, no hard-coded secrets | `backend/app/core/config.py`, `.env.example` |
| Shared vocabulary as enums used by the DB, API and UI | `backend/app/core/enums.py` (16 enums) |
| Argon2id passwords, HS256 JWTs, single-use rotating refresh tokens, account lockout | `backend/app/core/security.py`, `backend/app/api/v1/auth.py` |
| Four-role RBAC (owner, operator, viewer, service) with dependency-level enforcement | `backend/app/core/security.py:Role`, `backend/app/api/deps.py` |
| Fernet envelope encryption with a key ring for sensitive columns | `backend/app/core/crypto.py`, `backend/app/db/types.py` |
| Full data model, 18 tables, portable across PostgreSQL and SQLite | `backend/app/models/*.py`, `backend/app/db/` |
| One initial migration including the PL/pgSQL append-only audit trigger | `backend/alembic/versions/0001_initial.py` |
| Append-only, hash-chained audit log plus on-demand verification | `backend/app/services/audit.py`, `backend/app/models/audit.py`, `GET /api/v1/audit/verify` |
| Structured JSON logging with request-id correlation and secret redaction | `backend/app/core/logging.py` |
| Rate limiting, Redis-backed with in-process fallback | `backend/app/core/ratelimit.py` |
| Production preflight that refuses to start on an unsafe configuration | `backend/app/main.py:_production_preflight` |
| Export and erase, with audit anonymisation rather than deletion | `backend/app/api/v1/privacy.py` |

Test evidence: `backend/tests/integration/test_api_flow.py` (16),
`backend/tests/integration/test_audit_and_privacy.py` (8).

**Not in this phase, on purpose:** no password reset flow, no email verification, no
multi-tenant isolation beyond per-user row scoping, no OAuth or SSO.

---

## Phase 2 - Discovery: connectors, polite fetching, normalisation, dedupe

**Status: complete, with one honest caveat about live traffic.**

| Deliverable | Evidence |
|---|---|
| Connector contract that refuses any connector without an explicit compliance declaration | `backend/app/connectors/base.py:ConnectorRegistry.register` |
| Five ATS connectors against documented public endpoints | `greenhouse.py`, `lever.py`, `ashby.py`, `smartrecruiters.py`, `workable.py` |
| RSS/Atom feed connector | `backend/app/connectors/feeds.py` |
| Careers-page connector reading only schema.org JobPosting JSON-LD | `backend/app/connectors/careers_page.py` |
| Partner-API connectors, inert without your own credentials | `backend/app/connectors/partner.py` (Adzuna, LinkedIn, Indeed) |
| Manual entry path for anything that blocks automation | `POST /api/v1/jobs/manual`, `manual` connector |
| Polite HTTP: identifying UA, per-host throttle, robots.txt, ETag, bounded retries, bot-wall and login-wall refusal | `backend/app/connectors/http.py` |
| Normalisation: conservative salary parsing, sponsorship detection, skill extraction, requirement bullets | `backend/app/services/normalizer.py`, `taxonomy.py`, `locations.py` |
| Deduplication across boards with canonical-record selection | `compute_dedupe_hash`, `discovery.upsert_job`, `Job.canonical_job_id` |
| Per-source run state: status, error, failure streak, ETag, permanent disable on a policy block | `backend/app/models/job.py:JobSourceSubscription`, `backend/app/services/discovery.py` |
| Scheduled sweeps | `backend/app/workers/celery_app.py` (`discover-and-score`), `tasks.py` |

Test evidence: `backend/tests/unit/test_connectors.py` (14),
`backend/tests/unit/test_normalizer.py` (18), `backend/tests/unit/test_compliance.py` (12).

**The caveat.** The connectors are real HTTP clients, but they are verified against
**respx-mocked responses**, not live boards. That proves the parsing, the 304 handling,
the error paths and the refusals are correct for the payload shapes as documented. It does
not prove a given vendor has not changed their payload since. Your first real discovery
run is the real test, which is why `last_status` and `last_error` are surfaced on every
source.

**Also honest:** `DISCOVERY_MAX_CONCURRENCY` is declared in settings but no code reads it;
sources are polled sequentially. And `USAJOBS_API_KEY` / `USAJOBS_USER_AGENT` are reserved
in `.env.example` but **no USAJOBS connector ships**, despite the register table in
`docs/COMPLIANCE.md` naming it alongside Adzuna.

---

## Phase 3 - Profile, career facts, and ranking

**Status: complete.**

| Deliverable | Evidence |
|---|---|
| Candidate profile with search preferences and encrypted sensitive fields | `backend/app/models/profile.py:CandidateProfile`, `backend/app/api/v1/profile.py` |
| `career_facts` as the single permitted source of application content, gated by a human `verified` flag | `backend/app/models/profile.py:CareerFact`, `POST /api/v1/facts/verify` |
| Resume ingestion (PDF, DOCX, TXT, MD) proposing facts, always unverified | `backend/app/services/resume_parser.py` |
| Editing a fact resets its verification | `backend/app/api/v1/profile.py:update_fact` |
| Deterministic 0-100 scoring: 10 hard filters, 8 weighted components summing to 100 | `backend/app/services/ranking.py` |
| TF-IDF semantic similarity with IDF learned from the scoring batch | `ranking.SemanticIndex` |
| A written explanation stored on every match, including for rejections | `ranking.build_explanation`, `job_matches.explanation` |
| Per-user match persistence, shortlist, dismissal, rescore | `backend/app/services/matching.py`, `backend/app/api/v1/jobs.py` |
| Filtered job browsing and a dashboard that shows the arithmetic and the rejection reasons | `GET /api/v1/jobs`, `GET /api/v1/dashboard` |

Test evidence: `backend/tests/unit/test_ranking.py` (18), including an assertion that the
weights sum to 100.

**Not in this phase:** no learning from your accept/reject history, no embedding model, no
per-user weight tuning. The score is deterministic and auditable by design; see
`docs/ARCHITECTURE.md` section 4 for the full component and hard-filter tables.

---

## Phase 4 - Generation, truthfulness, and the policy gate

**Status: complete.**

| Deliverable | Evidence |
|---|---|
| Tailored resume, always template-rendered from verified facts only | `backend/app/services/document_generator.py:generate_resume` |
| Tailored cover letter, LLM-assisted when configured, template otherwise | `document_generator.generate_cover_letter` |
| Tailoring is selection and phrasing, never addition | `select_facts`, `_order_skills_for_job`, `_pick_highlights` |
| Post-generation fact guard flagging unverified links, employers, credentials, dates, metrics and work-authorization claims | `backend/app/services/fact_guard.py` |
| `INSUFFICIENT_FACTS` token treated as a hard stop | `fact_guard.check`, `llm.TRUTHFULNESS_SYSTEM` |
| Screening answers with no guessing branch; escalation with a reason | `backend/app/services/answers.py` |
| EEO defaulting to prefer-not-to-say, salary history refused, references never invented | `answers.answer_question` |
| Optional LLM that cannot change a score, approve anything, or bypass the guard | `backend/app/services/llm.py` |
| Prompt-injection stance: job-description text is data, not instructions | `llm.TRUTHFULNESS_SYSTEM` rule 6 |
| The single policy gate, with a prohibited-platform short circuit and the granted-policy distinction | `backend/app/services/policy.py` |
| Draft, validate, gate, then queue or open a review task | `backend/app/services/application_workflow.py` |
| Review queue carrying the direct apply link, the prefilled draft and the reason | `backend/app/models/application.py:ReviewTask`, `backend/app/api/v1/reviews.py` |
| Typed per-platform authorization with a verbatim acknowledgement string | `backend/app/schemas/settings.py:AUTHORIZATION_ACKNOWLEDGEMENT`, `backend/app/api/v1/settings_router.py` |
| LinkedIn and Indeed refused at the grant endpoint with HTTP 403 | `settings_router.grant_authorization`, `policy.HARD_PROHIBITED_PLATFORMS` |

Test evidence: `backend/tests/unit/test_fact_guard.py` (13),
`backend/tests/unit/test_answers.py` (15), `backend/tests/unit/test_policy.py` (14),
`backend/tests/integration/test_application_flow.py` (10).

---

## Phase 5 - Submission, interface, and operations

**Status: server side complete; the two client applications are being finished, and the
infrastructure files land alongside this document.**

| Deliverable | Status | Evidence |
|---|---|---|
| Browser-assistant API contract: task hand-out, question resolution, result reporting | **Complete** | `backend/app/api/v1/assistant.py`, 13 integration tests in `test_assistant_flow.py` |
| Policy re-evaluated at hand-out time, not just at drafting | **Complete** | `assistant._fresh_policy` |
| Guard rules shipped to the client (abort on CAPTCHA, login wall, bot protection, unknown or free-text question; headless forbidden; never solve a CAPTCHA; never spoof a fingerprint) | **Complete** | `assistant.GUARD_RULES` |
| Abort reasons mapped to review reasons, every failure becoming a review task and never a retry loop | **Complete** | `assistant.ASSISTANT_ABORT_REASONS`, `workflow.record_failure` |
| Submission attempts recorded permanently, with screenshots | **Complete** | `models/application.py:SubmissionAttempt`, `assistant._store_screenshot` |
| Daily limit enforced atomically | **Complete** | `models/audit.py:DailyCounter`, `workflow.bump_applications_today` |
| Kill-switch, pausable by anyone, resumable only by an owner | **Complete** | `POST /api/v1/settings/pause`, `/resume` |
| Notifications, digests, dashboard | **Complete** | `services/notifications.py`, `api/v1/notifications.py`, `api/v1/dashboard.py` |
| Celery worker and beat, 5 scheduled tasks | **Implemented, not covered by the automated tests** | `workers/celery_app.py`, `workers/tasks.py` |
| Browser assistant client (Node + Playwright, local and headed) | **In progress** | `browser-assistant/package.json`, `browser-assistant/src/` |
| Next.js frontend | **In progress** | `frontend/src/` |
| Docker, Compose, Makefile, nginx and systemd units | **Landing alongside this document** | repo root, `infra/` |

---

## What a real deployment still needs from you

None of this can be shipped in a repository. All of it is deliberate.

### 1. Your own resume, and verified facts

Seeding creates a profile full of `[PLACEHOLDER]` values and three **unverified**
placeholder career facts (`backend/seed/seed.py:PROFILE`, `PLACEHOLDER_FACTS`). Until you
replace them, upload a real resume and mark each extracted fact verified, the generators
have nothing to work with: unverified facts are invisible, not merely down-weighted.
Record your work-authorization status explicitly; it is the one thing the agent will never
infer, and leaving it blank sends every application with a work-authorization question to
your review queue.

### 2. Your own board identifiers

Seeding creates three example sources, all **disabled**, pointing at
`EXAMPLE_BOARD_TOKEN`, `EXAMPLE_SITE` and `EXAMPLE_BOARD`
(`backend/seed/seed.py:EXAMPLE_SOURCES`). Nobody can guess which companies you want to
work for. Add the Greenhouse board tokens, Lever site names, Ashby board names,
SmartRecruiters company identifiers, Workable subdomains, careers-page URLs or RSS feeds
that matter to you, and enable them. `GET /api/v1/connectors` tells you exactly what
identifier each connector wants.

### 3. Your own partner API keys, if you want those sources at all

`ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are a free developer registration and are the only
partner credentials that produce a working connector today. `LINKEDIN_PARTNER_API_TOKEN`
and `INDEED_PUBLISHER_API_TOKEN` make those connectors visible, but they still refuse to
run because no partner endpoint is wired up: the contract differs per agreement tier, so
you would have to point the connector at the exact endpoint your own agreement grants.
`USAJOBS_API_KEY` currently has no connector to configure. Nothing here ever scrapes as a
substitute for a credential you do not hold.

### 4. Your explicit decision to authorize any platform

This is the one that cannot be delegated, and the reason the product exists in this shape.

- `AUTOMATION_GLOBAL_ENABLED` is `false` in `.env.example` and in `Settings`.
- Every new user's `agent_settings.automation_enabled` is `false`
  (`api/v1/auth.py:register`, `seed/seed.py`).
- Every platform starts at `review_required`. There is no platform that is automated by
  default, and `test_compliance.py` asserts it.
- Granting automation is an owner-only POST that must carry, verbatim:

  ```
  I have read and accept this platform's terms and authorize automated submission
  ```

  The grant is stored with a timestamp, the encrypted acknowledgement text and an audit
  entry, and can be revoked instantly.
- LinkedIn and Indeed will refuse the grant with HTTP 403, always.

Until you do that, the agent will find jobs, score them, explain the scores, draft
documents and answers, check them for truthfulness, and hand you a review queue with the
apply link and the prefilled draft. That is a complete and useful product on its own, and
it is the mode the authors expect most people to stay in.

### 5. Operational ownership

Your database backups, your `ENCRYPTION_KEY` stored separately from those backups (see
`docs/DEPLOYMENT.md` section 6), your TLS, your alerting on the four signals in
`docs/DEPLOYMENT.md` section 9, and your judgement about volume. The daily limit defaults
to 10 for a reason.

And the part no configuration covers: you are responsible for every application sent in
your name. Read what goes out.
