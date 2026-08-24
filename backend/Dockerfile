# =============================================================================
#  Backend image - FastAPI API, Celery worker and Celery beat all run from this
#  same image; only the command differs (see docker-compose.yml).
#
#  Stage 1 builds a self-contained virtualenv from requirements.txt.
#  Stage 2 copies only that virtualenv plus the application source, so no
#  compiler, no pip cache and no build headers survive into the runtime image.
# =============================================================================

# --- build stage -------------------------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Every runtime dependency ships manylinux wheels for CPython 3.12
# (psycopg[binary] bundles its own libpq, cryptography and argon2-cffi are
# prebuilt), so this stage deliberately installs no compiler or -dev headers.
WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- runtime stage -----------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

# No extra OS packages: psycopg[binary] carries libpq, and the healthcheck below
# uses the standard library instead of pulling in curl or wget.

# Non-root from here on. The uid is fixed so a bind-mounted storage directory
# has a predictable owner on the host.
RUN groupadd --gid 10001 jobagent \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin jobagent

COPY --from=build /opt/venv /opt/venv

WORKDIR /app

# Application source. Kept narrow so an accidental context change cannot leak
# tests, the local SQLite scratch database or a stray .env into the image.
COPY --chown=jobagent:jobagent app ./app
COPY --chown=jobagent:jobagent alembic ./alembic
COPY --chown=jobagent:jobagent seed ./seed
COPY --chown=jobagent:jobagent alembic.ini pyproject.toml ./

# Generated resumes, cover letters and uploads live here. docker-compose mounts
# a host directory over it and sets STORAGE_LOCAL_PATH=/app/storage.
RUN mkdir -p /app/storage && chown -R jobagent:jobagent /app/storage

USER jobagent

EXPOSE 8000

# /health reports database and redis reachability. A non-200 response, or a
# process that cannot answer at all, marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
