---
status: Proposed
date: 2026-10-30
source: RFC 0001 D4, D13, §5.3, §5.4
supersedes:
superseded_by:
---

# 0007. Detect flakes as same-revision outcome flips; use three verdicts

## Context

"Flaky" needs a definition the comment can back with evidence, and the verdict
must never imply a failure is harmless or caused by the PR without proof.

## Constraints

- A `pull_request` run tests a merge of the PR into its base, not the head
  commit itself, so the same `head_sha` doesn't mean the same code.
- The environment (dependencies, services, secrets) can change even when the
  code doesn't.
- History starts empty for every newly installed repository.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **Pass and fail within one same-revision group** | Transparent, explainable evidence | Needs reruns or repeated runs to observe |
| Failure-rate threshold | Catches tests that rarely pass | Needs lots of data; can't separate flaky from broken |
| Same `head_sha` for every trigger | Simple | Wrong for PR runs (merge commits) |
| Four verdicts with a default-branch baseline (rev. 4) | Answers "is this my fault?" | Needs a baseline analysis that isn't designed yet |

## Decision

- **Test identity:** repository + workflow ID + suite + test key.
- **Comparison group:** `push` and `schedule` runs group by
  `(repository, workflow, suite, head_sha)`; `pull_request` runs group by
  `run_id` across attempts in which the test job ran.
- **Flake event:** a test has at least one pass and one fail in one group,
  counted once per group.
- **Window:** 30 days by run start time.
- **Verdicts** for each failed test, in order: *Has flaky history*, *Not enough
  history*, *No flaky history observed*.

## Consequences

- Tests that only run on PRs gain history slowly (only through reruns).
- Renamed tests start over.
- An environment change on the same commit can look like a flake; the comment
  says "observed flip", not "proven flaky".
- The tool doesn't say whether a failure is new or also failing on `main`.

## Revisit if

- Real histories show the verdicts mislead.
- Default-branch comparison is designed after the beta.
- More trigger types are supported.
