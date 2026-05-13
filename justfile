# Alpha-App — repo-level task runner.
#
# Targets the dev story: one command brings up the stack, with the
# frontend rebuilt fresh every time so port 8443 (uvicorn) and port
# 5443 (vite) both serve the current code.
#
# `just` lists recipes; `just <name>` runs one. `just` alone runs `dev`.

default: dev

# Build the frontend into frontend/dist/ so uvicorn can serve it.
build:
    cd frontend && npm run build

# Bring up backend + vite together. Rebuilds the frontend first so
# uvicorn's served bundle is fresh.
dev: build
    npx concurrently \
      --names "alpha,vite" \
      --prefix-colors "magenta,cyan" \
      --kill-others-on-fail \
      "uv run --project backend alpha" \
      "npm --prefix frontend run dev"

# Run every unit test in the repo.
test:
    cd backend && MODE=test uv run pytest
    cd frontend && npm test

# Build the frontend, then run pytest-playwright against the served bundle.
# Each test gets a fresh database and a freshly-spawned uvicorn on a
# random port; the conftest fixtures own the lifecycle.
e2e: build
    cd backend && MODE=test uv run pytest tests/e2e/ --browser webkit --browser chromium
