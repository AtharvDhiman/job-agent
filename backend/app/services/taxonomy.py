"""Lightweight, dependency-free taxonomies used by the normalizer and ranker.

Deliberately data, not model: it is inspectable, testable and deterministic.
The LLM layer refines explanations on top of this, it does not replace it.
"""

from __future__ import annotations

import re

from app.core.enums import EmploymentType, Seniority, WorkArrangement

# --- skills ---------------------------------------------------------------
# canonical -> aliases (all matched case-insensitively on word boundaries)
SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "python": ("python3", "py"),
    "javascript": ("js", "es6", "ecmascript"),
    "typescript": ("ts",),
    "java": (),
    "go": ("golang",),
    "rust": (),
    "c#": ("csharp", "c sharp", ".net", "dotnet"),
    "c++": ("cpp", "c plus plus"),
    "ruby": ("ruby on rails", "rails"),
    "php": ("laravel",),
    "scala": (),
    "kotlin": (),
    "swift": (),
    "sql": ("t-sql", "plsql", "ansi sql"),
    "react": ("react.js", "reactjs"),
    "next.js": ("nextjs",),
    "vue": ("vue.js", "vuejs", "nuxt"),
    "angular": ("angularjs",),
    "node.js": ("node", "nodejs"),
    "django": (),
    "flask": (),
    "fastapi": (),
    "spring": ("spring boot",),
    "graphql": ("apollo",),
    "rest api": ("restful", "rest apis"),
    "grpc": (),
    "postgresql": ("postgres", "psql"),
    "mysql": ("mariadb",),
    "mongodb": ("mongo",),
    "redis": (),
    "elasticsearch": ("opensearch", "elastic"),
    "kafka": ("event streaming",),
    "rabbitmq": (),
    "snowflake": (),
    "dbt": (),
    "spark": ("pyspark", "apache spark"),
    "airflow": ("apache airflow",),
    "aws": ("amazon web services", "ec2", "s3", "lambda"),
    "gcp": ("google cloud", "bigquery"),
    "azure": ("microsoft azure",),
    "docker": ("containers",),
    "kubernetes": ("k8s", "eks", "gke"),
    "terraform": ("iac", "infrastructure as code"),
    "ansible": (),
    "ci/cd": ("continuous integration", "continuous delivery", "github actions", "jenkins"),
    "linux": ("unix",),
    "git": ("github", "gitlab"),
    "machine learning": ("ml", "deep learning"),
    "pytorch": (),
    "tensorflow": ("keras",),
    "llm": ("large language model", "genai", "generative ai", "rag"),
    "nlp": ("natural language processing",),
    "pandas": ("numpy",),
    "data analysis": ("analytics", "data analytics"),
    "tableau": ("power bi", "looker"),
    "microservices": (),
    "distributed systems": (),
    "system design": ("architecture",),
    "testing": ("unit testing", "pytest", "jest", "tdd"),
    "playwright": ("selenium", "cypress"),
    "security": ("appsec", "infosec", "owasp"),
    "observability": ("prometheus", "grafana", "datadog", "opentelemetry"),
    "agile": ("scrum", "kanban"),
    "product management": ("roadmap",),
    "project management": ("pmp",),
    "stakeholder management": (),
    "figma": ("sketch",),
    "ux": ("user experience", "ui/ux"),
    "salesforce": (),
    "sap": (),
    "excel": ("spreadsheets",),
}

_SKILL_PATTERNS: list[tuple[str, re.Pattern]] = []
for canonical, aliases in SKILL_ALIASES.items():
    variants = [canonical, *aliases]
    escaped = sorted((re.escape(v) for v in variants), key=len, reverse=True)
    _SKILL_PATTERNS.append(
        (canonical, re.compile(r"(?<![\w])(" + "|".join(escaped) + r")(?![\w])", re.IGNORECASE))
    )


def extract_skills(text: str | None, *, limit: int = 60) -> list[str]:
    """Canonical skills mentioned in free text, ordered by first appearance."""
    if not text:
        return []
    hits: list[tuple[int, str]] = []
    for canonical, pattern in _SKILL_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append((match.start(), canonical))
    hits.sort()
    return [name for _, name in hits][:limit]


def canonicalize_skill(value: str) -> str:
    folded = (value or "").strip().lower()
    if folded in SKILL_ALIASES:
        return folded
    for canonical, aliases in SKILL_ALIASES.items():
        if folded == canonical or folded in aliases:
            return canonical
    return folded


# --- seniority ------------------------------------------------------------
SENIORITY_PATTERNS: list[tuple[Seniority, re.Pattern]] = [
    (Seniority.INTERN, re.compile(r"\b(intern|internship|placement student|co-?op)\b", re.I)),
    (Seniority.EXECUTIVE, re.compile(r"\b(chief|c[teofi]o|vp of|vice president|head of)\b", re.I)),
    (Seniority.DIRECTOR, re.compile(r"\bdirector\b", re.I)),
    (Seniority.MANAGER, re.compile(r"\b(engineering manager|manager|people lead)\b", re.I)),
    (Seniority.PRINCIPAL, re.compile(r"\b(principal|distinguished|fellow)\b", re.I)),
    (Seniority.STAFF, re.compile(r"\b(staff|architect)\b", re.I)),
    (Seniority.LEAD, re.compile(r"\b(lead|tech lead|team lead)\b", re.I)),
    (Seniority.SENIOR, re.compile(r"\b(senior|sr\.?|snr)\b", re.I)),
    (Seniority.JUNIOR, re.compile(r"\b(junior|jr\.?)\b", re.I)),
    (
        Seniority.ENTRY,
        re.compile(r"\b(entry[- ]level|graduate|new grad|associate|trainee)\b", re.I),
    ),
    (Seniority.MID, re.compile(r"\b(mid[- ]level|intermediate|ii|iii)\b", re.I)),
]


def infer_seniority(title: str, description: str = "") -> Seniority:
    for level, pattern in SENIORITY_PATTERNS:
        if pattern.search(title or ""):
            return level
    for level, pattern in SENIORITY_PATTERNS:
        if pattern.search((description or "")[:1500]):
            return level
    return Seniority.UNKNOWN


# --- work arrangement -----------------------------------------------------
_REMOTE_RE = re.compile(
    r"\b(fully remote|100% remote|remote[- ]first|work from home|wfh|remote)\b", re.I
)
_HYBRID_RE = re.compile(r"\bhybrid\b|\b\d\s*days? (?:per week )?in (?:the )?office\b", re.I)
_ONSITE_RE = re.compile(r"\b(on-?site|in-?office|in person)\b", re.I)


def infer_work_arrangement(
    location: str, description: str = "", remote_flag: bool | None = None
) -> WorkArrangement:
    blob = f"{location}\n{(description or '')[:2500]}"
    if _HYBRID_RE.search(blob):
        return WorkArrangement.HYBRID
    if remote_flag is True:
        return WorkArrangement.REMOTE
    if _REMOTE_RE.search(blob):
        return WorkArrangement.REMOTE
    if remote_flag is False or _ONSITE_RE.search(blob):
        return WorkArrangement.ONSITE
    return WorkArrangement.UNKNOWN


# --- employment type ------------------------------------------------------
_EMPLOYMENT_MAP = {
    "full_time": EmploymentType.FULL_TIME,
    "fulltime": EmploymentType.FULL_TIME,
    "full time": EmploymentType.FULL_TIME,
    "permanent": EmploymentType.FULL_TIME,
    "part_time": EmploymentType.PART_TIME,
    "part time": EmploymentType.PART_TIME,
    "contract": EmploymentType.CONTRACT,
    "contractor": EmploymentType.CONTRACT,
    "freelance": EmploymentType.CONTRACT,
    "temporary": EmploymentType.TEMPORARY,
    "temp": EmploymentType.TEMPORARY,
    "internship": EmploymentType.INTERNSHIP,
    "intern": EmploymentType.INTERNSHIP,
}


def infer_employment_type(raw: str, title: str = "") -> EmploymentType:
    folded = (raw or "").strip().lower().replace("-", "_")
    if folded in _EMPLOYMENT_MAP:
        return _EMPLOYMENT_MAP[folded]
    blob = f"{raw} {title}".lower()
    for needle, value in _EMPLOYMENT_MAP.items():
        if needle.replace("_", " ") in blob:
            return value
    return EmploymentType.UNKNOWN
