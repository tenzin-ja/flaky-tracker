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

- **App:** Python 3.13 · Django · Celery · Redis · PostgreSQL
- **GitHub integration:** GitHub App webhooks and REST API (httpx, PyJWT)
- **Tooling:** uv · Ruff · mypy · pytest
- **Delivery:** Docker · GitHub Actions · Terraform · AWS (ECS Fargate, RDS,
  ElastiCache, ECR, Secrets Manager, CloudWatch) · Sentry

## Local development

Requirements: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
# 1. Start PostgreSQL and Redis
docker compose up -d

# 2. Install dependencies (creates .venv from uv.lock)
uv sync

# 3. Configure environment variables, then set DJANGO_SECRET_KEY in .env
cp .env.example .env             # Windows PowerShell: Copy-Item .env.example .env

# 4. Apply migrations and run the web server
uv run python manage.py migrate
uv run python manage.py runserver     # http://localhost:8000/healthz

# 5. Run a Celery worker (separate terminal)
uv run celery -A config worker --loglevel=info

# 6. Run checks
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy .
```

On Windows, Celery's default worker pool isn't supported; add `--pool=solo` when
running the worker outside Docker.

## Project layout

```text
config/          Django project: settings, URLs, Celery app, WSGI
core/            Shared pieces (health check)
tests/           pytest suite
  fixtures/      Sample JUnit XML reports used by parser tests
docs/rfcs/       Design decisions (RFCs)
```

Feature apps (webhooks, ingestion, detection, comments) are added milestone by
milestone; see [RFC 0001](docs/rfcs/0001-flaky-tracker-v1.md).

## License

MIT
