"""One self-contained HTML file describing everything the agent has done.

The dashboard already answers these questions, but only while the API, the
database and the frontend are all running. This renders the same picture into a
single file the user can keep, mail to themselves, or open from a USB stick two
years from now -- so there is no <link>, no <script src>, no webfont and no
image URL anywhere in the output. Everything is inline, and nothing here fetches
anything.

Two rules hold this module together:

  * Every value goes through `_esc`. Titles, company names and apply URLs come
    from third-party feeds, and this document is opened as a local file, where
    an unescaped `<script>` in a job title would run against `file://` with no
    origin policy to contain it.
  * Nothing is computed here that a service already computes. Buckets, portal
    readiness and reason labels are read from the modules that own them; this
    file only decides what the result looks like.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import agent_settings_or_default
from app.core.config import settings as app_settings
from app.core.enums import MatchDecision
from app.models.application import Application, ReviewTask
from app.models.job import Job, JobMatch
from app.models.profile import CandidateProfile
from app.models.user import AgentSettings, User
from app.services import application_workflow as workflow
from app.services import llm, pipeline, portal_status

#: MatchDecision -> plain English. Mirrors MATCH_DECISION_LABEL in
#: frontend/src/lib/labels.ts; the backend has never had its own copy, and an
#: offline file cannot borrow the frontend's.
MATCH_DECISION_LABELS: dict[str, str] = {
    MatchDecision.SHORTLISTED.value: "Shortlisted",
    MatchDecision.BELOW_THRESHOLD.value: "Scored below your shortlist threshold",
    MatchDecision.REJECTED_HARD_FILTER.value: "Failed a hard filter",
    MatchDecision.EXCLUDED_COMPANY.value: "Company on your avoid list",
    MatchDecision.EXCLUDED_KEYWORD.value: "Contained an excluded keyword",
    MatchDecision.STALE_POSTING.value: "Older than your posting window",
    MatchDecision.DUPLICATE.value: "Duplicate of another listing",
}

#: Tone per decision, so the table reads at a glance: green is a shortlist, amber
#: is a rule the user wrote, grey is arithmetic.
_DECISION_TONES: dict[str, str] = {
    MatchDecision.SHORTLISTED.value: "ok",
    MatchDecision.REJECTED_HARD_FILTER.value: "warn",
    MatchDecision.EXCLUDED_COMPANY.value: "warn",
    MatchDecision.EXCLUDED_KEYWORD.value: "warn",
}

#: What each portal_status.STATUS_ORDER value means for a submission. This is the
#: "and WHY" half of portal readiness: the per-portal blockers say what is off,
#: these say what being in that state costs the user.
#:
#: Keyed by portal_status's own vocabulary, and test_html_report asserts the two
#: sets are equal -- a new status must arrive with the sentence that explains
#: what it costs, rather than rendering as a portal with nothing said about it.
PORTAL_STATUS_NOTES: dict[str, tuple[str, str]] = {
    "ready": ("ok", "Authorized and unblocked: an eligible job here can be submitted for you."),
    "authorized": ("warn", "Authorized, but something listed below stops a submission today."),
    "discovery_only": (
        "warn",
        "Review only. Jobs are found and drafted; you press submit yourself.",
    ),
    "blocked": (
        "stop",
        "Review only, permanently. This platform's terms forbid automated applying, "
        "so the agent will never submit here.",
    ),
    "unsupported": (
        "neutral",
        "Review only. Nothing is discovered automatically; you add postings yourself.",
    ),
}


# --------------------------------------------------------------------------
# escaping and formatting
# --------------------------------------------------------------------------


def _esc(value: Any) -> str:
    """Escape anything for HTML, including inside a double-quoted attribute."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _safe_href(url: str | None) -> str:
    """An escaped href for plain http(s) only, otherwise "".

    html.escape says nothing about the scheme. A feed that hands us a
    `javascript:` apply_url would otherwise become a one-click script execution
    in a file the user opened from their own disk.
    """
    candidate = (url or "").strip()
    lowered = candidate.lower()
    if not lowered.startswith(("http://", "https://")):
        return ""
    return _esc(candidate)


def _fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return "--"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_date(value: datetime | None) -> str:
    if value is None:
        return "--"
    return value.strftime("%Y-%m-%d")


def _chip(text: str, tone: str = "neutral") -> str:
    return f'<span class="chip chip--{_esc(tone)}">{_esc(text)}</span>'


# --------------------------------------------------------------------------
# data gathering
# --------------------------------------------------------------------------


def _candidate_name(db: Session, user: User) -> str:
    """Profile name first: it is the name that goes on the documents."""
    profile_name = db.execute(
        select(CandidateProfile.full_name).where(CandidateProfile.user_id == user.id)
    ).scalar_one_or_none()
    return (profile_name or "").strip() or (user.full_name or "").strip() or user.email


def _scored_jobs(db: Session, user: User, limit: int) -> list[tuple[JobMatch, Job]]:
    return [
        (match, job)
        for match, job in db.execute(
            select(JobMatch, Job)
            .join(Job, Job.id == JobMatch.job_id)
            .where(JobMatch.user_id == user.id)
            .order_by(JobMatch.score.desc(), Job.posted_at.desc().nullslast())
            .limit(limit)
        ).all()
    ]


def _decision_breakdown(db: Session, user: User) -> list[tuple[str, int]]:
    """Why jobs were not shortlisted, over every match rather than a window.

    The buckets above are deliberately windowed -- they answer "what happened
    lately". This answers "what keeps happening", which a 24-hour slice hides.
    """
    return [
        (str(decision), int(total))
        for decision, total in db.execute(
            select(JobMatch.decision, func.count(JobMatch.id))
            .where(
                JobMatch.user_id == user.id,
                JobMatch.decision != MatchDecision.SHORTLISTED.value,
            )
            .group_by(JobMatch.decision)
            .order_by(func.count(JobMatch.id).desc())
        ).all()
    ]


def _stop_breakdown(db: Session, user: User) -> list[tuple[str, int]]:
    """Why applications stopped, taken from the ReviewTask, not the status.

    "failed" never says why; the review task attached to it does.
    """
    return [
        (str(reason), int(total))
        for reason, total in db.execute(
            select(ReviewTask.reason, func.count(ReviewTask.id))
            .join(Application, Application.id == ReviewTask.application_id)
            .where(
                ReviewTask.user_id == user.id,
                Application.status.in_(pipeline.STOPPED_STATUSES),
            )
            .group_by(ReviewTask.reason)
            .order_by(func.count(ReviewTask.id).desc())
        ).all()
    ]


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------


def _header_html(
    name: str,
    generated_at: datetime,
    agent_settings: AgentSettings,
    submitted_today: int,
    hours: int,
) -> str:
    if not app_settings.automation_global_enabled:
        automation = ("stop", "Off (server kill-switch)")
    elif not agent_settings.automation_enabled:
        paused = agent_settings.paused_reason or ""
        automation = ("stop", "Paused" + (f" -- {paused}" if paused else ""))
    else:
        automation = ("ok", "On")

    usage = f"{submitted_today} / {agent_settings.daily_application_limit}"
    usage_tone = "warn" if submitted_today >= agent_settings.daily_application_limit else "ok"
    stats = (
        ("Automation", automation[1], automation[0]),
        ("Applications today", usage, usage_tone),
        ("Auto-submit at", f"{agent_settings.auto_submit_min_score}+", "neutral"),
        ("Shortlist at", f"{agent_settings.shortlist_min_score}+", "neutral"),
        ("Job freshness window", f"{agent_settings.job_max_age_hours}h", "neutral"),
        ("Drafting", "Claude" if llm.is_enabled() else "Deterministic", "neutral"),
    )
    cells = "\n".join(
        f'<div class="stat"><span class="stat__label">{_esc(label)}</span>'
        f'<span class="stat__value stat__value--{_esc(tone)}">{_esc(value)}</span></div>'
        for label, value, tone in stats
    )
    return f"""<header class="page-head">
  <div>
    <h1>Job agent report</h1>
    <p class="muted">{_esc(name)} &middot; generated {_esc(_fmt_datetime(generated_at))}
      &middot; activity window {_esc(hours)}h</p>
  </div>
  <div class="stats">
{cells}
  </div>
</header>"""


def _empty_html(state: dict[str, Any]) -> str:
    return f"""<section class="callout">
  <h2>Nothing scored yet</h2>
  <p>{_esc(state.get("message", ""))}</p>
  <p><strong>Next step:</strong> {_esc(state.get("next_step", ""))}</p>
</section>"""


def _buckets_html(counts: dict[str, dict], stop_reasons: list[tuple[str, int]]) -> str:
    """One card per bucket, from `pipeline.buckets` -- the API's own numbers.

    The `link` each bucket carries is a frontend route and is deliberately
    dropped: this file is opened from disk, where no such route resolves.
    """
    cards = []
    for key, label, note in pipeline.BUCKET_LABELS:
        value = counts.get(key, {}).get("count", 0)
        extra = ""
        if key == "failed_or_stopped" and stop_reasons:
            items = "".join(
                f"<li>{_esc(pipeline.reason_label(reason))} <b>{_esc(total)}</b></li>"
                for reason, total in stop_reasons
            )
            extra = f'<ul class="card__reasons">{items}</ul>'
        cards.append(
            f'<article class="card"><span class="card__label">{_esc(label)}</span>'
            f'<span class="card__value">{_esc(value)}</span>'
            f'<span class="card__note">{_esc(note)}</span>{extra}</article>'
        )
    joined = "\n".join(cards)
    return f"""<section>
  <h2>Pipeline</h2>
  <div class="cards">
{joined}
  </div>
</section>"""


def _jobs_html(rows: list[tuple[JobMatch, Job]], agent_settings: AgentSettings, total: int) -> str:
    if not rows:
        return """<section>
  <h2>Scored jobs</h2>
  <p class="muted">No jobs have been scored yet, so there is nothing to sort.</p>
</section>"""

    options = "\n".join(
        f'<option value="{_esc(value)}">{_esc(label)}</option>'
        for value, label in MATCH_DECISION_LABELS.items()
    )

    body = []
    for match, job in rows:
        decision = match.decision or ""
        tone = _DECISION_TONES.get(decision, "neutral")
        if match.score >= agent_settings.auto_submit_min_score:
            score_tone = "ok"
        elif match.score >= agent_settings.shortlist_min_score:
            score_tone = "warn"
        else:
            score_tone = "neutral"

        href = _safe_href(job.apply_url or job.source_url)
        link = (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">Apply</a>'
            if href
            else '<span class="muted">No link</span>'
        )
        dismissed = _chip("dismissed", "neutral") if match.dismissed_at else ""
        haystack = f"{job.title or ''} {job.company or ''} {job.connector_key or ''}".lower()
        posted_sort = job.posted_at.isoformat() if job.posted_at else ""
        body.append(
            f'<tr data-search="{_esc(haystack)}" data-decision="{_esc(decision)}" '
            f'data-score="{_esc(match.score)}">'
            f'<td data-sort="{_esc(match.score)}">'
            f'<span class="score score--{_esc(score_tone)}">{_esc(match.score)}</span></td>'
            f"<td>{_esc(job.title)}</td>"
            f"<td>{_esc(job.company)}</td>"
            f"<td>{_esc(job.connector_key)}</td>"
            f"<td>{_chip(MATCH_DECISION_LABELS.get(decision, decision), tone)}{dismissed}</td>"
            f'<td data-sort="{_esc(posted_sort)}">{_esc(_fmt_date(job.posted_at))}</td>'
            f"<td>{link}</td>"
            "</tr>"
        )
    rows_html = "\n".join(body)
    shown = len(rows)
    caption = (
        f"Showing the top {shown} of {total} scored jobs."
        if shown < total
        else f"All {total} scored jobs."
    )

    return f"""<section>
  <h2>Scored jobs</h2>
  <p class="muted">{_esc(caption)} Click a column heading to sort.</p>
  <div class="filters">
    <label>Search
      <input id="f-text" type="search" placeholder="title, company or source"
        autocomplete="off"></label>
    <label>Decision
      <select id="f-decision"><option value="">All</option>
{options}
      </select></label>
    <label>Minimum score
      <input id="f-score" type="number" min="0" max="100" step="5" value="0"></label>
    <span class="muted"><b id="shown-count">{_esc(shown)}</b> shown</span>
  </div>
  <div class="table-wrap">
    <table id="jobs">
      <thead>
        <tr>
          <th data-sortable data-type="number" data-desc-first tabindex="0"
            aria-sort="descending">Score</th>
          <th data-sortable tabindex="0">Title</th>
          <th data-sortable tabindex="0">Company</th>
          <th data-sortable tabindex="0">Source</th>
          <th data-sortable tabindex="0">Decision</th>
          <th data-sortable data-desc-first tabindex="0">Posted</th>
          <th>Link</th>
        </tr>
      </thead>
      <tbody>
{rows_html}
      </tbody>
    </table>
    <p id="no-matches" class="muted" hidden>No job matches those filters.</p>
  </div>
</section>"""


def _portals_html(states: list[dict[str, Any]]) -> str:
    if not states:
        return ""
    cards = []
    for state in states:
        status = str(state.get("status", ""))
        # A status with no sentence written for it renders no sentence at all,
        # rather than an empty paragraph that reads as "nothing to say here".
        tone, note = PORTAL_STATUS_NOTES.get(status, ("neutral", ""))
        note_html = f'<p class="portal__note">{_esc(note)}</p>' if note else ""
        blockers = state.get("blockers") or []
        blocker_html = (
            "<ul class='blockers'>"
            + "".join(f"<li>{_esc(line)}</li>" for line in blockers)
            + "</ul>"
            if blockers
            else '<p class="muted">Nothing is blocking this portal.</p>'
        )
        granted = state.get("granted_policy") or "none"
        meta = (
            f"{state.get('source_count', 0)} source(s), "
            f"{state.get('enabled_source_count', 0)} enabled &middot; "
            f"{state.get('jobs_seen', 0)} job(s) seen &middot; "
            f"tier {_esc(state.get('compliance_tier', ''))} &middot; "
            f"grant {_esc(granted)}"
        )
        cards.append(
            f'<article class="portal">'
            f'<div class="portal__head"><h3>{_esc(state.get("display_name", ""))}</h3>'
            f"{_chip(status.replace('_', ' '), tone)}</div>"
            f"{note_html}"
            f'<p class="muted portal__meta">{meta}</p>'
            f"{blocker_html}"
            f"</article>"
        )
    joined = "\n".join(cards)
    return f"""<section>
  <h2>Portal readiness</h2>
  <p class="muted">What each platform could do if a matching job arrived right now.
    services/policy.py still decides at action time; this only reports.</p>
  <div class="portals">
{joined}
  </div>
</section>"""


def _rejections_html(decisions: list[tuple[str, int]], stops: list[tuple[str, int]]) -> str:
    def table(title: str, note: str, rows: list[tuple[str, int]], labeller) -> str:
        if not rows:
            return (
                f"<div class='panel'><h3>{_esc(title)}</h3>"
                f"<p class='muted'>Nothing recorded yet.</p></div>"
            )
        body = "".join(
            f"<tr><td>{_esc(labeller(key))}</td><td class='num'>{_esc(total)}</td>"
            f"<td class='code'>{_esc(key)}</td></tr>"
            for key, total in rows
        )
        return (
            f"<div class='panel'><h3>{_esc(title)}</h3><p class='muted'>{_esc(note)}</p>"
            f"<table class='mini'><tbody>{body}</tbody></table></div>"
        )

    not_shortlisted = table(
        "Why jobs were not shortlisted",
        "Every scored job, not just this window.",
        decisions,
        lambda key: MATCH_DECISION_LABELS.get(key, key.replace("_", " ").capitalize()),
    )
    stopped = table(
        "Why applications stopped",
        "Taken from the review task, because a status of 'failed' never says why.",
        stops,
        pipeline.reason_label,
    )
    return f"""<section>
  <h2>Rejection reasons</h2>
  <div class="panels">
{not_shortlisted}
{stopped}
  </div>
</section>"""


# --------------------------------------------------------------------------
# inline assets -- kept whole so no external file is ever referenced
# --------------------------------------------------------------------------

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f5f6f8;
  --surface: #ffffff;
  --surface-2: #eef0f4;
  --border: #d6dae1;
  --text: #15181d;
  --muted: #5a6472;
  --accent: #2a4fbf;
  --ok-fg: #0f5f3c;
  --ok-bg: #dff2e7;
  --warn-fg: #7a4e00;
  --warn-bg: #fbeed2;
  --stop-fg: #8f1f24;
  --stop-bg: #fadfe0;
  --neutral-fg: #414b59;
  --neutral-bg: #e6e9ee;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101318;
    --surface: #181c23;
    --surface-2: #202632;
    --border: #303845;
    --text: #e8ebf0;
    --muted: #9aa5b5;
    --accent: #8fabff;
    --ok-fg: #86e0b0;
    --ok-bg: #14392a;
    --warn-fg: #f2c879;
    --warn-bg: #3d2f12;
    --stop-fg: #f5a3a6;
    --stop-bg: #401e21;
    --neutral-fg: #c2cad6;
    --neutral-bg: #262d39;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 24px;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 10px; }
h3 { font-size: 15px; margin: 0; }
p { margin: 6px 0; }
a { color: var(--accent); }
.muted { color: var(--muted); font-size: 13px; }
.page-head {
  display: flex; flex-wrap: wrap; gap: 16px; justify-content: space-between;
  align-items: flex-start; background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px;
}
.stats { display: flex; flex-wrap: wrap; gap: 8px; }
.stat {
  display: flex; flex-direction: column; gap: 2px; min-width: 116px;
  background: var(--surface-2); border-radius: 8px; padding: 8px 10px;
}
.stat__label { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; }
.stat__value { font-weight: 600; font-size: 14px; }
.stat__value--ok { color: var(--ok-fg); }
.stat__value--warn { color: var(--warn-fg); }
.stat__value--stop { color: var(--stop-fg); }
.callout {
  background: var(--warn-bg); color: var(--warn-fg); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; margin-top: 20px;
}
.callout h2 { margin: 0 0 6px; }
.cards, .portals, .panels {
  display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
}
.card, .portal, .panel {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px;
}
.card { display: flex; flex-direction: column; gap: 2px; }
.card__label { color: var(--muted); font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; }
.card__value { font-size: 28px; font-weight: 650; line-height: 1.1; }
.card__note { color: var(--muted); font-size: 12px; }
.card__reasons { margin: 8px 0 0; padding-left: 16px; font-size: 12px; color: var(--muted); }
.portal__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.portal__note { font-size: 13px; }
.portal__meta { font-size: 12px; }
.blockers { margin: 6px 0 0; padding-left: 16px; font-size: 12px; color: var(--muted); }
.chip {
  display: inline-block; border-radius: 999px; padding: 1px 8px; font-size: 11px;
  font-weight: 600; white-space: nowrap; margin-right: 4px;
  background: var(--neutral-bg); color: var(--neutral-fg);
}
.chip--ok { background: var(--ok-bg); color: var(--ok-fg); }
.chip--warn { background: var(--warn-bg); color: var(--warn-fg); }
.chip--stop { background: var(--stop-bg); color: var(--stop-fg); }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin: 10px 0; }
.filters label { display: flex; flex-direction: column; gap: 3px; font-size: 12px;
  color: var(--muted); }
.filters input, .filters select {
  background: var(--surface); color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 6px 8px; font: inherit; font-size: 13px; min-width: 150px;
}
/* A scroll box of its own so the sticky header has something to stick to; a
   plain overflow-x wrapper never scrolls vertically, and the header never pins. */
.table-wrap { overflow: auto; max-height: 70vh; border: 1px solid var(--border);
  border-radius: 10px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
thead th { background: var(--surface-2); position: sticky; top: 0; font-size: 12px;
  text-transform: uppercase; letter-spacing: .03em; color: var(--muted); }
th[data-sortable] { cursor: pointer; user-select: none; }
th[data-sortable]:hover { color: var(--text); }
th[aria-sort="ascending"]::after { content: " \\2191"; }
th[aria-sort="descending"]::after { content: " \\2193"; }
tbody tr:last-child td { border-bottom: 0; }
tr[hidden] { display: none; }
.score { display: inline-block; min-width: 30px; text-align: center; font-weight: 650;
  border-radius: 6px; padding: 1px 6px; background: var(--neutral-bg); color: var(--neutral-fg); }
.score--ok { background: var(--ok-bg); color: var(--ok-fg); }
.score--warn { background: var(--warn-bg); color: var(--warn-fg); }
.mini td { padding: 5px 8px; }
.mini .num { text-align: right; font-weight: 650; width: 60px; }
.code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px;
  color: var(--muted); }
#no-matches { padding: 12px; margin: 0; }
footer { margin-top: 28px; color: var(--muted); font-size: 12px; }
@media print {
  body { background: #fff; padding: 0; }
  .filters { display: none; }
  .table-wrap { max-height: none; overflow: visible; }
  .card, .portal, .panel, .page-head { break-inside: avoid; }
  /* The filter controls and the "N shown" counter both live in .filters, which
     is hidden above. Without this, printing after filtering silently produces
     a subset of the table under a caption that still claims to show all of it,
     with nothing on the page to say a filter was ever applied. Paper cannot be
     unfiltered, so paper always gets every row. */
  tr[hidden] { display: table-row !important; }
  #no-matches { display: none; }
}
"""

#: No data is interpolated into this script -- every value stays in the DOM as an
#: escaped attribute, so there is no string here that a feed could reach.
_SCRIPT = """
(function () {
  var table = document.getElementById("jobs");
  if (!table || !table.tBodies.length) { return; }
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var head = table.tHead.rows[0];
  var text = document.getElementById("f-text");
  var decision = document.getElementById("f-decision");
  var score = document.getElementById("f-score");
  var shown = document.getElementById("shown-count");
  var empty = document.getElementById("no-matches");

  function applyFilters() {
    var query = (text.value || "").trim().toLowerCase();
    var wanted = decision.value;
    var minimum = parseInt(score.value, 10);
    if (isNaN(minimum)) { minimum = 0; }
    var visible = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var haystack = row.getAttribute("data-search") || "";
      var rowScore = parseInt(row.getAttribute("data-score"), 10) || 0;
      var ok = (!query || haystack.indexOf(query) !== -1) &&
        (!wanted || row.getAttribute("data-decision") === wanted) &&
        rowScore >= minimum;
      row.hidden = !ok;
      if (ok) { visible++; }
    }
    shown.textContent = String(visible);
    if (empty) { empty.hidden = visible !== 0; }
  }

  function cellValue(row, index) {
    var cell = row.cells[index];
    if (!cell) { return ""; }
    var explicit = cell.getAttribute("data-sort");
    return explicit === null ? cell.textContent.trim() : explicit;
  }

  function sortBy(index, numeric, th) {
    // A first click on scores or dates should show the biggest and the newest;
    // on a name column it should read A-Z. After that it just toggles.
    var current = th.getAttribute("aria-sort");
    var descending = current ? current !== "descending" : th.hasAttribute("data-desc-first");
    for (var i = 0; i < head.cells.length; i++) { head.cells[i].removeAttribute("aria-sort"); }
    th.setAttribute("aria-sort", descending ? "descending" : "ascending");
    var ordered = rows.slice().sort(function (a, b) {
      var left = cellValue(a, index);
      var right = cellValue(b, index);
      var result = numeric
        ? (parseFloat(left) || 0) - (parseFloat(right) || 0)
        : left.localeCompare(right);
      return descending ? -result : result;
    });
    for (var j = 0; j < ordered.length; j++) { body.appendChild(ordered[j]); }
  }

  for (var c = 0; c < head.cells.length; c++) {
    (function (th, index) {
      if (!th.hasAttribute("data-sortable")) { return; }
      var numeric = th.getAttribute("data-type") === "number";
      th.addEventListener("click", function () { sortBy(index, numeric, th); });
      th.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sortBy(index, numeric, th);
        }
      });
    })(head.cells[c], c);
  }

  text.addEventListener("input", applyFilters);
  decision.addEventListener("change", applyFilters);
  score.addEventListener("input", applyFilters);
  applyFilters();
})();
"""


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def render_report(db: Session, user: User, *, hours: int = 24, job_limit: int = 500) -> str:
    """Render the whole report as a single HTML document string.

    Read-only: nothing here writes, flushes or commits. The caller owns the
    session and decides where the string goes.
    """
    generated_at = datetime.now(UTC)
    since = generated_at - timedelta(hours=hours)
    agent_settings = agent_settings_or_default(db, user)

    total_matches = int(
        db.execute(select(func.count(JobMatch.id)).where(JobMatch.user_id == user.id)).scalar_one()
    )
    rows = _scored_jobs(db, user, job_limit) if total_matches else []
    counts = pipeline.buckets(db, user, agent_settings, since)
    stops = _stop_breakdown(db, user)
    name = _candidate_name(db, user)

    sections = [
        _header_html(
            name,
            generated_at,
            agent_settings,
            workflow.applications_today(db, user.id),
            hours,
        )
    ]
    # An empty database must explain itself. Six zeroes with no sentence beside
    # them read as a broken report rather than as "you have not started yet".
    if total_matches == 0:
        sections.append(_empty_html(pipeline.empty_state(db, user, total_matches)))
    sections.append(_buckets_html(counts, stops))
    sections.append(_jobs_html(rows, agent_settings, total_matches))
    sections.append(_portals_html(portal_status.portal_states(db, user, agent_settings)))
    sections.append(_rejections_html(_decision_breakdown(db, user), stops))
    sections.append(
        f"<footer>Generated offline by {_esc(app_settings.app_name)}. "
        "This file makes no network requests: styles, scripts and data are inline. "
        "Only verified profile facts ever reach a generated document.</footer>"
    )
    body = "\n".join(sections)
    title = _esc(f"Job agent report - {name}")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
{body}
</main>
<script>{_SCRIPT}</script>
</body>
</html>
"""
