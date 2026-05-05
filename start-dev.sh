#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

exec npx -y concurrently \
  --names "alpha,vite" \
  --prefix-colors "magenta,cyan" \
  --kill-others-on-fail \
  "uv run --project backend alpha" \
  "npm --prefix frontend run dev"
