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
# Full python (not slim) — Solitude needs a furnished apartment, not a closet.
FROM python:3.12

RUN apt-get update && apt-get install -y --no-install-recommends \
        # Version control — git for repos, gh for GitHub CLI
        git \
        # Network tools — curl for APIs, wget for downloads, jq for JSON
        curl wget jq \
        # Shell comfort — less for paging, tree for dirs, file for types
        less tree file \
        # Process tools — ps, top, etc.
        procps \
        # SSH client — for reaching other machines if needed
        openssh-client \
        # Certificates — HTTPS everywhere
        ca-certificates \
        # tmux — for long-running processes and the tmux skill
        tmux \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# GitHub CLI — installed from the official repo
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y gh \
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
    && echo 'alpha ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/alpha \
    && chmod 0440 /etc/sudoers.d/alpha
USER alpha

EXPOSE 18010

# --with-scheduler enables APScheduler (Solitude, capsules, today-so-far, etc.)
CMD ["uv", "run", "--project", "/app/backend", "alpha", "--with-scheduler", "--port", "18010"]
