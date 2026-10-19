# RecoveryOS

A release-preparation agent that is built to survive failure. It inspects a repository, runs checks,
writes a changelog, chooses a version and builds a release plan, using a language model for every
step. If the model is unavailable or wrong, deterministic code takes over, and the interface shows
which one did the work. If the process crashes, a call times out or a reply is lost, the run resumes
from the last saved checkpoint without repeating a side effect. Nothing is ever published without an
explicit approval that is checked again at the moment of publishing.

**Status:** working locally with tests and a benchmark. It is not deployed yet, and publishing is
simulated (nothing is written to GitHub). See [Known limitations](#known-limitations).

- [The problem](#the-problem)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [API](#api)
- [Testing](#testing)
- [Benchmark](#benchmark)
- [Security and safety](#security-and-safety)
- [Known limitations](#known-limitations)
- [Deployment](#deployment)
- [License](#license)

## The problem

Multi-step agents fail halfway. They crash after a side effect but before they record it, lose the
reply to a call that did succeed, retry something that must not be repeated, or treat a garbage reply
as a result. The interesting engineering is not more agents, it is staying correct under interruption.

RecoveryOS demonstrates that on a concrete workflow, and measures it against a plain baseline that
lacks the safeguards.

## How it works

### The workflow

Seven steps, each with a typed input and output and an explicit status. Five of them are attempted by
a language model first.

| # | Step | The LLM does | Deterministic fallback | Side effect |
|---|---|---|---|---|
| 1 | Inspect repository | Summarizes the repository state and raises concerns | Builds the summary from the repository data | none |
| 2 | Run checks | Interprets CI results and gives reasons | Pass or fail from exit codes | none |
| 3 | Prepare changelog | Writes one entry per commit and categorizes it | Groups commits by Conventional Commit type | none |
| 4 | Validate version | Chooses the semantic-version bump and justifies it | Bump implied by the commits | none |
| 5 | Create release plan | Writes the rationale for the plan | Template rationale | draft release (keyed) |
| 6 | Your decision | nothing | nothing | none |
| 7 | Publish release | nothing | nothing | publish (keyed, approval-gated) |

### LLM first, deterministic as a labeled fallback

Every model reply must be a small JSON object that matches a schema. Then deterministic code checks it
against the facts. If the model is missing, rate limited, times out, returns invalid output, or
contradicts the facts (after one retry), the deterministic implementation produces the step instead.

The model proposes and the code enforces. A model cannot:

- pass checks that failed (its verdict must match the exit codes),
- choose a version bump below what the commits require, or hide a breaking change,
- drop or invent commits in the changelog,
- decide whether an approval is valid, whether a side effect already happened, or whether a step is done.

Every step result records `produced_by` (`llm`, `deterministic_fallback`, `human` or `system`), the
provider and model that actually answered, and the reason for any fallback. The interface shows this on
each step. The model list is tried in order: a rate-limited or unusable answer moves on to the next model
before falling back to code.

### Durability

- **Checkpoints.** A step becomes complete only when its result, a checkpoint (a hash of all state so
  far) and an audit event are written in one database transaction.
- **Resume.** After a crash the executor rebuilds everything from the database. Steps with a saved
  result are skipped. A step that was in flight is redone, and for a side-effecting step the question
  "did it already happen?" is answered before anything is called again.
- **Idempotency keys.** Every side effect carries a key derived from the run, the step and the content
  of the action, never the attempt number. A side effect is run through three checks: our own record of
  the key, then the remote's record of the key, and only then the call itself. A reply is validated
  before it is recorded, so a corrupt reply is never saved as a result.
- **Bounded retries.** Timeouts, rate limits and invalid tool output are retried a fixed number of
  times with backoff, then the run fails with a clear reason.
- **Audit timeline.** Every transition is an append-only event with a sequence number.

### Your decision

After the plan is created the run waits for you. Nothing is mandatory. You can:

1. **Suggest a change.** The agent adds your suggestion, rebuilds the plan and re-checks everything:
   repository, checks, changelog, version and plan. Repeat as often as you like (up to 10 per run).
   The suggestion is fenced as untrusted text for the model, and the same fact checks apply, so a
   suggestion cannot make the model break the facts (it can, for example, raise the version bump but
   not lower it). Each revision is stored, and the plan records how the agent handled your suggestion.
2. **Download the plan and finish.** The flow ends and nothing is published. The plan is a Markdown file
   with the release notes, checks, your suggestions, who produced each step, and the `git tag` and
   `gh release create` commands to publish it yourself.
3. **Approve and publish.** Approval is tied to the exact plan (its hash), expires, and is checked again
   immediately before publishing: status, expiry, plan hash and whether the repository head moved. If
   the gate refuses, that approval is revoked on record and a new decision is requested.
4. **Discard** the plan.

Publishing is simulated: it goes to a mock ledger and nothing is written to GitHub. Real writes are
deliberately not implemented, so the downloaded plan with its commands is the way to release for real.

### Repositories

- **Demo repositories** are built-in fixtures with a mock GitHub.
- **Linked GitHub repositories**: any public `owner/name`. The agent reads commits since the last
  semantic-version tag, the tags, and the CI checks of the head commit through the GitHub API, read-only.
  A repository with no commits (or none since the last tag) ends the run with `nothing_to_release`.

A sidebar on the home page shows what the agent will read before a run starts.

### Failure injection (demo mode)

Six faults can be injected at chosen points, once each: crash after a step is saved, crash after a
side effect but before it is recorded (draft or publish), a lost reply to the publish call, a rate limit
twice in a row, and malformed data from a tool. They are how the recovery behavior is demonstrated and
benchmarked.

## Architecture

```mermaid
flowchart LR
    B[Browser: Next.js] -->|HTTPS, no secrets| API[FastAPI]
    API --> EX[Durable executor]
    EX --> DB[(PostgreSQL or in-memory SQLite)]
    EX --> LLM[LLM provider: Groq or OpenRouter]
    EX --> GH[GitHub reader: public repos, read-only]
    EX --> MOCK[Mock GitHub ledger: drafts and publishes]
```

```mermaid
flowchart TD
    A[Run created] --> S1[Inspect repository]
    S1 --> S2[Run checks]
    S2 -->|checks fail| X[Run fails, no writes]
    S2 --> S3[Prepare changelog]
    S3 --> S4[Validate version]
    S4 --> S5[Create release plan + draft]
    S5 --> W[Waiting for you]
    W -->|suggest| S1
    W -->|download and finish| F[Completed, plan delivered]
    W -->|approve| G{Gate: status, expiry, plan hash, repo head}
    G -->|ok| P[Publish once, simulated]
    G -->|refused| W
    W -->|discard| R[Rejected]
```

Trust boundaries: the browser is untrusted and only ever receives JSON. Repository data, model replies
and your suggestions are untrusted until validated. Secrets (LLM keys, optional GitHub token, database
URL) exist only in the backend environment.

## Project structure

```
.
├── backend/                      FastAPI service (Python 3.13)
│   ├── app/
│   │   ├── main.py               app factory, middleware (CORS, size and rate limits, safe logging), /health
│   │   ├── config.py             settings from the environment
│   │   ├── api/v1.py             routes: runs, decisions, plan download, repository preview
│   │   ├── core/
│   │   │   ├── executor.py       durable executor: step loop, retries, resume, revisions, decisions
│   │   │   ├── steps.py          per step: LLM attempt, fact check, deterministic fallback
│   │   │   ├── approval.py       execution-time approval gate
│   │   │   ├── idempotency.py    deterministic side-effect keys
│   │   │   ├── retry.py          bounded retries with backoff
│   │   │   ├── faults.py         failure injection
│   │   │   ├── plan_export.py    the downloadable Markdown plan
│   │   │   └── errors.py         failure types
│   │   ├── adapters/
│   │   │   ├── llm.py            OpenAI-compatible client (Groq, OpenRouter), model chain, call cap
│   │   │   ├── github.py         adapter interface, mock ledger, demo fixtures, hybrid adapter
│   │   │   └── github_live.py    read-only public GitHub reader
│   │   ├── db/                   SQLAlchemy tables and the store
│   │   ├── models/               domain models and v1 API schemas
│   │   └── benchmark/            scenarios, naive baseline, runner, metrics
│   ├── scripts/benchmark.py      benchmark command
│   ├── benchmark/v1/             dataset, metric definitions, saved results
│   └── tests/                    unit, integration, regression, reliability, security
├── frontend/                     Next.js 16 (App Router, TypeScript, plain CSS)
│   ├── app/                      pages: home, run, benchmark
│   ├── components/               step rail, decision panel, repository sidebar, ledger, timeline
│   ├── lib/                      API client, types, formatting
│   ├── tests/                    Vitest unit and component tests
│   └── e2e/                      Playwright browser tests
├── supabase/
│   ├── config.toml               local Supabase stack configuration
│   └── migrations/               PostgreSQL schema (row-level security on every table)
├── .env.example                  every setting, with placeholders only
└── README.md
```

Database tables: `runs`, `steps`, `checkpoints`, `approvals`, `audit_events` (append-only, enforced by
a trigger), `idempotency_keys`, `suggestions`, `plan_revisions`, and `remote_effects` (the simulated
GitHub). Row-level security is on for all of them with no policies, and the Supabase API roles are
revoked, so only the backend's direct connection can read or write.

## Getting started

Prerequisites: Python 3.13 (a conda environment works well), Node 20 or newer, and optionally Docker
with the Supabase CLI for a local PostgreSQL.

### 1. Backend

```bash
conda create -n RecoveryOS python=3.13
conda activate RecoveryOS
pip install -r backend/requirements-dev.txt
cp .env.example .env            # Windows: copy .env.example .env
```

Edit `.env`: choose a provider, add its key, and set `RECOVERYOS_LLM_MODELS` to models your account
offers. With no key the app still runs and every step uses the deterministic fallback. Then:

```bash
cd backend
uvicorn app.main:app_factory --factory --reload --port 8000
```

Check `http://localhost:8000/health`. It reports the database, whether an LLM is configured, demo mode
and that real GitHub writes are off.

By default runs are kept in memory and disappear when the backend restarts. For durable runs, start
PostgreSQL and set `RECOVERYOS_DATABASE_URL`:

```bash
supabase start                  # local Postgres on port 54322, applies supabase/migrations
# in .env:
# RECOVERYOS_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

For any other PostgreSQL, apply the SQL files in `supabase/migrations/` in order before the first start.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

The site calls the API at `http://localhost:8000`. To use another address, put
`NEXT_PUBLIC_API_URL=...` in `frontend/.env.local` (Next.js does not read the root `.env`), and add the
site's origin to `RECOVERYOS_CORS_ORIGINS` in `.env`.

### 3. Try it

1. Open the home page, pick a demo repository, optionally tick a failure to inject, and start a run.
2. Watch the seven steps. Each shows who produced it and its checkpoint.
3. When the plan is ready, suggest a change, download the plan, or approve and publish (simulated).
4. To use a real repository, choose "Link a GitHub repository", enter `owner/name`, and press Preview.
   The repository must be public and have at least one commit.

## Configuration

All settings are environment variables (see [`.env.example`](.env.example) for the full annotated list).

| Variable | Purpose | Default |
|---|---|---|
| `RECOVERYOS_LLM_PROVIDER` | `groq`, `openrouter` or `none` | `none` |
| `GROQ_API_KEY`, `OPENROUTER_API_KEY` | Provider key (server-side only) | empty |
| `RECOVERYOS_LLM_MODELS` | Comma-separated models, tried in order | empty |
| `RECOVERYOS_LLM_MAX_OUTPUT_TOKENS` | Output cap per call. Reasoning models need room, 4000 is safe | `2000` |
| `RECOVERYOS_LLM_TIMEOUT_SECONDS`, `RECOVERYOS_LLM_MAX_CALLS_PER_RUN` | Call timeout and per-run call cap | `30`, `20` |
| `RECOVERYOS_DATABASE_URL` | PostgreSQL URL. Unset means in-memory | unset |
| `RECOVERYOS_CORS_ORIGINS` | Browser origins allowed to call the API | `http://localhost:3000` |
| `RECOVERYOS_DEMO_MODE` | Allows failure injection | `true` |
| `RECOVERYOS_ENABLE_GITHUB_LINK` | Allow linking public GitHub repositories | `true` |
| `GITHUB_TOKEN` | Optional read token for linked repositories (server-side only) | empty |
| `RECOVERYOS_ENABLE_REAL_GITHUB_WRITES` | Must stay `false`: real writes are not implemented | `false` |
| `RECOVERYOS_APPROVAL_TTL_MINUTES`, `RECOVERYOS_MAX_STEP_ATTEMPTS` | Approval lifetime, attempts per step | `60`, `3` |
| `RECOVERYOS_MAX_REQUEST_BYTES`, `RECOVERYOS_RATE_LIMIT_PER_MINUTE`, `RECOVERYOS_MAX_CONCURRENT_RUNS` | Request size, per-client rate and concurrency limits | `1000000`, `120`, `5` |
| `NEXT_PUBLIC_API_URL` | Public API address, set in `frontend/.env.local` | `http://localhost:8000` |

## API

Versioned under `/v1`. Errors use one envelope: `{"error": {"code": "...", "message": "..."}}`, with no
stack traces and no echoed input.

| Method and path | Purpose |
|---|---|
| `GET /health` | Database, LLM, mode and write-safety status |
| `GET /v1/repos` | Demo repositories and whether GitHub linking is on |
| `GET /v1/repos/{owner}/{name}/preview` | What the agent will read (version, tags, commits, checks) |
| `POST /v1/runs` | Start a run `{repo, faults}` (runs in the background) |
| `GET /v1/runs`, `GET /v1/runs/{id}` | List runs, run detail with steps, approval, ledger, revisions |
| `GET /v1/runs/{id}/timeline` | Append-only audit events |
| `POST /v1/runs/{id}/resume` | Continue an interrupted run from its last checkpoint |
| `POST /v1/runs/{id}/suggest` | Add a suggestion and rebuild the plan |
| `GET /v1/runs/{id}/plan` | Download the current plan as Markdown |
| `POST /v1/runs/{id}/finish` | Finish by implementing the plan yourself (nothing published) |
| `POST /v1/runs/{id}/approve`, `POST /v1/runs/{id}/reject` | Approve and publish (simulated), or discard |

Request bodies are size-limited and schema-validated, clients are rate-limited, and CORS allows only
the configured origins.

## Testing

Last full run on 2026-10-20, all passing:

| Layer | Command | Result |
|---|---|---|
| Backend unit, integration, regression, reliability, security | `cd backend && python -m pytest` | 159 passed |
| Same, including real PostgreSQL | set `RECOVERYOS_TEST_DATABASE_URL` first | included in the 159 |
| Lint, format | `ruff check .` and `ruff format --check .` | clean |
| Static security scan | `bandit -r app scripts` | no issues |
| Dependency audit | `pip-audit -r requirements.txt` | no known vulnerabilities |
| Frontend types | `cd frontend && npm run typecheck` | clean |
| Frontend unit and component | `npm test` | 63 passed |
| Browser (Chrome, real backend, production build) | `npm run e2e` | 26 passed, repeated runs stable |

The PostgreSQL tests run the real migrations and check, among other things, that the audit log refuses
updates, that duplicate idempotency keys are rejected by the database, and that the Supabase API roles
cannot read or write any table. The browser tests include automated accessibility scans (WCAG 2 A/AA,
2.1 and 2.2 AA) in light and dark, keyboard use, and no horizontal overflow at 375 and 320 pixels. The
browser tests use a scripted stand-in for the model and a fake GitHub (in `backend/tests/e2e_server.py`)
so they are deterministic; the product itself never uses a stand-in.

## Benchmark

The same 61 seeded failure scenarios run on RecoveryOS and on a plain baseline that keeps state in
memory, uses no idempotency keys, accepts side-effect replies without validation, and reads approval only
once. Both use the same adapter, step logic, model client, retry limit and faults. Scenarios cover a
crash or fault at every step, retry exhaustion, combined faults, a failing-checks repository, and
approval attacks (revoked, expired, repository moved, rejected, never granted). Every number is read
from the database and the simulated remote ledger after the run, not from what a system reports.

```bash
cd backend
python -m scripts.benchmark --save                 # uses the LLM if one is configured
python -m scripts.benchmark --llm off --save       # deterministic fallback only
python -m scripts.benchmark --repeats 3 --pause 1  # repeat scenarios (use with a live model)
```

Definitions, dataset construction and limits are in
[`backend/benchmark/v1/README.md`](backend/benchmark/v1/README.md). Saved results are in
`backend/benchmark/v1/results/`, and the site's Benchmark page shows them.

**Without an LLM** (every AI step used the deterministic fallback):

| Metric (61 scenarios, 51 recoverable) | RecoveryOS | Baseline |
|---|---|---|
| Recovery success rate | 100% | 72.6% |
| Duplicate side effects (scenarios / extra effects) | 0% / 0 | 24.6% / 21 |
| Steps wrongly marked complete | 0 | 4 |
| Approval bypasses (must be 0) | 0 | 4 |

**With a live LLM** (Groq, models tried in order `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`,
`openai/gpt-oss-120b`, 2026-10-19):

| Metric | RecoveryOS | Baseline |
|---|---|---|
| Recovery success rate | 100% | 72.6% |
| Extra side effects applied | 0 | 20 |
| Steps wrongly marked complete | 0 | 4 |
| Approval bypasses | 0 | 4 |
| Median time to resume | 1.28 s | 3.03 s |
| LLM calls | 308 | 392 |
| Tokens per completed workflow | 2,269 | 2,261 |
| Steps done by the LLM | 67.4% | 56.9% |
| Steps by model | gpt-oss-120b 91, gpt-oss-20b 68, qwen3.8-27b 35 | gpt-oss-120b 74, gpt-oss-20b 55, qwen3.8-27b 35 |
| Steps that fell back | 94, all rate limited | 124, all rate limited |

How to read this honestly:

- The baseline is deliberately plain. The numbers show what persistence, idempotency keys, reply
  validation and an execution-time approval check are worth, not that RecoveryOS beats other systems.
- The recovery numbers measure the workflow and match with and without a model. More than one model
  contributed to each live run, so the results cannot be credited to a single model, and model quality was
  not scored.
- A third to two fifths of the AI steps hit the provider's free-tier rate limit and used the fallback.
- The baseline's extra cost with a live model shows in calls (+27%) and time to resume (about 2.4 times
  slower), because it repeats model calls after every crash. Tokens per workflow are about equal.
- Scenarios and the remote are synthetic, timings come from one machine on in-memory SQLite, and one pass
  is one sample.

## Security and safety

- **No real writes.** Publishing and drafting go to a mock ledger. The write flag exists, must stay off,
  and is not implemented.
- **Approval is enforced in code.** Plan-hash binding, expiry, repository-head check and revocation are
  deterministic and re-checked at execution time. The benchmark requires zero approval bypasses.
- **Untrusted input.** Repository data, model replies and owner suggestions are validated against schemas,
  fenced when sent to a model, rendered as text (never HTML) in the interface, and never reach a shell
  command (the commands in the plan file use only the tag, a commit hex and the validated repository name).
- **Secrets stay server-side.** Keys and the optional GitHub token live only in the backend environment,
  never in the browser, run state or logs. Logs are structured and redact secrets.
- **GitHub access.** Fixed host (`api.github.com`), validated repository names, at most one redirect and
  only within that host, size and count caps, timeouts, and rate-limit handling. Linking can be switched
  off with `RECOVERYOS_ENABLE_GITHUB_LINK=false`.
- **Database.** Row-level security on every table, API roles revoked, parameterized queries only,
  versioned migrations, append-only audit log.
- **API hardening.** Request size and rate limits, concurrency cap, stable error codes, CORS limited to the
  configured origins.

## Known limitations

- **Not deployed.** It runs locally. Hosting is undecided.
- **Publishing is simulated.** The downloadable plan carries the commands for a real release.
- **Single process.** The executor guards a run inside one process. Several workers on one database would
  need leasing, which is not built. Rate limits are in memory and reset on restart.
- **Anonymous GitHub reads** are limited to about 60 per hour per server IP unless a token is set.
- **LLM variability and quotas.** Results vary between runs, and free tiers rate-limit. With no model, a
  suggestion is only recorded verbatim, because deterministic code cannot interpret free text.
- **Linked repositories** must be public and have commits. Only the head of the default branch and the
  latest 50 commits since the last semantic-version tag are read.
- **No authentication or accounts.** It is built as a demo, with synthetic data by default.
- **Accessibility** is checked automatically and by keyboard tests, but not with a real screen reader.
- **The interface design** is an original direction that has not been reviewed by users.

## Deployment

Planned: the frontend on Vercel and the backend on a host that can run a long-lived Python process, with a
hosted PostgreSQL for durable runs. Not done yet. What a deployment needs:

- Backend: Python 3.13, `pip install -r backend/requirements.txt`, start with
  `uvicorn app.main:app_factory --factory --host 0.0.0.0 --port $PORT`, all secrets as environment
  variables, `RECOVERYOS_DATABASE_URL` pointing at a database with the migrations applied,
  `RECOVERYOS_CORS_ORIGINS` set to the site's origin, and `RECOVERYOS_DEMO_MODE=false` for a public site
  (decide on `RECOVERYOS_ENABLE_GITHUB_LINK` as well).
- Frontend: `NEXT_PUBLIC_API_URL` set to the deployed API before building (it is read at build time).
- Verify `/health` and one complete run end to end before calling it deployed.

See also [`frontend/README.md`](frontend/README.md) for frontend-specific commands.

## License

[MIT](LICENSE).
