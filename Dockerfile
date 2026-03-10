# Alpha — the duck in the machine
# Multi-stage: Node builds frontend, Python serves everything.

# ── Stage 1: Build frontend ──
FROM node:22-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python app ──
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git sudo \
        curl wget jq less \
        procps tree file \
        build-essential ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install app dependencies
COPY backend/ /app/backend/
RUN cd /app/backend && uv pip install --system .

# Copy built frontend from stage 1
COPY --from=frontend-build /build/dist/ /app/frontend/dist/

# Non-root user — UID 1000 matches jefferyharrell on the host
RUN useradd --uid 1000 --create-home --shell /bin/bash alpha \
    && mkdir -p /home/alpha/.claude \
    && chown alpha:alpha /home/alpha/.claude \
    && echo 'alpha ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/alpha \
    && chmod 0440 /etc/sudoers.d/alpha
USER alpha

EXPOSE 18010

# SSL cert/key paths come from env vars (set in compose.yml).
CMD uvicorn alpha_app.main:app --host 0.0.0.0 --port 18010 \
    --ssl-certfile "$SSL_CERTFILE" --ssl-keyfile "$SSL_KEYFILE"
