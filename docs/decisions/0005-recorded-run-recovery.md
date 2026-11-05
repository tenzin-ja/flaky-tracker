---
status: Proposed
date: 2026-10-30
source: RFC 0001 rev. 5 D3, D7, T1, T2, §5.1, §5.2
supersedes:
superseded_by:
---

# 0005. Recover recorded runs with a guarded claim and a scheduled sweep

## Context

Once a run is recorded in PostgreSQL, it must eventually be processed, even if:

- the process dies after the commit but before the task reaches Redis;
- a worker is killed mid-job (crash, hard time limit, or a deploy);
- Redis redelivers a message after its visibility timeout;
- an execution assumed dead finishes late and tries to write.

Rev. 4 proposed a lease with a claim token checked on every write, plus an
always-on Celery Beat service. Neither was approved; rev. 5 simplifies both.

## Constraints

- One worker, concurrency 1, in the beta.
- ECS rolling deploys start the new task first by default; Fargate allows at
  most 120 seconds between the stop signal and a forced kill.
- Keep the infrastructure useful to learn from, but small.

## Alternatives considered

**Claim guard**

| Option | For | Against |
| --- | --- | --- |
| Conditional claim only | Simplest | A stale execution could overwrite a newer outcome |
| **Conditional claim + conditional final write** | Stale executions can't overwrite completed state, at the cost of one condition | Not safe for multiple workers on its own |
| Lease + claim token on every write (rev. 4) | Safe with multiple workers | More code and states than one worker needs |

**Sweep trigger**

| Option | For | Against |
| --- | --- | --- |
| Always-on Celery Beat service | Standard Celery | A third always-running service to pay for |
| Beat embedded in the worker (`celery worker -B`) | Least infrastructure | Teaches less; tied to the worker's lifecycle |
| **EventBridge Scheduler launching a short-lived ECS task** | Managed scheduling; real AWS experience; nothing idle | Another task definition, IAM role and schedule in Terraform |

## Decision

- Enqueue with `transaction.on_commit` after the run row commits. The row,
  not the message, is the source of truth.
- The claim is a conditional update that moves the run to `processing`,
  records `claimed_at` and commits before any network call. A claim that
  matches nothing makes the task a no-op.
- The final outcome write succeeds only if the run still holds the same claim.
  Observation writes stay idempotent through unique constraints.
- A recurring EventBridge Scheduler schedule launches a short-lived ECS task
  running a management command. It re-enqueues `pending` runs and
  `processing` runs whose claim is older than the hard time limit plus a
  safety margin.
- The worker service deploys stop-before-start (minimum healthy 0%, maximum
  100%) so two workers never overlap.

## Consequences

- Recovery latency is roughly the sweep threshold plus the schedule interval
  (minutes, not seconds).
- Deploys that interrupt a job are recovered through the same path as crashes.
- Locally, the sweep is run by hand (or on a timer) as a management command.
- Scaling past one worker requires revisiting this decision.

## Revisit if

- More than one worker, or concurrency above 1, is needed.
- Recovery latency matters to users.
- The broker changes (for example, to SQS).
