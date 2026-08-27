---
description: Walk through the career facts parsed from a resume and confirm each one with the user.
argument-hint: "[--fact-id ID] [--all] [--category NAME]"
---

# /verify-facts

Uploading a resume creates `career_facts` rows -- employers, titles, dates, skills,
education, achievements -- and every one of them is written **unverified**. This command
walks the user through them and marks verified only what the user confirms.

## Why this matters

This is the single step that decides how good the documents are.

- The document generator drops unverified facts before it writes a line. An unverified
  fact cannot appear in a resume or a cover letter, at all, ever.
- The fact guard then re-reads the generated text and flags any employer, title, date,
  degree, certification, number or link that does not trace back to a verified fact. A
  flagged document cannot be auto-submitted.
- Only verified facts feed the semantic component of ranking.

So an unverified profile does not produce a wrong resume -- it produces a **thin** one:
correct, traceable and nearly empty, because almost everything the parser found was
invisible to the generator. If the user says the drafts look sparse, this is almost
always the reason.

Verification is a human act. The parser proposes; the person confirms. Claude never
confirms on the user's behalf.

## Run this

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli verify-facts
```

In PowerShell, prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli verify-facts`.

With no arguments this lists what is still unverified, grouped by category, each with
its id. Then, for one fact the user has just said yes to:

```bash
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli verify-facts --fact-id <id>
```

`--all` widens the listing to include already-verified facts (it writes nothing).
`--category employment` narrows it. `--unverify` with `--fact-id` takes a verification
back. If a flag is rejected, run `--help` and use what it prints.

`--fact-id` takes exactly one id, and there is no batch flag. That is deliberate, not a
missing feature: a single "yes to all" against a long list is not a confirmation of each
item on it.

## How to run the walkthrough

1. List the unverified facts. They come back grouped by category -- employment,
   education, certification, skill, project, achievement -- so the user reviews like
   with like.
2. Show each fact **verbatim**, exactly as the command printed it: organization, title,
   dates, the value text and any highlights. Do not summarise, tidy or improve it in the
   display. The user is confirming the string that will end up in a document.
3. For each one, ask for a plain yes or no. A silence, a "looks fine", or a batch
   "yes to all" on a long list is not a confirmation of each item -- read them out.
4. Verify each yes on its own: `verify-facts --fact-id <id>`.
5. Where the parser got something wrong (a mangled date range, a title split across
   lines, a skill that is really a tool name), correct the fact **to what the user says
   is true** on the web app's Profile page first, then verify the corrected text. Editing
   a fact clears its verification on purpose, so the corrected wording gets its own yes.
   Never adjust a fact's wording to make it fit a job posting.
6. Anything the user is unsure about stays unverified. Unverified is the safe state, not
   a backlog item to clear.
7. Facts the user says are simply wrong should be deleted on the Profile page, not left
   unverified and ignored.

## Afterwards

Verified facts change the profile, and existing match scores were computed against the
profile as it was before. Re-run `/rank` so the shortlist reflects the confirmed profile,
then re-draft any application the user cares about.

## Guardrails that apply here

- **`verify-facts --fact-id` and the web app's Profile page are the only two ways a fact
  may ever become verified.** Never set `verified` by writing to the database, by calling
  the API, or by editing a model -- and never work around this command by calling the
  service modules directly. Every downstream guarantee in this system (the generator, the
  fact guard, what ranking means) rests on that flag meaning "a human said yes to this
  exact text". A route that skips the human makes all three claims false at once.
- Never mark a fact verified without an explicit yes from the user for that specific
  fact. Verification is the load-bearing claim in this whole system.
- Never invent a fact to fill a gap, and never expand a sparse one into something more
  impressive. If a bullet has no number in it, it has no number.
- Work authorization, visa status, salary history and expectations, and protected
  characteristics are not guessed from a resume. They come from explicit profile fields
  the user fills in, or they go to review.
- Some facts are marked sensitive; the listing says so. Do not echo them into a shared
  summary.
