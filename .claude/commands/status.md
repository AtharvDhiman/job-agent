---
description: "Where the pipeline stands: sources, portal readiness, the six buckets, and which gates are shut."
argument-hint: "[--hours N]"
---

# /status

A read-only snapshot of the whole pipeline for this account. It reports; it grants
nothing and changes nothing -- not even the agent-settings row for an account that has
none, which it renders from the defaults rather than creating.

## Run this

```bash
cd "D:/job agent/backend"
"D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli status
```

In PowerShell, prefix the interpreter with the call operator: `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli status`.

`--hours N` sets the window for the bucket counts (default 24, maximum 720). If a flag is
rejected, run `--help` and use what it prints.

## Reading the output

Work through it in pipeline order and name the first thing that is actually stopping
progress, rather than listing everything at equal weight. The command prints six blocks,
in this order:

- **The header** -- which database this is, whether automation is on or paused and why,
  whether the server kill-switch is on, applications used today against the daily limit,
  the auto-submit threshold, and whether drafting is running on Claude or
  deterministically.
- **Profile** -- completeness as "N of 14 fields", the specific fields still needed, the
  verified-versus-total fact count, and whether a resume has been uploaded. Only the
  fields that change what the agent does are counted, so this is not "percent of the
  form". A large unverified pile is the most common reason drafts come out thin; point at
  `/verify-facts`.
- **Sources** -- how many are subscribed and enabled, when each last ran, its
  `last_status`, and how many jobs it has seen. `blocked_by_policy` is permanent and the
  source is disabled. `never_run` on every source usually means the seeded `EXAMPLE_*`
  placeholders are still in place. Five consecutive errors disable a source
  automatically.
- **Portals** -- per-platform readiness: `ready`, `authorized`, `discovery_only`,
  `blocked`, `unsupported`, each with the nearest blocker in words and a count of how
  many more are behind it. Note that "discovery works" and "submission is supported" are
  two different facts about a portal, and a portal can be discoverable while its forms
  are not automatable at all.
- **Buckets** -- the six pipeline counts over the window: new jobs found, high match,
  queued for auto-submit, needs review, submitted, failed or stopped, plus a breakdown of
  what stopped the last group. These are the counts to quote for "how many are waiting on
  me" and "how many went out". The same function produces the web dashboard's numbers and
  the `/report` file's, so all three agree by construction.
- **Next steps** -- the readiness checklist, in order, of what is still shut.

That is the whole output. There is no per-status application table and no listing of
individual review tasks here -- the "needs review" bucket is the count, and the tasks
themselves live in the web app's review queue. `/rank` explains why a shortlist is empty;
`/report` writes the per-job detail to a file. Do not present sections this command does
not print.

If the user asks "why has nothing been submitted?", the honest answer is usually that
several gates are shut at once and that this is the shipped default. List them in order
and let the user decide which, if any, to open.

## Guardrails that apply here

- This is a reporting view. The portal readiness section reads the same inputs the policy
  gate reads and describes them; it does not decide anything. At action time the policy
  gate re-decides, and if the two ever disagree the policy gate wins.
- "Ready" means a matching job would get as far as a filled form on a portal the user
  authorized. It never means something will be sent without the user.
- Do not change a setting because status reported it as closed. Report it and ask.
