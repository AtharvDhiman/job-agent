---
description: Draft an application for one job, given its id or its URL, and stop at the review gate.
argument-hint: "<job-id | job-url>"
---

# /apply

Drafts a tailored resume and cover letter for a single job, answers the screening
questions a verified fact can answer, runs the fact guard and the document critic, and
then **stops**. The output is a draft plus the apply URL a human opens. This command does
not submit anything.

Target: `$ARGUMENTS` -- either a job id already in the database, or a job URL. The two
take different flags, so pick by the shape of the argument.

## Run this

The argument is **never positional**. If it is a UUID that is already in the database:

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli apply --job-id "$ARGUMENTS"
```

If it is a link, `--url` requires three more flags -- all of them, every time:

```bash
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli apply \
  --url "$ARGUMENTS" \
  --company "<employer>" \
  --title "<job title>" \
  --description-file "<path to a file holding the pasted description>"
```

`--location` is optional and takes the location as the posting states it. Pass exactly
one of `--job-id` and `--url`; passing both, or neither, is refused. In PowerShell,
prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli apply --job-id "<uuid>"`.
If a flag is rejected, run `--help` and use what it prints.

### If the argument is a URL the agent has never seen

Nothing is fetched from an arbitrary URL to build a job row. The supported path is
quick-add: the user pastes the **title, company, location and description text they are
already reading**, and the agent extracts skills, salary, seniority and sponsorship from
that text. Ask for those four things rather than trying to retrieve the page -- the
description goes into a file that `--description-file` points at, and an empty file is
refused. The host in the URL still determines the platform, so a Naukri link is recorded
as Naukri and inherits Naukri's prohibited submission policy.

## Reading the output

You get an application row plus a policy decision. Report all of it:

- **policy** -- the effective submission policy for this platform, and `granted_policy`,
  the authorization the user actually granted. `may_autofill` and `may_submit` are
  separate booleans; both false is the default and is not an error.
- **review_reasons** -- why it stopped. Common ones: the platform was never authorized,
  the platform is prohibited, the score is under the auto-submit threshold, the daily
  limit is reached, automation is disabled, a question has no verified fact behind it.
- **fact_guard flags** -- spans in the generated text that could not be traced to a
  verified fact. A flagged document cannot be auto-submitted. Show the offending spans.
- **critique** -- an advisory 0-100 score per document with specific findings (keyword
  coverage, length, weak verbs). It never blocks anything. Offer the findings; do not
  rewrite the document to chase the number by adding claims.
- **documents** -- each generated document is written twice: the markdown, and a
  single-column ATS-safe PDF rendered from it.
- **PDF text layer** -- the PDF is read back after rendering and compared with the text
  it came from, because a PDF is parsed by an ATS as a drawing that happens to contain
  text and can look immaculate while extracting as garbage. A clean pass is one line. A
  failure names the words lost or the characters mangled, the PDF is **not** attached,
  and the failure is counted as a validation error like any other. Report a failure; do
  not offer to send the PDF anyway.
- **validation errors** and **blocking questions** -- each blocking question carries the
  question text and the reason it could not be answered. These are for the human.
- **the apply URL** -- the link the human opens to finish the application themselves.

Say clearly what state this left things in: a draft exists, it is in the review queue,
and nothing has been sent.

## Guardrails that apply here

- **LinkedIn, Indeed and Naukri can never be auto-submitted.** They are pinned to
  `PROHIBITED` in code, the grant endpoint returns 403 for them, and no setting, flag or
  approval lifts it. Do not offer a workaround; there isn't one, by design.
- **On those three, this command refuses before it drafts anything.** It exits non-zero
  and prints the apply URL. No documents are generated, no application row is created,
  and **no review task is opened** -- so do not tell the user their LinkedIn job is
  waiting in the review queue, because nothing was queued. The job itself is saved and
  correctly attributed (a pasted link is filed by host first), and the honest next step
  is that the user opens the printed URL and applies themselves. The web app behaves
  differently here on purpose: it drafts and queues a review task. This command does not.
- Every other platform starts at `review_required`. Automated submission needs all of:
  the global kill-switch on, a typed per-platform authorization, a score at or above the
  auto-submit threshold, the daily limit not reached, a clean fact guard, and every
  required question answered from a verified fact.
- Every sentence in the resume and cover letter comes from a verified `career_facts` row
  or a stored profile field. Nothing is invented -- not an employer, a date, a degree, a
  metric, a visa status or a link. If the draft looks thin, the answer is `/verify-facts`,
  never better prose.
- Work authorization, visa status, salary history and expectations, and protected
  characteristics are never inferred. EEO fields default to "prefer not to say".
- Re-drafting an existing application is destructive: it bumps the version and replaces
  the previous documents and answers. Say so before re-running it on an application the
  user has already edited.
- You must not fill or submit the employer's form on the user's behalf from this command.
  Hand over the draft and the link.
