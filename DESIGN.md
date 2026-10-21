# Flaky Test Tracker — v1 Design

*Working name. Author: Tenzin Jamyang. Status: in development, v1 target December 2026.*

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
3. The app detects flaky tests: tests that both passed and failed on the **same
   commit**.
4. On a pull request with failing tests, the app posts (and keeps updating) one
   comment that classifies each failure:

```markdown
### 🧪 Test report for `a1b2c3d` — 3 failures in `CI / test`

| Test | Verdict | Evidence |
| --- | --- | --- |
| `tests/test_auth.py::test_login_expired_token` | ⚠️ Known flaky | Passed and failed on the same commit 4× in 30 days. Fails 8% of runs on `main`. |
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
  GitHub  ─────────────────────────────────────►  Web service (FastAPI)
    ▲                                              │ 1. verify signature
    │                                              │ 2. dedupe by delivery ID
    │                                              │ 3. INSERT delivery + job (one transaction)
    │                                              │ 4. return 202 fast
    │                                              ▼
    │                                         PostgreSQL ◄──── Dashboard (read-only)
    │                                              ▲
    │  list/download artifacts,                    │ claim job (FOR UPDATE SKIP LOCKED)
    │  get PR changed files,                       │ store results, flake events
    │  create/update PR comment                    │
    └────────────────────────────────────────  Worker process ───► LLM API
                                                                 (new failures only)
```

**One Docker image, two processes:** `web` (uvicorn) and `worker`
(`python -m app.worker`). They share the code and the database.

**Why the webhook handler does almost nothing:** GitHub expects a response within
about 10 seconds and treats slow responses as failed deliveries. Downloading
artifacts and calling an LLM can take much longer. The handler records the event
and returns; the worker does the slow work. This also means a crash in the worker
never loses an event, because the job is already in the database.

**Why Postgres is the queue (not SQS/Redis):** one less service to run and pay
for, and the job is created in the *same transaction* as the delivery record, so
"recorded but never queued" can't happen. `SELECT … FOR UPDATE SKIP LOCKED` lets
several workers pull jobs safely. Switching to SQS later is a trade-off worth
revisiting at scale: what do you gain and what do you lose?

**Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL, httpx,
PyJWT with `cryptography` (for RS256), `defusedxml`, pytest + respx, an LLM
provider SDK. Deployment follows Cardfolio's setup.

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
- uses: actions/upload-artifact@v4
  if: always()          # upload even when tests fail
  with:
    name: test-results
    path: reports/
```

The worker:

1. `GET /repos/{owner}/{repo}/actions/runs/{run_id}/artifacts` → find artifacts
   named `test-results*`.
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

A starting point; the source of truth is the Alembic migrations.

```
installations
  id (GitHub installation id, PK), account_login, active, created_at

repositories
  id (GitHub repo id, PK), installation_id → installations, full_name,
  default_branch, is_private, active, created_at

webhook_deliveries
  delivery_id (PK, from X-GitHub-Delivery), event, received_at

jobs
  id (PK), kind, payload (jsonb), status (queued|running|done|failed),
  attempts, run_after, locked_at, last_error, created_at
  INDEX (status, run_after)

workflow_runs
  (run_id, run_attempt) PK, repo_id → repositories, workflow_name,
  head_sha, head_branch, event, conclusion, pr_number (nullable),
  status (processed|no_artifacts|error), created_at, processed_at

tests
  id (PK), repo_id, test_key, file, classname, name, first_seen_at
  UNIQUE (repo_id, test_key)            -- test_key = "file::classname::name"

test_results
  id (PK), test_id → tests, run_id, run_attempt, head_sha, branch,
  outcome (passed|failed|skipped), duration_ms, failure_message (≤ 4 KB),
  created_at
  INDEX (test_id, created_at DESC)
  INDEX (test_id, head_sha)

flake_events
  id (PK), test_id → tests, head_sha, detected_at
  UNIQUE (test_id, head_sha)            -- one event per test per commit

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

**Flake event (the core signal):** a test has a flake event for a commit if it has
both a `passed` and a `failed` result **on the same `head_sha`**, across reruns or
different workflow runs. The code didn't change, but the result did. Record it once
per (test, commit) in `flake_events`.

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

## 9. Job queue

```sql
-- claim one job (run inside a transaction)
UPDATE jobs
SET status = 'running', locked_at = now(), attempts = attempts + 1
WHERE id = (
  SELECT id FROM jobs
  WHERE status = 'queued' AND run_after <= now()
  ORDER BY id
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

- **Success** → `done`. **Failure** → back to `queued` with
  `run_after = now() + backoff` (e.g. 30 s × 2^attempts), and `failed` after 5
  attempts with `last_error` saved.
- **Stuck jobs** (worker died): a periodic sweep requeues `running` jobs with
  `locked_at` older than 10 minutes.
- **Idempotency:** processing the same run twice must not duplicate anything.
  Use upserts and unique constraints (`workflow_runs` PK,
  `test_results (test_id, run_id, run_attempt)`), so retries are safe.
- **Rate limits:** GitHub API responses include rate-limit headers. On a 403/429
  rate limit, requeue with `run_after` set to the reset time.

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

- **Reuse Cardfolio's setup.** The cheapest option is the same host or database
  instance with a separate database.
- **Two processes from one image:** `web` behind HTTPS (GitHub requires a public
  HTTPS webhook URL) and `worker`.
- **CI:** lint + tests on every PR; deploy on merge to `main`.
- **Logging:** structured JSON logs that include `delivery_id`, `run_id` and
  `job_id`, so one webhook can be traced end to end.
- **Health:** `/healthz` for the web process; the worker logs a heartbeat.
- **Numbers to watch:** queue depth, job failure rate, time from webhook to PR comment, runs processed, LLM cost per day.

---

## 12. Testing strategy

- **Unit:** signature verification (known good/bad bodies), JUnit parser
  (fixtures from pytest, Jest and Go, plus malformed files, an XXE attempt and an
  oversized zip), detection rules (table-driven: given these results → this
  verdict), comment rendering, secret redaction.
- **Integration:** worker against a real Postgres test database with the GitHub
  API mocked using `respx`.
- **End to end:** a separate public repo, `flaky-playground`, with a few tests
  that fail at random (e.g. 30% of the time) and CI that uploads JUnit reports.
  Open PRs there and watch real comments appear. It also gives you the demo GIF.

---

## 13. Roadmap

**Week 0 — Setup (while finishing Cardfolio, ~2 evenings)**
- Register the GitHub App (no code). Create `flaky-playground` with a
  deliberately flaky test and the artifact upload step.

**Week 1 — Webhook receiver**
- FastAPI endpoint, signature check, delivery dedupe, delivery + job insert in
  one transaction. Schema v1 and migrations. Smee forwarding.
- *Done when:* rerunning a workflow in the playground creates exactly one job,
  and redelivering the same webhook creates none.

**Week 2 — Worker and ingestion**
- Job claim/retry loop, App JWT → installation token, artifact download, safe
  unzip, JUnit parser, storing results with upserts.
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
- Deploy web + worker with CI/CD. Read-only dashboard frontend.
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
- Swap the Postgres queue for SQS and write up the trade-offs
- Use failure-rate data to tune detection thresholds

---

## 15. Open decisions

- Final name (check that it isn't already taken on GitHub Marketplace).
- LLM provider and model.
- Hosting: share Cardfolio's infrastructure, or run separately?
- Publish the dashboard for public repos in v1, or ship comments only?
