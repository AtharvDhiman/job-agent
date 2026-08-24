# Compliance & Automation Policy

This document is **normative**. The code in `backend/app/connectors/` and
`browser-assistant/` enforces it mechanically; this file explains why.

Two independent questions are asked about every platform:

1. **Discovery** - may we *read* job postings from it, and how?
2. **Submission** - may we *write* an application to it with a browser?

A platform can be green for discovery and red for submission. Most are.

---

## 1. Discovery tiers

Declared per connector as `ComplianceTier` in `backend/app/connectors/base.py`.

| Tier | Meaning | Enforcement |
|---|---|---|
| `PUBLIC_JOB_API` | The vendor publishes a documented, unauthenticated JSON job-board endpoint intended for public consumption (embedding a careers page, aggregating your own board). | Allowed by default. Rate-limited, cached, User-Agent identifies us. |
| `PARTNER_API` | A real API exists but requires **your own** credentials under **your own** agreement with the vendor. | Connector is disabled and invisible until you supply a token in `.env`. We never ship credentials and never scrape as a substitute. |
| `PUBLIC_FEED` | RSS/Atom/JSON feed the publisher offers for syndication. | Allowed. `robots.txt` checked, conditional requests via ETag / If-Modified-Since. |
| `CAREERS_PAGE` | A company's own careers page, fetched politely, `robots.txt` obeyed, only structured data (JobPosting JSON-LD / meta tags) parsed. | Allowed for pages that permit it. Any Disallow rule, login wall, or bot-check aborts the connector and records `blocked_by_policy`. |
| `MANUAL_ONLY` | Automated reading is not permitted or not technically honest. | No fetching at all. You paste a URL; we store what you paste. |

### Connector register (as shipped)

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
| **Adzuna** | `PARTNER_API` | Offers a free, documented developer API: you register for your own app id and key. | `REVIEW_REQUIRED` (results link out to employers). |

`USAJOBS_API_KEY` and `USAJOBS_USER_AGENT` exist in `.env.example` but **no
USAJOBS connector ships in this repository**; they are reserved and currently
unused. The table above lists what is registered, and nothing else.

Adding a platform means adding a connector class with an explicit tier. There is
no "default allow" path: `ConnectorRegistry` refuses to register a connector
that does not declare one, refuses a key it cannot normalise, and refuses to
register `linkedin` or `indeed` with anything other than `PROHIBITED`.

The policy layer does not trust a stored row either. `submission_policy_default`
on a `jobs` row is a snapshot taken when the posting was ingested;
`services/policy.decide()` re-reads the registry and applies whichever of the two
is stricter, so a connector that is pinned to `PROHIBITED` later still wins over
rows created before. A `connector_key` the registry does not recognise at all is
treated as `PROHIBITED`.

---

## 2. Submission policy

Declared as `SubmissionPolicy` and stored per platform in the
`platform_authorizations` table.

| Policy | Behaviour |
|---|---|
| `PROHIBITED` | Never automated, regardless of settings. The UI cannot enable it. LinkedIn and Indeed are pinned here. |
| `REVIEW_REQUIRED` | **The default for every platform.** The agent drafts everything, opens a review task, and waits for you. |
| `ASSISTED_AUTOFILL` | Requires an explicit, typed authorization. The local browser assistant fills the form in a **visible browser on your machine** and stops at the submit button for your click. |
| `AUTO_SUBMIT` | Requires an explicit, typed authorization AND a match score at or above your threshold AND every pre-flight check passing. The assistant clicks submit. |

Enabling `ASSISTED_AUTOFILL` or `AUTO_SUBMIT` requires a POST containing the
literal acknowledgement string

```
I have read and accept this platform's terms and authorize automated submission
```

together with your user id and the platform key. The grant is stored with a
timestamp, the acknowledgement text and an audit entry. It can be revoked
instantly and is ignored while the global kill-switch is off.

### Approving a review is not an authorization

Pressing "approve" on a review task is how you close it, and for a `PROHIBITED`
platform that is all it is: an acknowledgement that you will apply yourself.
`services/policy.may_hand_out()` is the only thing that decides whether the
browser assistant may be given an application, and an approval clears only the
reasons in `HUMAN_APPROVAL_MAY_LIFT` -- a score under **your** threshold, **your**
daily cap, a pre-flight warning you have now read. It never clears a prohibited
platform, a platform you never granted, a paused kill-switch, or content the fact
guard flagged. Opening a page and typing into it is automation whether or not the
submit button is ever clicked, so LinkedIn and Indeed are refused there too --
and again in `browser-assistant/src/core/guards.ts`, by connector key *and* by
hostname, so a wrong answer from the server cannot reach the browser.

An attempt that ended on a hard stop is never put back in the queue: approving
its review resolves the review and leaves the application `blocked_by_policy`.
Nor is an application re-queued while an attempt is still open on it, or after it
has been submitted. The same application is handed out at most
`MAX_ATTEMPTS_PER_APPLICATION` (3) times, after which it is retired with a review
task; the hand-out itself is a conditional UPDATE, so two assistants polling at
once cannot both claim it.

After an assisted-autofill hand-off you tell the system what happened with
`POST /api/v1/applications/{id}/mark-submitted`. The attempt is recorded as
`submitted_by_human` and the audit entry names you: the agent never claims a
submission a person made, in either direction.

---

## 3. Hard stops (non-negotiable, enforced in code)

`browser-assistant/src/core/guards.ts` and
`backend/app/services/application_workflow.py` abort -- and create a review task
instead -- when any of these is true:

1. **CAPTCHA or bot-check detected** (reCAPTCHA, hCaptcha, Turnstile, DataDome,
   PerimeterX, Cloudflare interstitial, Kasada, Akamai sensor). We never solve,
   never outsource, never evade.
2. **Login or account creation is required.** The agent has no credentials and
   never asks for them.
3. **Paywall or gated content.**
4. **`robots.txt` disallows the path** for our user-agent.
5. **A question we cannot answer from a verified career fact** -- any free-text,
   ambiguous, or unmapped question.
6. **A question whose truthful answer is unknown** -- visa status, salary
   history, protected characteristics. These are always surfaced to you; EEO
   fields default to "prefer not to say" and are never guessed.
7. **The platform's ToS forbids automation**, or the page shows an
   anti-automation notice.
8. **A required attachment is missing**, or a field the validator cannot satisfy.
9. **The global kill-switch is off**, the daily limit is reached, or the score is
   under threshold.

We do not ship, and will not add: CAPTCHA solvers, residential-proxy rotation,
fingerprint spoofing, `navigator.webdriver` patching, human-behaviour emulation
intended to defeat detection, or credential stuffing. The browser assistant runs
headed by default and identifies itself in its User-Agent.

---

## 4. Truthfulness guarantees

* Every answer, resume bullet and cover-letter claim is generated **only** from
  rows in `career_facts`, which you enter and mark verified.
* `backend/app/services/fact_guard.py` re-checks generated text against the fact
  store. Employers, titles, dates, degrees, certifications, numeric claims and
  links that do not appear in verified facts are flagged. A flagged document
  cannot be auto-submitted; it goes to review with the offending spans listed.
* The LLM prompt forbids invention and instructs the model to emit
  `INSUFFICIENT_FACTS` rather than guess. That token short-circuits to review.
* Work authorization, visa status, salary history and expectation, disability,
  veteran status, race/ethnicity and references are **never** inferred. They come
  from explicit profile fields or they go to review.
* Portfolio and profile links are echoed verbatim from your profile. The
  generator cannot mint a URL.

---

## 5. Data protection

* Sensitive columns (phone, address, work-authorization notes, answer values,
  partner tokens) use Fernet envelope encryption at rest via the
  `EncryptedString` / `EncryptedJSON` SQLAlchemy types.
* Passwords: Argon2id. Never reversible, never logged.
* `GET /api/v1/privacy/export` returns everything we hold about you as JSON plus
  the stored files. `POST /api/v1/privacy/erase` hard-deletes it. Audit entries
  are anonymised rather than destroyed, so the chain stays verifiable.
* The audit log is append-only: no update or delete route exists, rows are
  hash-chained (`prev_hash` -> `entry_hash`) so tampering is detectable, and
  `GET /api/v1/audit/verify` re-walks the chain on demand.
