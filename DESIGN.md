# Flaky Test Tracker — v1 Design

*Working name. Author: Tenzin Jamyang. Implementation spec for
[RFC 0001](docs/rfcs/0001-flaky-tracker-v1.md) rev. 3.*

> The RFC holds the decisions, rules, risks and plan. This document holds the
> exact limits, API calls, schema and algorithms needed to build it. Where the
> two disagree, the RFC wins, and this document gets fixed.

A GitHub App that records every CI test result, detects which tests are flaky,
and comments on pull requests so developers know whether a failure is likely
theirs.

---

## 1. Problem

A **flaky test** passes and fails on the same code with no change. Flaky tests
make developers rerun CI over and over, teach teams to ignore red builds, and are
hard to spot by hand because the evidence is spread across hundreds of runs.
Detecting them needs **history across runs**, which is what this App keeps.

## 2. What v1 does

1. A maintainer installs the App and adds one artifact-upload step to CI (§5.4).
2. Every completed workflow run's JUnit reports are downloaded and stored.
3. Tests that pass and fail on **identical code** get flake events (§7).
4. Each PR gets one comment, with one section per workflow, that classifies
   every failure:

```markdown
<!-- flaky-tracker:v1 -->
### 🧪 `CI` · commit `a1b2c3d` — 4 of 214 tests failed

| Test | Verdict | Evidence |
| --- | --- | --- |
| `tests/test_auth.py::test_login_expired_token` | ⚠️ Has flaky history | Flipped on identical code 4× in 30 days ([latest](…)). Can still be a real failure. |
| `tests/test_api.py::test_rate_limit` | 🔴 Also failing on `main` | Latest `main` result failed ([`9f8e7d6`](…)) |
| `tests/test_cards.py::test_import_variant` | 🆕 New failure | Passed in the latest `main` run ([`4c1d2e0`](…)) |
| `tests/test_sync.py::test_backfill` | ❔ Not enough history | No `main` result in the last 30 days |

### ✅ `Lint` · commit `a1b2c3d` — all 12 reported tests passed
```

5. *After the core beta:* new failures on public repos get an AI explanation
   added to the comment (§8).

## 3. Scope

**In v1:** GitHub Actions; any runner that emits JUnit XML (pytest, Jest via
`jest-junit`, Gradle/Maven, Go via `go-junit-report`); flake detection; PR
comments; deployment on AWS; AI enrichment for public repos after the core beta.

**Not in v1:** other CI providers; quarantine or auto-retry; **fork-triggered
runs** (ignored entirely); AI for private repos; the dashboard; notifications
outside GitHub.

---

## 4. Architecture

```
GitHub ──webhooks──► web (Django + gunicorn)
                       │ 1. verify signature   2. record delivery + run (1 txn)
                       │ 3. on_commit → enqueue   4. return 202
                       ▼
                     Redis ──► worker (Celery) ──► GitHub REST API
                                   │        └────► LLM API (post-beta, public repos)
                                   ▼
                              PostgreSQL ◄── admin (/admin/)
                                   ▲
       beat (Celery) ──────────────┘  sweep (5 min) · webhook recovery (10 min)
```

**One image, three processes:** `web` (`gunicorn config.wsgi`), `worker`
(`celery -A config worker`) and `beat` (`celery -A config beat`).

**Why the handler does almost nothing:** GitHub expects a response within ~10 s.
Downloading artifacts takes longer, so the handler records the event and
returns; the worker does the slow work.

**Closing the Postgres–Redis gap (a lightweight transactional outbox):**

1. The task is enqueued with `transaction.on_commit`, only after the run row is
   committed.
2. The **run row is the source of truth.** If enqueueing never happens (a crash,
   or Redis is down), the run stays `pending`, and the beat sweep re-enqueues it.
   Processing is idempotent, so a duplicate message is harmless.
3. Webhooks that never reached us at all are recovered by the delivery-recovery
   task (§5.7).

---

## 5. GitHub integration

### 5.1 App registration

- **Permissions:** Actions **read**, Pull requests **read & write** (this also
  allows comments on PRs through the issues API), Metadata **read**.
- **Events:** `Workflow run`. Apps receive `installation` and
  `installation_repositories` events automatically.
- **Secrets:** webhook secret and private key (`.pem`), never committed.

### 5.2 Receiving webhooks

1. Reject bodies over **5 MB**. Read the **raw body** before parsing JSON.
2. HMAC-SHA256 over the raw body with the webhook secret; compare it to
   `X-Hub-Signature-256` with `hmac.compare_digest`. Mismatch → 401.
3. Route by `X-GitHub-Event`:
   - `workflow_run` with `action == "completed"`: continue.
   - `installation`, `installation_repositories`: lifecycle handling (§5.8).
   - Anything else: 204.
4. **Fork check:** `workflow_run.head_repository.id != repository.id` means a
   fork run. Record the delivery, create no run, return 202.
5. In one transaction: insert `Delivery(guid=X-GitHub-Delivery)` (a unique
   violation means a duplicate, so return 200) and upsert
   `WorkflowRun(run_id, run_attempt)` with `status=pending`.
6. `transaction.on_commit(lambda: process_run.delay(run.pk))`, then return 202.

All conclusions are accepted (`success`, `failure`, `cancelled`, `timed_out`,
…). The run's artifacts decide whether there's anything to ingest.

**Local development:** forward webhooks with smee.io. The App settings page lists
recent deliveries and can **redeliver** any of them.

### 5.3 Authenticating as the App

1. **App JWT:** RS256, `iat` = now − 60 s, `exp` ≤ now + 10 min, `iss` = client
   ID, signed with the private key. Used only for `/app/...` endpoints
   (installation tokens, webhook deliveries).
2. **Installation token:**
   `POST /app/installations/{installation_id}/access_tokens` → token valid about
   1 h. Cache it in Redis per installation until 5 min before expiry. Delete the
   cache entry on suspend or uninstall.

### 5.4 Test reports

**Naming convention:** `test-results-<suite>-<attempt>`, where `<attempt>` is
`${{ github.run_attempt }}` and `<suite>` names the job plus every matrix value.

```yaml
- run: pytest --junitxml=reports/junit.xml
- uses: actions/upload-artifact@v7
  if: always()
  with:
    name: test-results-unit-${{ matrix.os }}-py${{ matrix.python }}-${{ github.run_attempt }}
    path: reports/
```

**Name parsing:**

| Artifact name | Suite | Attempt | Accepted? |
| --- | --- | --- | --- |
| `test-results-unit-3` | `unit` | 3 | if this run attempt is 3 |
| `test-results-unit-ubuntu-py3.13-1` | `unit-ubuntu-py3.13` | 1 | if this run attempt is 1 |
| `test-results-2` | `default` | 2 | if this run attempt is 2 |
| `test-results-unit` (no numeric suffix) | `unit` | unknown | only when this run attempt is 1 |
| `coverage-report` | n/a | n/a | ignored |

The rule: strip the `test-results` prefix; if the last `-`-separated segment is
all digits, it's the attempt; the rest (or `default`) is the suite.

**Discovery:** `GET /repos/{o}/{r}/actions/runs/{run_id}/artifacts?per_page=100`,
following `Link` headers through every page. Filter by prefix in Python (the
`name` query parameter is exact-match). Skip `expired` artifacts and those
already in the `Artifact` table with `status=complete`.

**Limits (per run unless noted):**

| Limit | Value | On breach |
| --- | --- | --- |
| Artifacts considered | 20 | extra ones rejected (`too_many_artifacts`) |
| Compressed size per artifact | 50 MB (checked from `size_in_bytes` and while streaming) | artifact rejected |
| Total downloaded | 100 MB | remaining artifacts rejected |
| XML files per run | 500; non-`.xml` entries ignored | extra files skipped |
| Decompressed size per XML file | 25 MB | file rejected |
| Total decompressed | 200 MB | remaining files rejected |
| Test cases | 50,000 | parsing stops; artifact `partial` |
| Element nesting depth | 64 | file rejected |
| Name / classname / file length | 1,024 chars | truncated for display; identity uses a hash of the full value |
| Failure text stored | 4 KB after redaction | truncated |
| Processing time | Celery soft limit 240 s, hard limit 300 s | run `partial` or retried |

**Download and unzip:** the artifact endpoint returns a **302 redirect** to a
short-lived URL; httpx needs `follow_redirects=True`. Stream to a
`tempfile.SpooledTemporaryFile` while counting bytes. Read each zip entry through
`ZipFile.open()` in chunks, counting decompressed bytes. Never trust the size
the zip header claims.

**Parsing:** `defusedxml.ElementTree.iterparse` with `start`/`end` events;
track depth; on each `</testcase>`, extract and then `elem.clear()` to keep
memory flat. Outcome: a `<failure>` or `<error>` child → failed; `<skipped>` →
skipped; otherwise passed. Handle both `<testsuites>` and bare `<testsuite>`
roots and nested suites. Fixtures are in `tests/fixtures/junit/`.

**Aggregation:** the same test key appearing several times in one attempt
(runner-level retries) is stored as **counts** (`passed_count`, `failed_count`,
`skipped_count`) on one `TestResult`. A test with both passes and failures in one
attempt is itself a flake signal (§7).

**Atomicity:** each artifact's `TestResult` rows **and** its `status=complete`
are written in one transaction, so a crash can't leave half an artifact
ingested.

### 5.5 PR comments

- Only for runs with `event == "pull_request"` from the same repository, linked
  through `workflow_run.pull_requests[]`.
- **Current head check:** `GET /repos/{o}/{r}/pulls/{n}`. If `head.sha` differs
  from the run's `head_sha`, store the results but don't touch the comment.
- **Section ordering:** each section (per workflow) records `head_sha`,
  `run_id`, `run_attempt` and `run_started_at`. Replace it only if the incoming
  run is for the current head **and** (the section shows an older head, **or**
  the incoming `(run_started_at, run_attempt)` is newer).
- **Concurrency:** hold a Postgres advisory lock on `(repo, pr)` while reading
  sections, rendering and PATCHing, so two workers can't overwrite each other.
- **Create vs update:**
  1. If `PrComment.comment_id` is stored → `PATCH /repos/{o}/{r}/issues/comments/{id}`.
     On 404 (deleted), forget it and continue.
  2. Otherwise, list the PR's comments (all pages) and look for one whose
     author is the App's bot user and whose body contains the marker. If found,
     store its ID and PATCH.
  3. Otherwise `POST /repos/{o}/{r}/issues/{n}/comments` and store the ID in
     the same transaction that records the section.
- **States per section:** failures table; "✅ all N reported tests passed";
  "⚠️ no test report found (setup link)"; "⚠️ report partially processed:
  <reason>".
- **Escaping:** report text and model output go inside code spans or blocks
  with backticks escaped; `@` becomes `@​` so nobody gets pinged; no raw
  HTML or auto-linked URLs from report content.

### 5.6 API client behavior

- **Pagination:** always `per_page=100`; follow `Link: rel="next"` until it's
  gone.
- **Primary limit:** if `x-ratelimit-remaining == 0`, wait until
  `x-ratelimit-reset`.
- **Secondary limit:** 403/429 with `retry-after` → wait that long; with no
  header → wait ≥ 60 s, doubling on each retry.
- **Shared backoff:** store "backoff until" per installation in Redis; every
  task checks it before calling, so concurrent tasks don't pile on.
- **Permission errors:** 401, or 403 with no rate-limit signal → no retry; mark
  the run `failed` with `last_error`, visible in the admin.
- **Transient errors:** network errors and 5xx → Celery retry with exponential
  backoff and jitter, at most 5 times.

### 5.7 Webhook delivery recovery

GitHub **doesn't automatically retry** failed deliveries, and only allows
redelivering deliveries from the **past 3 days**.

Every 10 minutes (beat → `recover_failed_deliveries`):

1. Using the **App JWT**, page through `GET /app/hook/deliveries?per_page=100`
   (cursor pagination), from newest to oldest, until reaching the stored
   high-water mark or deliveries older than 3 days.
2. Group by `guid`. A GUID needs recovery if none of its attempts succeeded
   (2xx/3xx), **and** it isn't in our `Delivery` table, **and** we've
   redelivered it fewer than 3 times.
3. `POST /app/hook/deliveries/{id}/attempts` for each; record the attempt in
   `DeliveryRedelivery`.
4. Advance the high-water mark (newest delivery ID seen) in
   `DeliveryRecoveryState`.
5. Alert (CloudWatch metric + log) when any GUID exhausts its 3 attempts or the
   oldest unrecovered failure is more than 2 days old.

A redelivery arrives with the same GUID, so §5.2 step 5 dedupes it if the
original was in fact recorded.

### 5.8 Installation lifecycle

| Event | Action |
| --- | --- |
| `installation.created` | create the installation; sync its repositories |
| `installation.deleted` | delete the installation (cascades to all data); drop its cached token |
| `installation.suspend` / `unsuspend` | set or clear `suspended_at`; drop its cached token |
| `installation.new_permissions_accepted` | sync repositories |
| `installation_repositories.added` / `removed` | **sync repositories** (don't trust the diff; it can be empty when the selection mode changes) |

**Sync:** page through `GET /installation/repositories` with the installation
token. Upsert the repos returned; repos no longer returned are marked inactive
and their data deleted. A `process_run` for an inactive repo or a suspended
installation exits at the claim step without writing anything.

---

## 6. Data model

Django models; migrations are generated from them. Every model is registered in
the admin. `→` means a foreign key (`on_delete=CASCADE` unless noted).

```
Installation
  id (GitHub installation id, PK), account_login, suspended_at (null), created_at

Repository
  id (GitHub repo id, PK), installation →, full_name, default_branch,
  is_private, active, created_at

Delivery
  guid (PK), event, action, installation_id (null), received_at

DeliveryRecoveryState            -- single row
  high_water_delivery_id, updated_at

DeliveryRedelivery
  guid, delivery_id, attempted_at, attempt_number
  UNIQUE (guid, attempt_number)

WorkflowRun
  id (PK), repo →, run_id, run_attempt, workflow_id, workflow_path,
  event, head_sha, head_branch, pr_number (null), run_started_at, conclusion,
  status (pending|processing|processed|partial|no_reports|failed),
  claim_token (null), claimed_at (null), attempts, last_error, created_at
  UNIQUE (run_id, run_attempt)
  INDEX (status, created_at)                     -- sweep

Artifact
  id (GitHub artifact id, PK), run →, name, suite, attempt (null),
  size_bytes, status (complete|rejected|partial), reject_reason, processed_at

Test
  id (PK), repo →, workflow_id, suite, key_hash (sha256 of test_key),
  test_key (display, truncated), file, classname, name, first_seen_at
  UNIQUE (repo, workflow_id, suite, key_hash)

TestResult
  id (PK), test →, run →, artifact →, outcome (passed|failed|skipped),
  passed_count, failed_count, skipped_count, duration_ms,
  failure_text (≤ 4 KB, redacted), occurred_at (= run.run_started_at),
  is_default_branch, group_key
  UNIQUE (test, run)
  INDEX (test, is_default_branch, occurred_at DESC)   -- latest main result
  INDEX (test, group_key)                             -- flake detection

FlakeEvent
  id (PK), test →, group_key, occurred_at
  UNIQUE (test, group_key)
  INDEX (test, occurred_at DESC)

PrComment
  id (PK), repo →, pr_number, comment_id (null), updated_at
  UNIQUE (repo, pr_number)

PrCommentSection
  id (PK), comment →, workflow_id, head_sha, run →, run_started_at,
  run_attempt, body_markdown
  UNIQUE (comment, workflow_id)

AiExplanation                     -- post-beta
  id (PK), repo →, signature, prompt_version, model, text,
  input_tokens, output_tokens, created_at, expires_at
  UNIQUE (repo, signature, prompt_version, model)
```

**Outcome rule:** `outcome = failed` if `failed_count > 0`, else `passed` if
`passed_count > 0`, else `skipped`.

**Retention:** a nightly beat task deletes `TestResult` rows with `occurred_at`
older than 90 days, plus expired `AiExplanation` rows. Every statistic shown to
users states its window and sample count.

---

## 7. Flaky detection

**Group key** (computed at ingest and stored on `TestResult`):

| `event` | `group_key` |
| --- | --- |
| `push`, `schedule` | `sha:<workflow_id>:<head_sha>` |
| everything else (`pull_request`, `pull_request_target`, `merge_group`, `workflow_dispatch`, …) | `run:<run_id>` |

**Recording flake events:** after an artifact is stored, under
`pg_advisory_xact_lock(hash(repo_id))`, for each test touched:

```sql
-- does this test now have both outcomes within this group?
SELECT SUM(passed_count) > 0 AND SUM(failed_count) > 0, MAX(occurred_at)
FROM test_result
WHERE test_id = %s AND group_key = %s;
-- if yes: INSERT INTO flake_event (test_id, group_key, occurred_at) ... ON CONFLICT DO NOTHING
```

The lock matters: without it, two workers storing the passing and the failing
result for the same group at the same moment could each see only their own row,
and both would miss the flake.

**Verdicts** (failed tests in a PR run on the current head), in order:

1. **Has flaky history:** `count(FlakeEvent where occurred_at ≥ now − FLAKY_WINDOW_DAYS) ≥ FLAKY_MIN_EVENTS`.
2. **Not enough history:** no `TestResult` with `is_default_branch` and
   `outcome != skipped` within the window.
3. **Also failing on main:** the latest such result is `failed`.
4. **New failure:** the latest such result is `passed`.

**Known limitations (README):** renamed tests start with no history;
parametrized tests are tracked per parameter set; PR-only suites always show "not
enough history"; workflows that check out a different ref than the triggering
commit aren't supported.

---

## 8. AI explanations (after the core beta)

- **When:** only 🆕 new failures, only public repositories. Runs as a separate
  task (`explain_failures`) **after** the comment is posted. It edits the
  comment only if the PR head hasn't changed.
- **Input:** test key and redacted failure text (first 30 + last 30 lines,
  ≤ 6,000 chars), plus the PR's changed file names
  (`GET /repos/{o}/{r}/pulls/{n}/files`).
- **Output:** ≤ 3 sentences (likely cause, where to look, or "unsure"). It's
  rendered as untrusted text (§5.5) under *AI-generated, may be wrong*.
- **Limits:** 3 per run, 50 per installation per day, 20 s timeout. Failures
  are silent; the comment is already posted.
- **Cache:** per repository. `signature` = sha256 of (test key + normalized
  first error line, with numbers, hex and addresses stripped). The key includes
  `prompt_version` and `model`; entries expire after 30 days and cascade-delete
  with the repo.
- **Evaluation:** 10–15 real failures with known causes from the playground;
  re-run them whenever the prompt changes.

---

## 9. Background jobs (Celery)

| Task | Trigger | Does |
| --- | --- | --- |
| `process_run(run_pk)` | webhook (`on_commit`), sweep | claim → discover → ingest → detect → comment → finish |
| `sweep_runs()` | beat, every 5 min | re-enqueue runs `pending` > 5 min or `processing` with `claimed_at` > 10 min ago |
| `recover_failed_deliveries()` | beat, every 10 min | §5.7 |
| `sync_installation(installation_id)` | lifecycle events | §5.8 |
| `explain_failures(run_pk)` | after `process_run` (post-beta) | §8 |
| `prune_old_data()` | beat, nightly | §6 retention |

**Claim (committed before any network call):**

```python
token = uuid4()
claimed = (
    WorkflowRun.objects.filter(
        pk=run_pk,
        repo__active=True,
        repo__installation__suspended_at__isnull=True,
    )
    .filter(Q(status="pending") | Q(status="processing", claimed_at__lt=now() - LEASE))
    .update(status="processing", claim_token=token, claimed_at=now(), attempts=F("attempts") + 1)
)
if not claimed:
    return  # someone else has it, it's done, or the repo is gone
```

Every later write filters on `claim_token=token`. If it updates 0 rows, the
worker has lost its claim and stops.

**Timing (all must hold):** soft time limit 240 s < hard time limit 300 s <
lease 10 min < Redis `visibility_timeout` 1 h. A task is killed long before its
lease expires, and the broker never redelivers a message while its task can
still be running. Settings to add in M2: `CELERY_TASK_SOFT_TIME_LIMIT`,
`CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}`.

**Already configured:** `CELERY_TASK_ACKS_LATE`,
`CELERY_WORKER_PREFETCH_MULTIPLIER = 1`, `CELERY_TASK_TIME_LIMIT = 300`.

**Idempotency:** the claim, `Artifact.status`, `UNIQUE (test, run)`, `UNIQUE
(test, group_key)` on flake events, and the comment marker lookup.

**Testing tasks:** call the task functions directly against the test database,
with GitHub mocked by `respx`; one smoke test covers the Celery wiring.

---

## 10. Security and privacy

- Signed webhooks only; 5 MB body cap; constant-time comparison.
- Least-privilege permissions (§5.1); no repository contents.
- All report content is untrusted: streaming, limits (§5.4), `defusedxml`,
  depth cap, field caps, deadlines.
- **What's stored** (stated in the README): test identifiers, outcomes,
  durations, and the first 4 KB of failure output. That output can include
  source snippets or anything a test printed. Token-like strings (`ghp_…`,
  `github_pat_…`, `AKIA…`, `Bearer …`, `-----BEGIN … KEY-----`, long hex or
  base64) are redacted **before** storage, logging, rendering and model calls.
  This is best-effort.
- AI only on public repositories; model output is escaped like report text.
- Uninstalling or removing a repo deletes its data (cascade).
- Secrets: AWS Secrets Manager in production, `.env` locally; OIDC for CI
  deploys.

---

## 11. Deployment and operations

Infrastructure is defined in **Terraform** (`infra/`), deployed in M3.

| Piece | AWS service |
| --- | --- |
| Images | ECR |
| `web`, `worker`, `beat` | ECS Fargate: three services, one image |
| HTTPS for webhooks | ALB + ACM certificate |
| Database | RDS PostgreSQL (smallest; may share Cardfolio's instance) |
| Broker | ElastiCache Redis or a Redis container (open question) |
| Secrets | Secrets Manager → task environment variables |
| Logs, metrics, alarms | CloudWatch |
| Errors | Sentry |

- **CI/CD:** every PR runs lint, types, tests and a Docker build. A merge to
  `main` pushes the image to ECR, runs `migrate` as a one-off ECS task, then
  updates the services. AWS access uses OIDC.
- **Logs:** JSON lines carrying `delivery_guid`, `run_id`, `run_attempt` and
  the task ID.
- **Alarms:** runs stuck `pending` > 15 min; task failure rate; webhook recovery
  exhausted or lagging; 5xx on `/webhooks/github`.
- **Metrics:** processing time (receipt → comment) and GitHub delivery delay
  (`run.updated_at` → receipt), reported separately.
- **Cost:** smallest sizes, one task per service, retention, `terraform
  destroy` when idle.

---

## 12. Testing strategy

- **Unit:** signature verification; artifact-name parsing table (§5.4); JUnit
  parser with every fixture plus oversized and deeply nested files; group-key
  table; verdict table; comment rendering and escaping; redaction.
- **Integration:** tasks against a real Postgres test database, GitHub mocked
  with `respx`, including pagination and rate-limit responses.
- **Acceptance (M5):** deterministic flips; partial re-runs; reordered and
  duplicate deliveries; worker crash mid-run; failed delivery → recovery; two
  runs racing to update one comment; oversized artifact rejected.
- **End to end:** the `flaky-playground` repo with randomly failing tests,
  real PRs and real comments.

---

## 13. Roadmap

Milestones, estimates and dates are in RFC 0001 §10. In short:

M0 setup & spikes → M1 webhook intake → M2 ingestion worker → M3 first deploy
→ M4 detection & comments → M5 hardening, **core beta** → M6 AI enrichment &
launch.

## 14. Stretch goals (after v1)

- Fork PRs with an isolated, untrusted history
- GitHub Check Run alongside the comment
- Read-only dashboard (Django templates; public repos first)
- Weekly "flakiest tests" digest issue
- SQS as the Celery broker, with a write-up of the trade-offs
- Failure-rate thresholds tuned on collected data

## 15. Open decisions

See RFC 0001 §11.
