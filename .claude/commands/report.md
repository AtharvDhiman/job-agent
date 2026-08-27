---
description: "Write the offline HTML report of the recent pipeline: found, scored, drafted, blocked."
argument-hint: "[--hours N] [--out FILE]"
---

# /report

Renders one self-contained HTML file describing where the pipeline stands: the
six pipeline buckets over a recent window, every scored job with the decision
that was made about it, per-portal readiness, and the reasons things did not go
further. `/status` prints a snapshot to the terminal; this writes a document the
user can keep, mail to themselves, or open two years from now with nothing
running.

## Run this

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli report --hours 168
```

In PowerShell, prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli report --hours 168`.

`--hours N` sets the activity window the bucket counts use (default 24, maximum
720; 168 is a week). `--out FILE` chooses the destination; without it the file
goes to `<storage_root>/reports/dashboard.html`. Those two flags plus `--email`
are the whole surface — there is no `--days`. If a flag is rejected, run
`--help` and use what it prints.

## Reading the output

**The command's entire stdout is one line: the path it wrote.** There is no
narrative on the terminal to read back, so report where the file is, and open it
if the user wants the numbers. Do not assemble those numbers by querying the
database yourself -- they must come from the same code path the web app uses, or
the two will disagree.

The file contains, in this order:

- **Header stats** -- automation on / paused / killed and why, applications used
  today against the daily limit, the auto-submit score, the shortlist score, the
  job freshness window, and whether drafting is running on Claude or
  deterministically.
- **Pipeline** -- six cards: new jobs found, high match, queued for auto-submit,
  needs review, submitted, failed or stopped. The last one carries a breakdown by
  review-task reason, because a status of "failed" never says why. These are the
  same six counts `GET /dashboard` returns and `/status` prints; one function in
  `services/pipeline.py` computes them for all three.
- **Scored jobs** -- every scored job with its score, title, company, source,
  decision and posted date, sortable and filterable in the browser. Capped at the
  500 highest-scoring; the caption says so when it truncates.
- **Portal readiness** -- one card per platform: its status, what that status
  costs the user, how many sources point at it, and every blocker in words.
- **Rejection reasons** -- two panels. "Why jobs were not shortlisted" covers
  every match ever scored rather than the window, because a 24-hour slice hides
  what keeps happening. "Why applications stopped" is taken from the attached
  review task.

There is no per-source discovery table, no per-application detail, and no fact
guard or critique summary in this file. `/scrape` reports discovery per source;
`/apply` reports the fact guard and the critique for one application. Do not
describe sections the report does not contain.

## Guardrails that apply here

- This is read-only. It does not re-rank, re-draft or submit anything, and it
  does not create the agent-settings row for an account that has none.
- Every figure comes from stored rows. Do not estimate, extrapolate, or fill a
  gap with a plausible number.
- Do not include the contents of encrypted fields (phone, address,
  work-authorization notes, answer values, confirmation numbers) in a summary the
  user may share. The report itself contains none of them.
- The file is deliberately self-contained: styles, scripts and data are inline,
  and it makes no network request when opened. Do not add an asset to it.
- There is no PDF form of this report; it is HTML. Resumes and cover letters are
  a different thing -- `/apply` renders those to PDF with reportlab and reads the
  text layer back before attaching them. There is no LaTeX toolchain installed.
