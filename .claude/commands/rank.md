---
description: Score stored jobs against the profile and rebuild the shortlist.
argument-hint: "[--scan-limit N] [--limit N] [--min-score N]"
---

# /rank

Scores every recent job against this account's profile and verified facts, writes a
`job_matches` row per job, and marks the ones at or above the shortlist threshold
(`shortlist_min_score`, default 60). Deterministic: no network, no LLM. The same inputs
always produce the same number.

## Run this

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli rank
```

In PowerShell, prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli rank`.

Three flags, and they do different jobs:

- `--scan-limit N` sizes the **scoring pass**: how many stored jobs get scored (default
  500, maximum 2000).
- `--limit N` sizes the **printed table** only: how many matches appear afterwards
  (default 10). It changes nothing about what was scored.
- `--min-score N` hides printed matches below a score. If it hides every one, the command
  says so explicitly rather than claiming nothing was scored.

If a flag is rejected, run `--help` and use what it prints.

## Reading the output

The summary is three counts: **scored**, **shortlisted**, **rejected**.

If it exits non-zero with "Nothing could be scored: this account has no profile or no
agent settings yet", that is what it sounds like -- the account has no candidate profile
row or no agent settings row. Nothing is broken; there is simply nothing to score
against. Point the user at profile setup, not at a bug.

Each score is out of 100, assembled from eight weighted components: skills 35, semantic
similarity 20, title 15, seniority 10, location 10, salary 5, freshness 3, direct
employer 2. The stored explanation prints the points each component earned. Read that
arithmetic back to the user rather than paraphrasing it as a verdict -- "skills 12/35,
because three of your eleven listed skills appear in the posting" is useful; "weak match"
is not.

**Rejected** does not mean "scored low". A hard filter is an absolute disqualifier, and
the decision names which one fired:

- the company is on the avoid list, or the posting contains an excluded keyword
- the location is outside the preferred countries and the role is not remote
- the work arrangement is one the user said they do not accept
- the posting says it cannot sponsor and the profile requires sponsorship
- the advertised maximum salary is below the stated minimum, in comparable currency and
  period

Those come from the profile. If one looks wrong, the fix is the profile field, not the
ranking code.

## Guardrails that apply here

- **Re-ranking is destructive.** Every existing `job_matches` row for the account is
  deleted before the new pass runs -- including any match the user dismissed by hand, so
  those dismissals do not survive it. Say so before running this on an account where the
  user has been triaging a shortlist.
- Scores describe the profile **as it was when the match row was written**. If the
  profile or a verified fact has changed since, the old numbers describe someone who is
  no longer on file -- re-rank rather than reading stale matches. Losing the dismissals
  above is the price of that, and it is a deliberate trade, not a bug.
- Only **verified** career facts contribute to the semantic component. An unverified
  profile ranks thin because there is genuinely little to match on. `/verify-facts` is
  the fix; editing facts to flatter a posting is not.
- Ranking never contacts a job board and never writes an application. A high score is a
  shortlist entry, nothing more -- drafting and submission are separate, gated steps.
- The weights sum to 100 and the test suite asserts it. Do not tune a weight to move one
  job up a list.
