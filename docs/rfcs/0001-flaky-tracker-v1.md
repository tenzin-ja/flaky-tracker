# RFC 0001: Flaky Test Tracker v1

| | |
| --- | --- |
| **Author** | Tenzin Jamyang |
| **Status** | Draft, rev. 4 (addresses review round 1; see [§15](#15-review-responses)) |
| **Created** | 2026-10-20 |
| **Target** | First deploy ~2026-12-20 · core beta with external users ~2027-01-10 · v1 launch ~2027-01-24 |
| **Related** | [`DESIGN.md`](../../DESIGN.md): implementation specs (API calls, limits, schema detail, comment format) |

> **Reviewers:** this RFC covers *what* v1 is, the rules it follows, the key
> decisions, the risks and the plan. `DESIGN.md` holds exact numbers and API
> details. Where the two disagree, this RFC wins. Questions for reviewers are in
> [§12](#12-questions-for-reviewers).

---

## 1. Summary

A GitHub App that ingests JUnit XML test reports from GitHub Actions runs,
detects flaky tests by finding tests whose outcome changed **while the code under
test did not**, and keeps one comment on each pull request that classifies every
failure using the test's recorded history: *has flaky history*, *also failing on
main*, *new failure* or *not enough history*. After the core ships, new failures
on public repositories also get a short, clearly labelled AI-generated
explanation.

Flaky detection is deterministic, based on stored results. AI is an optional
enrichment and never decides a verdict.

v1 is a Django application with Celery workers (Redis as the broker) and
PostgreSQL as the system of record. One Docker image runs as three processes
(web, worker, scheduler) on AWS ECS Fargate, with infrastructure in Terraform.

## 2. Motivation

Flaky tests pass and fail on identical code. They cost teams in three ways:
reruns waste developer time and CI minutes; red builds stop meaning anything, so
real regressions get merged; and the evidence is spread across hundreds of runs,
so nobody can say which tests are flaky. At Google, almost 16% of tests showed
some flakiness, and 84% of observed pass-to-fail transitions involved a flaky
test.

Commercial tools address this for large organizations. Small teams, student
projects and most open-source repositories have nothing. GitHub's built-in AI can
explain a single failed run, but nothing in the default GitHub experience tracks
a test's **history across runs**, which is what it takes to say "this failure
probably isn't yours."

## 3. Goals and non-goals

**Goals (v1)**

- **G1. Easy setup.** Install the App and add one upload step to CI in under 5
  minutes.
- **G2. No lost or duplicated data.** Every available, valid test report from
  every completed run of a tracked workflow is recorded exactly once, including
  runs whose webhook delivery initially failed (recovered within GitHub's 3-day
  redelivery window). Cancelled and timed-out runs are ingested if they produced
  reports.
- **G3. Trustworthy detection.** A conservative rule that can be explained in
  one sentence, with evidence linked in every verdict.
- **G4. Fast feedback.** The PR comment is created or updated within 60 s (p95)
  of the webhook *arriving*. GitHub's own delivery delay is measured and
  reported separately, because it's outside the service's control.
- **G5. Safe AI enrichment** (after the core beta). Explanations for new
  failures on public repositories only, added after the comment is posted, under
  cost caps.
- **G6. Real deployment.** Runs on AWS, installed on repositories I don't own.

**Non-goals (v1)**

- CI systems other than GitHub Actions; test formats other than JUnit XML.
- Automatically quarantining, skipping or retrying tests.
- **Runs triggered from forks.** They are not ingested at all (§5.6).
- AI explanations for private repositories.
- A web dashboard (moved to stretch goals); notifications outside GitHub;
  billing; org-wide analytics.

## 4. User experience

**Maintainer setup**

1. Install the App from its GitHub page and pick repositories.
2. Add one step after the tests in each job that produces a report:

   ```yaml
   - uses: actions/upload-artifact@v7
     if: always()
     with:
       # test-results-<suite>-<attempt>. Matrix jobs put every matrix value in <suite>,
       # e.g. test-results-unit-${{ matrix.os }}-py${{ matrix.python }}-${{ github.run_attempt }}
       name: test-results-unit-${{ github.run_attempt }}
       path: reports/            # contains the JUnit XML file(s)
   ```

   The `<suite>` part keeps different jobs and matrix legs apart. The
   `<attempt>` suffix tells the App which re-run attempt produced the report
   (§5.3).

**Developer on a PR.** One comment per PR, with one section per workflow,
edited in place as runs finish:

> **🧪 `CI` · commit `a1b2c3d`** — 4 of 214 tests failed
>
> | Test | Verdict | Evidence |
> | --- | --- | --- |
> | `tests/test_auth.py::test_login_expired_token` | ⚠️ Has flaky history | Flipped on identical code 4× in 30 days. Can still be a real failure. |
> | `tests/test_api.py::test_rate_limit` | 🔴 Also failing on `main` | Latest `main` result failed (`9f8e7d6`) |
> | `tests/test_cards.py::test_import_variant` | 🆕 New failure | Passed in the latest `main` run |
> | `tests/test_sync.py::test_backfill` | ❔ Not enough history | No recent `main` result to compare against |
>
> **✅ `Lint`** — all 12 reported tests passed on `a1b2c3d`.

A section only describes its own workflow on the PR's current commit. A missing
or rejected report is stated explicitly, never shown as a pass.

## 5. Proposed design

### 5.1 Architecture

```
GitHub ──webhooks──► web (Django + gunicorn)
                       │ verify HMAC · dedupe delivery GUID
                       │ INSERT delivery + run (pending) in 1 txn · on_commit → enqueue · 202
                       ▼
                     Redis ──► worker (Celery) ──► GitHub REST API (artifacts, PRs, comments)
                                   │        └────► LLM API (async enrichment, public repos)
                                   ▼
                              PostgreSQL (system of record)
                                   ▲
       beat (Celery) ──────────────┘  · sweep: re-enqueue stuck runs
                                      · recovery: redeliver failed webhook deliveries
```

- **web:** verifies, records and returns quickly (GitHub expects a response
  within ~10 s). It also serves the admin.
- **worker:** processes runs, posts comments and runs the enrichment and
  recovery tasks.
- **beat:** schedules the sweep and webhook recovery.
- **PostgreSQL:** the system of record. A run row's `status` decides whether
  work is outstanding, not the broker.
- **Redis:** broker only. Losing it loses no data.

### 5.2 Event flow for one workflow run

1. **Receive.** Verify `X-Hub-Signature-256` over the raw body (size-capped).
   Ignore everything except `workflow_run.completed` and installation events. If
   the run's head repository differs from the base repository, it's a fork run:
   record the delivery and stop (§5.6).
2. **Record.** In **one transaction**, insert the delivery GUID (duplicate →
   return 200) and upsert the run attempt `(run_id, run_attempt)` as `pending`.
3. **Enqueue.** `transaction.on_commit` enqueues `process_run`, so the task can
   never run before the row exists.
4. **Claim.** A conditional update sets `processing`, a fresh `claim_token` and
   `claimed_at`, and **commits before any network call**. It succeeds only if the
   run is `pending`, or `processing` with an expired lease. Every later write
   checks the claim token, so a stale worker can't overwrite a newer one. The
   claim also checks that the repository and installation are still active.
5. **Discover reports.** List *all* pages of the run's artifacts and filter names
   client-side by the `test-results-` prefix. Parse each name into suite and
   attempt, and keep only artifacts produced by **this** attempt (§5.3). Skip
   artifacts already marked complete.
6. **Ingest each artifact.** Stream the download to a temp file and decompress
   in a streaming fashion, enforcing per-artifact and **per-run** limits on
   bytes, files, test cases, field lengths and time. Parse incrementally. Redact
   failure text, then store the results **and** mark the artifact complete in
   **one transaction**. If a limit is hit, record the artifact as rejected with
   the reason; the run ends as `partial`.
7. **Detect.** Under a per-repository advisory lock, record new flake events
   (§5.4).
8. **Comment.** Only for `pull_request` runs on same-repository PRs, and only if
   the run's commit is still the PR's current head; results for older commits
   are stored but don't change the comment. Render this workflow's section and
   upsert the sticky comment (§5.5).
9. **Finish.** Mark the run `processed` or `partial` (claim token checked). After
   the core beta, enqueue `explain_failures` for new failures on public repos.
   It edits the comment later, only if the PR head hasn't moved.
10. **Retries.** Transient errors retry with exponential backoff and jitter, up
    to 5 times, honoring GitHub's rate-limit signals (§5.7). Permission errors
    don't retry. After the final failure, the run is marked `failed` with the
    error.
11. **Safety nets (beat):**
    - **Sweep** (every 5 min): re-enqueue runs `pending` for over 5 min, or with
      an expired lease. The lease is longer than the task's hard time limit,
      which is longer than its soft limit.
    - **Webhook recovery** (every 10 min): page through the App's recent
      deliveries from a stored cursor and redeliver any that failed. A
      redelivery reuses the GUID, so step 2 dedupes it. Alert if failures
      persist or recovery falls behind GitHub's 3-day redelivery window.

### 5.3 Identity and provenance

- **Test identity** = repository + **workflow ID** + suite + test key
  (`file::classname::name`). The suite comes from the artifact name. Attempt
  numbers and sharding never enter identity, so the same test across attempts
  and shards is one test, while a CI workflow and a nightly workflow stay
  separate.
- **Observation** = one test in one run attempt. Repeated entries in one report
  (runner-level retries) are aggregated into pass/fail/skip counts, not
  collapsed.
- **Provenance:** the artifact API doesn't say which attempt produced an
  artifact, and a partial re-run can list earlier attempts' artifacts. The
  attempt therefore comes from the **artifact name suffix**. Fallback for
  artifacts without a suffix: accepted only when `run_attempt = 1`, where
  there's no ambiguity; otherwise skipped, and the comment explains how to add
  the suffix.
- **Time:** windows and recency use the run's **start time**, never when we
  ingested it, so delayed or recovered data doesn't look recent.

### 5.4 Flake detection

**Identical-code groups.** Each observation belongs to exactly one group:

| Trigger | Group | Why |
| --- | --- | --- |
| `push`, `schedule` | `sha:<workflow>:<head_sha>`, covering all runs and attempts on that commit | Same workflow, same commit, no inputs |
| `pull_request`, `pull_request_target`, `merge_group`, `workflow_dispatch` | `run:<run_id>`, covering only attempts of that run | A PR run tests a temporary merge commit that changes as the base moves; dispatch inputs can change behavior |

One canonical group per observation means a re-run of a push run can't be
counted twice.

**Supported contract:** the workflow checks out the triggering commit (the
default `actions/checkout` behavior). Workflows that check out other refs aren't
supported, and the README says so.

**Rules**

- **Flake event:** a group in which the test has at least one passing and at
  least one failing occurrence, including both within one attempt. Skips are
  ignored. One event per (test, group), timestamped by the run start time.
- **Has flaky history:** at least `FLAKY_MIN_EVENTS` events (default 1) within
  `FLAKY_WINDOW_DAYS` (default 30).

**Verdicts** for each failed test on the PR's current head, checked in order:

1. ⚠️ **Has flaky history**: the rule above. The wording never claims the
   current failure is harmless.
2. ❔ **Not enough history**: no non-skipped default-branch result for this test
   within the window. This covers new installs, renamed tests and PR-only
   suites.
3. 🔴 **Also failing on main**: the latest non-skipped default-branch result
   failed.
4. 🆕 **New failure**: the latest default-branch result passed.

### 5.5 PR comment

- One comment per PR, identified by a hidden marker. Its ID is stored after
  creation. **Before creating a comment**, the worker searches the PR's comments
  for an App-authored marker; this covers a crash between "GitHub accepted the
  POST" and "ID saved."
- Each workflow owns one section, keyed by workflow ID. A section is replaced
  only by a run for the PR's current head that started later than the run
  currently shown, so out-of-order deliveries can't roll it back.
- All text from reports or the model is rendered inside code spans or blocks,
  with backticks escaped, `@mentions` neutralized and links not auto-rendered.

### 5.6 Trust, forks and installation lifecycle

- **Forks:** runs whose head repository differs from the base repository are not
  ingested. The check compares repository IDs from the payload; an empty PR list
  is never treated as proof either way. Without this, untrusted XML from a fork
  could create flake events for the base repository's tests.
- **Lifecycle:** handle installation created, deleted, suspended and
  unsuspended, and repositories added or removed. On any change to repository
  selection, **re-sync** the accessible repository list from the API instead of
  trusting the event's diff. Uninstall deletes all of that installation's data;
  removing a repository deactivates it and deletes its data. Queued work for an
  inactive repository exits at the claim step and can't recreate deleted rows
  (foreign keys plus the claim check). Suspension invalidates cached tokens.

### 5.7 GitHub API client

- Read every page of every list (`per_page=100`, following `Link` headers).
  Filter artifact names client-side; the API's `name` filter is exact-match only.
- **Primary rate limit:** when the remaining quota is 0, wait until the reset
  time.
- **Secondary rate limits:** honor `Retry-After`, otherwise back off for at least
  60 s, increasing on each retry. Backoff is shared per installation (stored in
  Redis), so concurrent tasks don't keep hammering the API.
- Permission errors (401/403 without rate-limit signals) are failures, not
  retries, and are surfaced in the admin.

### 5.8 Module layout

```text
config/       Django project: settings, urls, celery.py, wsgi
core/         health check, shared helpers (redaction, markdown escaping)
webhooks/     view + signature check · Delivery model · installation lifecycle · delivery recovery task
githubapp/    App JWT, installation-token cache, REST client (pagination, rate limits)
ingest/       Run, Artifact, Test, TestResult models · tasks.py · artifacts.py (stream, limits) ·
              junit.py (incremental parse) · store.py
detection/    FlakeEvent model · flakes.py · verdicts.py
comments/     PrComment model · render.py · sticky.py
explain/      AiExplanation model · llm.py · prompts (after the core beta)
infra/        Terraform
```

`junit.py`, `flakes.py`, `verdicts.py`, `render.py` and the redaction helpers
are pure logic with no network calls, so they can be unit tested with fixtures.

## 6. Alternatives considered

| Decision | Chosen | Alternatives | Why |
| --- | --- | --- | --- |
| **D1. Integration model** | GitHub App + `workflow_run` webhook | GitHub Action that POSTs results; polling the Actions API | No secrets in user repos, native re-run visibility, comments as itself. Polling wastes rate limit and adds delay. |
| **D2. Result source** | JUnit XML artifacts with a naming convention | Raw job logs; check-run annotations | JUnit is near-universal and structured. The naming convention is the only reliable way to get provenance (§5.3). Cost: one CI step (R1). |
| **D3. Background jobs** | Celery + Redis; run row as source of truth; `on_commit` + sweep | Postgres `SKIP LOCKED` queue; Django 6 Tasks; SQS; RQ | Standard Python job stack with retries, time limits and scheduling. The Postgres–Redis dual-write gap is closed by the sweep (§5.2 step 11). |
| **D4. Flaky definition** | Outcome flip within an identical-code group | Failure-rate threshold; statistical model | Conservative and explainable, with no threshold to justify before there's data. |
| **D5. PR output** | One sticky comment, one section per workflow | Check Run; commit status | Most visible. Per-workflow sections avoid false global "all passing" claims. |
| **D6. Web framework** | Django | FastAPI + SQLAlchemy; Go | Familiar language; admin as an internal tool; covers both major Python frameworks across my projects. |
| **D7. Compute** | ECS Fargate, one image, three services | EC2; Lambda + SQS; EKS | No servers to patch. Lambda has cold starts and Postgres connection issues; EKS is too costly for one small service. |
| **D8. AI use** | Async enrichment, new failures only, public repos only, per-repo cache | Every failure; private repos with redaction; none | Verdicts come from data. Public-only avoids promising privacy that regex redaction can't guarantee. Async keeps G4. |
| **D9. Infrastructure** | Terraform | Console click-ops; AWS CDK | Reviewable, repeatable, destroyable; most widely used IaC tool. |
| **D10. Tooling** | uv, Ruff, strict mypy, pytest-django | pip; flake8/black; no types | Lockfile, fast installs, early bug detection. |
| **D11. Missed webhooks** | Scheduled redelivery of failed App deliveries | Reconcile against the Actions runs API; accept loss | GitHub's recommended approach. It reuses the normal intake path, and dedupe makes it safe. Limit: 3-day window, hence alerting. |
| **D12. Fork runs** | Ignore in v1 | Ingest into an isolated, untrusted history | Simplest safe option. Isolation adds a trust dimension to every query; revisit if fork-heavy users need it. |

## 7. Security and privacy

- **Webhooks:** HMAC-SHA256 over the raw body, constant-time comparison,
  request size cap.
- **Permissions:** least privilege — Actions *read*, Pull requests *read &
  write* (also covers PR comments), Metadata *read*. No repository contents.
- **Untrusted input:** artifacts and reports are hostile until parsed. Streaming
  download and decompression, aggregate per-run limits, `defusedxml`
  incremental parsing, bounded field lengths and nesting, processing deadlines,
  and explicit `rejected`/`partial` outcomes. Exact limits are in `DESIGN.md`.
- **What is stored (stated accurately in the README):** test identifiers,
  outcomes, durations and the first 4 KB of failure output. Failure output can
  include source snippets or data printed by tests. Redaction of token-like
  strings is **best-effort** and runs before storage, logging, rendering and
  any model call.
- **AI:** public repositories only. Model output is treated as untrusted text
  (§5.5). The cache is per repository, expires, and is deleted with the
  repository.
- **Data deletion:** uninstalling, or removing a repository, deletes its data
  through cascading foreign keys.
- **Secrets:** AWS Secrets Manager in production and an untracked `.env`
  locally. CI deploys through GitHub's OIDC role, so there are no long-lived AWS
  keys.
- **Django hardening:** HTTPS redirect (except `/healthz`), secure cookies,
  HSTS; `check --deploy` in the release pipeline.

## 8. Risks and mitigations

| # | Risk | L | I | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Users won't add the upload step or naming convention | Med | High | Copy-paste snippets for pytest, Jest and Go; the comment explains exactly what's missing |
| R2 | False "flaky" labels erode trust | Med | High | Identical-code groups, "not enough history" verdict, cautious wording, evidence links; audit before launch |
| R3 | Artifact behavior across attempts differs from assumptions | Med | Med | Naming-based provenance; M0 spikes cover full and partial re-runs and delayed processing |
| R4 | GitHub rate limits | Low | Med | Pagination-aware client, shared backoff per installation (§5.7) |
| R5 | Sensitive data in failure output | Med | High | Accurate disclosure, best-effort redaction everywhere, AI only on public repos |
| R6 | LLM cost or latency | Low | Med | Async, capped, cached per repo; comment never waits on it |
| R7 | **Schedule:** ~10 h/week alongside classes, Cardfolio and applications | High | High | Hour-based plan (§10), cut list, decision checkpoint on Dec 6 |
| R8 | Hosting cost | Low | Low | Smallest sizes, 90-day retention, `terraform destroy` when idle |
| R9 | Django, Celery and Terraform are new | Med | Med | Scaffold already wired; one new tool per milestone |
| R10 | Run saved but never enqueued | Low | Med | `on_commit` + sweep; idempotent processing |
| R11 | Webhooks missed for longer than GitHub's 3-day window | Low | Med | Recovery every 10 min; alerts on persistent failures or lag |
| R12 | Out-of-order or duplicate events corrupt the comment | Med | Med | Head-SHA and start-time checks per section; marker lookup before create |

**Cut list (in order):** AI enrichment → "also failing on main" (folds into
"new failure"). The **must-ship core** is reliable ingestion + flake detection +
sticky comment, deployed and used by at least one external repository.

## 9. Success metrics

- **Reliability:** across 500+ processed runs, zero lost or duplicated runs,
  checked by reconciling against GitHub's run list. p95 processing time ≤ 60 s
  from webhook receipt; GitHub's delivery delay reported separately.
- **Accuracy:** in a manual audit of up to 20 tests with flaky history, at least
  90% are genuinely flaky.
- **Beta:** at least 3 external repositories (not mine) installed by the
  core-beta date.
- **Later outcomes, not launch gates:** 10+ repositories, 1,000+ runs, and LLM
  cost under $1/day.

## 10. Plan

Capacity: about 10 hours a week (2–3 h × 4 days). Winter break may run
faster.

| Milestone | Est. | Dates | Scope | Exit criteria |
| --- | --- | --- | --- | --- |
| **M0** Setup & spikes | 6 h | Oct 22 – Nov 8 (alongside Cardfolio's deploy) | Register the App; capture real payloads as fixtures; spikes (§13) | Spike findings recorded; fixtures committed |
| **M1** Webhook intake | 10 h | Nov 9 – 15 | Models + migrations + admin; webhook view; dedupe; fork skip; installation lifecycle | Redelivery creates no duplicate; lifecycle tests pass |
| **M2** Ingestion worker | 18 h | Nov 16 – Dec 6 *(Thanksgiving)* | Claim/lease, retries; GitHub client (JWT, tokens, pagination, rate limits); artifact discovery + provenance; streaming limits; incremental parser; transactional storage | Real playground runs stored; re-processing changes nothing; killing the worker mid-run loses nothing |
| **M3** First deploy | 14 h | Dec 7 – 20 *(finals, then break)* | Terraform (ECR, ECS ×3, RDS, Redis, ALB/HTTPS, Secrets Manager); CI/CD via OIDC; CloudWatch alarms; Sentry; webhook recovery task | App runs in AWS and collects real history from the playground and Cardfolio while detection is built |
| **M4** Detection & comments | 14 h | Dec 21 – Jan 3 *(break)* | Groups and flake events; verdicts; per-workflow sticky comment; stale-head handling | Correct verdicts on the playground, including "not enough history" |
| **M5** Hardening → **core beta** | 8 h | Jan 4 – 10 | Acceptance tests (below); README, setup snippets; first external installs | Core beta live with ≥ 3 external repos |
| **M6** AI enrichment & launch | 10 h | Jan 11 – 24 | Public-repo explanations (async, cached, capped, evaluated); demo GIF; launch posts | v1 launched |

**Acceptance tests (M5):** deterministic flips; partial re-runs; reordered
deliveries; duplicate deliveries; worker crash mid-run; webhook delivery failure
followed by recovery; two runs for one PR racing to update the comment; a
rejected oversized artifact.

**Decision checkpoint, Dec 6:** if real playground results aren't stored
end-to-end by then, apply the cut list immediately and re-plan M3–M5.

**Why deploy before detection:** the deployed pipeline collects real history
while detection is being built, so M4 is developed against weeks of genuine data
instead of an empty database. It also surfaces infrastructure problems early.

Work happens on branches and merges through PRs; `main` is protected and
CI-gated.

## 11. Open questions

1. Final name; check GitHub Marketplace availability before M3.
2. LLM provider and model (decide in M6, by cost per explanation).
3. Production broker: ElastiCache or a Redis container on Fargate (decide in
   M3).
4. `FLAKY_MIN_EVENTS` = 1 or 2 (revisit after the audit).
5. Retention: 90 days, with every statistic showing its window and sample size.

## 12. Questions for reviewers

- Is the canonical-group table (§5.4) correct for every trigger listed?
- Does naming-based provenance (§5.3) hold for partial re-runs, given the M0
  spike results once they're filled in?
- Are the claim lease, Celery's late acknowledgement and the Redis visibility
  timeout configured so a running task is never duplicated (§5.2 steps 4 and 11)?
- Does webhook recovery plus the sweep make G2 achievable, apart from the
  documented 3-day limit?
- Is the hour-based plan (§10) realistic, and is the cut list in the right
  order?

## 13. Spike findings (M0)

- **Artifacts across attempts:** does a full re-run or a "re-run failed jobs"
  list earlier attempts' artifacts? Can the same artifact name be reused across
  attempts? _TBD_
- **Delayed processing:** when attempt 2 completes before attempt 1 is
  processed, what does each attempt's artifact list return? _TBD_
- **PR vs push:** `head_sha`, `head_repository` and `pull_requests` values for
  same-repo PRs, fork PRs and pushes. _TBD_
- **Delivery recovery:** cursor behavior of the App deliveries list; how a
  redelivery appears (same GUID, `redelivery: true`). _TBD_

## 14. Revision history

- **2026-10-20:** Initial draft.
- **2026-10-21:** Rev. 2. Switched to Django + Celery/Redis; added Terraform
  and tooling decisions; named the AWS services.
- **2026-10-21:** Rev. 3. Addressed review round 1 (§15); scope set to core
  beta first, then AI; hour-based plan; deploy before detection.
- **2026-10-21:** Rev. 4. Re-estimated the plan at ~80 h total; core beta
  moved to ~Jan 10.

## 15. Review responses

Round 1 reviewed rev. 1, before the Django switch. Every finding was accepted;
#5 and #7 in a simplified form, and #16 by re-planning.

| # | Sev. | Finding | Resolution | Where |
| --- | --- | --- | --- | --- |
| 1 | Blocking | "First seen" attempt isn't provenance | Attempt encoded in the artifact name; fallback only for attempt 1; spikes added | §4, §5.3, §13 |
| 2 | Blocking | Identity collides across workflows and matrix dimensions | Identity = repo + workflow ID + suite + test key; suite carries every matrix value | §4, §5.3 |
| 3 | Should fix | Identical-code contract too loose; groups overlap | One canonical group per observation; trigger table; checkout contract documented | §5.4 |
| 4 | Blocking | Webhooks that never arrive are lost | Scheduled recovery from a stored cursor + alerts (D11); G2 redefined; cancelled/timed-out runs ingested | §3, §5.2, §6 |
| 5 | Blocking | One passing workflow can claim everything passes | Per-workflow sections; current-head and start-time checks; explicit missing/rejected states | §4, §5.5 |
| 6 | Blocking | Global AI cache can leak across repos | Per-repo cache keyed by model and prompt version, with expiry and cascade delete | §7, D8 |
| 7 | Blocking | Fork reports can influence trusted verdicts | Fork runs not ingested, detected by repository IDs (D12) | §5.6 |
| 8 | Should fix | Installation lifecycle incomplete | Full event handling; re-sync on selection changes; claim-time activity check; token invalidation | §5.6 |
| 9 | Should fix | Leases and external side effects not retry-safe | Committed claim with token and lease; marker lookup before create; artifact results atomic | §5.2, §5.5 |
| 10 | Should fix | AI sequencing breaks the latency goal | AI is async enrichment after posting; latency measured from receipt | §3, §5.2 |
| 11 | Should fix | Limits only per archive | Streaming plus per-run limits, incremental parse, deadlines, partial/rejected outcomes | §5.2, §7 |
| 12 | Should fix | Regex redaction can't back a privacy promise | Accurate disclosure; redaction everywhere, best-effort; AI on public repos only; output escaping | §7, §5.5 |
| 13 | Should fix | No baseline shown as a new regression | "Not enough history" verdict; "has flaky history" wording | §5.4 |
| 14 | Should fix | Observation identity, timestamps, foreign keys unclear | Aggregated counts per attempt; run start time for windows; foreign keys across runs, artifacts and results | §5.3, `DESIGN.md` §6 |
| 15 | Should fix | Pagination and secondary rate limits missing | Full pagination, client-side filter, `Retry-After`, shared backoff, permission errors separate | §5.7 |
| 16 | Should fix | Plan overcommitted | Hour-based plan at ~10 h/week; dashboard cut; AI after core beta; deploy before detection; Dec 6 checkpoint | §8, §10 |
