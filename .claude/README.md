# Terminal commands

Slash commands for driving the job agent from Claude Code. Each file in `commands/` is
the instruction Claude follows when you type `/<name>`; each one names the exact CLI
command it runs and the guardrails that apply to it.

| Command | What it does |
|---|---|
| `/scrape` | Polls every enabled source once, normalises and de-duplicates the postings. |
| `/rank` | Scores stored jobs against your profile and rebuilds the shortlist. |
| `/apply <job-id \| url>` | Drafts a resume, cover letter and answers for one job, then stops at the review gate. |
| `/status` | Snapshot: sources, matches, drafts, open reviews, and which gates are shut. |
| `/report [--days N]` | Written summary of a recent window: found, scored, drafted, blocked. |
| `/verify-facts` | Walks you through the facts parsed from your resume so you can confirm each one. |
| `/add-portal <company \| url>` | Adds a source, after the compliance test that comes before any code. |

## One engine, one database

The terminal and the web app are the same program seen from two doors. The CLI imports
the same service modules the FastAPI app imports — discovery, matching,
application_workflow, policy — and runs them against the same `DATABASE_URL`. A draft you
create with `/apply` appears in the web review queue; an authorization you grant in the
web UI changes what `/apply` is allowed to do on the next run; a fact you confirm with
`/verify-facts` is the same row the generator reads when the background worker drafts
something at 3am. There is no terminal-only path and no terminal-only permission: every
decision about whether something may be filled or sent goes through the one policy gate,
which fails closed, and which does not care which door you came in by.

## Running the commands by hand

Every command file gives its exact invocation. Two things to know:

- The interpreter path contains a space, so it stays quoted:
  `"D:/job agent/backend/.venv/Scripts/python.exe"`.
- Windows PowerShell 5.1 has no `&&`, and a quoted executable path needs the call
  operator. Run the `cd` on its own line, then
  `& "D:/job agent/backend/.venv/Scripts/python.exe" -m app.cli <command>`.

If the CLI rejects a flag, run the same command with `--help` and use what it prints. Do
not guess flags, and do not reach past the CLI into the service modules — the numbers you
would produce that way are the ones that disagree with the web app.
