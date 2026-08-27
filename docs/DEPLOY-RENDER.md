# Deploying to Render

A step-by-step procedure for putting this app on [Render](https://render.com).

Every command and environment variable below was checked against the code in this
repo, not against generic Render advice. Where a step exists only to avoid a
specific trap, the trap is named.

---

## 0. What deploys, and what does not

| Part | Render service | Notes |
|---|---|---|
| Backend API | Web Service (Python) | Must be **public** — your laptop's assistant connects to it |
| Worker | Background Worker | Needed for scheduled discovery/scoring; needs Redis |
| Frontend | Web Service (Node) | The only thing you open in a browser |
| Postgres | Postgres | |
| Redis | Key Value | Optional — see step 3 |
| **Browser assistant** | **Does not deploy** | It runs on *your machine*, on purpose |

The browser assistant cannot be deployed and this is not a limitation to work
around. `browser-assistant/src/config.ts` refuses to start when `HEADLESS=true`,
because the entire safety model is that you can watch it fill a form and stop it.
A headless copy on a server is exactly the thing this app is built not to be.

### The good news about networking

The browser never talks to the backend directly. `frontend/src/lib/api.ts`
always fetches `/api/proxy/...` on its own origin, and the Next.js server attaches
the auth header from an httpOnly cookie. So:

- **No CORS configuration is needed** for normal use.
- **No cross-domain cookie problem** — `SameSite=Lax` is fine because the cookie
  is same-origin with the page setting it.
- Only one server-to-server URL matters: `API_INTERNAL_URL`.

---

## 1. Generate your secrets first

Do this **before** creating any service. These three values must be identical
everywhere they appear, and two of them can never be changed later without data loss.

```bash
make secrets
```

That prints `SECRET_KEY`, `ENCRYPTION_KEY`, and `BROWSER_ASSISTANT_TOKEN`. Put
them in a password manager now.

> **`ENCRYPTION_KEY` is not rotatable in place.** Eight profile columns are
> Fernet-encrypted with it. Lose it and that data is unrecoverable — a database
> backup alone will not save you.

---

## 2. The `APP_ENV` trap — read this before deploying anything

Set `APP_ENV=production` on the **very first backend deploy**, before you register
a single account.

If `APP_ENV` is blank or missing, `app/core/config.py:53` falls back to
`development`, and in development `app/core/crypto.py:34` *derives* the Fernet key
from `SECRET_KEY` instead of using `ENCRYPTION_KEY`. Everything written in that
state becomes permanently undecryptable the moment you later set `APP_ENV=production`
with a real key — every read raises `Could not decrypt a stored value`.

If you have already done this, recover before flipping `APP_ENV` by setting:

```bash
ENCRYPTION_KEY_PREVIOUS=$(python -c "import base64,hashlib; print(base64.urlsafe_b64encode(hashlib.sha256(b'dev-key::'+b'<THE-SECRET-KEY-YOU-USED').digest()).decode())")
```

then re-save each affected profile row so it re-encrypts under the primary key.

---

## 3. Create Postgres (and optionally Redis)

**Postgres** → New → Postgres. Name it `jobagent-db`. Copy both the Internal and
External Database URLs.

Render hands you a `postgres://…` URL. This app uses **psycopg 3**
(`psycopg[binary]` in `requirements.txt`), so you must rewrite the scheme:

```
postgres://user:pass@host/jobagent          <- what Render gives you
postgresql+psycopg://user:pass@host/jobagent  <- what you must paste
```

**Redis** → New → Key Value. Name it `jobagent-redis`, copy the Internal URL.

Skipping Redis is supported, but then nothing runs on a schedule — no automatic
discovery, no scoring, no drafting. You would drive everything manually from the UI.
If you skip it, **keep the `REDIS_URL` row and set it to an empty string**. Do not
delete the row: the default value is `redis://localhost:6379/0`, so a deleted row
leaves the API polling a Redis that does not exist and `/health` reports
`redis: error` forever.

---

## 4. Deploy the backend API

New → Web Service → connect the repo.

| Field | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |

`--host 0.0.0.0` and `--port $PORT` are both mandatory — Render routes to `$PORT`
and a service bound to `127.0.0.1` fails its health check.

Environment variables:

| Key | Value |
|---|---|
| `APP_ENV` | `production` — see step 2 |
| `PYTHON_VERSION` | `3.12.7` |
| `SECRET_KEY` | from step 1 (≥32 chars, must not start `dev-only`) |
| `ENCRYPTION_KEY` | from step 1 |
| `DATABASE_URL` | Internal URL, scheme rewritten per step 3 |
| `REDIS_URL` | Internal Redis URL, or empty string |
| `STORAGE_LOCAL_PATH` | `/opt/render/project/src/backend/storage` on free; `/var/data/storage` only if you attached a Disk |
| `FRONTEND_ORIGIN` | `https://jobagent-web.onrender.com` (fill in after step 7) |
| `BROWSER_ASSISTANT_TOKEN` | from step 1 |
| `AUTOMATION_GLOBAL_ENABLED` | `true` if you want auto-submit possible at all |
| `ANTHROPIC_API_KEY` | optional; without it the app uses its deterministic templates |

> Do not point `STORAGE_LOCAL_PATH` at `/var/data` without a Disk attached.
> `app/main.py:28` does `mkdir` on it as the first statement of the lifespan hook,
> so a non-writable path kills the process before uvicorn ever serves a request.

Health Check Path: `/health`.

---

## 5. Run the migrations — they never run by themselves

`app/main.py` contains no `alembic upgrade` and no `create_all`. If you skip this
step the API boots, `/health` returns **200**, and every real request 500s against
an empty schema. The health check will not warn you, because it reports
`degraded` with a 200 status.

On a **free** instance you cannot use the Shell tab (paid feature). Run it from
your own machine against the **External** URL:

```bash
cd backend && DATABASE_URL='postgresql+psycopg://USER:PASS@HOST.oregon-postgres.render.com/jobagent' APP_ENV=production SECRET_KEY='<same as Render>' ENCRYPTION_KEY='<same as Render>' alembic upgrade head
```

The env vars are not optional: `alembic/env.py` reads `settings.database_url`, so
Pydantic validation runs first and will reject a missing `ENCRYPTION_KEY` under
`APP_ENV=production`. Passing them inline also stops a stale local `.env` winning.

Verify:

```bash
psql "$EXTERNAL_URL" -c 'select version_num from alembic_version'
```

It must print `0003_relocation_tristate`.

---

## 6. Deploy the worker (skip if you skipped Redis)

New → Background Worker, same repo.

| Field | Value |
|---|---|
| Root Directory | `backend` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `celery -A app.workers.celery_app worker --loglevel=INFO --concurrency=2` |

For the scheduler, add a second Background Worker identical except:

```
celery -A app.workers.celery_app beat --loglevel=INFO
```

**Use a Render Environment Group** (`jobagent-shared`) holding `DATABASE_URL`,
`SECRET_KEY`, `ENCRYPTION_KEY`, `REDIS_URL`, `APP_ENV` and `STORAGE_LOCAL_PATH`,
and attach it to the API, worker and beat. Do not type the secrets separately per
service. Nothing validates that the copies match, and an `ENCRYPTION_KEY` that
differs by one character means the worker writes rows the API can never read —
silently, per row, unrecoverably.

---

## 7. Deploy the frontend

New → Web Service, same repo.

| Field | Value |
|---|---|
| Root Directory | `frontend` |
| Runtime | Node |
| Build Command | `npm ci --include=dev && npm run build` |
| Start Command | `npx next start -p $PORT` |

Two traps here:

**Do not set `NODE_ENV=production`.** npm's `omit` config defaults to `dev` when
`NODE_ENV=production`, and this project's `package.json` has only `next`, `react`
and `react-dom` as real dependencies — Tailwind, PostCSS and TypeScript are all
devDependencies. Setting it makes `npm ci` skip every build tool and `next build`
dies on the PostCSS plugins. `--include=dev` defends against Render setting it for
you. Cookies still get `Secure` because `next build` sets `NODE_ENV=production`
for the compile itself, which is what `session.ts:19` reads.

**`-p $PORT` is mandatory.** `package.json`'s `start` script is hardcoded to
`next start -p 3000`, which ignores Render's assigned port and fails the health check.

Environment variables:

| Key | Value |
|---|---|
| `API_INTERNAL_URL` | `https://jobagent-api.onrender.com/api/v1` |

That value needs the `https://` scheme **and** the `/api/v1` suffix, with no
trailing slash. `app/api/proxy/[...path]/route.ts:46` concatenates it directly, so a
bare hostname makes every server-side fetch throw and the app returns 503 on
100% of requests — including login — while still serving HTML. It looks like a
backend outage and is not one.

Do not wire this with Render's `fromService … property: host`: that substitutes a
bare hostname and cannot compose a path.

Now go back to the backend service and set `FRONTEND_ORIGIN` to this service's URL.

---

## 8. Create your owner account

Open `https://jobagent-web.onrender.com`. The login page offers **Create the
first account**. The first account registered becomes `owner`; every later one is
`viewer` and cannot change automation settings.

---

## 9. Point your local assistant at the deployment

On your own machine, in `browser-assistant/.env`:

```
API_BASE_URL=https://jobagent-api.onrender.com/api/v1
BROWSER_ASSISTANT_TOKEN=<the same token from step 1>
```

Then `npm start`. A visible Chromium opens on your desktop and works the queue.
This is why the backend must be a public service rather than a private one.

---

## 10. Back up the database

Nothing in Render does this for you, and on the free plan the database is
**deleted after 30 days**.

```bash
pg_dump "$EXTERNAL_DATABASE_URL" -Fc -f jobagent-$(date +%F).dump
```

Store it *alongside* — not inside — the password-manager entry holding
`ENCRYPTION_KEY`. A dump without that key restores eight profile columns as
opaque ciphertext.

---

## Known limitations

**The free tier wipes the filesystem on every deploy.** Uploaded resumes and
generated documents live on disk (`STORAGE_LOCAL_PATH`); only their metadata rows
survive in Postgres, so the UI lists documents that 404. There is no S3 backend in
this codebase. Attach a paid Disk mounted at `/var/data`, or accept re-uploading.

After a wipe, check what was lost — this is the only endpoint that reveals it, and
it returns 200 either way:

```bash
curl -H "Cookie: …" https://jobagent-api.onrender.com/api/v1/privacy/export | jq '.stored_files_omitted | length'
```

To restore a lost file you must **delete the orphaned row first**
(`DELETE /api/v1/profile/documents/{id}`) and then re-upload. Renaming the file
does not help: the dedupe key is a SHA-256 of the *content*, so a re-upload
short-circuits, returns 200 with a warning, and writes nothing.

**Free services spin down after 15 minutes idle** and take ~50s to wake. A
scheduled discovery run that fires while the worker is asleep is skipped, not queued.

**`/health` returns 200 when degraded.** It is a liveness probe, not a readiness
probe — it will not catch an unmigrated database or a broken Redis. Check the JSON
body, not the status code.
