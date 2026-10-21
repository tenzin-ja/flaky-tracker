# Flaky Test Tracker

A GitHub App that records CI test results, detects flaky tests and comments on
pull requests so developers know whether a failure is their fault.

> **Status:** in development. v1 is planned for December 2026. See
> [DESIGN.md](DESIGN.md) for the full design and roadmap.

## How it works

1. You install the App on a repository and have CI upload a JUnit XML report
   as an artifact named `test-results`.
2. When a workflow run finishes, the App downloads the report and stores each
   test's result.
3. A test that both passes and fails on the **same commit** is marked flaky.
4. On pull requests, the App keeps one comment up to date that labels each
   failure as **known flaky**, **also failing on main** or a **new failure**,
   with a short AI-generated explanation for new failures.

## Tech stack

Python · FastAPI · PostgreSQL · SQLAlchemy 2 · Alembic · httpx · Docker ·
GitHub Actions · AWS

## Local development

Requirements: Python 3.12+, Docker.

```bash
# 1. Start PostgreSQL (creates the dev and test databases)
docker compose up -d

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 3. Configure environment variables
cp .env.example .env             # Windows PowerShell: Copy-Item .env.example .env

# 4. Apply migrations and run the API
alembic upgrade head
uvicorn app.main:app --reload    # http://localhost:8000/healthz

# 5. Run checks
pytest
ruff check . && ruff format --check .
```

## Project layout

```text
app/
  config.py      Settings loaded from environment variables
  db.py          Database engine and sessions
  main.py        FastAPI app
  models/        SQLAlchemy models
migrations/      Alembic migrations
tests/
  fixtures/      Sample JUnit XML reports used by parser tests
docker/          Local database init script
```

## License

MIT
