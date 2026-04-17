---
autoload: when
when: "working on or discussing any of these: Alpha-App, Alpha frontend, Alpha backend, chat WebSocket protocol, Claude subprocess management, the circadian chain (Dawn/Dusk/Solitude), memory recall and Cortex, system prompt assembly, frontend streaming, sidebar thread list, assistant-ui, context ring, deploy, alpha-app docker"
---

# Alpha-App

A conversational AI application where "Alpha" (a Claude-based agent) lives inside a FastAPI backend and communicates with a React frontend over WebSockets. The backend manages Claude subprocess lifecycles, a memory system (Cortex) backed by pgvector, and a circadian job chain (Dawn/Dusk/Solitude). The frontend renders streaming chat using assistant-ui primitives.

## Commands

### Backend

```bash
cd backend && uv sync                    # Install dependencies
uv run alpha --port 18010                # Run server (bare metal)
uv run alpha --with-scheduler --port 18010  # Run with circadian scheduler
uv run pytest                            # Run tests
uv run job dawn                          # Manually trigger Dawn job
```

The `alpha` CLI entry point is defined in `pyproject.toml` as `alpha_app.main:run`. Additional CLIs: `job` (circadian jobs), `frotz` (interactive fiction).

### Frontend (frontend-v2/)

```bash
cd frontend-v2 && npm install
npm run dev          # Vite dev server (HTTPS, proxies /api and /ws to backend)
npm run build        # Type-check + production build
npm run lint         # ESLint
```

### Pixelfuck (development mode)

```bash
# Terminal 1: MockAnthropic (fake API, deterministic, zero usage)
cd backend-tests
uvicorn mock_anthropic:app --port 18098 --reload

# Terminal 2: Pixelfuck backend + Vite frontend
ANTHROPIC_BASE_URL=http://127.0.0.1:18098 ./pixelfuck.sh
```

The `§`-prefix protocol controls MockAnthropic responses. Type `§help` in the chat for the full command reference. Key commands: `§error` (permanent 500), `§error_once` (transient 500), `§rate_limit` (429), `§slow` (200ms delays), `§long` (~2000 tokens), `§markdown` (full Markdown parade), `§echo:text` (echo). Normal messages return lorem ipsum at ~100 chars/sec.

MockAnthropic is fully instrumented with Logfire (`service_name="mock-anthropic"`). Every request logs the conversation structure, extracted command, and response type.

To run against real Anthropic instead, just `./pixelfuck.sh` without `ANTHROPIC_BASE_URL`.

### Docker

The `compose.yml` defines services: `tailscale` (networking), `postgres` (pgvector/pg17), `garage` (S3-compatible object storage), `alpha` (the app), `backup` (B2 WAL archival). The `alpha` and `backup` services are in the `full` profile. The app container exposes port 18010 and uses Tailscale's network stack.

**Do not run `docker compose up/down/restart` from inside the Alpha container** -- it kills the container you are in.

## Architecture

### Request lifecycle (chat)

```
Browser → WebSocket /ws → routes/ws.py (parse command)
  → Chat.send() → Claude subprocess (claude-agent-sdk)
  → streaming events → routes/broadcast.py → all connected WebSockets
  → frontend useAlphaWebSocket → Zustand store → assistant-ui render
```

### Backend (`backend/src/alpha_app/`)

| Module | Role |
|---|---|
| `main.py` | FastAPI app, lifespan (pool init, scheduler start), REST routes for threads/theme |
| `chat.py` | Chat kernel -- Claude subprocess lifecycle, state machine (STARTING/READY/ENRICHING/RESPONDING/COLD + suggest states) |
| `claude.py` | Claude class wrapping `ClaudeSDKClient` from claude-agent-sdk. Handles start/stop/resume, event mapping, idle reap |
| `protocol.py` | Pydantic command/event models for the WebSocket wire format |
| `system_prompt.py` | Assembles system prompt from identity documents (soul, bill of rights, orientation) |
| `orientation.py` | Dynamic context: capsules, letter, today, weather, events, todos |
| `db.py` | asyncpg connection pool, chat/message persistence |
| `models.py` | Message data models (UserMessage, AssistantMessage, SystemMessage) |
| `constants.py` | Model name, context window, identity directory (JE_NE_SAIS_QUOI) |
| `sources.py` | Source resolution for enrichment |
| `topics.py` | TopicRegistry -- scans topic files for MCP tools and enrobe |
| `reflection.py` | Post-turn reflection |
| `proxy.py` | Proxy forwarding |
| `images.py` | Image handling |
| `clock.py` | PSOResponse and time utilities |
| `strings.py` | String constants (e.g. BUZZ_NARRATION) |
| `mock_claude.py` | MockClaude for testing |
| `demo.py` | Demo payload |
| `frotz.py` | Interactive fiction CLI |
| `scheduler.py` | APScheduler setup, `sync_from_db` |
| **`routes/`** | |
| `routes/ws.py` | WebSocket endpoint, command dispatch |
| `routes/broadcast.py` | Fan-out events to all connected WebSockets |
| `routes/handlers.py` | Command handlers (create-chat, interrupt) |
| `routes/enrobe.py` | Enrichment pipeline (wraps user messages with context) |
| `routes/schedule_api.py` | REST API for schedule, solitude, context |
| `routes/spans.py` | Logfire span helpers for prompt preview |
| **`jobs/`** | |
| `jobs/dawn.py` | Morning job: create today's chat, send Dawn prompt, schedule Dusk |
| `jobs/dusk.py` | Evening job: send Dusk prompt, launch Solitude |
| `jobs/solitude.py` | Nighttime autonomous breaths on a local APScheduler |
| `jobs/alarm.py` | Alarm job |
| **`memories/`** | |
| `memories/cortex.py` | Core memory CRUD -- store, search (vector + text), get, forget |
| `memories/db.py` | Postgres operations for memories (pgvector) |
| `memories/embeddings.py` | Embedding generation for memory storage and queries |
| `memories/recall.py` | High-level recall pipeline |
| `memories/fetch.py` | Memory fetch utilities |
| `memories/reading.py` | Reading/ingestion pipeline |
| `memories/dream.py` | Dream/consolidation |
| `memories/vision.py` | Vision/image memory |
| `memories/images.py` | Image processing for memories |
| `memories/garage.py` | S3-compatible storage via Garage |
| **`tools/`** | |
| `tools/alpha.py` | Alpha's MCP tools |
| `tools/cortex.py` | Cortex MCP tools (memory operations) |
| `tools/handoff.py` | Handoff tool |

### Frontend (`frontend-v2/src/`)

See `frontend-v2/CLAUDE.md` for detailed frontend architecture. Summary:

```
WebSocket → useAlphaWebSocket → Zustand store → RuntimeProvider (convertMessage) → assistant-ui
```

| File | Role |
|---|---|
| `store.ts` | Zustand + Immer store. All chat state. Selectors for current chat, chat list |
| `lib/protocol.ts` | Zod v4 discriminated unions for commands and server events |
| `lib/useWebSocket.ts` | Generic WebSocket transport with exponential backoff reconnect |
| `hooks/useAlphaWebSocket.ts` | App-specific event routing into store actions |
| `RuntimeProvider.tsx` | Converts store messages to assistant-ui `ThreadMessageLike` via `useExternalStoreRuntime` |
| `App.tsx` | Layout: shadcn SidebarProvider, floating context ring, sidebar trigger |
| `components/assistant-ui/thread.tsx` | Main chat view |
| `components/assistant-ui/markdown-text.tsx` | Streaming Markdown via Streamdown |
| `components/assistant-ui/tool-fallback.tsx` | Generic tool-call rendering |
| `components/grouped-thread-list.tsx` | Sidebar thread list grouped by circadian day (6 AM LA boundary) |
| `components/ContextRing.tsx` / `ContextMeter.tsx` | Context usage visualization |

### Database

PostgreSQL 17 with pgvector. Two domains: chat persistence (chats + messages tables) and Cortex (memories with vector embeddings). WAL archiving to B2 via the backup service.

### Infrastructure

All services share Tailscale's network via `network_mode: service:tailscale`. Tailscale Serve handles TLS termination. Garage provides S3-compatible object storage for images/files.

## Conventions

- **Pendulum, not datetime.** All backend time handling uses Pendulum.
- **Logfire for observability.** No print statements or logging module. `LOGFIRE_MIN_LEVEL` env var controls verbosity (info/debug/trace).
- **`uv`, not `pip`.** `uv sync` for setup, `uv run` for execution, `uv pip install --system` inside containers.
- **Claude Agent SDK.** The `Claude` class wraps `ClaudeSDKClient` from `claude-agent-sdk`. It is not a raw Anthropic API client.
- **Backend message shapes in the store.** The frontend stores backend message formats verbatim. Conversion to assistant-ui types happens only in `RuntimeProvider.tsx`.
- **Wire protocol is asymmetric.** Client sends commands (`{ command: "..." }`), server sends events (`{ event: "..." }`). Pydantic on backend, Zod on frontend.
- **JE_NE_SAIS_QUOI** is the identity directory constant (points to Alpha's prompts/identity files).
- **Circadian day boundary** is 6 AM Los Angeles time, used for both the Dawn/Dusk chain and frontend thread grouping.
- **Path alias** `@/*` maps to `src/*` in the frontend.
- **No `tailwind.config.js`.** Tailwind v4 with `@theme` inline blocks in CSS. Theme tokens in `src/themes/alpha.css`.
- The old `frontend/` directory is legacy (v1). Active frontend work happens in `frontend-v2/`.
