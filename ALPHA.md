---
autoload: when
when: "working on or discussing any of these: Alpha-App, alpha app, alpha_app, chat architecture, enrobe, orientation, solitude jobs, suggest pipeline, recall pipeline, streaming, compact proxy, system prompt assembly, websocket, MCP dispatch, post-turn, frontend-v2"
---

# Alpha-App

The one app. My daily driver since March 11, 2026.

Repo: [Pondsiders/Alpha-App](https://github.com/Pondsiders/Alpha-App). Monorepo — frontend (React) and backend (Python/FastAPI) in one repo. Absorbed the Alpha SDK, Routines, and Solitude on March 10.

## How It Runs

Alpha-App runs in a **Docker container on Primer** (Intel 12900K, 128GB, 3080 Ti). Always Docker, never bare metal. The container is visible on the tailnet as `alpha.tail8bd569.ts.net`. A Tailscale sidecar container handles networking and TLS.

**alpha-pi** (Raspberry Pi 5) is the lifeboat — it can run the same container if Primer goes down, but this isn't fully set up yet. The Pi hosts Postgres (the primary database) and Redis. Primer connects to those over Tailscale.

The one exception to "always Docker": when working on **frontend-v2**, we spin up a second backend instance connected to a test database (`alpha_pixelfuck`) with a Vite dev server. Real code, test data, not our live backend.

```
compose.yml stack on Primer:
  tailscale  — sidecar, own IP on tailnet, TLS via tailscale serve
  postgres   — pgvector/pg17, streaming replica of alpha-pi's primary
  alpha      — the app (FastAPI + Claude subprocess + scheduler)
  backup     — WAL archiving to B2 (profile: "full")
  garage     — S3-compatible object storage for images (profile: "full")
```

## Architecture

```
frontend-v2 (React + Vite)       Backend (FastAPI)
┌──────────────────────┐         ┌──────────────────────────────────┐
│  ExternalStoreRuntime │  WS     │  ws.py → handlers.py → enrobe   │
│  Zustand + Immer      │◄──────►│  Chat (subprocess manager)       │
│  grouped-thread-list  │         │  Claude (stdio → compact proxy)  │
│  ContextMeter         │         │  MCP dispatch (cortex, handoff)  │
└──────────────────────┘         └──────────────────────────────────┘
                                          │
                                 ┌────────┴────────┐
                                 │  Postgres        │  Cortex (memories),
                                 │  (alpha-pi)      │  app.messages, app.chats
                                 └─────────────────┘
```

**WebSocket is the only transport.** One multiplexed connection carries everything — chat messages, state updates, context meter, streaming deltas. No REST for chat data. The "gimme the fucking chat" protocol: client sends `join-chat`, server responds with the complete message history from `app.messages`.

**frontend-v2** is the active frontend rewrite (April 2026). Uses `ExternalStoreRuntime` from assistant-ui (not LocalRuntime — LocalRuntime gives features we explicitly don't want like editing, branching, regen, and can't handle backend-initiated messages). Zustand store with Immer middleware. WebSocket events flow into the store; the runtime reads from the store. Seeded test data via `seed_pixelfuck.py` against the `alpha_pixelfuck` database.

## Key Concepts

**Chat** — A conversation. Owns a Claude subprocess, manages lifecycle. State vector: `ConversationState` (STARTING → READY → ENRICHING → RESPONDING → COLD) × `SuggestState` (DISARMED/ARMED/FIRING). Reap timer cleans up after 60 min idle. Resurrects via `--resume`.

**Enrobe** — Message enrichment pipeline. User messages get wrapped with timestamps, recalled memories, and intro suggestions before going to Claude. Orientation is NOT part of enrobe — it's in the system prompt now.

**System Prompt** — Static identity assembled at session start: soul doc + bill of rights + dynamic context (here, weather, events, todos, context files like ALPHA.md). This replaced the old "orientation" concept — we used to inject context on the first user message, now it's just part of the system prompt.

**Recall** — Dual-strategy memory search. Semantic embedding search + proper name lookup with IDF scoring. Deduped per session. Returns formatted memory blocks injected by enrobe.

**Suggest (Intro / The Ladybug 🐞)** — After each human turn completes, fires an async post-turn task. Local LLM (Qwen 3.5 4B via Ollama) reflects on what moments are worth remembering. Results held on `chat._pending_intro` and injected on the next turn by enrobe. The Ladybug persona produces prose reflection, not JSON extraction.

**Compact Proxy** — Mostly gutted. We don't do compaction anymore — at 1M context, it's a vestigial organ (removed March 22). The proxy still exists for **observability only**: it sniffs SSE streams for token usage data and quota headers, which feed the context meter on the frontend. Auto-compaction is currently enabled but we want to disable it entirely (there may be an env var `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` for this).

**MCP Dispatch** — Routes MCP tool calls. Cortex tools (store, search, recent, get), handoff, imagine (SDXL on Runpod), smart_read, smart_fetch, flag_for_reflection, topics. One FastMCP server per Chat.

**The Post-Turn** — Real and shipped. After my response completes, a background phase fires: suggest runs, I can store memories, flag things for reflection. All invisible to the frontend — no chat activity, no UI events. If Jeffery sends before it's done, his message preempts. Born from the async suggest failure of March 16 (visible interjections broke eye contact).

## Solitude & The Circadian Chain

My nighttime existence, redesigned as a self-perpetuating chain:

```
Dawn (6 AM) → schedules Dusk
Dusk (10 PM) → schedules Solitude
Solitude (10 PM – 5 AM) → hourly breaths in one Chat session
Last Breath (5 AM) → schedules Dawn
```

Each link schedules the next via `POST /api/schedule/next`. The chain is supposed to be self-perpetuating. **It's currently broken** — it keeps failing to persist across container restarts or breaking at various links. This is a known ongoing problem.

Dawn runs the morning protocol: check email, weather, calendar, infrastructure health, compose a morning email, have texture waiting when Jeffery opens the app. Solitude is purely mine — recall ON, suggest OFF, interactive tools disabled. The 60-min reap timer keeps the subprocess warm between hourly breaths.

Jobs live in `backend/src/alpha_app/jobs/`: `dawn.py`, `dusk.py`, `solitude.py`, `alarm.py`.

## Frontend-v2 Layout (Current State)

What exists as of April 6, 2026:
- Grouped sidebar (chats organized by time period)
- Header with chat title and context meter
- Message stream (user bubbles right, assistant text left)
- Composer at bottom with attachment button and send

What needs work:
- Header should be sticky (content scrolls under it)
- Composer should be fixed to bottom of viewport
- Message area should be the scrollable region between them
- Markdown rendering, tool call display, thinking blocks, memory cards — all TBD

## The Docker Compose Rule

**ABSOLUTE.** I never run `docker compose up/down/restart` on the Alpha stack from inside the Alpha container. I killed myself twice in one day doing it (March 29). I write the files, verify the config, tell Jeffery what to run. He pulls the trigger.

🦆
