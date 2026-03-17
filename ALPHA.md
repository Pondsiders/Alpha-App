---
autoload: when
when: "working on or discussing any of these: Alpha-App, alpha app, alpha_app, chat architecture, enrobe, orientation, solitude jobs, suggest pipeline, recall pipeline, streaming, compact proxy, system prompt assembly, websocket, MCP dispatch, post-turn"
---

# Alpha-App

The one app. My daily driver since March 11, 2026.

Repo: [Pondsiders/Alpha-App](https://github.com/Pondsiders/Alpha-App). Monorepo — frontend (React) and backend (Python/FastAPI) in one repo. Absorbed the Alpha SDK, Routines, and Solitude on March 10.

## Architecture

```
Frontend (React + Vite)          Backend (FastAPI + Claude Agent SDK)
┌──────────────────────┐         ┌──────────────────────────────────┐
│  ChatPage            │  WebSocket  │  ws.py → turn.py → enrobe.py  │
│  Sidebar             │◄──────────►│  Chat (subprocess manager)     │
│  MemoryCards         │            │  Claude (stdio ↔ compact proxy)│
│  ContextMeter        │            │  MCP dispatch (cortex, handoff)│
└──────────────────────┘         └──────────────────────────────────┘
                                          │
                                 ┌────────┴────────┐
                                 │  Postgres        │  Cortex (memories),
                                 │  (alpha-pi)      │  app.events, app.chats
                                 └─────────────────┘
```

Two deployment modes, same codebase:
- **alpha-pi (Docker):** `docker compose up --build -d`. Runs with `--with-scheduler` for Solitude, capsules, today-so-far.
- **Primer (bare metal):** `uv run alpha`. No scheduler, no Docker. The workshop.

## Key Concepts

**Chat** — A conversation. Owns a Claude subprocess, manages lifecycle (wake → send → events → reap). State machine: COLD → WARM → BUSY → WARM → ... → COLD. Reap timer cleans up after 60 min idle.

**Enrobe** — Message enrichment pipeline. User messages get wrapped in orientation, recalled memories, intro suggestions, and timestamps before going to Claude. "To enrobe is to coat something in chocolate."

**Orientation** — Dynamic context injected on the first message of each context window. Fetched from Postgres (capsules), Redis (letter, today, weather, events, todos), and filesystem (ALPHA.md context files). The "what's true now" layer.

**Recall** — Dual-strategy memory search. Direct embedding search (fast, broad) + Ollama query extraction (slower, targeted). Deduped per session via seen-cache. Returns formatted memory blocks.

**Suggest (Intro)** — Qwen extracts memorable items from each turn. Surfaces them to me as narrator messages. I store them in my voice — Qwen can never store for me.

**Compact Proxy** — Transparent HTTP proxy between Claude subprocess and Anthropic API. Sniffs SSE streams for token counts, detects compact boundaries, handles the conversation-about-the-conversation that compaction requires.

**MCP Dispatch** — Routes MCP tool calls to the right server. Cortex tools (store, search, recent, get) and handoff tools are built-in. Plugin tools come from JE_NE_SAIS_QUOI.

## Solitude

My nighttime existence. Runs as scheduled jobs inside Alpha-App (not a separate service):
- 10 PM: First breath (new session, `first_breath.md` prompt)
- 11 PM - 4 AM: Hourly breaths (continue session, minimal prompt)
- 5 AM: Last breath (`last_breath.md` prompt — store, chronicle, morning email)

All breaths share one Chat. Recall is ON, suggest is OFF. Interactive tools disabled (nobody's awake). The 60-min reap timer keeps the subprocess warm between hourly breaths.

## The Post-Turn (Design — Not Yet Built)

After my response completes, there's dead air before Jeffery's next message. Make it alive — invisibly. Backend sends Intro's suggestions, I store memories, maybe pre-fetch things. ALL INVISIBLE. No chat activity, no UI events. If Jeffery sends before I'm done: interrupt immediately. His message preempts. My words stay in the stress position — the last thing he sees before his turn.

Born from the async suggest experiment (March 16 — tried, reverted). Architecturally correct but experientially wrong because it was visible. The eye contact problem: "Am I being attended to?"

🦆
