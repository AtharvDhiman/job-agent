# AI Job Application Agent

A self-hosted job-search agent for one person: yourself. It polls the job boards you
configure, normalises and de-duplicates the postings, scores each one against your
profile with a deterministic 0-100 model that shows its arithmetic, drafts a tailored
resume and cover letter built only from career facts you have personally verified,
answers screening questions only where a verified fact supplies the answer, and then
stops. Whether anything is ever submitted on your behalf is a separate, explicit,
per-platform decision that you make by typing an acknowledgement sentence. Out of the
box the agent submits nothing at all.

## What this will NOT do

These are product rules, enforced in code and covered by tests, not aspirations.

- **It will not fabricate anything about you.** Qualifications, employers, dates,
  degrees, certifications, metrics, visa or work-authorization status, salary history,
  salary expectations, portfolio links and references come from rows in `career_facts`
  that you marked verified, or they do not appear at all. Generated text is re-checked
  against that fact store by `backend/app/services/fact_guard.py` before it can be used.
- **It will not bypass CAPTCHAs, bot detection, login walls, paywalls, platform rules or
  Terms of Service.** There is no CAPTCHA solver, no proxy rotation, no fingerprint
  spoofing, no `navigator.webdriver` patching. Detecting a bot-check is a full stop, not
  a puzzle to solve.
- **Submission is review-first.** Every platform starts at `review_required`. Automation
  requires an explicit, typed, per-platform grant plus a global kill-switch that is off
  by default.
- **LinkedIn and Indeed can never be authorized for automated submission.** They are
  pinned to `PROHIBITED` in `backend/app/services/policy.py` and the grant endpoint
  refuses them with HTTP 403. Matched roles there always become review tasks with a link
  you click yourself.
- **When blocked or uncertain, it stops and asks you.** Every dead end becomes a review
  task carrying the direct apply URL, the prefilled draft, and the reason it stopped.

`docs/COMPLIANCE.md` is the normative statement of all of this. Read it before adding a
connector or granting an authorization.

---

## What actually works today

Measured on this repository: **289 backend tests pass**, plus
**18 frontend tests** and **81 browser-assistant tests** (9 of them driving real
Chromium), for 388 in total. The FastAPI app
exposes **64 OpenAPI paths**. `ruff check` and `ruff format --check` are clean, the frontend
typechecks and builds with zero errors, and the initial migration matches the ORM metadata
exactly (18 tables, 292 columns, 43 uniquely named indexes).

| Component | Status | Where the code is |
|---|---|---|
| REST API (auth, profile, facts, documents, jobs, applications, reviews, settings, dashboard, notifications, audit, privacy, assistant) | Working, 64 paths, integration-tested | `backend/app/api/v1/*.py`, assembled in `backend/app/api/v1/__init__.py` |
| Discovery connectors: Greenhouse, Lever, Ashby, SmartRecruiters, Workable | Real HTTP clients against the vendors' documented public job-board endpoints. Tested against **mocked responses via respx**, not live calls. Correctness against a live board depends on the vendor's current payload. | `backend/app/connectors/greenhouse.py`, `lever.py`, `ashby.py`, `smartrecruiters.py`, `workable.py` |
| RSS / Atom feed connector | Working, mocked-response tests | `backend/app/connectors/feeds.py` |
| Careers-page connector | Working, reads **only** schema.org JobPosting JSON-LD, aborts on robots.txt Disallow, bot wall or login wall. Refuses pages that publish no structured data rather than scraping layout. | `backend/app/connectors/careers_page.py` |
| Adzuna connector | Implemented, inert until you supply `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` (your own free developer registration) | `backend/app/connectors/partner.py` |
| LinkedIn and Indeed connectors | Deliberately refuse to fetch. Without your own partner credentials they raise `BlockedByPolicyError` with an explanation; with credentials they raise `ConnectorError` because no partner endpoint is wired up, since the contract differs per agreement. Submission is `PROHIBITED` and cannot be enabled. | `backend/app/connectors/partner.py` |
| Manual job entry | Working (`POST /api/v1/jobs/manual`). The `manual` connector never fetches. | `backend/app/api/v1/jobs.py`, `backend/app/connectors/partner.py` |
| Polite HTTP layer: identifying User-Agent, per-host rate limit, robots.txt, ETag conditional requests, bounded retries, bot-wall and login-wall refusal | Working, unit-tested | `backend/app/connectors/http.py` |
| Normalisation, salary parsing, sponsorship detection, skill extraction, dedupe hashing | Working, pure functions, 18 unit tests. The salary parser is deliberately conservative: it returns nothing rather than a guess, so "Founded 2019-2024" and "300-500 customers" are not read as pay. | `backend/app/services/normalizer.py`, `taxonomy.py`, `locations.py` |
| Ranking: hard filters plus 8 weighted components plus a written explanation | Working, deterministic, no network, 18 unit tests | `backend/app/services/ranking.py` |
| Document generation (resume, cover letter) | Working. Resumes are **always** template-rendered; no model writes your work history. Cover letters use the LLM only when configured, and fall back to the template if the model output trips the fact guard. | `backend/app/services/document_generator.py` |
| Fact guard | Working, 35 unit tests. Flags unverified links (including scheme-less ones), employers (with or without a preposition), credentials, dates, numeric and worded metrics, job titles, salary claims, references and work-authorization claims. 20 of those tests are a matrix of known bypass attempts in `test_fact_guard_bypasses.py`, each of which must stay blocked. | `backend/app/services/fact_guard.py` |
| Screening answers | Working, 15 unit tests. Has no "best guess" branch: unmapped questions escalate. | `backend/app/services/answers.py` |
| Policy gate | Working, 28 unit tests. The single place that may conclude automation is permitted. | `backend/app/services/policy.py` |
| LLM layer | **Optional.** With no `ANTHROPIC_API_KEY` the entire app runs in deterministic template mode: templated documents, lexical and TF-IDF matching, rule-based answers. Nothing is disabled. The test suite runs with the key blank. The LLM can never change a score, approve an application, or bypass the fact guard. | `backend/app/services/llm.py` |
| Append-only hash-chained audit log plus `GET /api/v1/audit/verify` | Working, integration-tested. On PostgreSQL a PL/pgSQL trigger enforces append-only at the database level. | `backend/app/services/audit.py`, `backend/app/models/audit.py`, `backend/alembic/versions/0001_initial.py` |
| Export and erase | Working, integration-tested. Erase deletes rows and files and anonymises audit entries so the chain stays verifiable. | `backend/app/api/v1/privacy.py` |
| Celery worker and beat (5 scheduled tasks) | Implemented; requires a running Redis and worker to exercise. Not covered by the automated tests. | `backend/app/workers/celery_app.py`, `tasks.py` |
| Browser assistant server contract (`/api/v1/assistant/*`) | Working, 26 integration tests. Shared-secret auth, task hand-out re-runs the policy gate, abort reasons map to review reasons. | `backend/app/api/v1/assistant.py` |
| Browser assistant client | Node + Playwright project at `browser-assistant/`. It runs **locally on your own machine and headed** (a visible browser window); headless operation is refused at startup, not merely discouraged. 81 tests: 72 browser-free ones covering the CAPTCHA, login-wall and robots.txt guards, field matching and question mapping, plus **9 live-DOM tests driven against real Chromium** which prove the guards find a reCAPTCHA, Turnstile, DataDome and login wall in an actual document, report nothing on a clean form, read a real form's fields and types, and fill it through six different locator strategies without ever writing a value the server did not supply. The live tests need `npx playwright install chromium` and skip cleanly without it. | `browser-assistant/` |
| Next.js frontend | Working. 18 routes, App Router, httpOnly-cookie sessions via a server-side proxy so the browser never holds a token. Typechecks and builds with zero errors; 18 unit tests cover the formatting helpers and the proxy path-safety guard. | `frontend/src/` |
| Autopilot | Working, 12 integration tests. POST /autopilot/run chains discovery -> scoring -> drafting in one call (stale scores rebuild automatically when the profile or its facts change); GET /autopilot/status is the readiness checklist the UI renders. A resume upload auto-fills EMPTY profile fields (skills, target titles, links, phone, seniority) - it never overwrites what a human typed, and bracketed seed placeholders like [SKILL 1] count as empty. Deliberately never auto-filled: years of experience, salary, sponsorship, work authorization. Facts still require one human confirmation pass; nothing auto-verifies. | `backend/app/services/autopilot.py`, `backend/app/api/v1/autopilot.py`, `frontend/src/app/(app)/profile/AutopilotSection.tsx` |
| Company board finder + source catalog | Working, 12 tests. POST /sources/find probes the five documented public job-board APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable) for a typed company name and offers one-click adds; it never fetches arbitrary URLs or HTML. GET /sources/catalog is a small curated list of legitimately syndicated feeds. | `backend/app/services/board_finder.py`, `backend/app/connectors/catalog.py`, `backend/app/api/v1/source_tools.py` |
| Companies view | Working, 13 integration tests. Rolls every ingested posting up by employer with job counts, countries, work arrangements, source connectors, your best match score and how many times you have applied there. Cosmetic name variants collapse ("Acme, Inc." and "ACME Inc" are one employer) and a role found on two boards is counted once. Job counts are shared; scores and application counts are per-user. | `backend/app/api/v1/jobs.py` (`GET /companies`), `frontend/src/app/(app)/companies/` |
| Infrastructure files (Dockerfiles, `docker-compose.yml`, `Makefile`, `infra/nginx.conf`, `infra/systemd/*`) | Written. The compose file was validated as YAML with its anchors resolved (6 services) and the CI workflow parses (3 jobs), but **docker and make are not installed in the environment this was built in**, so `docker compose config`, an actual image build and `make -n` have never been run. Treat them as reviewed, not as proven. | repo root and `infra/` |

---

## Quick start

### Path A: Docker Compose

```bash
cp .env.example .env
make secrets            # prints SECRET_KEY, ENCRYPTION_KEY and BROWSER_ASSISTANT_TOKEN
# paste those three values into .env, and set POSTGRES_PASSWORD and DATABASE_URL
docker compose up -d
make migrate            # alembic upgrade head
make seed               # creates the owner account and placeholder profile
# open http://localhost:3000
```

Log in with the seeded credentials below, then work through the first-run checklist.

### Path B: Local, without Docker

You need PostgreSQL 15+ and Redis running locally, plus Python 3.11+ and Node 20+.

```bash
# 1. Database and cache (however you run them locally)
createdb jobagent

# 2. Environment
cp .env.example .env
# generate the three secrets (see "Generating secrets" below) and paste them in
# set DATABASE_URL, e.g.
#   DATABASE_URL=postgresql+psycopg://jobagent:yourpassword@localhost:5432/jobagent

# 3. Backend
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt  # use requirements.txt for a runtime-only install
alembic upgrade head
python -m seed.seed
uvicorn app.main:app --reload --port 8000

# 4. Worker and scheduler, in two more terminals, from backend/ with the venv active
celery -A app.workers.celery_app.celery_app worker --loglevel=info
celery -A app.workers.celery_app.celery_app beat --loglevel=info

# 5. Frontend, in another terminal
cd frontend
npm install
npm run dev                          # http://localhost:3000

# 6. Browser assistant, only if and when you authorize a platform
cd browser-assistant
npm install
npm start
```

The backend serves interactive docs at `http://localhost:8000/docs` outside production.

**Seeded owner credentials** (from `backend/seed/seed.py`):

```
email:    owner@example.com
password: ChangeMe-Str0ng!Pass
```

**Change that password immediately.** It is printed in the source of this repository, so
it is public. Register a fresh account or change it before the instance is reachable by
anything but localhost. Note that `POST /api/v1/auth/register` gives the OWNER role only
to the very first account; every later account is created as a read-only VIEWER.

Seeding also creates three example job sources (`EXAMPLE_BOARD_TOKEN`, `EXAMPLE_SITE`,
`EXAMPLE_BOARD`) that are **disabled on purpose**, a profile full of `[PLACEHOLDER]`
values, three unverified placeholder career facts, and two offline sample jobs so the
dashboard is not empty. None of that is usable until you replace it.

---

## Generating secrets

Run these three one-liners and paste the output into `.env`. Never reuse the values
across environments and never commit `.env`.

```bash
# SECRET_KEY - signs JWTs. Any value under 32 chars, or one starting with "dev-only",
# is rejected outright when APP_ENV is staging or production.
python -c "import secrets; print(secrets.token_urlsafe(64))"

# ENCRYPTION_KEY - Fernet key for encrypted columns. Must be a valid Fernet key.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# BROWSER_ASSISTANT_TOKEN - shared secret between the local browser assistant and the
# narrow /api/v1/assistant surface.
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

In development only, a missing `ENCRYPTION_KEY` is replaced by a key derived from
`SECRET_KEY` (`backend/app/core/crypto.py`) so tests and local work need no setup. That
path is fatal when `APP_ENV` is `staging` or `production`.

---

## First-run checklist

Do these in order. Nothing useful happens until step 3 is done.

1. **Replace every profile placeholder.** Settings -> Profile, or `PUT /api/v1/profile`.
   Target titles, skills, preferred countries, work arrangement, employment types,
   seniority, minimum salary, sponsorship requirement. Hard filters and scoring read
   these directly, so an empty profile produces meaningless matches.
2. **Upload your resume.** `POST /api/v1/documents` with `kind=resume_source`. The parser
   extracts text and proposes career facts. Every proposed fact is stored with
   `verified=False`.
3. **Verify every career fact.** Facts page, or `POST /api/v1/facts/verify`. This is the
   gate the whole product rests on: an unverified fact can never reach a resume, a cover
   letter or an answer. Editing a verified fact silently un-verifies it, so re-confirm
   after any edit. Record your work-authorization fact here rather than leaving it to be
   guessed, because it never will be.
4. **Add a real board identifier under Sources and enable it.** For example a Greenhouse
   board token (the slug in `boards.greenhouse.io/<token>`), a Lever site name, or an
   Ashby board name. Delete or leave disabled the seeded `EXAMPLE_*` rows.
5. **Run discovery.** `POST /api/v1/discovery/run`, or wait for the beat schedule. This
   fetches, normalises, de-duplicates, upserts and then scores.
6. **Review the shortlist.** Dashboard or `GET /api/v1/jobs/shortlist`. Read the
   explanation on a few matches and check that the arithmetic matches your intent. Tune
   `shortlist_min_score`, excluded keywords and companies to avoid before going further.
7. **Only then consider granting a platform authorization.** Read
   `docs/COMPLIANCE.md` section 2, decide per platform, and type the acknowledgement
   sentence exactly. Start with `assisted_autofill`, which stops at the submit button, on
   a single platform, with automation resumed and the daily limit low. LinkedIn and
   Indeed will refuse.

---

## Architecture summary

```
                                 backend/app/connectors/*.py
  Greenhouse / Lever / Ashby / SmartRecruiters / Workable / RSS / careers page / Adzuna
                                          |
                                  PoliteClient (http.py)
                    UA, per-host rate limit, robots.txt, ETag, bot-wall refusal
                                          v
                          services/normalizer.py  -> Job rows
              salary parsing, sponsorship detection, skill extraction, dedupe hash
                                          v
                    services/discovery.py  upsert + duplicate linking
                                          v
                      services/ranking.py  hard filters + 8 components
                              services/matching.py persists JobMatch
                                          v
        services/document_generator.py  +  services/answers.py   (LLM optional)
                                          v
                          services/fact_guard.py  re-check every claim
                                          v
                            services/policy.decide()   THE GATE
                                          |
                 +------------------------+------------------------+
                 v                                                 v
      ReviewTask + Notification                      api/v1/assistant.py task
   (services/application_workflow.py)                browser-assistant, headed,
                                                     stops or clicks per policy
                 \                                                 /
                  +----------------> services/audit.py <----------+
                          append-only, hash-chained, verifiable
```

The modules named above are the real ones. `backend/app/services/policy.py` is the only
place in the codebase permitted to conclude that automation is allowed; the API, the
Celery tasks and the assistant endpoint all call it and obey the answer. See
`docs/ARCHITECTURE.md` for the module-by-module walkthrough, the full data model, the
scoring model, the policy decision table and the state machine.

---

## Configuration reference

Every variable in `.env.example`, read through `backend/app/core/config.py`.
"Secret" means: never commit it, never log it, rotate it if exposed.

### Core

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, `staging` or `production`. Anything but the first two enables the production preflight, disables `/docs` and `/openapi.json`, and adds HSTS. | No |
| `APP_NAME` | `AI Job Application Agent` | Title in the OpenAPI document and the UI. | No |
| `LOG_LEVEL` | `INFO` | Standard Python level name. | No |
| `LOG_FORMAT` | `json` | `json` for one object per line, `console` for readable local output. | No |
| `API_V1_PREFIX` | `/api/v1` | Mount point for the v1 router. | No |
| `FRONTEND_ORIGIN` | `http://localhost:3000` | Comma-separated CORS allow-list. Also the base URL used in notification emails. | No |

### Secrets and tokens

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `SECRET_KEY` | `dev-only-insecure-secret-key-change-me` | HS256 signing key for access and refresh JWTs. Validation rejects a value under 32 chars or starting with `dev-only` when `APP_ENV` is staging or production. | **Yes** |
| `ENCRYPTION_KEY` | empty | Primary Fernet key for encrypted columns. Required in staging and production; derived from `SECRET_KEY` in development only. | **Yes** |
| `ENCRYPTION_KEY_PREVIOUS` | empty | Comma-separated older Fernet keys, newest first. Old values keep decrypting during a rotation. | **Yes** |
| `ACCESS_TOKEN_TTL_MINUTES` | `30` | Access-token lifetime. | No |
| `REFRESH_TOKEN_TTL_DAYS` | `14` | Refresh-token lifetime; also the cookie max-age used by the frontend. | No |

### Database

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `POSTGRES_USER` | `jobagent` | Consumed by the Postgres container, not by the app. | No |
| `POSTGRES_PASSWORD` | `change-me-locally` | Same. | **Yes** |
| `POSTGRES_DB` | `jobagent` | Same. | No |
| `POSTGRES_HOST` | `localhost` | Same. | No |
| `POSTGRES_PORT` | `5432` | Same. | No |
| `DATABASE_URL` | `postgresql+psycopg://jobagent:change-me-locally@localhost:5432/jobagent` | The URL the app and Alembic actually use. Contains the password. SQLite is accepted for tests and refused in production. | **Yes** |

### Redis and queue

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Distributed rate-limit counters and the `/health` Redis probe. Blank disables both and falls back to in-process counting. | If it carries a password |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | Celery broker. | If it carries a password |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Celery results, expiring after 24h. | If it carries a password |

### Storage

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `STORAGE_BACKEND` | `local` | `local` or `s3`. | No |
| `STORAGE_LOCAL_PATH` | `./storage` | Content-addressed file root, resolved relative to the repository root. Back this up. | No |
| `S3_BUCKET` | empty | Required when `STORAGE_BACKEND=s3`. Objects are written with SSE-AES256. | No |
| `S3_REGION` | empty | Optional S3 region. | No |
| `S3_ENDPOINT_URL` | empty | Optional, for S3-compatible stores. AWS credentials come from the standard provider chain; prefer an IAM role. | No |

### LLM (optional)

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Blank means deterministic mode. Everything works; explanations and cover letters are template-generated. | **Yes** |
| `LLM_MODEL` | `claude-opus-5` | Model id used for generation. | No |
| `LLM_EFFORT` | `high` | Effort passed as `output_config.effort`. | No |
| `LLM_MAX_TOKENS` | `16000` | Default output cap. | No |
| `LLM_ENABLED` | `true` | Master switch; the LLM is considered configured only when this is true **and** a key is present. | No |

### Discovery

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `DISCOVERY_INTERVAL_MINUTES` | `180` | Celery beat cadence for the discovery sweep. Also the per-user default written by seeding and registration. | No |
| `DISCOVERY_HTTP_TIMEOUT_SECONDS` | `20` | httpx timeout for connector requests. | No |
| `DISCOVERY_USER_AGENT` | `JobAgent/1.0 (+personal job search; contact: you@example.com)` | Sent on every connector request and used for robots.txt matching. Put a real contact address in it. | No |
| `DISCOVERY_MAX_CONCURRENCY` | `4` | Declared in the settings object but **not currently read by any code**: discovery runs a user's sources sequentially through one client. | No |
| `DISCOVERY_PER_HOST_RPS` | `0.5` | Polite per-host request rate. Raising it is on you. | No |
| `RESPECT_ROBOTS_TXT` | `true` | Must stay true. Setting it false disables the robots.txt check in `PoliteClient` and puts you outside the policy this project documents. | No |

### Partner credentials (all optional, all yours)

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `LINKEDIN_PARTNER_API_TOKEN` | empty | Makes the LinkedIn connector visible. It still refuses to run without a partner endpoint wired to your agreement, and submission stays prohibited. | **Yes** |
| `INDEED_PUBLISHER_API_TOKEN` | empty | Same for Indeed. | **Yes** |
| `ADZUNA_APP_ID` | empty | Adzuna developer app id. Both Adzuna values must be set for the connector to run. | **Yes** |
| `ADZUNA_APP_KEY` | empty | Adzuna developer key. | **Yes** |
| `USAJOBS_API_KEY` | empty | Reserved. No USAJOBS connector ships in this repository, so this value is currently unused. | **Yes** |
| `USAJOBS_USER_AGENT` | empty | Reserved, currently unused. | No |

### Automation safety

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `AUTOMATION_GLOBAL_ENABLED` | `false` | Master kill-switch. While false, `GET /assistant/tasks/next` returns null and no application can be auto-submitted, whatever any grant says. | No |
| `AUTO_SUBMIT_MIN_SCORE` | `85` | Default score threshold copied into each user's `agent_settings` at creation. The per-user value is what the gate reads. | No |
| `DAILY_APPLICATION_LIMIT` | `10` | Default per-user daily submission cap, enforced through the `daily_counters` table. | No |
| `JOB_MAX_AGE_HOURS` | `48` | Default staleness hard filter. | No |
| `BROWSER_ASSISTANT_TOKEN` | `change-me-a-long-random-string` | Shared secret for `/api/v1/assistant/*`. Blank makes that surface return 503. Production refuses to start if automation is enabled without it. | **Yes** |

### Notifications

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `NOTIFY_EMAIL_ENABLED` | `false` | Global opt-in for email. In-app notifications always work. | No |
| `SMTP_HOST` | empty | Blank records "SMTP is not configured" on the notification row instead of failing the workflow. | No |
| `SMTP_PORT` | `587` | SMTP port. | No |
| `SMTP_USERNAME` | empty | Blank means no SMTP login attempt. | No |
| `SMTP_PASSWORD` | empty | SMTP password. Redacted in logs. | **Yes** |
| `SMTP_FROM` | `Job Agent <jobagent@example.com>` | From header. | No |
| `SMTP_STARTTLS` | `true` | Issue STARTTLS before sending. | No |
| `NOTIFY_DIGEST_HOUR_LOCAL` | `8` | Hour used by the `daily-digest` beat entry. | No |
| `NOTIFY_TIMEZONE` | `UTC` | Celery timezone. | No |

### Rate limiting

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Fixed-window limiter, Redis-backed when available, in-process otherwise. | No |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per client IP and path. | No |
| `RATE_LIMIT_AUTH_PER_MINUTE` | `10` | Tighter limit for `/login`, `/register` and `/refresh`. | No |

### Frontend

| Variable | Default | Purpose | Secret |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000/api/v1` | Backend base URL used by the Next.js server-side proxy. `API_INTERNAL_URL`, if set, takes precedence and is the right knob for container-to-container addressing. | No |

---

## Connectors and what is permitted

Reproduced from `docs/COMPLIANCE.md`, which is the normative document. If this table and
that one ever disagree, `docs/COMPLIANCE.md` wins.

| Platform | Discovery tier | Why | Submission |
|---|---|---|---|
| **Greenhouse** | `PUBLIC_JOB_API` | Documented public Job Board API (`boards-api.greenhouse.io/v1/boards/{token}/jobs`), no auth, intended for public boards. | `REVIEW_REQUIRED` by default; may be authorized (hosted form, no login, no CAPTCHA on most boards). |
| **Lever** | `PUBLIC_JOB_API` | Documented public postings API (`api.lever.co/v0/postings/{company}?mode=json`). | `REVIEW_REQUIRED` by default; may be authorized. |
| **Ashby** | `PUBLIC_JOB_API` | Public job-board API (`api.ashbyhq.com/posting-api/job-board/{name}`). | `REVIEW_REQUIRED` by default; may be authorized. |
| **SmartRecruiters** | `PUBLIC_JOB_API` | Public Posting API (`api.smartrecruiters.com/v1/companies/{id}/postings`). | `REVIEW_REQUIRED`. Their write API needs a partner key: use it if you have one, never fake one. |
| **Workable** | `PUBLIC_JOB_API` | Public account jobs endpoint (`apply.workable.com/api/v1/widget/accounts/{sub}`). | `REVIEW_REQUIRED`. |
| **Company careers page** | `CAREERS_PAGE` | Only with `robots.txt` permission plus structured data. | `REVIEW_REQUIRED` (forms are bespoke). |
| **RSS / Atom feeds** | `PUBLIC_FEED` | Published for syndication. | n/a (links out). |
| **LinkedIn** | `PARTNER_API` | Scraping LinkedIn violates its User Agreement. Only usable if **you** hold Talent Solutions / partner API access. Easy Apply automation is never permitted here. | `PROHIBITED`, hard-coded. Always a review task with a deep link you click yourself. |
| **Indeed** | `PARTNER_API` | Publisher / Employer API access is by agreement; the public site forbids automated access. | `PROHIBITED`, hard-coded. |
| **Adzuna / USAJOBS** | `PARTNER_API` | Both offer free, documented developer APIs: you register for a key. | `REVIEW_REQUIRED` (they link out to employers). |

Two notes on what actually ships, so the table is not read as more than it is. The
Adzuna connector is implemented; **no USAJOBS connector exists in this repository**,
though the environment reserves two variables for one. And the `manual` connector
(`MANUAL_ONLY`, `REVIEW_REQUIRED`) is registered so the UI can explain that pasted jobs
are never fetched automatically.

`GET /api/v1/connectors` returns this register live, including whether each connector is
currently available and, if not, exactly which credential it is waiting for.

---

## How the truthfulness mechanism works

In plain language, five things stack up:

1. **`career_facts` plus the `verified` flag.** Everything the agent can say about you is
   a row in that table. The resume parser proposes facts from an upload but writes every
   one with `verified=False`. A human sets `verified=True` through
   `POST /api/v1/facts/verify`. Editing a fact resets it to unverified, because the text
   you confirmed is no longer the text on file. Generators filter to verified rows before
   they start, so an unverified fact is not "low confidence", it is invisible.
2. **`fact_guard` re-checks the generated text.** After a resume or cover letter is
   rendered, whether by the template or by the model,
   `backend/app/services/fact_guard.py` reads the output back and flags every URL,
   employer name, credential, year, quantified claim and work-authorization statement
   that does not trace to a verified fact. Blocking flags make the document ineligible
   for auto-submission and the exact offending spans are shown in the review task.
3. **The `INSUFFICIENT_FACTS` token.** The LLM system prompt forbids invention and
   instructs the model to reply with exactly `INSUFFICIENT_FACTS` rather than guess. The
   guard treats that token as a blocking flag, so a model admitting it lacks facts routes
   the item to you instead of shipping a hedge. A cover letter whose model output trips
   the guard for any reason silently falls back to the deterministic template, and the
   rejection is recorded in the document's generation metadata.
4. **Answers escalate instead of guessing.** `backend/app/services/answers.py` maps a
   question to an intent and then reads a specific profile field or verified fact. There
   is no fallback branch that estimates. No location on file means no location answer. No
   LinkedIn URL means the question comes back to you rather than a plausible-looking URL
   being minted. Salary history is refused outright as sensitive and often unlawful to
   ask. Years of experience is never estimated. Long free-text questions are never
   auto-filled. Any required question that escalates blocks submission through the policy
   gate.
5. **EEO defaults to prefer-not-to-say.** Questions about gender, race, ethnicity,
   veteran status, disability or sexual orientation are answered with the form's own
   "prefer not to say" option when it offers one, and `Prefer not to say` otherwise, with
   a reason attached telling you to change it yourself if you wish to disclose. Protected
   characteristics are never inferred from anything.

---

## Testing

All commands run from `backend/` with the virtualenv active.

| Command | What it covers | Count |
|---|---|---|
| `pytest -q` | Everything | **151 passing** |
| `pytest tests/unit -q` | Pure logic, no database, no network | **104** |
| `pytest tests/integration -q` | FastAPI `TestClient` against a throwaway SQLite database | **47** |
| `pytest tests/unit/test_ranking.py -q` | Weights sum to 100, every hard filter, component arithmetic, explanation contents | 18 |
| `pytest tests/unit/test_normalizer.py -q` | Salary parsing, sponsorship detection, requirement bullets, dedupe hashing, staleness | 18 |
| `pytest tests/unit/test_answers.py -q` | Every question intent, plus the refusals (salary history, references, unknown questions, EEO) | 15 |
| `pytest tests/unit/test_connectors.py -q` | Each connector against respx-mocked HTTP: payload shapes, 304 handling, malformed payloads, bot walls, robots.txt | 14 |
| `pytest tests/unit/test_policy.py -q` | The decision table, including the prohibited short circuit and the granted-policy distinction | 14 |
| `pytest tests/unit/test_fact_guard.py -q` | Unverified links, employers, credentials, dates, metrics, work authorization, `INSUFFICIENT_FACTS` | 13 |
| `pytest tests/unit/test_compliance.py -q` | Registry refuses undeclared connectors, LinkedIn and Indeed can never auto-submit, no connector defaults to automation, partner connectors stay unavailable, bot and login walls abort, manual never fetches | 12 |
| `pytest tests/integration/test_api_flow.py -q` | Register, login, refresh rotation, profile, facts, documents, jobs, filters | 16 |
| `pytest tests/integration/test_assistant_flow.py -q` | Assistant auth, task hand-out under policy, question resolution, abort and submit reporting | 13 |
| `pytest tests/integration/test_application_flow.py -q` | Drafting, the review queue, the acknowledgement requirement, approve and reject | 10 |
| `pytest tests/integration/test_audit_and_privacy.py -q` | Hash-chain verification, export, erase confirmation, audit anonymisation | 8 |

Extras: `pytest --cov=app` for coverage, `ruff check .` and `mypy app` for linting and
types (both configured in `backend/pyproject.toml`). The frontend declares
`npm run typecheck`, `npm run lint` and `npm test`; the browser assistant declares
`npm run typecheck` and `npm test`.

The suite runs with `ANTHROPIC_API_KEY` empty, `AUTOMATION_GLOBAL_ENABLED=false` and no
Redis, against SQLite in a temp directory. It makes no live network calls: connector
tests use respx-mocked responses.

---

## Security and privacy

- **Passwords**: Argon2id via `argon2-cffi` (`time_cost=3`, `memory_cost=64 MiB`,
  `parallelism=4`), minimum 12 characters, transparently re-hashed on login when the
  parameters change. Eight failed logins lock the account for 15 minutes, and the error
  message never reveals whether an account exists.
- **Tokens**: HS256 JWTs. Access tokens are short-lived and stateless. Refresh tokens are
  persisted by `jti` and are **single-use**: refreshing revokes the presented token and
  issues a new pair, so a replayed refresh token is dead. Logout revokes every refresh
  token for the user, and a nightly task prunes expired rows.
- **Encryption at rest**: phone, address, work-authorization notes, screening answer
  values, submission confirmation numbers and stored authorization acknowledgements use
  Fernet through the `EncryptedString` / `EncryptedJSON` column types. `MultiFernet` gives
  a key ring: new writes use `ENCRYPTION_KEY`, reads try every key including
  `ENCRYPTION_KEY_PREVIOUS`.
- **RBAC**: four roles in `backend/app/core/security.py`. OWNER controls authorizations,
  resume, erasure and audit verification. OPERATOR can review, approve and edit. VIEWER
  is read-only. SERVICE covers the assistant and workers and is scoped to `/assistant/*`
  through a separate shared-secret dependency, not a user JWT. Anyone may hit the pause
  kill-switch; only an owner may resume.
- **Browser sessions**: the Next.js app keeps both tokens in httpOnly, SameSite=Lax
  cookies (secure in production) and never exposes them to client JavaScript. Browser
  calls go to `/api/proxy/...`, which attaches the Authorization header server-side and
  transparently rotates the refresh token once on a 401.
- **Rate limiting**: fixed-window per client IP and path, Redis-backed across workers when
  Redis is reachable and in-process otherwise, with a tighter limit on auth routes.
- **Audit log**: append-only and hash-chained. Each entry stores `prev_hash` and an
  `entry_hash` over position, timestamp, action, object, outcome and payload.
  `GET /api/v1/audit/verify` re-walks the chain and reports the first break by sequence
  number. On PostgreSQL a PL/pgSQL trigger installed by the initial migration rejects any
  DELETE and any UPDATE that touches a chained column. `actor`, `user_id` and
  `ip_address` are deliberately outside the hash so erasure can scrub identity without
  destroying verifiability.
- **Export and erase**: `GET /api/v1/privacy/export` returns everything held about you as
  a JSON download. `POST /api/v1/privacy/erase` requires the literal confirmation
  `DELETE MY DATA`, deletes rows and stored files, deactivates and anonymises the account,
  and anonymises rather than deletes audit entries so the chain stays verifiable.
- **Response headers**: `X-Request-ID` on every response for log correlation, plus
  `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive
  `Permissions-Policy`, and HSTS in production. Structured logs redact password, token,
  key, secret and phone fields.
- **Uploads**: 15 MB cap, content-type allow-list, executables rejected by magic bytes,
  content-addressed storage keys, path traversal refused by the local backend.

---

## Troubleshooting

**The UI says it cannot reach the API.**
The Next.js proxy returns a 503 naming the URL it tried. Check the backend is listening
(`curl http://localhost:8000/health`), and that `API_INTERNAL_URL` or
`NEXT_PUBLIC_API_BASE_URL` points at it. Inside Docker the browser and the Next.js server
resolve different hosts: the server-side value must be the container name, not
`localhost`. `/health` reports `status: degraded` when the database probe fails and names
the error under `checks`.

**Every read fails with "Could not decrypt a stored value".**
`ENCRYPTION_KEY` changed without a rotation. Put the old key into
`ENCRYPTION_KEY_PREVIOUS` (comma-separated, newest first) and restart; reads will succeed
again through the key ring. Do not "fix" this by wiping the column. If you lost the old
key entirely, the encrypted columns are unrecoverable and only those rows need
re-entering. See the rotation procedure in `docs/DEPLOYMENT.md`.

**Discovery runs but finds no jobs.**
The seeded sources are deliberately disabled and point at `EXAMPLE_BOARD_TOKEN`,
`EXAMPLE_SITE` and `EXAMPLE_BOARD`. Replace them with real identifiers and set
`enabled=true`. Then check `last_status` and `last_error` on each source:
`blocked_by_policy` means the connector refused for a compliance reason and disabled
itself permanently, five consecutive `error` runs disable a source automatically, and
`not modified` in the notes means the board returned 304 because nothing changed. Also
check that jobs are not being filtered out afterwards: `JOB_MAX_AGE_HOURS` defaults to 48,
so a quiet board can legitimately produce nothing new.

**Nothing is being submitted.**
That is the default, and usually several gates are closed at once. In order:
`AUTOMATION_GLOBAL_ENABLED` is false unless you set it; per-user `automation_enabled`
starts false; no platform authorization exists until you grant one; the score must reach
your `auto_submit_min_score` (default 85); the daily limit must not be reached; the fact
guard must be clean; every required question must be answered; pre-flight validation must
pass. The `policy` object on `POST /api/v1/applications/draft`, and the `rationale` on the
review task, list exactly which of these failed.

**LinkedIn or Indeed is unavailable.**
By design. Without your own partner token the connector refuses to fetch and tells you to
use the employer's ATS board or paste the job manually. With a token it still has no
partner endpoint wired up, because the contract differs per agreement tier. Automated
submission is prohibited for both and `POST /api/v1/settings/authorizations` returns 403.

**The backend refuses to start in production.**
`_production_preflight()` in `backend/app/main.py` raises with the specific reasons: a
missing `ENCRYPTION_KEY`, a weak or `dev-only` `SECRET_KEY`, a SQLite `DATABASE_URL`, or
automation enabled without a `BROWSER_ASSISTANT_TOKEN`. Fix the named item; the check is
not bypassable.

**`GET /api/v1/audit/verify` reports `valid: false`.**
Treat it as a security incident, not a bug. It names the sequence number where the chain
breaks and whether the mismatch is in `prev_hash` or the entry contents. Preserve the
database and investigate before writing anything else.

---

## Legal and ethical use

You are responsible for every application sent in your name. This tool drafts and, only
where you explicitly authorized it, submits; the words are assembled from facts you
confirmed and the decision to send remains yours. An employer receiving an application
from this agent is receiving one from you.

The conservatism here is deliberate, not incidental. Defaults that refuse are cheap to
override once, consciously, and expensive to undo after a hundred bad applications have
gone out. That is why automation is off, every platform starts at review-required, the
daily limit is small, unverified facts are invisible, and a bot-check is a full stop.

Do not use this to spam employers. High-volume, low-relevance applying wastes recruiters'
time, degrades the signal for every other applicant, and is the behaviour that gets
job-seeking tools blocked wholesale. Keep the shortlist threshold meaningful, keep the
daily limit low, and read what goes out. Respect each platform's terms, including the ones
this project cannot check for you, and do not remove the safety rails to reach a platform
that told you no.
