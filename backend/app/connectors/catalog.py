"""Hand-curated catalog of sources a user can add with one click.

Entries here are starting points, not endorsements of unlimited use: every
source is validated on its first discovery fetch, and a source that turns out
to be blocked is disabled, not retried. Anyone extending this list must read
docs/COMPLIANCE.md first -- "published somewhere" is not the same as
"published for syndication".
"""

from __future__ import annotations

_RSS_COMPLIANCE = (
    "Published RSS feeds are offered for syndication; robots.txt is still "
    "checked and requests are conditional. The first discovery run validates "
    "the feed, and a blocked source is disabled, not retried."
)

CATALOG: list[dict] = [
    {
        "connector_key": "rss",
        "identifier": "https://weworkremotely.com/remote-jobs.rss",
        "display_name": "We Work Remotely - all jobs",
        "note": "Every remote role posted on We Work Remotely, across all categories.",
        "compliance_note": _RSS_COMPLIANCE,
        "requires_credentials": [],
    },
    {
        "connector_key": "rss",
        "identifier": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "display_name": "We Work Remotely - programming",
        "note": "Only the programming category, for a quieter feed.",
        "compliance_note": _RSS_COMPLIANCE,
        "requires_credentials": [],
    },
    {
        "connector_key": "rss",
        "identifier": "https://remotive.com/remote-jobs/feed",
        "display_name": "Remotive - all remote jobs",
        "note": "Remotive's public feed of remote roles across categories.",
        "compliance_note": _RSS_COMPLIANCE,
        "requires_credentials": [],
    },
    {
        "connector_key": "adzuna",
        "identifier": "gb:software engineer",
        "display_name": "Adzuna (your own free API key)",
        "note": (
            "Aggregated listings via Adzuna's developer API. Register for your "
            "own free app id and key, then adjust the country and query."
        ),
        "compliance_note": (
            "Uses your own Adzuna API agreement; results link out to the "
            "employer, so every application still goes through your review."
        ),
        "requires_credentials": ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
    },
]
