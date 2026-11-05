---
status: Proposed
date: 2026-10-30
source: RFC 0001 D2, D14, D15, §4, §5.3
supersedes:
superseded_by:
---

# 0002. Read results from attempt-suffixed JUnit XML artifacts

## Context

Flake detection needs structured, per-attempt results: which tests ran in
which run attempt, and whether each passed, failed or was skipped. GitHub's
artifact records say which run uploaded them, but not which **attempt**.

## Constraints

- Report content is untrusted: archives and XML can be malicious or huge.
- Store as little user data as possible.
- Onboarding must stay one workflow step.
- Reruns, including "Re-run failed jobs", are how flakes are usually observed.

## Alternatives considered

| Option | For | Against |
| --- | --- | --- |
| **JUnit XML artifact named `test-results-<suite>-<attempt>`** | Standard format most runners emit; the name carries the attempt | Users must follow the naming; the suffix is self-declared |
| Unsuffixed artifact; infer attempt from creation time or list order | Simpler setup | Wrong provenance when attempts overlap or artifacts persist across attempts (rev. 1 review finding) |
| Scrape job logs | No setup | Fragile, runner-specific, large |
| Check-run annotations | Visible in the GitHub UI | Lossy; not every runner produces them |

## Decision

- Accept only artifacts named `test-results-<suite>-<attempt>` whose suffix
  equals the run's current attempt. A stale suffix is not this attempt's report.
- Beta contract: one non-matrix test job and one stable suite per workflow,
  with pytest as the documented setup.
- Support "Re-run all jobs" and "Re-run failed jobs" when the test job ran in
  that attempt. An attempt where it didn't adds no observation (pending M0
  validation; fall back to full reruns only if it can't be detected reliably).
- Store test identity, outcome counts, duration and provenance only. Failure
  messages and stack traces are discarded.
- Parse with `defusedxml` under byte, file, test-case and field limits.

## Consequences

- Missing, rejected or unsupported reports must be shown explicitly; the
  absence of a report is never treated as a pass.
- Matrix and sharded jobs are unsupported in the beta.
- Without stored failure text, failure explanations would need this decision
  revisited.

## Revisit if

- M0 shows failed-jobs reruns can't be attributed to an attempt.
- Users ask for matrix support or non-pytest onboarding.
- A feature needs failure text (privacy and redaction design first).
