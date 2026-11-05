# RFC 0001: Flaky Test Tracker v1

| | |
| --- | --- |
| **Author** | Tenzin Jamyang |
| **Status** | **Proposed draft, rev. 5 — pending review/approval** |
| **Created** | 2026-10-20 |
| **Updated** | 2026-10-30 |
| **Targets (provisional)** | Local end-to-end demonstration ~2026-12-20 · AWS deploy ~2027-01-10 · public core beta ~2027-01-24 |
| **Related** | [`DESIGN.md`](../../DESIGN.md): implementation specs (API calls, limits, schema detail, comment format) |

> **Reviewers:** this RFC covers *what* v1 is, its boundaries, behavior,
> architectural decisions, risks and plan. `DESIGN.md` contains exact schemas,
> API details and numerical limits. **`DESIGN.md` still describes rev. 4 and
> is reconciled with this revision during M0 (§10, §16), before any affected
> module is implemented.** Where the two disagree, this RFC is the proposed
> source of truth, **once approved**. M0 spike findings remain unverified
> until experiments are completed.

---

## 1. Summary

Flaky Test Tracker is a GitHub App that consumes JUnit XML reports uploaded as
GitHub Actions artifacts, records test-result history, detects tests that have
both passed and failed in a supported same-revision comparison group, and
maintains **one sticky PR comment with one section per workflow**.

For the first beta, a failed test receives one of three conservative verdicts:
**has flaky history**, **not enough history**, or **no flaky history observed**.
The tool never labels a failure as *new* or *already failing on main*: comparing
against the default branch is deferred. A flaky-history verdict is evidence of
inconsistency, **not** proof that the current failure is harmless.

v1 uses **Django**, **PostgreSQL** (durable system of record), **Celery + Redis**
(asynchronous execution), and **AWS ECS Fargate + Terraform** for an actual
cloud deployment. Start with one web service and one single-concurrency worker;
an EventBridge Scheduler schedule launches a short-lived ECS task that sweeps
durable, recorded work. The design
retains idempotent database effects and fault testing without claiming
exactly-once task execution or production-scale availability.

This is a backend/platform engineering portfolio project with a modest user
adoption goal, **not a startup or full CI observability platform**. The core
beta should be demonstrable and resume-ready before additional features.

## 2. Motivation

Flaky tests make CI results harder to trust. Reruns waste engineering time and
CI minutes; past evidence is scattered across workflow executions. An
explainable comment attached to a PR can give developers useful historical
context without requiring a separate dashboard.

Existing commercial and open-source tools already address parts of this
problem. The project does **not** claim category novelty. Its early audience is
small Python projects, student teams and open-source maintainers who value
low-friction GitHub-native feedback. A few active external installations and
feedback-driven changes would be meaningful outcomes.

## 3. Goals and non-goals

**Goals (core beta)**

- **G1. Easy setup for a defined audience.** A maintainer can install the App
  and add one documented `pytest`/JUnit artifact-upload step. Aim for ~5 minutes
  on a compatible repository; measure this rather than treating it as guaranteed.
- **G2. Durable and idempotent accepted work.** For supported workflow events
  that reach and are recorded by the service, valid accessible reports are
  retried and stored without duplicate database effects. Tasks may execute
  more than once. Explicitly expose missing, skipped or rejected reports.
  **Missed webhooks outside the service are not automatically recovered in v1.**
- **G3. Trustworthy, limited detection.** Flaky verdicts use deterministic,
  reproducible historical evidence. The tool distinguishes lack of evidence
  from absence of observed flakiness and never equates a flaky label with safety.
- **G4. Observable feedback latency.** Measure webhook-to-comment latency and
  GitHub delivery delay separately. A 60-second p95 is an *aspirational later
  target*, not a beta acceptance guarantee.
- **G5. Real deployment and operations.** Deploy a usable, monitored,
  containerized product to AWS through Terraform and CI/CD; demonstrate
  recovery of recorded jobs after a worker failure.
- **G6. Feedback-driven development.** Test onboarding with an independent
  repository and invite external maintainers. Three active external
  repositories is an adoption *target*, not a technical release gate.

**Supported beta contract**

- GitHub Actions only; JUnit XML only; documented **pytest** configuration.
- Same-repository `push`, `schedule` and `pull_request` workflows (subject to
  M0 provenance validation), with **one test-producing, non-matrix job and one
  stable artifact suite per workflow**. Multiple separate workflows may each
  contribute their own comment section.
- **Reruns** are in scope because repeated observations are central to flake
  detection: both **"Re-run all jobs"** and **"Re-run failed jobs"**, as long
  as the test-producing job actually re-runs in that attempt and its artifact
  carries that attempt's suffix (§5.3). An attempt in which the test job did
  not re-run adds no observation and leaves the previous section in place.
- **PR comments are written only by `pull_request` runs.** `push` and
  `schedule` runs contribute history but never edit a PR comment, even when
  their payload lists an open PR.
- If an event or artifact's commit, PR or attempt provenance is ambiguous,
  record it as unsupported/skipped rather than guessing.

**Non-goals (core beta)**

- Complex matrix/sharded test jobs, multiple test-producing jobs per workflow,
  partial reruns whose artifact cannot be tied to one attempt, and
  mixed-attempt artifact aggregation; more trigger types such as `merge_group`,
  `workflow_dispatch` and `pull_request_target` until tested and designed.
- Default-branch comparisons; **"also failing on main"** and **"new failure"**
  verdicts. These are later feature candidates, not silently renamed to each
  other.
- AI-generated explanations, LLM integrations, prompt caching and costs.
- Raw failure/stack-trace storage, advanced secret redaction, and a web
  dashboard.
- CI providers besides GitHub Actions; broad language-specific onboarding
  beyond pytest; auto-quarantine; notifications outside GitHub; billing.
- Automated recovery of GitHub webhook deliveries that never reached this
  service; distributed per-installation API throttling; multi-worker scaling;
  multi-AZ/high-availability infrastructure; advanced tracing/Sentry.
- Runs from forked repositories: excluded from ingestion (§5.6).

## 4. User experience

**Maintainer setup**

1. Install the GitHub App and select one or more repositories.
2. For each supported single-job workflow, run pytest with JUnit XML output
   and upload the result after tests finish. For example (assumes the test
   command writes to `reports/junit.xml`):

   ```yaml
   - uses: actions/upload-artifact@v7
     if: always()
     with:
       # One non-matrix test-producing job per supported workflow.
       # Suffix is mandatory: it ties the report to its run attempt.
       name: test-results-unit-${{ github.run_attempt }}
       path: reports/junit.xml
       if-no-files-found: error
   ```

   The artifact name follows `test-results-<suite>-<attempt>` with a stable
   `<suite>` (e.g. `unit`). Its suffix is the run attempt. Validate the
   installed upload action version and real artifact behavior in M0.
3. On a failed upload or unsupported configuration, use the run logs and
   documentation for corrective instructions. The service must not pretend an
   absent report is a passing test run.

**Developer on a PR.** One App-authored comment per PR; one section per
supported workflow; edits replace the relevant section in place:

> **🧪 `CI` · tested revision `a1b2c3d`** — 3 of 214 tests failed
>
> | Test | Verdict | Evidence |
> | --- | --- | --- |
> | `tests/test_auth.py::test_login_expired_token` | ⚠️ Has flaky history | Passed and failed in 2 supported same-revision groups in 30 days; check linked runs. Current failure may still be real. |
> | `tests/test_cards.py::test_import_variant` | ◻️ No flaky history observed | 7 prior recorded observations in 30 days, no same-revision flip observed. Not proof of stability. |
> | `tests/test_sync.py::test_backfill` | ❔ Not enough history | No earlier comparable non-skipped observation in the 30-day window. |
>
> **✅ `Lint`** — 12 reported tests passed on its current supported revision.

Test identities and run/commit evidence are linked where available. The comment
must state when a supported workflow has a **missing, invalid, rejected or
unsupported** report; it must never claim an unreported test has passed.

## 5. Proposed design

### 5.1 Architecture

```text
GitHub ──webhooks──► web (Django + gunicorn, ECS)
                       │ verify HMAC · dedupe delivery GUID
                       │ commit delivery + run (pending) in PostgreSQL
                       └─ on_commit → Redis broker ──► Celery worker (ECS, concurrency=1)
                                                      │ GitHub REST API
                                                      │ artifact parser + classifier
                                                      │ PR comment updater
                                                      ▼
                                               PostgreSQL (system of record)
                                                      ▲
EventBridge Scheduler ──► short-lived ECS sweep task ──┘
  (recurring schedule)   re-enqueue stale recorded work; NOT webhook redelivery
```

- **Web:** handles GitHub webhook verification, durable intake, installation
  events, health checks and an internal admin surface; responds promptly.
- **Worker:** one logical worker replica with Celery concurrency 1 in beta,
  processing ingestion, classification, comments and bounded retries. The
  worker ECS service deploys with **minimum healthy 0% / maximum 100%**, so
  the old task stops before the new one starts (ECS's default starts the new
  task first and would briefly run two workers). A deploy can therefore stop
  a task mid-job; recovery handles that like any other crash.
- **PostgreSQL:** canonical delivery, run, artifact, observation and comment
  records; unique constraints enforce idempotent database effects.
- **Redis:** task broker only. It is not the system of record.
- **Recovery:** a recurring **EventBridge Scheduler** schedule launches a
  short-lived ECS task (same image, a Django management command) that finds
  old `pending` runs and `processing` runs whose claim is older than the hard
  task time limit plus a safety margin, re-enqueues them and exits. No
  always-running Celery Beat service; no scheduler embedded in the worker; no
  GitHub delivery polling.
- **AWS:** one image for web, worker and scheduled management command;
  ECR/ECS Fargate, RDS PostgreSQL, a suitably sized Redis broker, ALB/HTTPS,
  IAM/Secrets Manager, CloudWatch and Terraform. Cost, broker topology and
  network placement must be finalized before production provisioning.

### 5.2 Event flow for one workflow run

1. **Receive.** Verify `X-Hub-Signature-256` against the size-limited raw
   webhook body. Only supported `workflow_run.completed` and installation/
   repository lifecycle events proceed. Reject unsupported/fork provenance
   conservatively (§5.6).
2. **Record.** In one database transaction, dedupe the GitHub delivery GUID
   and upsert `(run_id, run_attempt)` as a pending run. A repeated delivery
   must not create duplicate work or results.
3. **Enqueue.** Use `transaction.on_commit` to publish a Celery task. This
   prevents the worker seeing uncommitted rows but is **not** an atomic
   PostgreSQL-to-Redis operation. The scheduled sweep addresses recorded runs
   that were committed but not queued.
4. **Process with bounded execution.** Use a single worker, a conditional
   database claim, task time limits, late acknowledgement where appropriate
   and idempotent writes. A crashed or lost task may run again, and a broker
   may redeliver a message after its visibility timeout. A claim that fails
   (run already claimed or finished) makes the task a no-op.
   - **Claim:** a conditional update moves the run to `processing` and records
     the claim time (`claimed_at`); it commits before any network call.
   - **Conditional final write:** the update that records the outcome only
     succeeds if the run is still `processing` *with the same claim the task
     took*. If the sweep has since re-enqueued the run and a newer execution
     claimed it, the stale execution's final write matches nothing and is
     discarded. This stops a stale worker from overwriting completed job
     state; observation writes stay idempotent through unique constraints.
   - **Sweep threshold:** the sweep never re-enqueues a `processing` run
     until its claim is older than the hard time limit plus a safety margin.
   - **Deferred:** full multi-worker leases with heartbeats and separate
     claim tokens. This design is not presented as safe for arbitrary worker
     scaling.
5. **Discover supported reports.** Page through the run's artifacts, select
   `test-results-<suite>-<attempt>`, verify the attempt suffix equals the
   current run attempt and enforce one supported suite per workflow. For a
   "Re-run failed jobs" attempt, check (per M0 findings) whether the
   test-producing job ran in this attempt: if it did, its suffixed artifact
   is required; if it did not, record "no new observation" rather than a
   missing report. Skip ambiguous/mixed-attempt or matrix cases with a reason.
6. **Safely ingest.** Fetch under a strict network timeout and input-size cap.
   Bound compressed bytes, decompressed bytes, XML files, test cases, field
   lengths and processing time. Use safe XML parsing; do not extract archive
   paths to disk unchecked. Store test identifiers, outcomes and optional
   timing only; **discard failure text and stack traces**. In a transaction,
   save observations and mark an artifact complete, using unique constraints.
   Mark rejected or missing reports explicitly.
7. **Classify.** Compute or incrementally store same-revision flake events
   according to §5.4. No default-branch regression comparison.
8. **Update comment.** Only `pull_request` runs from the same repository
   update a comment. Verify the run's `head_sha` matches the PR's current head
   commit, then update only its workflow section. Serialize
   read/modify/write activity for a PR (§5.5). On uncertain association or a
   stale head, store the data but do not publish a misleading comment.
9. **Finish.** Persist `processed`, `partial`, `unsupported`, `rejected` or
   `failed` outcome as appropriate, together with a bounded diagnostic code.
   Never log or retain raw assertion output.
10. **Retry.** Transient GitHub/network errors retry with bounded exponential
    backoff and jitter. Respect primary/secondary rate-limit signals.
    Permanent permissions errors are surfaced and not blindly retried.
11. **Recover recorded work.** The scheduled sweep re-enqueues eligible stale
    pending/processing rows after respecting task timeouts. Test a worker
    termination and a commit-before-enqueue failure. This does not recover
    GitHub webhook deliveries that never reached the app; operators may use
    GitHub's manual redelivery interface where available.

### 5.3 Identity and provenance

- **Test identity:** repository ID + workflow ID + stable suite + test key
  (`file::classname::name`). Attempts are observations, not distinct tests.
  Matrix dimensions/sharding are not supported in the beta identity contract.
- **Observation:** one test in one accepted run attempt; repeated entries
  from a test runner (if emitted in JUnit) retain pass/fail/skip counts rather
  than silently overwriting one another. Skips do not establish flakiness.
- **Artifact provenance:** accept only an unambiguous
  `test-results-<suite>-<attempt>` artifact matching the run attempt. GitHub's
  artifact records do not say which attempt uploaded them, so the name suffix
  is the only provenance; do not infer it from creation time or list order.
  Reruns ("Re-run all jobs" and "Re-run failed jobs") are supported **only
  if** M0 verifies the naming/listing behavior and a reliable way to tell
  whether the test-producing job ran in a given attempt. A stale suffix (e.g.
  `-1` on attempt 2) is not the current attempt's report. Missing suffixes or
  ambiguous combinations are rejected/skipped with actionable instructions.
- **Revision provenance:** two separate questions.
  - *Which PR head does a run belong to?* For `pull_request` runs,
    `workflow_run.head_sha` is expected to be the PR's head commit (confirm
    in M0). Comparing it with the PR's current `head.sha` is the staleness
    check for comments.
  - *What code was tested?* A `pull_request` run tests a merge of the PR head
    into the base; that merge commit is not in the webhook payload. Reruns
    are expected to reuse the original run's commit, so attempts of one run
    test the same code; this is why PR runs are grouped by `run_id` (§5.4).
    M0 confirms this by printing `$GITHUB_SHA` and `git rev-parse HEAD` in
    each attempt.
  - If either answer is uncertain for a run, store the data but don't comment.
- **Time:** 30-day windows use workflow run start time, not ingestion time.
  Late reports do not count as newly run tests.

### 5.4 Flake detection

**Supported same-revision grouping** (subject to M0 validation):

| Trigger | Comparison group | Qualification |
| --- | --- | --- |
| `push`, `schedule` | `(repository, workflow, suite, head_sha)` across supported runs and rerun attempts in which the test job ran | Same revision *under the documented checkout contract*; not guaranteed identical external environment/dependencies |
| `pull_request` | `(repository, workflow, suite, run_id)` across rerun attempts in which the test job ran | Attempts of one run are expected to test the same merge commit; validate in M0 before enabling |

Only workflows that check out the expected triggering revision are supported.
Changing runtime dependencies, external services or secrets can influence
results even with an unchanged commit. A verdict describes an observed
same-revision outcome flip, not a proof that the environment was identical.

**Rules:**

- **Flake event:** the same test has at least one passing and one failing
  occurrence in one supported comparison group (possibly within one attempt
  if the report captures runner-level retries). Count a group at most once.
- **Has flaky history:** at least `FLAKY_MIN_EVENTS = 1` event in the last
  `FLAKY_WINDOW_DAYS = 30` days, with evidence linking the relevant runs.
- **History used for other verdicts:** a previous, distinct accepted run
  attempt for the test within the 30-day window, excluding the current
  attempt. This measures historical observations, not default-branch health.

**Verdicts for each failed test, in precedence order:**

1. ⚠️ **Has flaky history:** observed a supported same-revision pass/fail flip.
   Does **not** clear the current PR of a real defect.
2. ❔ **Not enough history:** no earlier eligible, non-skipped observation in
   the 30-day window and no qualifying flake event.
3. ◻️ **No flaky history observed:** at least one earlier observation exists,
   but no supported same-revision flip was recorded. This does **not** mean
   the test is stable, and **does not** mean the PR introduced a regression.

The comment must show the observation count and window when relevant.
Post-beta work may add default-branch baseline comparison and associated
new-failure/also-failing-on-main verdicts, with a separate design review.

### 5.5 PR comment

- **One comment per PR**, identified by a hidden stable marker. Save the
  GitHub comment ID; before creating, search for an existing App-authored
  marker to handle a crash after GitHub accepts a POST but before the ID is
  persisted.
- **Serialize comment updates:** the beta uses the one-concurrency worker
  and a per-PR PostgreSQL advisory lock around the comment's read/modify/
  write operation to protect accidental overlapping tasks or deployments.
  Bound API-call duration and lock wait. This is a simple correctness guard,
  not a scaling architecture. Test simultaneous creation and updates.
- **Only `pull_request` runs write comments.** Many workflows trigger on both
  `push` and `pull_request`, producing two runs per commit with the same
  workflow ID; a `push` run's payload can also list the open PR. Letting both
  write would make the same section flip between runs. `push`/`schedule`
  runs feed history only.
- **Per-workflow sections** are keyed by workflow ID; update only from an
  eligible current-head run and prevent an older eligible execution from
  replacing a newer section. An attempt that adds no observation (test job not
  re-run) leaves the section unchanged.
- **Untrusted text** (test names, suite names and report metadata) is safely
  escaped in GitHub Markdown. Neutralize mentions and unsafe links; no
  failure-output or model-generated text is rendered.

### 5.6 Trust, forks and installation lifecycle

- **Fork runs:** reject/skips if head repository differs from the base
  repository. Validate repository IDs from supported payloads. A missing
  `pull_requests` list is not proof a run is safe.
- **Lifecycle:** handle installation creation, deletion and suspension;
  repository additions/removals with basic state changes. Check installation
  and repository access before processing or commenting. Revoked access
  stops new processing; uninstall and repository removal delete relevant
  persisted data. Token cache entries are invalidated on revocation.
- **Deferred:** automatic full accessible-repository resynchronization on
  every selection change and advanced multi-replica token cache
  coordination. When event state is ambiguous, fail closed and verify access
  against GitHub rather than processing with stale assumptions.

### 5.7 GitHub API client

- Paginate all relevant list endpoints and filter artifact names client-side.
- Respect primary quota/reset information and `Retry-After` or GitHub's
  secondary-rate-limit guidance, using bounded exponential retries.
- Treat 401/403 without applicable rate-limit signals as access/permission
  failures; surface actionable status without endless retries.
- **Deferred:** distributed per-installation Redis backoff shared among many
  workers, because the beta has one logical worker.

### 5.8 Module layout

```text
config/       Django settings, urls, celery.py, wsgi
core/         health check, safe Markdown rendering helpers
webhooks/     webhook view, signature check, Delivery model, lifecycle handling
              and durable-run recovery management command
githubapp/   App JWT, installation token handling, REST client (pagination,
              rate-limit responses)
ingest/       Run, Artifact, Test, TestResult models, tasks, artifact limits,
              safe junit parsing, transactional storage
detection/    FlakeEvent model, grouping and verdict classification
comments/     PrComment model, render, serialize/upsert sticky comments
infra/        Terraform + ECS task/service definitions
```

This is a **proposed logical layout**, not a claim the scaffold already
contains these modules. Keep parsing, classification and rendering logic
separable from network calls for deterministic unit testing.

## 6. Alternatives considered and current decisions

| Decision | Proposed v1 approach | Alternative / deferred approach | Reason |
| --- | --- | --- | --- |
| **D1. Integration model** | GitHub App + `workflow_run` webhook | GitHub Action POST; bulk polling | Native installation model and artifact access; M0 validates event semantics. |
| **D2. Result source** | JUnit XML in attempt-suffixed artifacts, one suite/job | Log scraping; matrix and multi-job association | Structured and testable; conservative provenance. |
| **D3. Background jobs** | Celery + Redis, PostgreSQL source of truth, on-commit enqueue + scheduled sweep, one worker, conditional claim and conditional final write | SQS, Postgres queue, full leases with heartbeats/claim tokens for multiple workers; no final-write guard | Demonstrates async jobs/idempotency; the final-write guard stops a stale execution overwriting completed state at the cost of one condition. |
| **D4. Flake definition** | Same-revision observation flip with caveat about environment | Failure-rate model, dependency/environment fingerprinting | Transparent evidence, appropriately conservative claims. |
| **D5. Output** | One sticky comment with per-workflow sections and per-PR serialization; written only by `pull_request` runs | Check runs, per-run comments, separate dashboard; comments from `push` runs | GitHub-native visibility and tractable concurrency correctness; one writer per section avoids push/PR flip-flops. |
| **D6. Framework** | Django + PostgreSQL | FastAPI + SQLAlchemy, Go | Django admin and Python depth complement existing projects. |
| **D7. Cloud topology** | ECS Fargate web + one worker (stop-before-start deploys), EventBridge Scheduler launching a short-lived recovery task, RDS, Redis, ALB | Always-on Beat service; scheduler embedded in the worker (`celery worker -B`); autoscaling, multi-AZ, EKS | Real AWS/platform work, including managed scheduling, without beta-scale overhead. Embedded Beat would be less Terraform but teaches less. |
| **D8. AI** | **No AI in beta**; possible user-requested follow-up | Async LLM explanation, prompt cache, redaction | Low relevance to core detection and disproportionate scope. |
| **D9. IaC** | Terraform + OIDC CI/CD | Console provisioning, CDK | Reproducible cloud deployment and useful infra learning. |
| **D10. Tooling** | uv, Ruff, mypy, pytest-django | pip, flake8/black, untyped code | Automated repeatable quality checks. |
| **D11. Missed webhooks** | No automatic redelivery; manual recovery when feasible; honest limitation | GitHub deliveries API polling/redelivery and reconciliation | Focus on durable recovery of recorded jobs first. |
| **D12. Forks** | Exclude fork-origin runs | Trust-isolated fork histories | Avoid mixing untrusted inputs into trusted evidence. |
| **D13. Verdicts** | Flaky / insufficient history / no flakiness observed | Default-branch baseline and "new failure" | Do not claim regression status without baseline analysis. |
| **D14. Beta integrations** | pytest example, single non-matrix suite per workflow, "Re-run all jobs" and "Re-run failed jobs" when the test job re-runs | Jest, Go, full reruns only, matrix/shards | "Re-run failed jobs" is how developers usually retry a flaky failure; supporting it keeps the tool useful without widening the job shape. |
| **D15. Failure data** | Store test identity/outcome/time and provenance only | Raw failure text, advanced redaction, AI | Lower privacy risk, simpler trustworthy product. |
| **D16. Observability** | CloudWatch logs, health checks, essential alarms/metrics | Sentry, distributed tracing, elaborate dashboards | Enough evidence to operate and debug the beta. |

These decisions remain **proposed** until approved; copy significant accepted
tradeoffs into the future `DECISIONS.md` without duplicating the whole RFC.

## 7. Security and privacy

- **Webhooks:** HMAC-SHA256 verification over the original raw body,
  constant-time comparison, request size cap.
- **Permissions:** minimize GitHub App permissions (Actions read; relevant PR
  comment write and metadata permissions), verify exact requirements in M0.
  Do not request repository contents permission unless justified.
- **Untrusted artifacts:** never trust compressed archives or XML. Enforce
  compressed/decompressed total-byte caps, file/count/field limits, timeouts,
  safe ZIP member handling, and entity-expansion-safe XML parsing. Full
  streaming optimization is deferred, **not** essential input safety.
- **Data minimization:** retain test identities, outcomes, durations (where
  present), workflow/commit provenance and generated verdict evidence. Do not
  store or log failure messages, tracebacks, source snippets, raw reports or
  LLM data. Delete temporary archives promptly; test names themselves may
  contain unexpected content, so escape and limit them.
- **Lifecycle and isolation:** installation/repository IDs are part of
  authorization and data boundaries. Reject events after access revocation;
  delete repository data on removal and installation data on uninstall.
- **Secrets:** AWS Secrets Manager for deployment, untracked `.env` locally;
  use GitHub OIDC for deployment rather than permanent AWS keys.
- **Django hardening:** HTTPS, appropriate secure cookies and HSTS, and
  `check --deploy` validation in CI. Protect the internal admin.

## 8. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Maintainers do not complete the upload step | Med | High | One verified pytest snippet; actionable setup/missing-artifact errors; onboarding trial |
| R2 | Flaky classifications are misleading | Med | High | Explicit comparison groups, clear non-regression wording, deterministic fixtures and manual evidence audit |
| R3 | Rerun artifact provenance (full or failed-jobs), test-job detection per attempt, or PR merge-commit assumptions fail | Med | High | M0 live spikes; fail closed; if failed-jobs reruns can't be attributed reliably, fall back to full reruns only before M2 |
| R4 | GitHub delivery never reaches service | Med | Med | Explicit beta limitation; manual delivery redelivery or later reconciliation; no completeness guarantee |
| R5 | Dangerous archive, oversized report or sensitive identifiers | Med | High | Safe parser, bounded inputs, no failure text retention, escaping and tenant isolation |
| R6 | Worker crashes, is stopped mid-job by a deploy, or enqueue publishing fails | High | High | Durable run rows, idempotent writes, time limits, conditional final write, scheduled stale-run sweep and fault injection (run in a Linux container; Celery time limits are not enforced under `--pool=solo`) |
| R7 | School, job applications and new tooling extend timeline | High | High | Hour ranges, scope freeze, resume-ready local checkpoint, re-estimate each milestone |
| R8 | AWS fixed costs exceed beta budget | Med | High | Up-front cost estimate/cap, AWS Budget alerts, minimum viable service sizes, teardown plan |
| R9 | Unintended second worker (e.g. overlapping deploy) causes duplicate comment updates or stale state writes | Med | High | Stop-before-start worker deploys, conditional final write, PR-scoped serialization, duplicate/stale tests, explicit scaling boundary |
| R10 | Uninstalled or removed repo continues to be processed | Med | High | Permission checks, lifecycle deactivation, data deletion, bounded token lifetime |
| R11 | API rates/permissions stop processing | Med | Med | Pagination, GitHub response-aware backoff and actionable errors |
| R12 | Observability too weak to debug service failures | Med | Med | Structured logs, health checks, queue/backlog and failure visibility in CloudWatch |

**Scope guard:** Do not sacrifice webhook verification, safe parsing,
installation access checks, database idempotency, comment correctness or
recorded-work crash recovery to meet an arbitrary deadline. If behind, limit
compatible workflow shapes first and move optional polish/user-requested
features later.

## 9. Success metrics and release criteria

**Engineering gates (controlled by the project):**

- **Local end-to-end demo:** a real supported GitHub Actions workflow emits a
  report; the app stores results, reproduces an intentional flake through full
  reruns, and updates exactly one PR comment with evidence.
- **Correctness:** automated fixtures cover flake, no-flake, insufficient
  history, unsupported attempts, duplicate deliveries, stale PR heads,
  comment races and rejected artifacts. Unsupported results never appear as
  passes or regressions.
- **Recovery:** a duplicate task creates no duplicate database effects; a
  terminated worker or lost enqueue is recovered for **already-recorded** runs;
  a stale execution finishing after a newer one cannot overwrite the recorded
  outcome. Do not claim general exactly-once task execution.
- **Cloud/operations:** AWS deploy is provisioned through Terraform, CI/CD
  authenticates via OIDC, health/logging/at least one useful alert is verified,
  and the application can be redeployed from documented steps.
- **Observability:** record processing duration, errors/retries, backlog age,
  and webhook-to-comment latency. Set numerical service objectives after
  observing real load, not before.

**Adoption outcomes (not release gates):**

- One independent maintainer attempts onboarding and gives concrete feedback.
- Aim for ~3 external active repositories, 1–2 developer-requested fixes or
  enhancements, and eventually a longer-running load sample. These are targets,
  not prerequisites for calling the technical beta complete.
- A 500+ run reconciliation study and a formal precision target are **future
  validation work**, not automatic claims of reliability or accuracy.

## 10. Plan and resume-ready checkpoints

Planning capacity: approximately **10 focused hours/week** (may vary with
finals, other projects and recruiting). Estimates below include code review,
manual verification and learning time; ranges are not guarantees. Dates are
provisional and must be reset when a spike or deployment blocker invalidates
an assumption. **M0 is already underway; do not restart it.**

| Milestone | Effort | Target window | Scope | Exit criteria |
| --- | --- | --- | --- | --- |
| **M0. Setup & spikes** | ~8–9 h total | Oct 22 – Nov 8 | Existing scaffold and RFC; register App; webhook tunnel to the local machine; capture real payloads; test full and failed-jobs rerun artifacts and PR commit association; reconcile `DESIGN.md` with rev. 5 (~2–3 h, §16) | Findings and sanitized fixtures recorded; unsupported assumptions called out; `DESIGN.md` matches the approved RFC |
| **M1. Durable webhook intake** | 8–12 h | Nov 9 – 15 | Installation state, signature check, delivery/run models and migrations, webhook dedupe, basic fork and revocation checks | Repeated delivery creates one recorded attempt; lifecycle/security tests pass |
| **M2. Ingestion and background jobs** | 18–25 h | Nov 16 – Dec 6 | GitHub auth/API pagination, Celery/Redis, artifact provenance, bounded safe JUnit ingest, idempotent storage, basic retries | Real playground history saved; repeated processing creates no duplicate effects; unsupported reports explicit |
| **M3. Classification + PR comments** | 14–20 h | Dec 7 – 20 | Same-revision flake events, 3 verdicts, comment upsert/serialization, current-head safety and automated fixtures | **Local end-to-end resume-ready demo** with a real PR and intentional rerun flake |
| **M4. First AWS deployment** | 20–30 h | Dec 21 – Jan 10 | Terraform ECR/ECS web+worker (stop-before-start worker deploys), RDS, broker, ALB/HTTPS, IAM/Secrets, OIDC CI/CD, CloudWatch, EventBridge Scheduler recovery task, cost controls | **Cloud-deployed resume-ready service**; can redeploy, trace a run, kill/recover a worker |
| **M5. Hardening → core beta** | 10–16 h | Jan 11 – 24 | E2E failure tests, security checks, setup docs, external onboarding trial, fixes from first feedback if available | Technical beta released; installation demonstrated; external adoption not required to pass |
| **M6. Post-beta iteration** | Not precommitted | After core beta | Additional workflows, better main-branch comparisons, scaling, observability and user-driven changes; AI only if justified | Prioritized by observed pain; own RFC/ADRs as warranted |

**Resume reporting:** After M3, describe only completed, reproducible local
end-to-end functionality. After M4, explicitly claim AWS/Terraform operations
*only if* actually deployed and tested. Installation counts, uptime, accuracy
and throughput appear on the resume only when measured. Use the actual status
(`in development` or `deployed`) rather than projected features.

**M5 acceptance scenarios:** deterministic pass/fail flip on a full rerun and
on a "Re-run failed jobs" attempt; a failed-jobs rerun in which the test job
did not re-run (no new observation, section unchanged); insufficient history;
no observed flake; malformed/oversized report; bad signature; duplicate
delivery/task; crashed worker and recovered recorded job; worker stopped
mid-job by a deploy; stale execution finishing after a newer one (final write
discarded); commit-before-enqueue recovery; stale PR head; a `push` run for a
PR's commit (no comment change); simulated concurrent comment creation/update;
repository removal or installation suspension; missing report, and proper
handling of unsupported matrix inputs.

**Checkpoint after M2 (around Dec 6):** if real reports cannot be stored
end-to-end, stop expanding the architecture and re-plan M3–M5 based on actual
blockers. Do not remove correctness or security just to meet a calendar date.

**Ordering change:** Unlike rev. 4's deploy-before-detection plan, rev. 5
builds one complete local GitHub→database→verdict→comment path *before* the AWS
deploy. This makes the feature demonstrable sooner and prevents the cloud
milestone from blocking a working resume project. Deployment and operating
experience remain mandatory for the planned cloud checkpoint.

Work on feature branches, review through PRs, keep `main` protected and CI-
gated. Each milestone must include an acceptance demonstration and a short
architecture review.

## 11. Open questions / decisions to validate

1. GitHub App final name and exact Marketplace/install permission requirements.
2. In M0, confirm that `workflow_run.head_sha` is the PR head for
   `pull_request` runs, that rerun attempts test the same merge commit, and
   how to tell whether the test-producing job ran in a failed-jobs rerun.
3. Confirm artifact upload action version and JUnit XML behavior with pytest
   (especially skipped/failed test commands).
4. Before M4, select and price Redis broker topology (managed service versus
   a carefully configured container), and set a monthly AWS spending cap.
5. Confirm the EventBridge schedule interval, sweep safety margin, ECS stop
   timeout and Celery soft/hard/visibility timeouts through crash tests,
   rather than assigning them arbitrary values here. The sweep threshold must
   exceed the hard time limit.
6. Revisit 30-day retention, `FLAKY_MIN_EVENTS`, and verdict wording after
   testing with real histories. Default 90-day retention is a tentative cap.
7. Reconcile `DESIGN.md` to the decisions in §16 during M0, before
   implementation of affected modules.
8. Choose a webhook tunnel for local development and confirm it forwards the
   raw request body unchanged (signature verification depends on it).

## 12. Questions for reviewers

- Are supported reruns (full and failed-jobs) and PR merge-commit association
  demonstrably safe? What should be skipped if the event payload is ambiguous?
- Is the one-worker/DB-sweep recovery model with a conditional final write
  sufficient for the beta, including stop-before-start worker deploys and
  tasks killed at the hard time limit?
- Is PR-scoped serialization plus marker lookup sufficient to prevent
  duplicate/lost comments after retries or brief overlapping deployments?
- Can the v1 XML/file limits be enforced without full streaming, including
  decompression bombs and malicious archive paths?
- Is the AWS bill tolerable for an always-available low-traffic product?
- Are M2–M4 time ranges credible *including* manual learning and debugging?
- Does the three-verdict system convey the evidence without implying
  unproven safety or regression causality?

## 13. Spike findings (M0 — unverified)

- **Full rerun provenance:** when a workflow is rerun, which artifacts from
  previous attempts appear in artifact listings? Can attempt-suffixed names
  be reused safely? **_TBD_**
- **"Re-run failed jobs" behavior:** in the playground, add a second job
  (e.g. lint) and test two cases: (a) the test job failed and is re-run;
  (b) only the other job failed, so the test job is not re-run. For each,
  record the artifact list, the attempt's jobs (`/attempts/{n}/jobs` and each
  job's `run_attempt`) and the webhook payload. Specify how to tell whether
  the test job ran in the attempt; if no reliable signal exists, fall back to
  full reruns only (R3). **_TBD_**
- **PR versus push revision identity:** capture `workflow_run.head_sha`,
  the PR's head/base SHAs, repository IDs, `pull_requests` (including on a
  `push` run for a branch with an open PR), and print `$GITHUB_SHA` and
  `git rev-parse HEAD` in each attempt of a re-run PR workflow. **_TBD_**
- **Webhook validation:** capture sanitized genuine deliveries through the
  local tunnel, verify signature processing and manual redelivery/dedupe
  behavior. Automatic
  discovery/redelivery is *not* in beta. **_TBD_**
- **Artifact parser feasibility:** capture a small real pytest JUnit report
  and confirm test names/outcomes and artifact upload action behavior.
  **_TBD_**

## 14. Revision history

- **2026-10-20:** Initial draft.
- **2026-10-21:** Rev. 2. Switched to Django + Celery/Redis; added Terraform
  and tooling decisions; named the AWS services.
- **2026-10-21:** Rev. 3. Addressed review round 1 (§15); scope set to core
  beta first, then AI; hour-based plan; deploy before detection.
- **2026-10-21:** Rev. 4. Re-estimated the plan at ~80 h total; core beta
  moved to ~Jan 10.
- **2026-10-29:** Proposed rev. 5. Reduced beta scope (F1–F6, T1–T7),
  preserved backend/cloud core, revised guarantees, staged resume-ready
  checkpoints, and flagged `DESIGN.md` reconciliation. Not yet approved.
- **2026-10-30:** Rev. 5 draft updated after review: conditional final write
  against stale executions; stop-before-start worker deploys; EventBridge
  Scheduler recovery task confirmed; "Re-run failed jobs" supported when the
  test job re-runs; `pull_request`-only comment rule; PR revision provenance
  clarified; webhook tunnel and `DESIGN.md` reconciliation added to M0.
  Still a draft pending review.

## 15. Review responses

Historical note: the table below documents resolutions accepted during the
rev. 1–4 review cycle. **It is not a statement that all those mechanisms remain
in v1.** Rev. 5 intentionally supersedes some resolutions: automated missed-
webhook recovery (#4), advanced claim fencing (#9), streaming requirements
(#11), stored failure output / AI (#6, #10, #12), cross-installation rate-limit
coordination (#15), and the prior schedule (#16). See §16 for the definitive
rev. 5 change list. Keep this table as audit history rather than rewriting the
prior reviewers' findings.

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

## 16. Rev. 5 scope changes and `DESIGN.md` reconciliation checklist

The following is a **proposed revision**, not an approved architecture change.
Historic review outcomes in §15 are preserved for traceability; this section
controls how the v1 contract differs from rev. 4.

| ID | Rev. 5 action | Required corresponding changes in `DESIGN.md` / implementation planning |
| --- | --- | --- |
| **F1** | Defer AI entirely | Remove AI route/tasks, explanation schema/cache, LLM cost targets, privacy/escape rules for generated content and M6 AI commitment |
| **F2** | Defer default-branch comparisons | Remove old 4-verdict precedence, main baseline queries, `new failure` claims; specify 3 conservative verdicts and historical-count evidence |
| **F3** | Narrow supported workflows | Single non-matrix test job/suite; full and failed-jobs reruns via suffix when the test job re-runs; "no new observation" outcome when it doesn't; reject ambiguous reruns/shards; keep per-workflow sections; comments from `pull_request` runs only |
| **F4** | Defer automatic missed-webhook recovery | Remove GitHub delivery cursor/redelivery scheduler; retain DB stale-run sweep and manual redelivery when possible; narrow G2 claims |
| **F5** | Do not retain failure output | Remove failure text fields, redaction pipeline and failure-output rendering; retain safe XML processing and test-identifier escaping |
| **F6** | pytest onboarding first | Remove pre-beta Jest/Go setup acceptance; one verified pytest snippet |
| **T1** | Keep AWS + Terraform, simplify topology | Web + one worker ECS service (min healthy 0% / max 100%) plus an EventBridge Scheduler schedule launching a short-lived recovery task; remove always-on Beat service/autoscaling/HA assumptions; add budget estimate |
| **T2** | Keep queues, retries, idempotency, crash recovery | One worker concurrency 1; conditional claim recording `claimed_at`; conditional final write on the same claim; sweep threshold above the hard time limit; defer heartbeat leases and separate claim tokens; prove replay correctness |
| **T3** | Simplify artifact processing | Bound download/decompression/memory/CPU and safe XML; full streaming implementation is optional; do not relax untrusted-input protections |
| **T4** | Defer shared distributed throttling | Keep pagination, primary/secondary API-limit handling and retries; remove global Redis backoff coordinator |
| **T5** | Simplify installation lifecycle | Keep installation state, revocation, suspensions and data deletion; defer eager full resync/token coordination; fail closed |
| **T6** | Keep comment correctness, simplify concurrency | Single comment-write path with per-PR serialization, marker recovery, stale-head checks and race tests |
| **T7** | Simplify observability | Keep structured CloudWatch logs, health checks, essential alerts/latency/backlog; defer Sentry, tracing, extensive dashboards |
| **Schedule** | Local E2E before cloud | Replace rev. 4 M3/M4 ordering and total ~80-hour certainty with ranges and two truthful resume checkpoints |

**Before coding against rev. 5:** review any schema, endpoint, acceptance
criteria, configuration, Terraform resource or comment template in `DESIGN.md`
that contradicts the table. Record substantive approved decisions in
`DECISIONS.md` after acceptance. `ARCHITECTURE.md` should describe only the
implementation that actually exists, not this proposed target state.
