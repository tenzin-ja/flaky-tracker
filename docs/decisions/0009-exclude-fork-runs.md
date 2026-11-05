---
status: Proposed
date: 2026-10-30
source: RFC 0001 D12, §5.6
supersedes:
superseded_by:
---

# 0009. Exclude fork-origin runs from ingestion

## Context

A pull request from a fork runs code the repository owner doesn't control, so
its reports can be anything the contributor wants. If those results joined the
trusted history, an outsider could manufacture or hide flaky verdicts.

## Constraints

- No permission to read repository contents.
- The `pull_requests` list in a `workflow_run` payload is empty for fork runs,
  so an empty list doesn't prove a run is safe.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **Skip fork runs entirely** | Simple, closes the poisoning path | Fork contributors get no comments |
| A separate, untrusted history per fork | Useful to open-source projects | Separate storage and verdict rules to design |
| Ingest with a "from fork" label | Some value for fork PRs | Untrusted data still shares tables and queries |

## Decision

Skip a run when `head_repository.id` differs from `repository.id`, or when
`head_repository` is missing. Record the delivery, create no run.

## Consequences

- Open-source projects that mostly receive fork PRs get little value in the
  beta. This matters for the target audience.
- No fork data ever needs cleanup or isolation.

## Revisit if

- Early users are mostly open-source maintainers with fork contributors.
