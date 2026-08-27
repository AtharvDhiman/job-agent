---
description: Add a new job source, after it passes the compliance test that comes before any code.
argument-hint: "<company name>"
---

# /add-portal

Adds a source to poll: `$ARGUMENTS`. Most of the time this means finding a company's
existing board on an ATS this project already integrates. Occasionally someone wants a
platform that is not integrated at all -- that is a code change, and it does not start
with code.

## The compliance test, before anything is written

A new portal has to pass all of this **first**. If it fails any point, the answer is no,
and the honest alternative is pasting individual jobs with `/apply`.

1. **There is a route the publisher actually offers.** One of:
   - a documented, unauthenticated public job-board API intended for public consumption;
   - a partner API you hold your **own** credentials for, under your **own** agreement;
   - an RSS/Atom/JSON feed published for syndication.

   Reading HTML the publisher did not publish for that purpose is not a route. "The data
   is visible in a browser" is not the same as "published for syndication".

2. **robots.txt can be fetched and read, and it allows the path** for this agent's
   User-Agent. This is the part people skip. A crawler that cannot read robots.txt cannot
   claim permission from it, so an unreadable robots.txt fails closed.

   **Worked example -- Naukri.** It publishes no candidate-facing job-search API: no
   developer portal, no self-serve key, no documented endpoint. That leaves scraping as
   the only technical route, and `naukri.com/robots.txt` answers **403** to an ordinary
   request. The one file that would say what is permitted cannot be read at all. So
   Naukri is registered as `MANUAL_ONLY` with submission `PROHIBITED`, it refuses to
   fetch, and it exists in the registry only so a user searching for it is told why
   rather than assuming it was forgotten. A site that blocks robots.txt is refused. There
   is no version of this where we fetch it anyway.

3. **The tier is declared explicitly.** Every connector states a `ComplianceTier` and a
   default `SubmissionPolicy`. The registry refuses to register one that does not; there
   is no implicit allow anywhere.

4. **Discovery permission is not submission permission.** Being able to read a board says
   nothing about whether an automated browser may touch its application form.
   `browser_submission_supported` defaults to false and stays false until someone
   deliberately opts the connector in.

Only after all four is it worth writing a connector -- and that also means updating
`docs/COMPLIANCE.md`, which is the normative statement, plus tests. This command does not
write connectors.

## Run this

Search for a company's board:

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli add-portal "$ARGUMENTS"
```

Subscribe to one of the results:

```bash
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli add-portal --add <connector> --identifier <identifier>
```

`--catalog` lists the curated starting-point sources instead of searching, with a column
saying which are usable on this install and which are already added. In PowerShell,
prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli add-portal "<company>"`.
If a flag is rejected, run `--help` and use what it prints.

The web app's Settings -> Sources drives the same two functions, so either front door is
fine and both land in the same place.

## Reading the output

- The argument is a **company name**, and only a company name. The finder derives the
  obvious board slugs and probes the five documented public job-board APIs this project
  already integrates -- Greenhouse, Lever, Ashby, SmartRecruiters, Workable. It probes
  those endpoints and nothing else: no search engines, no arbitrary URLs, no HTML. You
  get back the boards that actually exist and their job counts, and you subscribe to one.
- **A board URL is not a supported argument.** Nothing here resolves a pasted link to a
  connector. If the user has a URL, either work out which company it belongs to and
  search for that, or add the individual posting with `/apply`.
- Nothing found is a normal outcome. Many companies do not run a public board. Say so and
  offer `/apply` with a pasted job, rather than hunting for a scrapeable page.
- Subscribing goes through the same admission check as the API: a `MANUAL_ONLY` connector
  is refused with the reason, and so is one whose credentials are not configured. A
  refusal here is the system working; report it and stop.
- A newly added source is validated on its first discovery run. If that run comes back
  `blocked_by_policy`, the source is disabled and not retried. That is also the system
  working.

## Guardrails that apply here

- Never add LinkedIn, Indeed or Naukri as a pollable source. LinkedIn and Indeed need
  your own partner credentials and still have no endpoint wired up; Naukri cannot be
  polled at all. All three are prohibited for automated submission, permanently.
- Never write or extend a connector that fetches arbitrary HTML. The careers-page
  connector reads published structured data only, and aborts on a robots.txt Disallow, a
  login wall or a bot wall.
- Never add a probe outside the documented five without the same compliance review as a
  new connector.
- Do not work around this command by inserting a `job_source_subscriptions` row directly
  or by calling the service modules -- the admission check lives in the route both front
  doors call, and bypassing it is how an unreviewed platform gets polled.
- Do not remove a rail to reach a platform that already said no. That is the one failure
  mode this project is built to prevent.
