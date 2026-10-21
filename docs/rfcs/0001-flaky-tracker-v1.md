# RFC 0001: Flaky Test Tracker v1

| | |
| --- | --- |
| **Author** | Tenzin Jamyang |
| **Status** | Draft — open for review (rev. 2: Django stack) |
| **Created** | 2026-10-20 |
| **Target** | v1 deployed with real installs by 2026-12-20 |
| **Related** | [`DESIGN.md`](../../DESIGN.md) (detailed specs: API calls, schema, comment format) |

> **Reviewers:** this RFC covers *what* v1 is, the key decisions and why, the
> risks, and the build plan. `DESIGN.md` holds the lower-level detail. Where the
> two disagree, this RFC wins and `DESIGN.md` gets updated. Specific questions
> for reviewers are in [§12](#12-questions-for-reviewers).

---

## 1. Summary

Build a GitHub App that ingests JUnit XML test reports from GitHub Actions runs,
detects flaky tests by finding tests whose outcome changed **while the code under
test did not**, and keeps one comment on each pull request that labels every
failure as *known flaky*, *also failing on main* or *new failure*. New failures
get a short, clearly labelled AI-generated explanation.

v1 is a Django application with Celery workers (Redis as the broker) and
PostgreSQL as the system of record. It ships as one Docker image running as three
processes (web, worker, scheduler) on AWS ECS Fargate, with all infrastructure
defined in Terraform.

## 2. Motivation

Flaky tests pass and fail on identical code. They cost teams in three ways:
reruns waste developer time and CI minutes; red builds stop meaning anything, so
real regressions get merged; and the evidence is spread across hundreds of runs,
so nobody can say which tests are flaky.

Commercial tools solve this for large organizations. Small teams, student
projects and most open-source repositories have nothing. GitHub's built-in AI
features can explain a single failed run, but nothing in the default GitHub
experience tracks a test's **history across runs**, which is what it takes to
say "this failure is not your fault."

## 3. Goals and non-goals

**Goals (v1)**

- G1. Install on any repository in under 5 minutes: install the App, add one
  upload step to CI.
- G2. Record every test result from every completed workflow run, without loss
  or duplication.
- G3. Detect flaky tests with a rule that is conservative and explainable in one
  sentence.
- G4. Comment on PRs within 60 seconds (p95) of the workflow run completing.
- G5. Explain new failures with AI under strict cost and privacy limits, without
  ever blocking the comment.
- G6. Run in production on AWS, installed on real repositories I don't own.

**Non-goals (v1)**

- CI systems other than GitHub Actions.
- Test formats other than JUnit XML.
- Automatically quarantining, skipping or retrying tests.
- Commenting on PRs opened from forks (results are still recorded).
- Dashboards for private repositories (needs GitHub OAuth login).
- Notifications outside GitHub (Slack, email), billing, org-wide analytics.

## 4. User experience

**Maintainer setup**

1. Install the App from its GitHub page and pick repositories.
2. Add one step after tests in CI:

   ```yaml
   - uses: actions/upload-artifact@v7
     if: always()
     with:
       name: test-results          # matrix jobs: test-results-${{ matrix.python }}
       path: reports/              # contains the JUnit XML file(s)
   ```

**Developer on a PR.** When tests fail, one comment appears (and is edited in
place on every later run), for example:

| Test | Verdict | Evidence |
| --- | --- | --- |
| `tests/test_auth.py::test_login_expired_token` | ⚠️ Known flaky | Flipped on identical code 4× in 30 days |
| `tests/test_api.py::test_rate_limit` | 🔴 Also failing on `main` | Failing on `main` since `9f8e7d6` |
| `tests/test_cards.py::test_import_variant` | 🆕 New failure | Never failed on `main`. AI: likely cause … |

When a later run passes, the comment is updated to "✅ All tests passing."

## 5. Proposed design

### 5.1 Architecture

```
GitHub ──workflow_run / installation webhooks──► web (Django + gunicorn)
                                                  │ verify HMAC · dedupe delivery
                                                  │ INSERT delivery + run (pending), 1 txn
                                                  │ on_commit → enqueue · 202
                                                  ▼
                                   Redis (broker) ──► worker (Celery) ──► LLM API
                                                         │      │
                         GitHub REST API ◄───────────────┘      ▼
                         (artifacts, PR files, comments)    PostgreSQL ◄── dashboard (Django
                                                                 ▲          templates)
                         beat (Celery) ── sweep stuck runs ──────┘
```

- **web**: Django behind gunicorn. Does the minimum and returns fast; GitHub
  expects a response within about 10 seconds. Also serves the admin and the
  read-only dashboard.
- **worker**: Celery. Authenticates as the App, downloads artifacts, parses,
  stores, detects, comments.
- **beat**: Celery's scheduler. Runs the periodic sweep for stuck runs.
- **PostgreSQL**: the system of record. A run's `status` column, not the
  broker, decides whether work is outstanding.
- **Redis**: the Celery broker only. Losing it loses no data.

### 5.2 Event flow for one workflow run

1. `workflow_run` with `action=completed` arrives. The web view verifies
   `X-Hub-Signature-256` against the raw body. In **one transaction** it
   inserts `X-GitHub-Delivery` into `webhook_deliveries` (a unique violation
   means a duplicate, so it returns 200) and upserts the `workflow_runs` row
   with `status=pending`.
2. `transaction.on_commit` enqueues `process_workflow_run(run_id)` to Celery,
   so the task can't run before the row exists.
3. The worker claims the run (`pending → processing`, a conditional update so
   two workers can't both claim it), gets an installation token (cached until
   shortly before it expires) and lists the run's artifacts whose names start
   with `test-results`.
4. For each artifact **not already in the `artifacts` table**, it downloads the
   zip, unzips in memory within size limits, parses each XML file with
   `defusedxml` and upserts tests and results.
5. Holding a per-repo Postgres advisory lock, it records any new flake events
   (see §5.3).
6. If the run belongs to a PR from the same repository, it computes verdicts for
   failed tests, requests AI explanations for new failures (within limits) and
   creates or edits the sticky comment.
7. It marks the run `processed`. Transient errors retry with exponential backoff
   and jitter, up to 5 times, then mark the run `failed` with `last_error`.
8. **Safety net:** every 5 minutes, beat re-enqueues runs still `pending` after
   5 minutes, or `processing` past the task time limit. This covers a crash or a
   Redis outage between commit and enqueue. Steps 3–7 are idempotent, so a
   duplicate enqueue is harmless.

### 5.3 Flake detection (revised from `DESIGN.md`)

The signal is a test that passed and failed **on identical code**. "Identical
code" needs care:

- **Attempts of the same run** (`run_id`, any `run_attempt`) always test the
  same commit and ref, because a re-run reuses the original `GITHUB_SHA` and
  `GITHUB_REF`. *Strongest signal.*
- **Different `push` (or `workflow_dispatch`/`schedule`) runs with the same
  `head_sha`** test the same commit. *Strong signal.*
- **Different `pull_request` runs with the same `head_sha` are *not* identical
  code.** A PR run tests a temporary merge commit of the PR into its base
  branch, and that changes whenever the base branch moves. `DESIGN.md`'s
  original rule (same `head_sha` across any runs) would produce false flakes
  here, so v1 excludes them.

Test identity is **(repository, suite, test key)**. The suite is the artifact
name, so matrix legs (`test-results-py312`, `test-results-py313`) are tracked as
separate tests. Otherwise a test that always fails on one Python version would
look flaky.

- **Flake event:** one row per (test, identical-code group) with both a passed
  and a failed result. Skipped results are ignored.
- **Known flaky:** at least `FLAKY_MIN_EVENTS` (default 1) flake events in the
  last `FLAKY_WINDOW_DAYS` (default 30).
- **Verdicts** for each failed test on a PR run, checked in order: known flaky →
  also failing on main (latest default-branch result failed) → new failure.

### 5.4 Data model changes vs `DESIGN.md`

- New `artifacts` table: `id` (GitHub artifact ID, PK), `run_id`,
  `run_attempt` (attempt that first saw it), `name`, `size_bytes`,
  `processed_at`. This makes ingestion idempotent per artifact and handles
  re-runs that keep earlier attempts' artifacts.
- `tests` gets a `suite` column; uniqueness becomes `(repo_id, suite,
  test_key)`.
- `test_results` gets `UNIQUE (test_id, run_id, run_attempt)` and an `event`
  column (copied from the run), so detection queries need no join.
- `workflow_runs` gets `status` (`pending|processing|processed|no_artifacts|failed`),
  `attempts` and `last_error`, replacing the separate `jobs` table from the
  original design. The run row is the unit of work.
- `flake_events` is keyed by `(test_id, group_key)`, where `group_key` is
  `run:<run_id>` or `sha:<head_sha>`, matching the two identical-code groups.

### 5.5 Module layout

```text
config/          Django project: settings, urls, celery.py, wsgi
core/            health check, shared helpers
webhooks/        Django app: view + signature check · WebhookDelivery model · installation handling
githubapp/       plain package: App JWT, installation-token cache, REST client with rate-limit handling
ingest/          Django app: WorkflowRun, Artifact, Test, TestResult models · tasks.py ·
                 artifacts.py (download, safe unzip) · junit.py (parse) · store.py (upserts)
detection/       Django app: FlakeEvent model · flakes.py · verdicts.py
comments/        Django app: PrComment model · render.py · sticky.py
explain/         Django app: AiExplanation model (cache) · redact.py · llm.py
dashboard/       Django app: read-only views and templates
infra/           Terraform (Week 5)
```

Boundaries: `junit.py`, `flakes.py`, `verdicts.py`, `render.py` and `redact.py`
are pure logic with no network calls, so they can be unit tested with fixtures.
Only `githubapp/` and `explain/llm.py` talk to the network. Every model is
registered in the Django admin.

## 6. Alternatives considered

| Decision | Chosen | Alternatives | Why |
| --- | --- | --- | --- |
| **D1. Integration model** | GitHub App + `workflow_run` webhook | (a) A GitHub Action that POSTs results to the API; (b) polling the Actions API | An App needs no secrets in user repos, sees re-runs natively and can comment as itself. (a) is simpler but asks users to trust the service with a token in every repo. (b) wastes rate limit and adds delay. |
| **D2. Result source** | JUnit XML artifacts | Parsing raw job logs; check-run annotations | JUnit is the one structured format almost every runner emits. Logs are unstructured and runner-specific. Annotations are capped and lossy. Cost: one extra CI step for users (see R1). |
| **D3. Background jobs** | Celery + Redis, with `on_commit` enqueue and a beat sweep | Custom Postgres queue (`SKIP LOCKED`); Django 6 Tasks + a database backend; SQS; RQ | Celery is the most widely used Python job system, with retries, backoff, time limits and scheduling built in. A Postgres queue gives atomic enqueue with fewer services, but means hand-building what Celery provides. Django's built-in Tasks API ships without a production worker. The dual-write gap between Postgres and Redis is closed by treating the run row as the source of truth (§5.2, step 8). |
| **D4. Flaky definition** | Outcome flip on identical code | Failure-rate threshold; statistical model | Conservative and explainable; no threshold to justify before there's data. Rates are collected and displayed so v2 can use them. |
| **D5. PR output** | One sticky issue comment | Check Run with annotations; commit status | Comments are the most visible and work without extra UI. A Check Run is a v2 option. |
| **D6. Web framework** | Django (ORM, migrations, admin, templates) | FastAPI + SQLAlchemy + Alembic; Go | Stays in Python, the language I know best, so time goes into new concepts rather than a new language. The admin doubles as an internal tool for inspecting runs and flake events, and templates cover the dashboard without a frontend framework. Django's synchronous request model is fine for a webhook endpoint that does one insert. My other project uses FastAPI, so this covers both major Python frameworks. |
| **D7. Compute** | ECS Fargate (web, worker, beat from one image) | EC2 instance; Lambda + SQS; Kubernetes (EKS) | No servers to patch, and one image with three commands. Lambda adds cold starts and Postgres connection-pool issues. EKS is powerful but too costly and complex for a single small service. |
| **D9. Infrastructure** | Terraform | Click-ops in the AWS console; AWS CDK | Reviewable, repeatable and easy to destroy when not in use. Terraform is the most widely used infrastructure-as-code tool and isn't tied to one cloud. |
| **D10. Tooling** | uv, Ruff, mypy (django-stubs), pytest-django | pip + requirements.txt; flake8/black; no type checking | uv gives a lockfile and fast, reproducible installs. Ruff replaces several linters. Strict type checking catches bugs in the parsing and detection logic early. |
| **D8. LLM use** | Only for new failures, capped and cached | Every failure; none | Known-flaky and failing-on-main verdicts are already explained by data. Caps make cost predictable. |

## 7. Security and privacy

- Every webhook is verified with HMAC-SHA256 using a constant-time comparison.
  The request body size is capped.
- Least-privilege App permissions: Actions *read*, Pull requests *read & write*,
  Metadata *read*. No access to repository contents.
- Artifacts are untrusted input: 50 MB compressed / 200 MB uncompressed / 50
  files max, `*.xml` only, parsed with `defusedxml` (rejects entity expansion
  and XXE; fixtures included).
- Stored data is limited to test names, outcomes, durations and failure messages
  truncated to 4 KB. No source code.
- Text sent to the LLM is redacted for token and key patterns, truncated and
  sent only for new failures. Comments label it as AI-generated.
- On `installation.deleted`, all of that installation's data is deleted.
- Secrets (App private key, webhook secret, LLM key, Django secret key) live in
  AWS Secrets Manager and are injected as environment variables; locally they
  come from an untracked `.env`. CI deploys through GitHub's OIDC role, so there
  are no long-lived AWS keys.
- Django's production security settings are on (HTTPS redirect except
  `/healthz`, secure cookies, HSTS); `manage.py check --deploy` runs before
  release.

## 8. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Adoption: users won't add an upload step | Med | High | Copy-paste snippets for pytest, Jest and Go; detect a missing artifact and say so in a one-time PR comment |
| R2 | False "known flaky" labels erode trust | Med | High | Identical-code rule (§5.3); evidence and run links in every verdict; manual audit of flagged tests before launch |
| R3 | Artifact behavior across re-run attempts differs from assumptions | Med | Med | Week 0 spike in `flaky-playground`; `artifacts` table makes ingestion idempotent either way |
| R4 | GitHub API rate limits | Low | Med | Token caching, minimal calls per run, back off until the reset time on 403/429 |
| R5 | Secrets in failure output reach storage or the LLM | Med | High | Truncation and redaction before storage and before LLM calls; README states what is stored |
| R6 | LLM cost or latency spikes | Low | Med | Per-run and per-day caps, signature cache, 20 s timeout, comment never waits on AI |
| R7 | **Schedule:** Thanksgiving, finals and job applications overlap the build | High | High | Cut list below; deploy early in Week 5; one buffer week |
| R8 | Hosting cost grows with users | Low | Low | 90-day retention, shared database instance, caps; ElastiCache can fall back to a Redis container |
| R9 | Learning curve: Django, Celery and Terraform are new | Med | Med | Scaffold already wired and tested; one new tool per week in the plan; Terraform only in Week 5 |
| R10 | Run saved in Postgres but never enqueued (crash or Redis outage) | Low | Med | `on_commit` enqueue plus the beat sweep for stuck runs (§5.2, step 8); idempotent processing |

**Cut list if behind schedule (cut in this order):** dashboard → AI
explanations → "also failing on main" verdict. The **must-ship core** is
ingestion + flake detection + sticky PR comment, deployed and installed on real
repos.

## 9. Success metrics

- **Reliability:** zero lost or duplicated runs across 1,000+ processed runs
  (checked by comparing GitHub's run list to the `workflow_runs` table); p95
  webhook-to-comment ≤ 60 s.
- **Accuracy:** in a manual audit of 20 tests labelled known flaky, at least 18
  are genuinely flaky.
- **Adoption:** installed on 10+ repositories, at least 5 not owned by me.
- **Cost:** under $1/day of LLM spend at launch volume.

## 10. Plan

| Week | Dates (2026) | Scope | Exit criteria |
| --- | --- | --- | --- |
| 0 | Oct 21 – Nov 8 (alongside Cardfolio) | Register the GitHub App; capture real `workflow_run` and `installation` payloads as fixtures; **spikes:** re-run artifact behavior (R3), `head_sha`/event semantics for PR vs push runs | Spike findings written into §13; fixtures committed |
| 1 | Nov 9 – 15 | Django models v1 + migrations + admin; webhook view (signature, dedupe, run insert, `on_commit` enqueue); installation handling | Redelivering a webhook from GitHub's UI creates no second run or task; tests for bad signatures and duplicates |
| 2 | Nov 16 – 22 | Celery task (claim, retries with backoff, rate-limit handling) + beat sweep; App JWT → installation token; artifact download + safe unzip; JUnit parser; idempotent storage | Real playground runs appear in `test_results`; re-processing a run changes nothing; killing the worker mid-task loses nothing; parser passes all fixtures |
| 3 | Nov 23 – 29 *(Thanksgiving, lighter)* | Flake events (§5.3) with advisory lock; verdicts; comment rendering; sticky create/edit | A playground PR gets one comment with correct verdicts that updates per push |
| 4 | Nov 30 – Dec 6 | AI explanations: redaction, cache, caps, timeout fallback, 10–15 case evaluation set | Removing the LLM key still produces correct comments |
| 5 | Dec 7 – 13 *(finals, lighter)* | Terraform: ECR, ECS Fargate (web, worker, beat), RDS, Redis, ALB + HTTPS, Secrets Manager; CI/CD deploy via OIDC; CloudWatch alarms; Sentry; public App page; dashboard templates only if ahead | App installable by anyone; runs with my laptop off; `terraform destroy` / `apply` recreates it |
| 6 | Dec 14 – 20 | Install on Cardfolio + 3–5 classmates' repos + small OSS projects; README, demo GIF; launch posts | Metrics in §9 being collected; first external installs |
| — | Dec 21 – 31 | Buffer, fixes from early users, audit for §9 accuracy | — |

Each week lands with tests and green CI. Work happens on branches and merges
through PRs, so the project's own CI history is visible.

## 11. Open questions

1. Name: "Flaky Test Tracker" is a working name. Check availability on GitHub
   Marketplace before Week 5.
2. LLM provider and model (decide in Week 4, based on cost per explanation).
3. Broker in production: ElastiCache Redis or a Redis container on Fargate
   (cost vs. operational simplicity)? Decide in Week 5.
4. Should `FLAKY_MIN_EVENTS` default to 2 to reduce false positives at the
   cost of slower detection? Revisit after the audit.
5. Retention: is 90 days of `test_results` enough for useful failure rates?
6. Should the playground's deliberately flaky tests double as the public demo,
   or should the demo use a real open-source repo?

## 12. Questions for reviewers

- Is the identical-code rule in §5.3 correct for all trigger types? Are there
  cases (merge queues, `pull_request_target`, scheduled runs) where it is wrong?
- Does the `on_commit` enqueue plus beat sweep (§5.2, steps 2 and 8) fully close
  the gap between saving a run and queuing it? Is the `pending → processing`
  claim safe with Celery's late acknowledgement and redelivery?
- Do the data model changes in §5.4 cover re-runs, matrix jobs and multiple
  workflows per repository?
- Any security gaps in §7, especially around untrusted artifacts and data sent
  to the LLM?
- Is the plan in §10 realistic for one person working part-time, and is the cut
  list in the right order?

## 13. Spike findings (filled in during Week 0)

- **Artifacts across re-run attempts:** _TBD_
- **`head_sha` and event fields for PR vs push runs:** _TBD_
- **Payload samples captured:** _TBD_

## 14. Revision history

- 2026-10-20: Initial draft.
- 2026-10-21: Switched to Django + Celery/Redis (D3, D6), added Terraform and
  tooling decisions (D9, D10), named the AWS services, added risks R9–R10.
