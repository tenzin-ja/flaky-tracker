---
status: Proposed
date: 2026-10-30
source: RFC 0001 rev. 5 D11, F4, G2
supersedes:
superseded_by:
---

# 0006. Don't automatically recover missed webhook deliveries in the beta

## Context

GitHub doesn't retry a failed webhook delivery. Failed deliveries can be
redelivered by hand or through the API, but only for about 3 days. A delivery
that never reaches the service leaves nothing in PostgreSQL for
[0005](0005-recorded-run-recovery.md) to recover.

Rev. 4 proposed a scheduled task that polls the App's delivery log and
redelivers failures. It was never approved; rev. 5 defers it.

## Constraints

- Beta scope and schedule.
- Losing one run's results costs one observation, not correctness.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| Poll `GET /app/hook/deliveries` and redeliver failures (rev. 4) | Closes the gap automatically within 3 days | Cursor state, redelivery limits and alerts to build and test |
| Reconcile by listing recent workflow runs per repository | Independent of delivery history | Spends API quota on every repository |
| **Accept the gap; redeliver by hand** | No extra machinery | Runs are lost when the service is down and nobody notices |

## Decision

No automatic recovery in the beta. Deduplication on the delivery GUID makes
manual redelivery safe. The limitation is stated in the RFC, and goes in the
README's setup docs when they're written.

## Consequences

- Downtime loses observations unless someone redelivers in time.
- An alert on webhook-endpoint errors is the main safeguard.
- The durability guarantee covers only work the service has recorded.

## Revisit if

- An outage loses data that users notice.
- Deployment is stable and there's time after the core beta.
