# Architecture Decision Records

Each record captures one significant engineering decision: the problem, the
options, what was chosen and why, and when to reconsider it.

How these relate to the other documents:

| Document | Answers |
| --- | --- |
| [RFC 0001](../rfcs/0001-flaky-tracker-v1.md) | What v1 is, its scope, plan and risks |
| [`DESIGN.md`](../../DESIGN.md) | Exact specs: schema, limits, API calls, algorithms |
| [Architecture](../architecture/README.md) | What is implemented today |
| ADRs (this folder) | Why each significant choice was made, and its status |

## Status

- **Proposed:** written down but not approved. Don't build on it as settled.
- **Accepted:** approved by the project owner. A decision can be accepted
  before it's implemented.
- **Superseded:** replaced by a later ADR. Kept for history and linked to its
  replacement; never deleted.

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-github-app-integration.md) | Integrate as a GitHub App driven by `workflow_run` webhooks | Proposed |
| [0002](0002-junit-artifact-results.md) | Read results from attempt-suffixed JUnit XML artifacts | Proposed |
| [0003](0003-django-and-python-toolchain.md) | Django, PostgreSQL and a strict Python toolchain | Accepted |
| [0004](0004-celery-redis-background-jobs.md) | Celery with Redis as broker for background work | Accepted |
| [0005](0005-recorded-run-recovery.md) | Recover recorded runs with a guarded claim and a scheduled sweep | Proposed |
| [0006](0006-missed-webhook-deliveries.md) | Don't automatically recover missed webhook deliveries in the beta | Proposed |
| [0007](0007-flake-detection-and-verdicts.md) | Detect flakes as same-revision outcome flips; three verdicts | Proposed |
| [0008](0008-sticky-pr-comment.md) | One sticky PR comment with per-workflow sections | Proposed |
| [0009](0009-exclude-fork-runs.md) | Exclude fork-origin runs from ingestion | Proposed |
| [0010](0010-aws-ecs-terraform.md) | Deploy to AWS ECS Fargate with Terraform | Proposed |

ADRs 0003 and 0004 are accepted because the scaffold implementing them was
reviewed and merged in PR #1. The rest come from the RFC's decision table,
which has not been approved yet.

## Process

1. Before implementing a significant change, lay out 2–3 options with their
   trade-offs and a recommendation.
2. Write the ADR as **Proposed**, or update the existing one.
3. Mark it **Accepted** only after the project owner approves it.
4. To change an accepted decision, write a new ADR, set the old one to
   **Superseded**, and link both ways.
5. Commit the ADR in the same pull request as the code it affects.

**Worth an ADR:** a choice that is expensive to reverse, changes a component's
responsibilities or a guarantee (security, durability, correctness), or that
an interviewer would ask "why?" about.

**Not worth an ADR:** library versions, naming, refactors, numeric limits
(those live in `DESIGN.md`).

## Template

```markdown
---
status: Proposed
date: YYYY-MM-DD
source: RFC 0001 §…
supersedes:
superseded_by:
---

# NNNN. Title in the imperative

## Context
## Constraints
## Alternatives considered
## Decision
## Consequences
## Revisit if
```
