---
status: Proposed
date: 2026-10-30
source: RFC 0001 D1, §5.1, §7
supersedes:
superseded_by:
---

# 0001. Integrate as a GitHub App driven by `workflow_run` webhooks

## Context

The service needs test results from a repository's CI and a way to post
feedback on pull requests, without each maintainer creating and rotating
tokens.

## Constraints

- Setup must be one install plus one workflow step.
- Least privilege: no access to repository contents.
- GitHub expects a webhook response within about 10 seconds.
- One developer builds and operates it.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **GitHub App + `workflow_run` webhook** | Installation model with per-repo scoping; short-lived installation tokens; artifact and PR access through one identity | Needs a public HTTPS endpoint, signature checks, JWT and token handling |
| GitHub Action that POSTs results to the service | No webhooks to receive; simple to explain | Every repo stores a secret for the service; runs inside CI the service doesn't control; still needs an identity to comment |
| Polling the Actions API | No inbound endpoint | Slow feedback; spends rate limit on repos with no activity |
| OAuth App | Familiar flow | Acts as a user with broader scopes; tied to one person's account |

## Decision

Build a GitHub App subscribed to `workflow_run` (only `completed` is processed)
and the installation lifecycle events. Request Actions read, Pull requests
read & write, and Metadata read; confirm the exact set in M0. Authenticate as
the App with a JWT, and per installation with installation tokens.

## Consequences

- The web service needs a signed, size-limited webhook endpoint (M1) and a
  tunnel to receive deliveries during local development.
- Deliveries can be duplicated, reordered or never arrive (see
  [0006](0006-missed-webhook-deliveries.md)).
- Rate limits apply per installation.
- Users must allow third-party Apps on their organization.

## Revisit if

- M0 spikes show `workflow_run` lacks something the design depends on.
- Target users commonly can't install Apps (organization policy).
- A push-based Action turns out to be much easier to adopt.
