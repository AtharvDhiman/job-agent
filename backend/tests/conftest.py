"""Test fixtures.

Environment is set BEFORE any app module is imported, so the settings object and
the engine are built against a throwaway SQLite database and a temp storage root.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="jobagent-tests-"))
os.environ.update(
    APP_ENV="test",
    DATABASE_URL=f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    STORAGE_LOCAL_PATH=str(_TMP / "storage"),
    SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256-signing",
    ENCRYPTION_KEY="",  # exercises the derived dev key path
    ANTHROPIC_API_KEY="",  # deterministic mode: no network in tests
    AUTOMATION_GLOBAL_ENABLED="false",
    RATE_LIMIT_ENABLED="false",
    NOTIFY_EMAIL_ENABLED="false",
    REDIS_URL="",
    BROWSER_ASSISTANT_TOKEN="test-assistant-token",
    DISCOVERY_PER_HOST_RPS="1000",  # no artificial sleeps in tests
    LOG_LEVEL="WARNING",
    LOG_FORMAT="console",
)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.enums import FactCategory, Seniority, WorkArrangement  # noqa: E402
from app.core.security import Role, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.models.profile import CandidateProfile, CareerFact  # noqa: E402
from app.models.user import AgentSettings, User  # noqa: E402
from app.services.normalizer import compute_dedupe_hash  # noqa: E402
from app.utils.text import normalize_company, normalize_title  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        # Keep every test isolated without paying for a schema rebuild.
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture
def client(db):
    from app.db.session import get_db

    def _override():
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(db) -> User:
    row = User(
        email="owner@example.com",
        hashed_password=hash_password("Sup3r-Secret-Passw0rd!"),
        full_name="Test Owner",
        role=Role.OWNER.value,
    )
    db.add(row)
    db.flush()
    db.add(
        AgentSettings(
            user_id=row.id,
            automation_enabled=False,
            auto_submit_min_score=85,
            daily_application_limit=10,
            job_max_age_hours=48,
            shortlist_min_score=60,
        )
    )
    db.flush()
    return row


@pytest.fixture
def profile(db, user) -> CandidateProfile:
    row = CandidateProfile(
        user_id=user.id,
        full_name="Test Owner",
        headline="Backend engineer focused on Python services",
        contact_email="owner@example.com",
        phone="+1 415 555 0100",
        location_city="Austin",
        location_region="TX",
        location_country="US",
        linkedin_url="https://www.linkedin.com/in/testowner",
        portfolio_urls=["https://github.com/testowner"],
        target_titles=["Senior Backend Engineer", "Backend Engineer"],
        skills=["python", "postgresql", "docker", "kubernetes", "aws", "sql"],
        preferred_countries=["US"],
        work_arrangement_preference=[WorkArrangement.REMOTE.value],
        employment_types=["full_time"],
        companies_to_avoid=["Blocked Corp"],
        excluded_keywords=["unpaid"],
        seniority_level=Seniority.SENIOR.value,
        years_experience=7,
        min_salary_amount=140000,
        min_salary_currency="USD",
        salary_period="year",
        willing_to_relocate=False,
        requires_sponsorship=False,
        work_authorization={"authorized": True, "country": "US"},
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def facts(db, profile) -> list[CareerFact]:
    rows = [
        CareerFact(
            profile_id=profile.id,
            category=FactCategory.EMPLOYMENT.value,
            title="Senior Backend Engineer",
            organization="Northwind Systems",
            value="Senior Backend Engineer at Northwind Systems",
            start_date=date(2021, 3, 1),
            is_current=True,
            highlights=[
                "Built a Python and PostgreSQL service used by internal teams",
                "Moved deployments to Kubernetes on AWS",
            ],
            tags=["python", "postgresql", "kubernetes", "aws"],
            verified=True,
            verified_at=datetime.now(UTC),
        ),
        CareerFact(
            profile_id=profile.id,
            category=FactCategory.EDUCATION.value,
            value="B.S. Computer Science, State University",
            organization="State University",
            start_date=date(2014, 9, 1),
            end_date=date(2018, 6, 1),
            verified=True,
            verified_at=datetime.now(UTC),
        ),
        CareerFact(
            profile_id=profile.id,
            category=FactCategory.SKILL.value,
            key="python",
            value="python",
            verified=True,
            verified_at=datetime.now(UTC),
        ),
        CareerFact(
            profile_id=profile.id,
            category=FactCategory.LINK.value,
            value="https://github.com/testowner",
            evidence_url="https://github.com/testowner",
            verified=True,
            verified_at=datetime.now(UTC),
        ),
        # Deliberately unverified: nothing may ever use this.
        CareerFact(
            profile_id=profile.id,
            category=FactCategory.CERTIFICATION.value,
            value="AWS Certified Solutions Architect",
            verified=False,
        ),
    ]
    db.add_all(rows)
    db.flush()
    return rows


def make_job(**overrides) -> Job:
    now = datetime.now(UTC)
    data = dict(
        connector_key="greenhouse",
        compliance_tier="public_job_api",
        submission_policy_default="review_required",
        external_id=f"test:{uuid.uuid4()}",
        source_url="https://boards.greenhouse.io/example/jobs/1",
        apply_url="https://boards.greenhouse.io/example/jobs/1",
        is_direct_employer=True,
        title="Senior Backend Engineer",
        company="Northwind Systems",
        description_text=(
            "We need a Senior Backend Engineer with Python, PostgreSQL, Docker, "
            "Kubernetes and AWS experience.\n"
            "- 5+ years of experience\n"
            "The salary range is $150,000 - $190,000 per year."
        ),
        location_raw="Remote - US",
        location_country="US",
        work_arrangement=WorkArrangement.REMOTE.value,
        employment_type="full_time",
        seniority=Seniority.SENIOR.value,
        salary_min=150000,
        salary_max=190000,
        salary_currency="USD",
        salary_period="year",
        posted_at=now - timedelta(hours=4),
        first_seen_at=now,
        last_seen_at=now,
        extracted_skills=["python", "postgresql", "docker", "kubernetes", "aws"],
        requirements=["5+ years of experience"],
        raw={},
    )
    data.update(overrides)
    data.setdefault("title_normalized", normalize_title(data["title"]))
    data.setdefault("company_normalized", normalize_company(data["company"]))
    data.setdefault(
        "dedupe_hash",
        compute_dedupe_hash(data["company"], data["title"], data.get("location_country", "")),
    )
    return Job(**data)


@pytest.fixture
def job(db) -> Job:
    row = make_job()
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def auth_headers(client, user) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Sup3r-Secret-Passw0rd!"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def assistant_headers() -> dict:
    return {"X-Assistant-Token": "test-assistant-token"}
