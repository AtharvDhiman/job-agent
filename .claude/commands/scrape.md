---
description: Poll every enabled job source once, then normalise and de-duplicate what comes back.
argument-hint: "[--limit N] [--min-score N]"
---

# /scrape

Runs one discovery pass over the sources this account has subscribed to. Each source is
fetched with the polite HTTP client, the postings are normalised (salary, location,
seniority, skills), de-duplicated against what is already stored, and written to the
`jobs` table. Everything stored is then scored. Nothing is drafted or submitted here.

## Run this

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli scrape --limit 10
```

In PowerShell, prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli scrape --limit 10`.

**Every enabled source is polled, always.** No flag caps that, and none should be
invented: a pass that silently skipped sources would report "nothing new" for a board it
never asked. `--limit N` sets how many matches are printed after scoring (default 10),
and `--min-score N` hides printed matches below a score; both shape the table, not the
work. If the CLI rejects a flag, run the same command with `--help` and use what it
prints. Do not guess flags, and do not work around the CLI by calling the service modules
directly -- the CLI is the supported entry point.

## Reading the output

You get one line per source: connector key, identifier, and counts for fetched, created,
updated and duplicates, plus a status.

- **`ok`** -- the source answered. `created 0` is normal and usually means the board has
  posted nothing new, or returned `304 Not Modified` because the ETag matched. Say that
  plainly rather than implying something failed.
- **`blocked_by_policy`** -- the connector refused for a compliance reason (robots.txt
  disallowed the path, a bot wall or login wall appeared, or the platform forbids
  automated fetching). This is permanent. The source is disabled and will not be retried.
  Report the reason verbatim. Do not re-enable it, do not retry it with different
  headers, and do not look for another way in.
- **`error`** -- a transient failure: timeout, bad payload, 5xx. It will be retried on the
  next pass. Five consecutive errors disable the source automatically.

Then the scoring summary -- scored, shortlisted, set aside -- and the printed table.

New jobs are not the same as jobs you will see. `JOB_MAX_AGE_HOURS` (default 48) filters
older postings out at ranking time, so a quiet board can legitimately produce a scrape
that creates rows and a rank that shortlists nothing.

With no enabled sources the command prints "No enabled sources. Nothing was fetched." and
still scores what is already stored; that is not a failure. If every source reports
`never_run` or points at `EXAMPLE_BOARD_TOKEN`, the seeded placeholders were never
replaced. Tell the user to add a real source with `/add-portal` before running this
again.

## Guardrails that apply here

- Every host's `robots.txt` is fetched and obeyed before any other request. A site whose
  robots.txt cannot even be read is treated as a refusal, not as permission.
- Requests identify this agent in the User-Agent, are rate-limited per host, and use
  conditional requests so an unchanged board is not re-downloaded.
- A CAPTCHA, bot-check, login wall or paywall is a full stop. There is no solver, no
  proxy rotation and no fingerprint spoofing in this codebase, and none may be added.
- LinkedIn and Indeed refuse to fetch without your own partner credentials. Naukri cannot
  be polled at all -- it publishes no candidate API and serves 403 for its own robots.txt.
  For all three, the supported path is pasting an individual job with `/apply`.
- Careers-page sources read only published structured data (schema.org JobPosting
  JSON-LD). A page with no structured data is skipped rather than scraped by layout.

Adding a new source is a separate decision with its own compliance test. Use
`/add-portal`; never widen a connector's reach to make a scrape succeed.
