# Flaky Test Tracker — v1 Design

*Working name. Author: Tenzin Jamyang. Status: in development, v1 target December 2026.*

> Decisions, alternatives, risks and the build plan are in
> [RFC 0001](docs/rfcs/0001-flaky-tracker-v1.md). Where the two disagree, the RFC wins.

A GitHub App that records every CI test result, figures out which tests are flaky,
and comments on pull requests so developers know whether a failure is their fault.

---

## 1. Problem

A **flaky test** passes and fails on the same code with no change. Flaky tests:

- make developers rerun CI over and over, wasting time and compute
- teach teams to ignore red builds, so real failures slip through
- are hard to spot by hand, because evidence is spread across hundreds of runs

Large companies pay for tools that track this. Small teams, student projects and
most open-source repos don't have one. GitHub Copilot can explain a single failed
run, but nothing in the default GitHub experience tracks **a test's history
across runs**, and history is what you need to know whether a failure is flaky.

## 2. What v1 does

1. A maintainer installs the GitHub App on a repo and makes CI upload a JUnit XML
   test report as an artifact (one extra step in their workflow; see §5.4).
2. Every time a workflow run completes, the app downloads the report and stores
   each test's result.
3. The app detects flaky tests: tests that both passed and failed on **identical
   code** (see §7).
4. On a pull request with failing tests, the app posts (and keeps updating) one
   comment that classifies each failure:

```markdown
### 🧪 Test report for `a1b2c3d` — 3 failures in `CI / test`

| Test | Verdict | Evidence |
| --- | --- | --- |
| `tests/test_auth.py::test_login_expired_token` | ⚠️ Known flaky | Passed and failed on identical code 4× in 30 days. Fails 8% of runs on `main`. |
| `tests/test_api.py::test_rate_limit` | 🔴 Also failing on `main` | Failing on `main` since `9f8e7d6`. Probably not your change. |
| `tests/test_cards.py::test_import_variant` | 🆕 New failure | Has never failed on `main`. Likely caused by this PR. |

<details><summary>🤖 Possible cause of <code>test_import_variant</code> (AI-generated, may be wrong)</summary>

The assertion expects 2 variants but got 3. This PR changed `catalog_import.py`,
which now creates a variant for `reverse_holofoil`. Check whether the test's
fixture data needs updating.
</details>
```

5. A small read-only dashboard lists each repo's flakiest tests (public repos
   only in v1).

## 3. Scope

**In v1**

- GitHub Actions only, triggered by the `workflow_run` webhook
- Any test runner that can output JUnit XML (pytest, Jest via `jest-junit`,
  JUnit/Gradle, Go via `go-junit-report`, …)
- Flaky detection, PR verdict comments, AI explanation for new failures
- Read-only dashboard for public repos
- Deployed on AWS, installed on real repos

**Not in v1** (see §14 for stretch goals)

- Other CI providers (CircleCI, GitLab, Jenkins)
- Automatic quarantine or retry of flaky tests
- Comments on PRs from forks (they're recorded, but not commented on; see §5.5)
- Slack/email notifications, billing, org-level analytics
- Dashboard for private repos (needs GitHub OAuth login)

---

## 4. Architecture

```
                    workflow_run (completed)
  GitHub  ─────────────────────────────────────►  web (Django + gunicorn)
    ▲                                              │ 1. verify signature
    │                                              │ 2. INSERT delivery + run (status=pending),
    │                                              │    one transaction; duplicate delivery → 200
    │                                              │ 3. on commit: enqueue Celery task
    │                                              │ 4. return 202 fast
    │                                              ▼
    │                                   Redis (broker) ──► worker (Celery)
    │                                                        │ store results, flake events
    │  list/download artifacts,                              ▼
    │  get PR changed files,                            PostgreSQL ◄── dashboard (Django
    │  create/update PR comment                              ▲          templates, read-only)
    └──────────────────────────────────────────────── worker ┘───► LLM API (new failures only)

  beat (Celery) ── every 5 min ──► re-enqueue runs stuck in "pending"
```

**One Docker image, three processes:** `web` (gunicorn serving Django),
`worker` (`celery -A config worker`) and `beat` (`celery -A config beat`, the
scheduler for the periodic sweep). They share the code, Postgres and Redis.

**Why the webhook handler does almost nothing:** GitHub expects a response within
about 10 seconds and treats slow responses as failed deliveries. Downloading
artifacts and calling an LLM can take much longer. The handler records the event
and returns; the worker does the slow work.

**Why Celery + Redis:** it's the standard background-job stack for Django, with
retries, backoff, time limits and scheduling built in. The catch is that the
database and the queue are separate systems, so "saved to Postgres" and "queued
in Redis" can't happen atomically. Two patterns close that gap:

1. **`transaction.on_commit`:** the task is enqueued only after the transaction
   that saved the run commits, so a worker never picks up a run that doesn't
   exist yet.
2. **The run row is the source of truth.** If the process dies or Redis is down
   between commit and enqueue, the run stays `pending`. A periodic **sweep**
   (Celery beat) re-enqueues runs that have been `pending` too long. Because
   processing is idempotent, an occasional double-enqueue is harmless.

Together these are a lightweight version of the *transactional outbox* pattern.

**Stack:** Python 3.13, Django (ORM, migrations, admin), Celery + Redis,
PostgreSQL, gunicorn, httpx, PyJWT with `cryptography` (for RS256),
`defusedxml`, pytest + pytest-django + respx, uv, Ruff, mypy (with
django-stubs), an LLM provider SDK. Infrastructure on AWS via Terraform (§11).

---

## 5. GitHub integration

### 5.1 App registration

- **Permissions** (least privilege):
  - Actions: **Read** (list and download artifacts)
  - Pull requests: **Read & write** (changed files, PR comments)
  - Metadata: **Read** (required by GitHub)
- **Subscribed events:** `Workflow run`. Apps receive `installation` and
  `installation_repositories` events automatically.
- **Secrets:** a webhook secret and the App's private key (`.pem`). Never commit
  either; load them from environment variables or a secrets manager.

### 5.2 Receiving webhooks

1. Read the **raw request body** (before JSON parsing).
2. Compute HMAC-SHA256 of the body with the webhook secret and compare it to the
   `X-Hub-Signature-256` header using a **constant-time comparison**
   (`hmac.compare_digest`). Reject with 401 if they don't match.
3. Read `X-GitHub-Delivery` (a unique ID per delivery). If it's already in
   `webhook_deliveries`, return 200 and do nothing: GitHub can deliver the same
   event more than once, and redeliveries reuse the ID.
4. Only enqueue `workflow_run` events where `action == "completed"` and
   `conclusion` is `success` or `failure`. Passing runs matter too, because
   they're half of the history. Handle `installation` events by
   creating/deactivating installations and repos (and delete data on uninstall).

**Local development:** forward webhooks to your laptop with a tunnel (smee.io is
what GitHub's docs use). The App settings page has a *Recent Deliveries* tab where
you can **redeliver** any past webhook, which is very useful for testing.

### 5.3 Authenticating as the App

Two-step auth:

1. **App JWT:** sign a short-lived JWT (RS256, `iat` a little in the past, `exp`
   ≤ 10 minutes ahead, `iss` = the App's client ID) with the private key. This
   proves "I am the App" but can't access any repo.
2. **Installation token:** call
   `POST /app/installations/{installation_id}/access_tokens` with the JWT. You get
   a token scoped to that installation's repos and permissions, valid for about an
   hour. Cache it in memory until shortly before expiry.

### 5.4 Getting test results

The user's workflow must upload a JUnit report as an artifact whose name starts
with `test-results`. Example for pytest:

```yaml
- run: pytest --junitxml=reports/junit.xml
- uses: actions/upload-artifact@v7
  if: always()          # upload even when tests fail
  with:
    name: test-results
    path: reports/
```

Matrix jobs need one artifact per leg, e.g. `test-results-${{ matrix.python }}`.
The artifact name becomes the test's **suite**, so each leg is tracked separately.

The worker:

1. `GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts` → find artifacts
   named `test-results*` and skip any whose ID is already in the `artifacts`
   table (re-runs can keep earlier attempts' artifacts).
2. Download each artifact's zip. The API answers with a **redirect** to a
   short-lived URL. httpx doesn't follow redirects by default.
3. Unzip **in memory with limits**: max 50 MB compressed, 200 MB uncompressed, 50
   files, only `*.xml`. (Why: protect against zip bombs from untrusted repos.)
4. Parse with `defusedxml`, not the standard library parser. (Why: XML from
   untrusted sources can trigger entity-expansion and XXE attacks.)

**JUnit XML in one paragraph:** `<testsuite>` elements contain `<testcase
classname="…" name="…" time="…">`. A testcase with a `<failure>` or `<error>`
child failed; with `<skipped>` it was skipped; otherwise it passed. Test runners
differ in small ways (nested suites, `file` attributes), so the parser should be
lenient and well tested with fixtures from several runners.

### 5.5 Finding the PR and commenting

- `workflow_run.pull_requests` lists PRs for the run's branch. It's **empty for
  PRs from forks**, so v1 records those results but doesn't comment. (Stretch:
  look up PRs by commit SHA.)
- Keep **one comment per PR**. The first time, create it with
  `POST /repos/{owner}/{repo}/issues/{pr_number}/comments` and store its ID. After
  that, edit it with `PATCH …/issues/comments/{comment_id}`. Include a hidden
  marker (`<!-- flaky-tracker -->`) so the comment can be found again if the
  stored ID is lost.
- If a later run passes, update the comment to "✅ All tests passing on
  `<sha>`" rather than deleting it.
- Changed files for the AI prompt: `GET /repos/{owner}/{repo}/pulls/{n}/files`
  (file names only in v1).

---

## 6. Data model

A starting point. These become Django models; the source of truth is the
migrations Django generates from them. The admin is registered for each model,
so data can be inspected at `/admin/` while debugging.

```
installations
  id (GitHub installation id, PK), account_login, active, created_at

repositories
  id (GitHub repo id, PK), installation_id → installations, full_name,
  default_branch, is_private, active, created_at

webhook_deliveries
  delivery_id (PK, from X-GitHub-Delivery), event, received_at

workflow_runs
  (run_id, run_attempt) PK, repo_id → repositories, workflow_name,
  head_sha, head_branch, event, conclusion, pr_number (nullable),
  status (pending|processing|processed|no_artifacts|failed), attempts,
  last_error, created_at, processed_at
  INDEX (status, created_at)            -- for the stuck-run sweep

artifacts
  id (GitHub artifact id, PK), run_id, run_attempt (first seen), name,
  size_bytes, processed_at              -- makes ingestion idempotent

tests
  id (PK), repo_id, suite, test_key, file, classname, name, first_seen_at
  UNIQUE (repo_id, suite, test_key)     -- suite = artifact name
                                        -- test_key = "file::classname::name"

test_results
  id (PK), test_id → tests, run_id, run_attempt, head_sha, branch, event,
  outcome (passed|failed|skipped), duration_ms, failure_message (≤ 4 KB),
  created_at
  UNIQUE (test_id, run_id, run_attempt)
  INDEX (test_id, created_at DESC)
  INDEX (test_id, head_sha)

flake_events
  id (PK), test_id → tests, group_key, detected_at
  UNIQUE (test_id, group_key)           -- group_key = "run:<run_id>" or "sha:<head_sha>"

pr_comments
  (repo_id, pr_number) PK, comment_id, last_head_sha, updated_at

ai_explanations
  signature (PK, hash), explanation, model, input_tokens, output_tokens,
  created_at
```

Open questions for the schema:

- `test_results` will be by far the biggest table. What indexes do the detection
  queries actually need? Check with `EXPLAIN ANALYZE`.
- Why is `(run_id, run_attempt)` the key, not just `run_id`? (Reruns of the same
  run are the best flakiness signal.)
- Retention: drop `test_results` older than 90 days? What does that do to the
  stats?

---

## 7. Flaky detection

Keep the rules simple enough to explain in one sentence.

**Outcome normalization:** `failure` and `error` → `failed`; `skipped` is ignored
for detection.

**Flake event (the core signal):** a test has a flake event when it has both a
`passed` and a `failed` result **on identical code**. Two groups count as
identical code:

- **Attempts of the same run** (`run:<run_id>`): a re-run reuses the original
  commit and ref.
- **`push`, `workflow_dispatch` or `schedule` runs with the same `head_sha`**
  (`sha:<head_sha>`).

Separate `pull_request` runs with the same `head_sha` do **not** count. A PR run
tests a temporary merge of the PR into its base branch, which changes whenever
the base branch moves, so the code may differ. Record one event per (test,
group) in `flake_events`.

**Known flaky:** at least `FLAKY_MIN_EVENTS` (default 1) flake events in the last
`FLAKY_WINDOW_DAYS` (default 30).

**Verdict for each failed test on a PR run** (checked in this order):

1. ⚠️ **Known flaky:** the test meets the rule above.
2. 🔴 **Also failing on main:** the test's most recent result on the default
   branch is `failed`.
3. 🆕 **New failure:** neither applies. This is probably caused by the PR, so it's
   the only verdict that gets an AI explanation.

**Displayed but not used for verdicts in v1:** failure rate on the default branch
over the last 50 results. (Why not use it? Picking a threshold like "5% = flaky"
is hard to justify without data. Collect the data first, then decide.)

**Concurrency gotcha:** if two workers store results for the same commit at the
same moment, each may see only its own results and both miss the flake. Fix:
take a per-repo lock (`pg_advisory_xact_lock(repo_id)`) while inserting results
and checking for flake events. Trade-off: this serializes ingestion per repo, which
is fine at v1 scale.

**Known limitations (state them in the README):** renamed tests start with no
history; parametrized tests are tracked per parameter set; a test that only ever
runs on PRs has no `main` history.

---

## 8. AI explanations

Only for 🆕 new failures, and the comment must work fine without them.

- **Input:** test key, failure message and traceback (first 30 + last 30 lines,
  capped around 6,000 characters), and the PR's changed file names.
- **Output:** at most 3 sentences: likely cause, where to look, and a plain
  "unsure" when the evidence is thin. Always labelled *AI-generated, may be
  wrong*.
- **Guardrails:**
  - Max 3 explanations per run and 50 per installation per day
  - 20-second timeout. On any error, post the comment without the explanation.
  - **Redact before sending:** patterns that look like tokens or keys (`ghp_…`,
    `AKIA…`, `Bearer …`, long hex strings). CI logs leak secrets more often than
    you'd think.
  - **Cache** by signature = hash of (test key + normalized first error line,
    with numbers, hex and memory addresses stripped). The same failure seen again
    reuses the explanation.
  - Log input/output tokens per call so you can report the cost.
- **Model:** a small, cheap model is enough. Use a config variable so it can
  change.
- **Evaluate it:** keep 10–15 real failures from the playground repo with the
  cause you know, and check the explanations against them when you change the
  prompt.

---

## 9. Background jobs (Celery)

**Tasks**

| Task | Triggered by | Does |
| --- | --- | --- |
| `process_workflow_run(run_id)` | webhook (on commit), sweep | download → parse → store → detect → comment |
| `sweep_pending_runs()` | beat, every 5 min | re-enqueue runs `pending` for more than 5 min |
| `handle_installation_event(payload)` | webhook (on commit) | create/deactivate installations and repos; delete data on uninstall |

**Settings already in `config/settings.py`:** `CELERY_TASK_ACKS_LATE` (a task
is redelivered if the worker dies mid-task), `CELERY_WORKER_PREFETCH_MULTIPLIER
= 1` (one task at a time) and a 300 s hard time limit.

**Retries:** network errors and GitHub 5xx retry with exponential backoff and
jitter (Celery's `autoretry_for` + `retry_backoff`), up to 5 times. After that
the run is marked `failed` with `last_error` saved, and shows up in the admin.

**Rate limits:** GitHub API responses include rate-limit headers. On a 403/429
rate-limit response, retry with `countdown` set to the reset time instead of
the normal backoff.

**Idempotency:** with late acks, retries and the sweep, a run can be processed
more than once, and that must change nothing. Mechanisms: the run's `status`
check at the start of the task, the `artifacts` table (each artifact ingested
once), and unique constraints with upserts (`test_results (test_id, run_id,
run_attempt)`, `flake_events (test_id, group_key)`).

**Testing tasks:** call the task function directly in tests (no broker needed);
Celery wiring is covered by one smoke test.

---

## 10. Security and privacy

- Verify every webhook signature; reject unsigned requests.
- Least-privilege App permissions (§5.1). No access to source code contents.
- Store only test names, outcomes, durations and truncated failure messages.
- Treat all artifact contents as untrusted: size limits, `defusedxml`, no
  executing anything.
- Redact secrets before LLM calls (§8).
- On `installation.deleted`, delete that installation's data.
- Private-repo data never appears on the public dashboard.
- Secrets live in env vars / AWS secrets store, never in the repo.

---

## 11. Deployment and operations

All infrastructure is defined in **Terraform** (`infra/`), so it can be
reviewed, recreated and torn down with one command.

| Piece | AWS service |
| --- | --- |
| Container images | ECR |
| `web`, `worker`, `beat` | ECS Fargate: three services, one image, different commands |
| HTTPS endpoint for webhooks | Application Load Balancer + ACM certificate |
| Database | RDS PostgreSQL (smallest instance; can share Cardfolio's instance with a separate database) |
| Broker | ElastiCache Redis (or a Redis container if cost matters) |
| Secrets | Secrets Manager, injected into tasks as environment variables |
| Logs and alarms | CloudWatch Logs; alarms on task failures and stuck runs |
| Errors | Sentry (free tier) for exceptions in web and worker |

- **CI/CD:** GitHub Actions runs lint, types, tests and a Docker build on every
  PR. On merge to `main`, it builds and pushes the image to ECR, runs
  migrations as a one-off ECS task, then updates the services. AWS access uses
  GitHub's OIDC role, not long-lived keys.
- **Logging:** structured JSON logs that include `delivery_id`, `run_id` and
  the Celery task ID, so one webhook can be traced end to end.
- **Health:** `/healthz` for the load balancer; Celery worker health via
  `celery inspect ping`.
- **Numbers to watch:** runs stuck in `pending`, task failure rate, time from
  webhook to PR comment, runs processed, LLM cost per day.
- **Cost control:** smallest Fargate sizes, one task per service, 90-day data
  retention, and `terraform destroy` for anything not in use.

---

## 12. Testing strategy

- **Unit:** signature verification (known good/bad bodies), JUnit parser
  (fixtures from pytest, Jest and Go, plus malformed files, an XXE attempt and an
  oversized zip), detection rules (table-driven: given these results → this
  verdict), comment rendering, secret redaction.
- **Integration:** tasks called directly against a real Postgres test database
  (pytest-django creates it), with the GitHub API mocked using `respx`.
- **End to end:** a separate public repo, `flaky-playground`, with a few tests
  that fail at random (e.g. 30% of the time) and CI that uploads JUnit reports.
  Open PRs there and watch real comments appear. It also gives you the demo GIF.

---

## 13. Roadmap

**Week 0 — Setup (while finishing Cardfolio, ~2 evenings)**
- Register the GitHub App (no code). Create `flaky-playground` with a
  deliberately flaky test and the artifact upload step.

**Week 1 — Webhook receiver**
- Django webhook view, signature check, delivery dedupe, delivery + run insert in
  one transaction with `on_commit` enqueue. Models v1 and migrations. Smee forwarding.
- *Done when:* rerunning a workflow in the playground creates exactly one run
  row and one task, and redelivering the same webhook creates neither.

**Week 2 — Worker and ingestion**
- Celery task with retries and backoff, stuck-run sweep, App JWT → installation
  token, artifact download, safe unzip, JUnit parser, storing results with upserts.
- *Done when:* real playground runs show up as rows in `test_results`, and
  processing a run twice doesn't duplicate anything.

**Week 3 — Detection and PR comments**
- Flake events, verdict logic, comment rendering, sticky create/update.
- *Done when:* a playground PR gets one comment with correct verdicts that
  updates on each push.

**Week 4 — AI explanations**
- Prompt, redaction, caching, limits, timeout fallback, small evaluation set.
- *Done when:* new failures get a useful explanation, and turning off the LLM
  API key doesn't break comments.

**Week 5 — Deploy and dashboard**
- Terraform for ECR, ECS Fargate (web, worker, beat), RDS, Redis, ALB and
  secrets; CI/CD deploy on merge; Sentry. Read-only dashboard with Django templates.
- *Done when:* the App is installable by anyone from its public page and runs
  without your laptop.

**Week 6 — Real users and launch**
- Install on Cardfolio and 3–5 classmates' repos; reach out to a few small
  open-source projects with CI flakiness. README with setup steps, limitations
  and a demo GIF. Post as a Show HN / on r/github.

---

## 14. Stretch goals (after v1)

- Comments on fork PRs (find the PR by commit SHA)
- GitHub Check Run instead of / alongside the comment
- Weekly "top 5 flakiest tests" issue or digest
- Private-repo dashboard behind GitHub OAuth login
- Try SQS as the Celery broker and write up the trade-offs vs Redis
- Use failure-rate data to tune detection thresholds

---

## 15. Open decisions

- Final name (check that it isn't already taken on GitHub Marketplace).
- LLM provider and model.
- Hosting: share Cardfolio's RDS instance, or run separately?
- Broker on AWS: ElastiCache, or a Redis container to save cost?
- Publish the dashboard for public repos in v1, or ship comments only?
