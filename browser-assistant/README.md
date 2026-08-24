# Local Browser Assistant

A small Node + TypeScript + Playwright program that runs **on your own machine**.
It asks the backend for application tasks you have already authorized, opens a
**visible** Chromium window, fills the form with values the server supplies, and
stops at the first sign of anything it is not allowed to do.

It is deliberately unimpressive. It has no cleverness for getting past
obstacles, because getting past obstacles is the thing it must not do.

---

## What it refuses to do

This is the important section. The same list is printed in the startup banner,
and lives in code as `NEVER_DO` in `src/core/guards.ts`.

| It will never | Why |
|---|---|
| Solve a CAPTCHA, or send one to a human or paid solving service | A CAPTCHA is the site saying "no automation here". Answering it is lying to the site. |
| Patch `navigator.webdriver` or any automation flag | Same reason. Detection exists so the site can decide; we do not take that decision away. |
| Spoof a fingerprint, or mask the user agent | The assistant *appends* its identity to the real user agent. It never replaces it. |
| Rotate proxies or residential IPs | Hiding where the traffic comes from is evasion, not automation. |
| Run headless | You have to be able to watch it. `HEADLESS=true` is refused at startup with an error. |
| Type a username, password, or any credential | It holds no credentials and never asks for any. A login wall is a full stop. |
| Click through a consent banner or terms-of-service gate | Accepting terms is your decision, not a script's. |
| Invent an answer, date, employer, salary, visa status, or link | Every value comes from a human-verified career fact via the server. If the server cannot answer, the attempt stops. |
| Fill or submit a page `robots.txt` disallows | Checked before the browser opens. |
| Retry an attempt that stopped for a policy reason | One stop, one review task, no second guessing. |
| Report a submission it did not make | The result endpoint is only ever told "submitted" after a real click on a real confirmation page. |

LinkedIn and Indeed are **prohibited** for automated submission and cannot be
enabled from anywhere, including here. See `docs/COMPLIANCE.md`.

### It runs headed, on your machine

The browser window is a normal Chromium window on your desktop. You can watch
every keystroke, scroll around, correct a field, or close the window and kill
the run at any moment. `SLOW_MO_MS` exists so the actions are slow enough to
follow, not to defeat any rate limiter.

---

## Install

```bash
cd browser-assistant
npm install
npx playwright install chromium
```

Node 20 or newer is required.

## Configure

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `API_BASE_URL` | `http://localhost:8000/api/v1` | Backend API root, including `/api/v1`. |
| `BROWSER_ASSISTANT_TOKEN` | *(required)* | Shared secret sent as `X-Assistant-Token`. Must match the backend. |
| `POLL_INTERVAL_SECONDS` | `20` | Wait between polls when the queue is empty. |
| `HEADLESS` | `false` | Must stay false. `true` is refused with an error, not ignored. |
| `SLOW_MO_MS` | `120` | Delay between actions so you can follow along. |
| `MAX_RUNTIME_SECONDS` | `180` | Hard ceiling per application. Exceeding it reports a failure and closes the browser. |
| `SCREENSHOT_ON_SUCCESS` | `true` | Attach a confirmation screenshot to the submission receipt. |
| `ASSISTANT_USER_AGENT_SUFFIX` | `JobAgentBrowserAssistant/1.0 ...` | Appended to the real user agent. Also the token used for `robots.txt` matching. |
| `DRY_RUN` | `true` | Fill everything, never click submit. Turn off deliberately. |

## Run

```bash
npm start          # run the poll loop
npm run dev        # same, restarting on file changes
npm run typecheck  # tsc --noEmit
npm test           # vitest, no browser needed
npm run build      # compile to dist/
```

Stop it with `Ctrl+C`. Nothing keeps running in the background.

---

## What one attempt looks like

1. `GET /assistant/health`. If the server's global automation kill-switch is off,
   nothing is filled; the assistant says so and keeps polling.
2. `GET /assistant/tasks/next`. Empty queue means sleep and poll again.
2b. The task's `connector_key` **and** the hostname of its apply URL are checked
   against this assistant's own never-automate list (`linkedin`, `indeed`). A
   match aborts with `unsupported_platform` before Chromium is even launched.
   The backend gate is the primary control; this one does not depend on the
   backend being right.
3. `robots.txt` is fetched and evaluated for the apply URL. A `Disallow` that
   matches, or any failure to read the rules, aborts with `robots_disallowed`.
   Only a 404 - the site publishes no `robots.txt` at all - counts as permission.
4. Chromium launches **headed**, with a normal 1440x900 viewport and a user agent
   that is the real one plus the configured suffix.
5. The page loads and is scanned for hard stops: CAPTCHA, bot protection, login
   wall, paywall. Any finding aborts before a single character is typed.
6. The real form is read and reported to
   `POST /assistant/tasks/{id}/questions`. The **server** answers; the assistant
   never derives an answer. If the server cannot answer a required question, the
   attempt aborts with `unknown_question`, or `free_text_question` when every
   blocking question is a free-text one, and the unanswered questions travel
   along as guard findings so the review task can show them.
7. Attachments are downloaded to a temp directory and attached to the matching
   file inputs. A required upload with no document is a `missing_attachment` abort.
8. Fields are filled from the server's answers, then the page is scanned **again**,
   because sites inject challenges after the first interaction.
9. Hand-off or submit (below).
10. Anything thrown along the way is reported as `failed` with
    `submission_error`. Policy aborts are never retried.

## The assisted-autofill hand-off

When the server says `may_click_submit: false` - which is what an
`ASSISTED_AUTOFILL` authorization means - the assistant does **not** finish the
job. It:

* takes a screenshot for your records,
* **leaves the browser window open**,
* prints instructions telling you to review every field and click submit
  yourself,
* **posts no result at all**, and
* stops polling so it cannot claim another task while your window is open.

The application stays `in_progress` on the server, and is not offered to the
assistant again, until you record what you did with
`POST /api/v1/applications/{id}/mark-submitted` (the "I submitted this" action in
the app). That stores the attempt as `submitted_by_human` and names you in the
audit entry. This is the whole point: the assistant must never report a
submission it did not make, and it cannot know whether you clicked. Press
`Ctrl+C` when you are done and the process exits.

`DRY_RUN=true` behaves the same way even for auto-submit tasks: the form is
filled, every guard is run, the submit button is left alone, and the run says so
loudly. No result is posted.

## Auto-submit

Only when **all** of these are true does the assistant click anything:

* the platform has an explicit, typed `AUTO_SUBMIT` authorization, and
* the server returns `may_click_submit: true` for this application, and
* `DRY_RUN=false` in your `.env`.

After the click it waits for the confirmation page, extracts a confirmation
number with a few common patterns, screenshots the result, and posts
`outcome: "submitted"` with the receipt. If no submit button can be identified,
it aborts with `validation_failed` rather than clicking something that looks
close enough.

## Abort reasons

Every abort uses a key from `ASSISTANT_ABORT_REASONS` in
`backend/app/api/v1/assistant.py`:

`captcha_detected`, `login_required`, `bot_protection_detected`,
`robots_disallowed`, `unknown_question`, `free_text_question`,
`missing_attachment`, `validation_failed`, `unsupported_platform`,
`submission_error`.

The server turns each of them into a review task with a direct link and the
prefilled draft, so you can finish the application by hand in a minute.

A paywall is reported as `login_required`: from the applicant's side it is the
same gate, and the backend has no separate reason for it.

## Layout

```
src/
  index.ts            CLI entry: banner, poll loop, Ctrl+C handling
  config.ts           env parsing and validation (refuses HEADLESS=true)
  api.ts              typed backend client (retries 5xx only, never a 4xx)
  runner.ts           the lifecycle of one attempt
  core/
    guards.ts         hard stops, robots.txt, the NEVER_DO list
    fill.ts           locator ladder and resilient filling
    discover.ts       reading the real form into DiscoveredQuestion[]
    guards.test.ts        browser-free tests
    fill.test.ts          browser-free tests
    live-browser.test.ts  the guards, form reader and filler driven against
                          real Chromium using the fixtures below
tests/
  fixtures/
    clean-application.html   an ordinary ATS form, no challenge
    recaptcha-wall.html      a reCAPTCHA widget as Google renders it
    turnstile-wall.html      a Cloudflare interstitial
    datadome-wall.html       a DataDome challenge
    login-wall.html          a sign-in gate
```

## Tests

```bash
npm test          # everything
npm run typecheck
```

Most tests are browser-free and run in milliseconds. Nine of them are not: they
launch real Chromium against the fixture pages above and prove that

* `detectHardStops` finds a reCAPTCHA, a Turnstile interstitial, a DataDome
  challenge and a login wall inside an actual document, not just inside a string;
* it reports **nothing** on an ordinary application form, because a guard that
  stopped on every page would turn the product into a manual review queue;
* `discoverQuestions` reads a real form's fields, types and required flags, and
  recognises the demographic question as EEO;
* the filler resolves fields through six different locator strategies (name, id,
  data-qa, label text, placeholder, aria-label) and writes the values back into
  the live DOM;
* a required field it cannot locate is **reported**, never invented;
* a field the server supplied no value for is left untouched.

Those nine need the browser binary:

```bash
npx playwright install chromium
```

Without it they skip with a warning and the rest of the suite still passes, so a
fresh clone is never red for want of a download.

They run headless, which is not a contradiction of the headed rule: that rule
exists so a human can watch the assistant act on a real employer's site. A local
fixture file has no human to protect and no site to be honest with. `config.ts`
still refuses `HEADLESS=true` for the real runner.

## Troubleshooting

* **"TOKEN REJECTED"** - `BROWSER_ASSISTANT_TOKEN` does not match the backend.
  Nothing was filled or submitted.
* **"HEADLESS=true is refused"** - working as intended. Set it to false.
* **Everything aborts with `login_required`** - the guards fail closed on
  purpose. If a page merely mentions signing in, it stops. Finish that one by
  hand from the review task.
* **`npx playwright install chromium` was skipped** - the launch will fail with a
  missing-executable error from Playwright.
