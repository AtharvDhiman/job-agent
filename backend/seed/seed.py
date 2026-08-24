"""Seed a working local environment.

Creates the owner account, a profile pre-filled with PLACEHOLDERS (replace them
in Settings -> Profile), a few verified career facts, example job sources, and
two offline sample jobs so the dashboard has something to show before your
first discovery run.

Run:  python -m seed.seed            (add --reset to drop and recreate tables)
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.enums import FactCategory, Seniority, WorkArrangement
from app.core.security import Role, hash_password
from app.db.base import Base
from app.db.session import engine, session_scope
from app.models.job import Job, JobSourceSubscription
from app.models.profile import CandidateProfile, CareerFact
from app.models.user import AgentSettings, User
from app.services import matching
from app.services.normalizer import compute_dedupe_hash
from app.utils.text import normalize_company, normalize_title

DEMO_EMAIL = "owner@example.com"
# A deliberately obvious local placeholder. The README tells you to change it
# immediately, and seeding is refused outright when APP_ENV is production.
DEMO_PASSWORD = "ChangeMe-Str0ng!Pass"  # noqa: S105

# Replace every [PLACEHOLDER] through the UI or by editing this file before seeding.
PROFILE = {
    "full_name": "[YOUR NAME]",
    "headline": "[YOUR ONE-LINE HEADLINE]",
    "contact_email": "[YOUR EMAIL]",
    "phone": "[YOUR PHONE]",
    "location_city": "[YOUR CITY]",
    "location_region": "[YOUR REGION]",
    "location_country": "",
    "timezone": "UTC",
    "linkedin_url": "",
    "portfolio_urls": [],
    "target_titles": ["[TARGET TITLE 1]", "[TARGET TITLE 2]"],
    "skills": ["[SKILL 1]", "[SKILL 2]", "[SKILL 3]"],
    "preferred_countries": [],
    "preferred_timezones": [],
    "work_arrangement_preference": [WorkArrangement.REMOTE.value],
    "industries_priority": [],
    "companies_to_avoid": [],
    "excluded_keywords": ["unpaid", "commission only"],
    "employment_types": ["full_time"],
    "seniority_level": Seniority.UNKNOWN.value,
    "years_experience": None,
    "min_salary_amount": None,
    "min_salary_currency": "USD",
    "salary_period": "year",
    "willing_to_relocate": False,
    "requires_sponsorship": None,
    "notice_period_days": None,
}

EXAMPLE_SOURCES = [
    ("greenhouse", "EXAMPLE_BOARD_TOKEN", "Replace with a real Greenhouse board token"),
    ("lever", "EXAMPLE_SITE", "Replace with a real Lever site name"),
    ("ashby", "EXAMPLE_BOARD", "Replace with a real Ashby job board name"),
]


def _sample_job(title: str, company: str, description: str, hours_ago: int, **kw) -> Job:
    now = datetime.now(UTC)
    country = kw.pop("country", "US")
    slug = normalize_title(title).replace(" ", "-")
    return Job(
        connector_key="manual",
        compliance_tier="manual_only",
        submission_policy_default="review_required",
        external_id=f"sample:{slug}:{normalize_company(company)}",
        source_url=f"https://example.com/jobs/{slug}",
        apply_url=f"https://example.com/jobs/{slug}/apply",
        is_direct_employer=True,
        title=title,
        title_normalized=normalize_title(title),
        company=company,
        company_normalized=normalize_company(company),
        description_text=description,
        location_raw=kw.pop("location", "Remote"),
        location_country=country,
        work_arrangement=kw.pop("arrangement", WorkArrangement.REMOTE.value),
        employment_type="full_time",
        seniority=kw.pop("seniority", Seniority.SENIOR.value),
        posted_at=now - timedelta(hours=hours_ago),
        first_seen_at=now,
        last_seen_at=now,
        extracted_skills=kw.pop("skills", []),
        requirements=[],
        raw={"sample": True},
        dedupe_hash=compute_dedupe_hash(company, title, country),
        **kw,
    )


PLACEHOLDER_FACTS = [
    dict(
        category=FactCategory.EMPLOYMENT.value,
        title="[YOUR JOB TITLE]",
        organization="[YOUR EMPLOYER]",
        value="[YOUR JOB TITLE] at [YOUR EMPLOYER]",
        start_date=date(2021, 1, 1),
        is_current=True,
        highlights=["[A REAL ACHIEVEMENT, IN YOUR OWN WORDS]"],
    ),
    dict(
        category=FactCategory.EDUCATION.value,
        value="[YOUR DEGREE], [YOUR INSTITUTION]",
        organization="[YOUR INSTITUTION]",
    ),
    dict(
        category=FactCategory.WORK_AUTHORIZATION.value,
        key="work_authorization",
        value="[STATE YOUR ACTUAL WORK AUTHORIZATION]",
        sensitive=True,
    ),
]

SAMPLE_JOBS = [
    dict(
        title="Senior Backend Engineer",
        company="Northwind Systems",
        description=(
            "We are hiring a Senior Backend Engineer. You will work with Python, PostgreSQL, "
            "Docker and Kubernetes on AWS.\n"
            "- 5+ years building production services\n"
            "- Strong SQL and system design\n"
            "The salary range is $150,000 - $190,000 per year.\n"
            "Visa sponsorship is available for this role."
        ),
        hours_ago=6,
        skills=["python", "postgresql", "docker", "kubernetes", "aws", "sql"],
    ),
    dict(
        title="Data Analyst",
        company="Contoso Retail",
        description=(
            "Data Analyst wanted. SQL, Excel and Tableau required. Hybrid, 3 days per week "
            "in office.\n"
            "- 2 years of analytics experience\n"
            "We are unable to sponsor visas for this position."
        ),
        hours_ago=20,
        location="Austin, TX",
        arrangement=WorkArrangement.HYBRID.value,
        seniority=Seniority.MID.value,
        skills=["sql", "excel", "tableau", "data analysis"],
    ),
]


def seed(reset: bool = False) -> None:
    if reset:
        print("Dropping and recreating all tables...")
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with session_scope() as db:
        user = db.execute(select(User).where(User.email == DEMO_EMAIL)).scalar_one_or_none()
        if user is None:
            user = User(
                email=DEMO_EMAIL,
                hashed_password=hash_password(DEMO_PASSWORD),
                full_name=PROFILE["full_name"],
                role=Role.OWNER.value,
            )
            db.add(user)
            db.flush()
            print(f"Created owner {DEMO_EMAIL} / {DEMO_PASSWORD}   <-- change this password")
        else:
            print(f"Owner {DEMO_EMAIL} already exists")

        profile = db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        ).scalar_one_or_none()
        if profile is None:
            profile = CandidateProfile(user_id=user.id, **PROFILE)
            db.add(profile)
            db.flush()

        has_settings = db.execute(
            select(AgentSettings).where(AgentSettings.user_id == user.id)
        ).scalar_one_or_none()
        if has_settings is None:
            db.add(
                AgentSettings(
                    user_id=user.id,
                    automation_enabled=False,  # always starts paused
                    auto_submit_min_score=settings.auto_submit_min_score,
                    daily_application_limit=settings.daily_application_limit,
                    job_max_age_hours=settings.job_max_age_hours,
                    discovery_interval_minutes=settings.discovery_interval_minutes,
                )
            )

        has_facts = db.execute(
            select(CareerFact).where(CareerFact.profile_id == profile.id).limit(1)
        ).scalar_one_or_none()
        if has_facts is None:
            for payload in PLACEHOLDER_FACTS:
                db.add(CareerFact(profile_id=profile.id, verified=False, **payload))
            print("Added placeholder career facts, all UNVERIFIED until you confirm them.")

        for connector_key, identifier, note in EXAMPLE_SOURCES:
            exists = db.execute(
                select(JobSourceSubscription).where(
                    JobSourceSubscription.user_id == user.id,
                    JobSourceSubscription.connector_key == connector_key,
                    JobSourceSubscription.identifier == identifier,
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(
                    JobSourceSubscription(
                        user_id=user.id,
                        connector_key=connector_key,
                        identifier=identifier,
                        display_name=note,
                        enabled=False,  # stays off until you supply a real identifier
                    )
                )

        if db.execute(select(Job).limit(1)).scalar_one_or_none() is None:
            for payload in SAMPLE_JOBS:
                db.add(_sample_job(**payload))
            print(f"Added {len(SAMPLE_JOBS)} offline sample jobs")

        db.flush()
        result = matching.score_for_user(db, user, notify=False)
        print(f"Scored {result.get('scored', 0)} job(s)")

    print("\nSeed complete. Next steps:")
    print("  1. Log in at http://localhost:3000 with the credentials above.")
    print("  2. Replace every [PLACEHOLDER] in Profile, then upload your resume.")
    print("  3. Verify each career fact. Unverified facts are never used in an application.")
    print("  4. Put real board identifiers under Sources and enable them.")
    print("  5. Automation stays OFF until you enable it and authorize a platform.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the job agent database")
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables")
    args = parser.parse_args()
    if args.reset and settings.is_production:
        sys.exit("Refusing to --reset in a production environment")
    seed(reset=args.reset)
