# Deployment

Running the AI Job Application Agent somewhere other than your laptop. Read
`docs/COMPLIANCE.md` first: nothing here changes what the agent is permitted to do, and
none of it is a reason to relax a safety default.

This document assumes a single-tenant deployment: one person, one instance. That is what
the product is. Multi-tenant hosting of a job-application agent raises consent and
liability questions this codebase does not attempt to answer.

---

## 1. Production checklist

`backend/app/main.py` runs `_production_preflight()` during application startup whenever
`APP_ENV` is `staging` or `production`, and **raises `RuntimeError` rather than serving
traffic** if anything fails. There is no override flag. The four checks, exactly as coded:

| Check | Failure message | How to satisfy it |
|---|---|---|
| `settings.encryption_key` is non-empty | `ENCRYPTION_KEY is required` | Generate a Fernet key and set it. The development fallback that derives a key from `SECRET_KEY` is disabled here on purpose. |
| `len(SECRET_KEY) >= 32` and it does not start with `dev-only` | `SECRET_KEY must be a strong unique value` | `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Note that `Settings` also validates this at construction time, so a weak value fails even earlier. |
| `DATABASE_URL` does not start with `sqlite` | `SQLite is not supported in production; use PostgreSQL` | Point it at PostgreSQL with the `postgresql+psycopg://` driver. |
| If `AUTOMATION_GLOBAL_ENABLED` is true, `BROWSER_ASSISTANT_TOKEN` is set | `BROWSER_ASSISTANT_TOKEN must be set when automation is enabled` | Either generate the token, or leave the kill-switch off. |

Beyond what the code enforces, before you expose the instance:

- [ ] `APP_ENV=production`. This also disables `/docs` and `/openapi.json` and adds
      `Strict-Transport-Security` to every response.
- [ ] `FRONTEND_ORIGIN` lists exactly your real origin, comma-separated if there is more
      than one. It is the CORS allow-list, and it is also the base URL written into
      notification emails.
- [ ] `AUTOMATION_GLOBAL_ENABLED=false` for the first deployment. Turn it on later, on
      purpose, after you have watched a few review cycles.
- [ ] `LOG_FORMAT=json`, `LOG_LEVEL=INFO`.
- [ ] `RATE_LIMIT_ENABLED=true` with Redis reachable, so limits hold across processes
      rather than per worker.
- [ ] `DISCOVERY_USER_AGENT` contains a real contact address. Board operators use it to
      reach you instead of blocking you.
- [ ] `RESPECT_ROBOTS_TXT=true`. Leave it.
- [ ] The seeded `owner@example.com` account either does not exist or has had its password
      changed. The seed password is published in this repository.
- [ ] `STORAGE_LOCAL_PATH` (or the S3 bucket) is on durable, backed-up storage.
- [ ] TLS terminates in front of the app; the app never speaks TLS itself.
- [ ] `ENCRYPTION_KEY` is backed up somewhere that is **not** the database backup.

---

## 2. Docker Compose

The simplest deployment. One host, five containers: Postgres, Redis, the API, the Celery
worker and beat, and the Next.js frontend.

```bash
git clone <your fork> /opt/jobagent
cd /opt/jobagent

cp .env.example .env
make secrets                  # prints SECRET_KEY, ENCRYPTION_KEY, BROWSER_ASSISTANT_TOKEN
$EDITOR .env                  # paste them, set APP_ENV=production, POSTGRES_PASSWORD,
                              # DATABASE_URL, FRONTEND_ORIGIN

docker compose up -d
make migrate                  # alembic upgrade head, inside the api container
make seed                     # first deployment only; change the password immediately
docker compose ps
docker compose logs -f api
```

Points to get right:

- **Internal addressing.** The browser and the Next.js server resolve different hosts.
  `NEXT_PUBLIC_API_BASE_URL` is what a browser would use; `API_INTERNAL_URL` is what the
  Next.js server-side proxy uses and takes precedence in `frontend/src/lib/session.ts`.
  In Compose that should be the service name, for example
  `API_INTERNAL_URL=http://api:8000/api/v1`.
- **Do not publish Postgres or Redis ports** to the host. Only the frontend, and the API
  if you proxy it separately, need to be reachable.
- **The browser assistant does not run in Docker.** It drives a visible browser on your
  own machine. Run it locally with `BROWSER_ASSISTANT_TOKEN` and the API base URL, and
  reach the API over TLS. One assistant per machine.
- **Migrations run as a separate step**, not on container start. Two API replicas starting
  at once must not both try to migrate.

The frontend builds with `output: 'standalone'` (see `frontend/next.config.mjs`), so its
image copies `.next/standalone/server.js` rather than the whole `node_modules` tree.

---

## 3. Single host with nginx and systemd

For a plain VM without Docker. Unit files and the nginx site live under `infra/`.

### Layout

```
/opt/jobagent/                 the checkout
/opt/jobagent/backend/.venv/   the backend virtualenv
/opt/jobagent/storage/         STORAGE_LOCAL_PATH, uploaded and generated files
/etc/jobagent/env              the environment file, mode 0600, owned by root
/etc/nginx/sites-enabled/jobagent.conf   from infra/nginx.conf
/etc/systemd/system/jobagent-*.service   from infra/systemd/
```

### Install

```bash
sudo useradd --system --home /opt/jobagent --shell /usr/sbin/nologin jobagent
sudo git clone <your fork> /opt/jobagent
sudo chown -R jobagent:jobagent /opt/jobagent

sudo -u jobagent python3 -m venv /opt/jobagent/backend/.venv
sudo -u jobagent /opt/jobagent/backend/.venv/bin/pip install -r \
     /opt/jobagent/backend/requirements.txt

sudo install -d -m 0750 /etc/jobagent
sudo install -m 0600 /dev/null /etc/jobagent/env
sudo $EDITOR /etc/jobagent/env        # the full .env contents

cd /opt/jobagent/frontend
sudo -u jobagent npm ci
sudo -u jobagent npm run build
```

### Services

Three units, referenced as `infra/systemd/jobagent-*.service`:

| Unit | Command | Notes |
|---|---|---|
| `jobagent-api.service` | `uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4` | Bind to loopback only; nginx is the only thing that talks to it. |
| `jobagent-worker.service` | `celery -A app.workers.celery_app.celery_app worker --loglevel=info` | Scale by adding `--concurrency`, or by running the unit on more hosts. |
| `jobagent-beat.service` | `celery -A app.workers.celery_app.celery_app beat --loglevel=info` | **Exactly one instance, ever.** Two beats double every scheduled sweep. |

A fourth unit for the Next.js server (`node .next/standalone/server.js` on port 3000) is
needed unless you serve the frontend some other way.

Each unit should carry `EnvironmentFile=/etc/jobagent/env`, `User=jobagent`,
`WorkingDirectory=/opt/jobagent/backend`, `Restart=on-failure`, and the usual hardening
(`NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict` with `ReadWritePaths`
covering the storage directory).

```bash
sudo cp /opt/jobagent/infra/systemd/jobagent-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jobagent-api jobagent-worker jobagent-beat
sudo systemctl status jobagent-api
```

### nginx

`infra/nginx.conf` terminates TLS and reverse-proxies the frontend on `/` and the API on
`/api/`. Things it must do:

- Set `X-Forwarded-For`; `backend/app/api/deps.py:client_ip` reads it for rate limiting
  and audit entries, taking the first entry. Only trust it because nginx sets it.
- Pass through or generate `X-Request-ID` so a request can be traced from the access log
  into the structured application logs.
- Allow a body large enough for resume uploads. The application cap is 15 MB
  (`storage.MAX_UPLOAD_BYTES`), so `client_max_body_size 16m;` is the matching value.
- Not add its own CORS headers. The application already emits them from
  `FRONTEND_ORIGIN`, and two sets of headers is worse than none.
- Not cache API responses. Sensitive endpoints already send `Cache-Control: no-store`.

The application sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy` and, in production, HSTS. The frontend adds a Content-Security-Policy
in `frontend/next.config.mjs`. Do not duplicate these in nginx.

---

## 4. PostgreSQL

**Use a managed instance** unless you have a reason not to. Point-in-time recovery,
automated backups and patching are exactly the things that get forgotten on a personal
deployment, and this database holds encrypted personal data and a tamper-evident audit
chain.

Requirements and notes:

- PostgreSQL 14 or newer. The schema uses `JSONB`, native `uuid`, `BIGINT` and PL/pgSQL.
- The connection URL must use the psycopg 3 driver: `postgresql+psycopg://...`.
- **Pooling.** `backend/app/db/session.py` configures `pool_size=10`, `max_overflow=20`,
  `pool_recycle=1800` and `pool_pre_ping=True` per process. Multiply by your uvicorn
  worker count and Celery concurrency when sizing `max_connections`, or put PgBouncer in
  front in transaction mode. Note that `pool_recycle` exists because managed providers
  and load balancers silently drop idle connections.
- **The audit trigger.** `backend/alembic/versions/0001_initial.py` installs, on
  PostgreSQL only, a PL/pgSQL function `jobagent_audit_is_append_only()` and a
  `BEFORE UPDATE OR DELETE ... FOR EACH ROW` trigger `audit_logs_append_only` on
  `audit_logs`. It raises on any DELETE, and on any UPDATE that changes `seq`,
  `created_at`, `action`, `object_type`, `object_id`, `outcome`, `payload`, `prev_hash`,
  or a non-empty `entry_hash`. The only mutable columns are `user_id`, `actor` and
  `ip_address`, which is exactly what `POST /api/v1/privacy/erase` needs in order to
  anonymise a user without breaking the hash chain. Those three fields are deliberately
  excluded from `AuditLog.compute_hash()` for the same reason.
- The application role needs `CONNECT`, `USAGE` on the schema, and DML on all tables. The
  migration role additionally needs DDL and the ability to create functions and triggers.
  They can be the same role on a small deployment; separating them is better.
- Do not grant the application role permission to drop the trigger.

---

## 5. Migrations

Alembic reads `DATABASE_URL` from application settings (`backend/alembic/env.py`);
`alembic.ini` deliberately contains no `sqlalchemy.url`.

```bash
cd /opt/jobagent/backend
source .venv/bin/activate

alembic current                  # what is applied now
alembic history --verbose        # what exists
alembic upgrade head             # apply
```

Generating a revision after a model change:

```bash
alembic revision --autogenerate -m "add employer_contact to applications"
$EDITOR alembic/versions/<new>.py     # ALWAYS read it before applying
alembic upgrade head
```

Rules:

- **Read every autogenerated revision.** Autogenerate does not understand the
  `EncryptedString` and `EncryptedJSON` types beyond their `Text` impl, does not see the
  trigger, and will happily propose dropping something it does not recognise.
- **The audit trigger must survive.** If a revision recreates or renames `audit_logs`,
  re-create the function and trigger in the same revision; copy the
  `AUDIT_IMMUTABILITY_TRIGGER` constant from `0001_initial.py`. After any migration that
  touches that table, verify:

  ```sql
  SELECT tgname FROM pg_trigger WHERE tgname = 'audit_logs_append_only';
  ```

  and then call `GET /api/v1/audit/verify` and confirm `valid: true`.
- **Never change encryption at the same time as schema.** A migration that re-encrypts
  columns should be its own revision, run with the app stopped.
- Migrate before deploying new application code, and keep each revision backward
  compatible with the currently running version, so a rollback does not need a downgrade.
- `python -m seed.seed` is for a fresh environment only. It refuses `--reset` when
  `APP_ENV` is staging or production, but plain seeding is still not something to run
  against live data.

---

## 6. Backups

Three things must be backed up, and **two of them are useless without the third**.

| What | Where | How |
|---|---|---|
| The database | PostgreSQL | `pg_dump -Fc` nightly plus WAL archiving or your provider's point-in-time recovery. Restore-test it. |
| The storage directory | `STORAGE_LOCAL_PATH` (default `./storage`), or the S3 bucket | Uploaded resumes, generated resumes and cover letters, submission screenshots. Content-addressed, so incremental sync works well. On S3, enable versioning. |
| The encryption key ring | `ENCRYPTION_KEY` and `ENCRYPTION_KEY_PREVIOUS` | A password manager or a secrets manager. **Not** in the same backup as the database, and not in the repository. |

> **Warning.** A database backup is worthless without `ENCRYPTION_KEY`. Phone numbers,
> addresses, work-authorization details, screening answer values, submission confirmation
> numbers and stored authorization acknowledgements are Fernet ciphertext in the dump.
> With the key lost, those columns are unrecoverable and every read of them raises
> `RuntimeError: Could not decrypt a stored value`. The rest of the row survives, so a
> restore without the key means re-entering the encrypted fields by hand.
>
> The mirror image is also true: storing the key **inside** the database backup destroys
> the point of encrypting the columns. Keep them separate, and prove you can retrieve both
> before you need to.

Also worth capturing: `/etc/jobagent/env` or your `.env` (it is a secret in its own
right), and a note of which Alembic revision the dump was taken at.

---

## 7. Rotating the encryption key

`backend/app/core/crypto.py` uses `MultiFernet`. Writes always use the primary key; reads
try the primary and then every key in `ENCRYPTION_KEY_PREVIOUS`, newest first. That makes
rotation a rolling operation rather than a big-bang re-encryption.

1. **Back up first.** Database and key ring, and verify the backup restores.
2. **Generate the new key.**

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. **Demote the current key.** Move the existing `ENCRYPTION_KEY` value to the front of
   `ENCRYPTION_KEY_PREVIOUS` (comma-separated, newest first), and put the new key in
   `ENCRYPTION_KEY`:

   ```ini
   ENCRYPTION_KEY=<new key>
   ENCRYPTION_KEY_PREVIOUS=<previous key>,<older key if any>
   ```

4. **Restart every process** that touches the database: API, worker, beat. `_fernet()` is
   `lru_cache`d, so a running process keeps the old ring until it restarts.
5. **Verify.** Read a page that renders an encrypted field (the profile phone number, an
   application answer) and confirm it decrypts. New writes are now under the new key; old
   rows still decrypt under the previous one.
6. **Re-encrypt at your own pace.** Nothing forces you to, but leaving old ciphertext
   around means you can never drop the old key. `crypto.rotate(ciphertext)` re-encrypts a
   single value under the primary key. No management command ships for this, so it is a
   short one-off script: with the app stopped, load each row that has an encrypted column,
   read the attribute (which decrypts through the ring) and write it straight back (which
   encrypts under the primary key), then commit. The encrypted columns are `phone` and
   `address` and `work_authorization` on `candidate_profiles`, `acknowledgement_text` on
   `platform_authorizations`, `answer_value` on `application_answers`, and
   `confirmation_number` on `applications`.
7. **Drop the old key** from `ENCRYPTION_KEY_PREVIOUS` only after step 6 is complete and
   verified, and only after your backup retention window has passed: restoring an older
   dump will need the key that dump was written with.

If you skipped step 3 and simply replaced the key, the symptom is immediate and loud:
every read of an encrypted column raises `Could not decrypt a stored value. The
ENCRYPTION_KEY changed without rotation: put the old key in ENCRYPTION_KEY_PREVIOUS.`
Doing exactly that fixes it.

---

## 8. Secrets management

Every secret is read from the environment through `backend/app/core/config.py`. Nothing is
hard-coded, and nothing is read from the database.

| Secret | Blast radius if leaked | Rotation |
|---|---|---|
| `SECRET_KEY` | Forge any access or refresh token, impersonate any user | Change it. Every issued token becomes invalid immediately, which is the desired effect. Everyone re-logs in. |
| `ENCRYPTION_KEY` | Decrypt the encrypted columns from a stolen dump | Section 7. |
| `DATABASE_URL` | Everything | Change the database password, update the URL, restart. |
| `BROWSER_ASSISTANT_TOKEN` | See below | Change it and restart; the local assistant needs the new value. |
| `ANTHROPIC_API_KEY` | Billing | Revoke in the Anthropic console and issue a new one. The app keeps working without it, in deterministic mode. |
| `SMTP_PASSWORD`, partner API tokens | Their own services | Per provider. |

Practical rules: use a secrets manager or a 0600 root-owned env file, never a committed
`.env`; give the CI system no production secrets; and remember that
`backend/app/core/logging.py` redacts password, token, key, secret and phone fields from
structured logs, which is a safety net and not a licence to log them.

**On the assistant token specifically.** It is not a user credential and it is
deliberately weaker than one. It grants access to `/api/v1/assistant/*` and nothing else:
health, take the next task the policy gate already approved, download that application's
attachments, ask the server to answer discovered form questions, and report the outcome.
It cannot read your profile or your career facts, cannot change settings, cannot grant
itself an authorization, and cannot make an application eligible that the gate did not
already approve. A holder of the token can, however, see the tasks that are queued and
report false outcomes for them, so treat it as sensitive; it is just not equivalent to
your password. If `BROWSER_ASSISTANT_TOKEN` is blank the entire surface returns HTTP 503.

---

## 9. Observability

### Logs

structlog emits one JSON object per line to stdout when `LOG_FORMAT=json`. Every line
carries `event`, `level`, `timestamp`, and, inside a request, `request_id` and `actor_id`
from contextvars. The request middleware logs one `request` event per response with
method, path, status and `duration_ms`; unhandled exceptions log `request.unhandled` with
a traceback before the error propagates. Collect stdout with journald or your log shipper;
the application writes no log files itself.

Useful events to index: `request`, `audit`, `discovery.blocked`, `discovery.error`,
`matching.completed`, `worker.drafted`, `worker.discovery_failed`,
`ratelimit.redis_unavailable`, `llm.generate_text`, `notification.email_failed`.

### `X-Request-ID`

The middleware honours an inbound `X-Request-ID` header or generates a hex id, binds it to
a contextvar, and echoes it on the response. It appears on every log line emitted while
serving that request **and** on the `request_id` column of every audit entry written
during it. Have nginx pass one through so an access-log line, an application log line and
an audit row can be joined.

### `/health`

Unauthenticated, cheap, and honest:

```json
{"status":"ok","version":"1.0.0","environment":"production","database":"ok",
 "redis":"ok","llm":"deterministic","automation_enabled":false,"checks":{}}
```

- `status` is `ok` only when the database probe (`SELECT 1`) succeeds, and `degraded`
  otherwise. It is **not** affected by Redis: Redis being down degrades rate limiting to
  per-process counting rather than breaking the app, so it is reported without failing the
  check.
- `redis` is `ok`, `error` or `not_configured`.
- `llm` is `claude` or `deterministic` and is a quick way to confirm whether the API key
  reached the process.
- `checks` carries the truncated error strings when something failed.

Use it as a load-balancer liveness probe, and alert on `status != "ok"` and on
`redis == "error"` separately.

### What to alert on

| Signal | Where it comes from | Why it matters |
|---|---|---|
| **Submission failures** | Applications entering `failed` or `blocked_by_policy`; `application.failed` audit entries; `SUBMISSION_FAILED` notifications | A run of these usually means a form changed or a site added protection. Each one already created a review task; a spike means the assistant is wasting attempts. |
| **`blocked_by_policy` spikes** | `policy.block` audit entries; `subscription.last_status == "blocked_by_policy"`; assistant results with `outcome=aborted` | The system is working as designed, but a sudden rise means a site started serving bot checks or a robots.txt changed. Investigate the source rather than raising the retry count. |
| **Audit chain verification failure** | `GET /api/v1/audit/verify` returning `valid: false` | Treat as a security incident. Run it on a schedule and page on failure. It names the `broken_at_seq` and whether the mismatch is in `prev_hash` or in the entry contents. |
| **Discovery `consecutive_failures`** | `job_source_subscriptions.consecutive_failures` | Five consecutive failures disable the source silently. Alert at three so you fix it before it turns itself off. |
| Health degraded | `/health` `status`, `database` | Obvious. |
| Queue backlog or dead beat | Celery broker depth; the absence of `discover-and-score` runs | If beat dies, discovery quietly stops and the UI looks fine. |
| Unusual auth failures | `user.login_failed` audit entries, HTTP 429s on `/auth/*` | Credential stuffing. |
| Daily limit reached every day | `daily_counters`, `daily_limit_reached` review reasons | Either the limit is too low or the shortlist threshold is too generous. Both are worth a human decision. |

---

## 10. Scaling, and the limits that are deliberate

This system is small on purpose. Most of what looks like a bottleneck is a safety
property.

**What scales horizontally**

- The API is stateless apart from the database and Redis. Add uvicorn workers or hosts
  behind nginx freely.
- Celery workers scale out; add concurrency or hosts. `acks_late` and
  `prefetch_multiplier=1` mean a killed worker's task is redelivered rather than lost.
- Redis-backed rate limiting is shared across processes, so limits stay correct as you add
  replicas. Without Redis, each process counts separately and the effective limit
  multiplies.

**What does not, and should not**

| Limit | Where | Why it stays |
|---|---|---|
| **Exactly one Celery beat** | `jobagent-beat.service` | Two schedulers double every sweep and every digest. |
| **Polite per-host crawl rate** | `DISCOVERY_PER_HOST_RPS`, default 0.5 req/s, enforced by `PoliteClient._throttle` | This is the difference between a well-behaved client and one that gets IP-banned. It is the main reason discovery takes minutes rather than seconds, and that is the correct trade. Note the throttle is per process, so adding workers multiplies your real rate against a host: scale discovery workers with that in mind. |
| **Sequential sources per user** | `discovery.run_all_for_user` uses one shared client | `DISCOVERY_MAX_CONCURRENCY` exists in settings but no code reads it. Sources are polled one at a time. |
| **Daily application limit** | Per-user `daily_application_limit`, default 10, enforced through `daily_counters` under a row lock | The product is not a volume machine. Raising this is the single fastest way to make the tool obnoxious. |
| **One browser assistant per machine** | It drives a real, visible browser | It cannot be parallelised without either headless operation or a fleet of browsers, both of which are exactly the anti-detection behaviour this project refuses to ship. |
| **`JOB_MAX_AGE_HOURS`, default 48** | Hard filter | Keeps the scoring batch small and the applications timely. |
| **Job scoring batch of 500** | `matching.candidate_jobs` | Bounds memory and the TF-IDF index. |
| **15 MB uploads, 10 SmartRecruiters pages, 900 s Celery time limit** | `storage.MAX_UPLOAD_BYTES`, `smartrecruiters.MAX_PAGES`, `task_time_limit` | Bounded work per unit. |

If discovery genuinely is your bottleneck, add **sources**, not speed: more boards polled
politely beats one board polled aggressively, and it is the only version of "faster" this
project will help you with.

---

## 11. Rollback

Design for rollback by keeping each migration backward compatible with the previous
application version. Then a bad deploy is a code rollback, not a database restore.

### Code only (the normal case)

```bash
# Docker Compose
cd /opt/jobagent
git checkout <previous tag>
docker compose up -d --build
docker compose logs -f api

# systemd
sudo -u jobagent git -C /opt/jobagent checkout <previous tag>
sudo -u jobagent /opt/jobagent/backend/.venv/bin/pip install -r \
     /opt/jobagent/backend/requirements.txt
cd /opt/jobagent/frontend && sudo -u jobagent npm ci && sudo -u jobagent npm run build
sudo systemctl restart jobagent-api jobagent-worker jobagent-beat
```

Leave the newer schema in place if the old code tolerates it. An extra column is harmless;
a dropped one is not, which is why additive migrations are the rule.

### When the schema must go back too

1. **Stop the writers first**, in this order: beat, workers, then the API. Nothing may be
   mid-transaction during a downgrade.
2. `alembic downgrade -1` (or to a named revision). Read the `downgrade()` body first and
   accept what it deletes; a downgrade that drops a column drops the data in it.
3. If the revision being reversed touched `audit_logs`, confirm the trigger still exists
   and that `GET /api/v1/audit/verify` returns `valid: true` afterwards.
4. Deploy the previous code, then start the API, the workers and beat.

### First aid before you roll back

- **Stop automation instantly** without a deploy: `POST /api/v1/settings/pause` (any
  authenticated role may pause; only an owner may resume), or set
  `AUTOMATION_GLOBAL_ENABLED=false` and restart. Either makes `/assistant/tasks/next`
  return nothing. Drafting and review continue, which is usually what you want.
- **Stop discovery** by disabling the sources, or by stopping beat.
- **Revoke a platform authorization** with `DELETE /api/v1/settings/authorizations/{key}`;
  it returns the platform to `review_required` immediately.
- Nothing already submitted can be un-submitted. That is the reason the defaults are what
  they are.
