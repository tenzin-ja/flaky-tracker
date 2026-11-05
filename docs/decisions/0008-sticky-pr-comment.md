---
status: Proposed
date: 2026-10-30
source: RFC 0001 D5, §5.5
supersedes:
superseded_by:
---

# 0008. Keep one sticky PR comment with per-workflow sections

## Context

Developers should see flaky-test evidence where they already look, without a
new comment on every run. Several workflows and reruns update the same PR, and
deliveries can arrive out of order.

## Constraints

- Tasks can run twice; a crash can happen after GitHub accepts a comment but
  before its ID is saved.
- Workflows often trigger on both `push` and `pull_request`, giving two runs
  per commit with the same workflow ID.
- Test names come from untrusted reports.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **One comment per PR, one section per workflow** | Low noise; visible in the conversation | Read-modify-write must be serialized |
| Check runs | Native status UI | Needs another permission; less visible than a comment |
| A comment per run | Simplest writes | Floods the PR |
| A separate dashboard | Rich views | Users have to go somewhere else |

## Decision

- One comment per PR, found by a hidden marker. Its ID is stored; before
  creating a comment, look for an existing App-authored one with the marker.
- Sections are keyed by workflow ID.
- Only same-repository `pull_request` runs write. `push` and `schedule` runs
  feed history only.
- A run updates its section only if its `head_sha` matches the PR's current
  head, and an older execution never replaces a newer section.
- Updates hold a per-PR PostgreSQL advisory lock around read, render and write.
- Untrusted text is escaped; mentions and links are neutralized.

## Consequences

- Comment updates need the PR's current head (one API call) and paging through
  comments when the stored ID is missing.
- Workflows triggered only by `push` never appear in PR comments.
- The lock is held during a GitHub API call, so its duration must be bounded.

## Revisit if

- Users prefer check runs.
- Multiple workers make the lock a bottleneck.
