# RecoveryOS frontend

Next.js (App Router, TypeScript) interface for the RecoveryOS API. No UI library; styles are plain CSS
from design tokens in `app/globals.css`.

Screens:

- `/` explains the product, starts a run on a demo repository or a linked public GitHub repository
  (read-only; publishing is always simulated), and lists runs. A sidebar shows what the agent will read:
  version, tags, commits since the last tag and CI checks.
- `/runs/[id]` shows the seven workflow steps with a checkpoint on each, who produced every result
  (`LLM`, `Fallback` with the reason, `Human`, `System`), the remote ledger of what the mock GitHub really
  did, the checkpoints and the audit timeline. It polls while a run is active. When the plan is ready you
  choose: suggest a change (the agent rebuilds and re-checks everything), download the plan and finish
  (you implement it yourself), approve and publish (simulated), or discard.
- `/benchmark` shows the stored benchmark result (copied from `backend/benchmark/v1/results` at build time).

## Run locally

Start the API first (see the backend), then:

```
cd frontend
npm install
echo NEXT_PUBLIC_API_URL=http://localhost:8000 > .env.local   # only if the API is elsewhere
npm run dev                                                    # http://localhost:3000
```

Next.js reads `frontend/.env.local`, not the root `.env` (that one is for the backend; `.env.*` is git-ignored).
`NEXT_PUBLIC_API_URL` is the only variable the browser sees. It is the public API address, never a secret.
The API must list the site origin in `RECOVERYOS_CORS_ORIGINS`.

## Checks

```
npm run typecheck
npm test            # unit and component tests (Vitest)
npm run e2e         # browser tests (Playwright, installed Google Chrome, real backend)
npm run build
```

`npm run e2e` starts `backend/tests/e2e_server.py` (in-memory database and a scripted test-only LLM client
so the LLM label paths can be exercised) and a production build of this site. The scripted client is a
test fixture and is never used by the product.

## Deploy

Set `NEXT_PUBLIC_API_URL` to the deployed API in the host's environment before building. Hosting is
undecided; Vercel is the planned target for this folder.
