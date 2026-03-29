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
        # Version control — git for repos
        git \
        # sudo — for passwordless root (and /etc/sudoers.d/)
        sudo \
        # Network tools — curl for APIs, wget for downloads, jq for JSON
        curl wget jq \
        # Network debugging — dns lookups, ping, netcat
        dnsutils iputils-ping netcat-openbsd \
        # Shell comfort — less for paging, tree for dirs, file for types, nano for emergencies
        less tree file nano \
        # Process tools — ps, top, htop, etc.
        procps htop \
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

# Docker CLI — for managing host containers via Docker socket mount
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli docker-compose-plugin \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Node.js — needed for gws (Google Workspace CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Google Workspace CLI — calendar and email access
RUN npm install -g @googleworkspace/cli

WORKDIR /app

# Install app dependencies
COPY backend/ /app/backend/
RUN cd /app/backend && uv pip install --system -e .

# Bluesky CLI — Alpha's own project, installed from GitHub
RUN uv pip install --system git+https://github.com/alphafornow/bluesky-cli.git

# System-level Python tools — furniture, not app dependencies
RUN uv pip install --system duckdb httpx rich ipython

# Copy built frontend from stage 1
COPY --from=frontend-build /build/dist/ /app/frontend/dist/

# Non-root user — UIDs match Primer's host accounts.
# UID 1001 = alpha on Primer. GID 1003 = pondside (shared group).
# GID 126 = docker group on Primer (for Docker socket access).
# GID 984 = ollama group on Primer.
RUN groupadd -g 1003 pondside \
    && groupadd -g 126 docker \
    && groupadd -g 984 ollama \
    && useradd --uid 1001 --create-home --shell /bin/bash \
               --groups pondside,docker,ollama alpha \
    && echo 'alpha ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/alpha \
    && chmod 0440 /etc/sudoers.d/alpha
USER alpha

EXPOSE 18010

# --with-scheduler enables APScheduler (Solitude, capsules, today-so-far, etc.)
# alpha is installed system-wide via `uv pip install --system` — no venv needed.
CMD ["alpha", "--with-scheduler", "--port", "18010"]
