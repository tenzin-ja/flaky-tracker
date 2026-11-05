---
status: Accepted
date: 2026-10-21
source: RFC 0001 D3; implemented in the scaffold (PR #1)
supersedes:
superseded_by:
---

# 0004. Run background work with Celery, using Redis as the broker

## Context

The webhook handler must answer GitHub within seconds, but downloading
artifacts, parsing reports and updating comments take longer and need retries.
That work has to run outside the request.

## Constraints

- Low traffic, small budget, one developer.
- Must run locally with Docker Compose.
- The tools should be ones used in industry.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **Celery + Redis broker** | Very common in Django shops; retries, time limits and routing built in; Redis is easy to run locally | Redis is not a durable queue; Celery's delivery guarantees need care |
| Celery + SQS | Managed and durable | Harder local development; Celery's SQS support has limits |
| PostgreSQL-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`) | One fewer service; transactional with the data | More hand-built machinery; less common |
| RQ or Dramatiq | Simpler | Smaller ecosystems |
| Process in the request | No queue | Webhook timeouts; no retries |

## Decision

Use Celery with Redis as the **broker only**. Anything that must survive a
crash lives in PostgreSQL, not Redis. Workers acknowledge messages late, take
one message at a time and run under a hard time limit. These settings are in
`config/settings.py`.

## Consequences

- Execution is at-least-once: a task can run more than once, so all effects
  must be idempotent.
- If Redis loses a message, the work must be recoverable from PostgreSQL. How
  that recovery works is a separate, proposed decision
  ([0005](0005-recorded-run-recovery.md)).
- Celery time limits aren't enforced with `--pool=solo` (Windows), so crash
  tests run the worker in a Linux container.

## Revisit if

- The broker needs durability or multiple workers need coordination (consider
  SQS).
- Running Redis on AWS costs too much for the beta.
