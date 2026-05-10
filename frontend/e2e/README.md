# e2e tests

Playwright drives WebKit + Chromium against the running dev server.

## Architecture

The e2e suite does not boot the backend or Vite. It assumes both are
running and reaches them at `BASE_URL` (default
`https://alpha.tail8bd569.ts.net:5443`).

Before each suite run, the global setup at `reset-db.ts` drops the
`app` schema in the dev database and re-runs Alembic, giving every
run a clean slate.

## One-time setup

```
cd frontend
npm install
npx playwright install --with-deps webkit chromium
```

The `--with-deps` flag uses `apt` to install the system libraries
WebKit and Chromium need (libgtk-4, libgraphene, libwoff, etc.).

## Running

With the dev server running on alpha:

```
npm run test:e2e                       # both browsers, headless
npm run test:e2e -- --project=webkit   # WebKit only
npm run test:e2e -- --headed           # see the browser, useful when debugging
npm run test:e2e -- --debug            # Playwright Inspector, step through
```

The suite reads `DATABASE_URL` from the environment or from
`backend/.env`; the global setup uses it to wipe the `app` schema and
re-run Alembic between runs.

## Configuration

Environment variables consumed by the suite:

- `BASE_URL` — where the dev server is. Default
  `https://alpha.tail8bd569.ts.net:5443`.
- `DATABASE_URL` — the dev database connection string. Used by the
  global setup. Falls back to whatever Alembic reads via `alpha.settings`.
- `BACKEND_DIR` — where to invoke `uv run alembic` from. Default
  `../backend` (relative to `frontend/`).
