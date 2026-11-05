---
status: Accepted
date: 2026-10-21
source: RFC 0001 D6, D10; implemented in the scaffold (PR #1)
supersedes:
superseded_by:
---

# 0003. Use Django, PostgreSQL and a strict Python toolchain

## Context

The service needs a web framework, an ORM with migrations, an internal admin
view for inspecting runs, and quality checks that run in CI. The stack should
stay in Python, but use tools that are common in industry.

## Constraints

- One developer, about 10 hours a week.
- Python experience already exists (FastAPI and SQLAlchemy in another project).
- Every change is checked in CI.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **Django + PostgreSQL** | ORM, migrations and admin built in; widely used | Synchronous ORM; new conventions to learn |
| FastAPI + SQLAlchemy + Alembic | Already familiar; async | Admin, settings and app structure built by hand; repeats existing experience |
| Go | Fast, simple deployment | New language on a short schedule |

Tooling: **uv** (lockfile, fast installs) over pip/Poetry; **Ruff** over
flake8 + black + isort; **mypy strict with django-stubs**; **pytest-django**.

## Decision

Django with PostgreSQL. Settings come from environment variables
(`django-environ`). uv manages dependencies; Ruff lints and formats; mypy runs
in strict mode; pytest-django runs the tests. CI enforces all of them and fails
if a model change has no migration.

## Consequences

- Database work is synchronous; slow I/O belongs in background tasks
  ([0004](0004-celery-redis-background-jobs.md)).
- Strict typing with Django needs the stubs plugin and occasional casts.
- Every schema change goes through `makemigrations`.

## Revisit if

- The web tier becomes I/O-bound in a way background tasks can't absorb.
