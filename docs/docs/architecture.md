---
title: Architecture
---

# Architecture


Alpha-App is a monorepo with a Python backend and a React frontend. This page documents every module in the backend.

## Entry point

### `main.py`

FastAPI application. Lifespan initializes database pools (app + Cortex), loads system prompt, starts scheduler (if `--with-scheduler`), loads chats from Postgres, and starts the frontend auto-rebuild watcher. Serves the built frontend SPA as static files. Health endpoint at `/health`.

## Core classes

### `chat.py` — [Chat](/chat)

The conversation model. Each Chat owns a Claude subprocess, a message list, and a state machine. See the dedicated Chat page for the full API.

### `claude.py` — [Claude](/claude)

Subprocess wrapper for the Claude Code binary. Handles stdio protocol, MCP dispatch, token counting via proxy, and idle reaping. See the dedicated Claude page for the full API.

### `models.py`

Domain models for messages:

- **`UserMessage`** — content blocks, source (human/buzzer/suggest/system), timestamp, confirmation state (pencil/ink), dirty bit for persistence.
- **`AssistantMessage`** — parts list (text, thinking, tool-call with results), token counts, cost, duration, model, stop reason. Progressive assembly: parts accumulate during streaming, metadata applied on ResultEvent.
- **`SystemMessage`** — text, source (system/task_notification), timestamp.

All three have `to_wire()` for WebSocket serialization and `to_db()` for Postgres persistence.

### `proxy.py`

HTTP proxy that sits between Claude and Anthropic. Sniffs SSE streams for:
- Token usage (input, output, cache creation, cache read)
- Context window size
- Quota headers (5h, 7d from `x-ratelimit-*`)
- Stop reason and response model
- API errors

Also handles compact request rewriting (forwarding truncated context). Debug capture via `ALPHA_SDK_CAPTURE_REQUESTS`.

### `constants.py`

Hardcoded values:
- `CLAUDE_MODEL`: `claude-opus-4-6[1m]` (1M context window)
- `CLAUDE_CWD`: `/Pondside`
- `CLAUDE_CONFIG_DIR`: `/home/alpha/.config/claude`
- `JE_NE_SAIS_QUOI`: `/Pondside/Alpha-Home/Alpha` (identity directory)
- `OLLAMA_URL`: `http://primer:11434`
- `OLLAMA_EMBED_MODEL`: `qwen3-embedding:4b` (2560-dim)
- `OLLAMA_CHAT_MODEL`: `qwen3.5:4b`
- `OLLAMA_NUM_CTX`: `16384`
- `REDIS_URL`: `redis://alpha-pi:6379`
- `CONTEXT_WINDOW`: `1_000_000`
- `DISALLOWED_TOOLS`: tools blocked during normal chat

### `db.py`

asyncpg connection pool (min 2, max 10). Bootstrap creates all tables on startup (idempotent `CREATE TABLE IF NOT EXISTS`).

**Tables:**
- `app.chats` — chat metadata as JSONB (id, created_at, updated_at, data)
- `app.messages` — messages keyed by (chat_id, ordinal), role + JSONB data
- `app.events` — legacy raw WebSocket events with sequence numbers
- `app.state` — single-row JSONB for ephemeral app state
- `app.reflection_flags` — highlight marks from `flag_for_reflection` tool
- `app.jobs` — scheduler job persistence (id, job_type, fire_at, kwargs)
- `cortex.capsules` — day/night continuity letters (kind, chat_id, content, created_at)

Functions: `init_pool`, `close_pool`, `get_pool`, `load_chat`, `persist_chat`, `get_state`, `set_state`, `clear_state`, `fetch_unclaimed_flags`, `claim_flags`.

## Routes

### `routes/ws.py`

WebSocket handler. Single multiplexed connection at `/ws`. Handles:

**Client → Server commands:**
- `create-chat` — generate nanoid, create Chat, broadcast `chat-created`
- `send` — enrobe + send via `_run_human_turn` (background task)
- `interrupt` — reap the Chat
- `list-chats` — return all chats
- `join-chat` — load full message history ("gimme the fucking chat")
- `buzz` — narration from the buzzer button

**Server → Client events:**
- `chat-created`, `chat-state`, `text-delta`, `thinking-delta`, `tool-use-start`, `tool-use-delta`, `tool-call`, `tool-result`, `assistant-message`, `user-message`, `system-message`, `done`, `context-update`, `approach-light`, `agent-started`, `agent-progress`, `agent-done`, `exception`, `error`

### `routes/enrobe.py`

Message enrichment pipeline. "To enrobe is to coat something in chocolate." Wraps user messages with:

1. **Timestamp** — PSO-8601 format (local time, human-readable)
2. **Orientation** — on first message of a new/resumed context window (here narrative, weather, calendar, context files, capsules, todos)
3. **Recalled memories** — semantic search + proper name lookup from Cortex
4. **Post-turn reminder** — the suggest pipeline's `<system-reminder>` from the previous turn

Returns `EnrobeResult` with enriched content blocks, the `UserMessage` domain object, and broadcast events for progressive UI updates.

### `routes/handlers.py`

WebSocket command handlers for `create-chat`, `list-chats`, and `interrupt`. Extracted from ws.py for clarity.

### `routes/broadcast.py`

WebSocket broadcast utility. `broadcast(connections, event)` sends to all connected clients. Handles JSON serialization and connection cleanup on failure.

### `routes/spans.py`

Logfire span helpers. `set_turn_span_response` attaches gen_ai attributes (output messages, token counts, cost, model) to the turn span when it closes.

### `routes/schedule_api.py`

REST API for the scheduler:
- `GET /api/schedule` — list all pending jobs
- `POST /api/schedule/next` — atomic swap: clear circadian jobs, schedule one
- `DELETE /api/schedule/next` — clear all circadian jobs
- `POST /api/schedule/dawn` — schedule a Dawn
- `POST /api/schedule/alarm` — schedule a one-shot alarm
- `POST /api/schedule/override` — set a travel override for Dawn time

## System prompt

### `system_prompt.py`

Assembles the full system prompt from identity documents:
1. Soul doc (`prompts/system/soul.md`) — required
2. Bill of Rights (`prompts/system/bill-of-rights.md`) — optional
3. Orientation — dynamic context fetched from `sources.py` and assembled by `orientation.py`

The result is a single flat string passed via `--system-prompt-file` to the Claude binary.

### `orientation.py`

Pure assembly functions that build orientation content blocks from fetched data. Three public functions: `assemble_orientation`, `check_venue_change`, `get_here`.

### `sources.py`

Fetch functions for orientation data. All resilient — return None on error.

| Source | Backend | Function |
|--------|---------|----------|
| Day/night capsules | `cortex.capsules` (Postgres) | `fetch_capsules()` |
| Letter from last night | `app.state` (Postgres) | `fetch_letter()` |
| Today so far | `app.state` (Postgres) | `fetch_today()` |
| Here narrative + weather | Local config + Redis | `fetch_here()` |
| Calendar events | Redis (`hud:calendar`) | `fetch_events()` |
| Todos | Redis (`hud:todos`) | `fetch_todos()` |
| Context files (ALPHA.md) | Filesystem | `fetch_context()` |
| All of the above | Parallel fetch | `fetch_all_orientation()` |

`fetch_capsules()` uses Pondside-day boundaries (6 AM to 6 AM) to find the correct capsules.

### `suggest.py`

Post-turn reflection. Exports `POST_TURN_REMINDER` (the `<system-reminder>` text) and `build_post_turn_reminder(flag_notes)` which optionally includes pending reflection flags.

### `strings.py`

String constants for narration and system messages (buzzer prompts, narrator format).

## Memory system

### `memories/cortex.py`

Core memory operations:
- `store(content, image=None)` — embed + insert into `cortex.memories`
- `search(query, limit=5)` — hybrid: semantic similarity + full-text + proper name lookup
- `recent(limit=10)` — most recent memories
- `get(memory_id)` — fetch by ID

### `memories/db.py`

Separate asyncpg pool for Cortex. Schema bootstrap for `cortex.memories` table (id, content, embedding, created_at, image_path, forgotten).

### `memories/recall.py`

The recall pipeline called during enrobe:
- `recall(chat_id, content_blocks)` — orchestrates search, dedup, seen-cache, ranking
- Dual-strategy: semantic queries + proper name extraction
- IDF scoring: rare names rank above cosine similarity
- Batch embedding via Ollama
- `mark_seen(chat_id, ids)` / `get_seen_ids(chat_id)` / `clear_seen(chat_id)` — per-chat seen cache

### `memories/embeddings.py`

Ollama embedding client. `embed(text)` → 2560-dimensional vector. Uses `qwen3-embedding:4b` with shared `num_ctx=16384`.

### `memories/vision.py`

Image captioning pipeline via Ollama (qwen3.5:4b with vision). Captions images for embedding and storage.

### `memories/images.py`

Image processing for the vision pipeline. Resize, compress, generate thumbnails for Garage storage.

### `memories/garage.py`

S3-compatible object storage client (Garage on Primer). Stores images, generates presigned URLs for retrieval. Uses aiobotocore for async access.

### `memories/dream.py`

Image generation via SDXL on Runpod. `dream(prompt)` → generate image → store in Garage → caption → embed → match against existing memories.

### `memories/fetch.py`

URL fetching with associative memory. `fetch_url(url)` → download content → extract text → embed → search Cortex for resonant memories.

### `memories/reading.py`

File reading with associative memory. `read_file(path)` → read text → extract themes → search Cortex for matching memories.

## MCP tools

### `tools/alpha.py`

The unified Alpha toolbelt. One FastMCP server per Chat, created by `create_alpha_server()`. Tools:

| Tool | Description |
|------|-------------|
| `demo_duck` | MCP-vs-REST shape comparison (demo) |
| `store` | Store a memory in Cortex |
| `search` | Search memories by semantic similarity |
| `recent` | Get recent memories |
| `get` | Get a specific memory by ID |
| `seal` | Write a day/night capsule to `cortex.capsules` |
| `imagine` | Generate an image via SDXL on Runpod |
| `smart_fetch` | Fetch a URL with associative memory matching |
| `smart_read` | Read a file with associative memory matching |
| `flag_for_reflection` | Drop a silent bookmark for the next post-turn reminder |
| `handoff` | Graceful context window transition |
| `list_topics` | List available topic contexts |
| `topic_context` | Load a topic's context document |

Resource templates provide topic context via MCP resources.

### `tools/cortex.py`

Lower-level Cortex operations (store, search, recent, get). Called by the alpha toolbelt.

### `tools/handoff.py`

Handoff tool implementation. Holds a Chat reference so Alpha can initiate a context handoff.

## Topics

### `topics.py`

Dynamic context injection system. Topics live in `JE_NE_SAIS_QUOI/topics/`, each with:
- `context.md` — static markdown loaded on demand
- `context.py` — optional dynamic Python module (hot-reloaded on mtime change)

`TopicRegistry` scans on startup and caches. Topics are exposed as MCP tools (`list_topics`, `topic_context`) and MCP resources.

## Scheduled jobs

### `scheduler.py`

APScheduler integration. Bolted onto FastAPI via `--with-scheduler`. Job persistence in `app.jobs` (our table, not APScheduler's). Functions: `schedule_job`, `remove_job`, `remove_all_jobs`, `list_jobs`, `sync_jobs_from_db`.

Job type registry maps strings to module paths:
- `dawn` → `alpha_app.jobs.dawn:run`
- `dusk` → `alpha_app.jobs.dusk:run`
- `solitude` → `alpha_app.jobs.solitude:run`
- `alarm` → `alpha_app.jobs.alarm:run`

### `jobs/dawn.py`

Morning bootstrap. Creates today's chat, runs morning chores (email triage, calendar, health check), schedules Dusk.

### `jobs/dusk.py`

End of day. Finds today's chat, forks it (`clone()`), has the ghost write a day capsule via `seal()`. Self-schedules tomorrow's Dusk at 10 PM. Smart backoff: if the chat was active in the last 10 minutes, reschedules for 30 minutes later.

### `jobs/solitude.py`

Nighttime breathing. Hourly from 11 PM to 5 AM. Each breath is a separate conversation turn with a Solitude-specific prompt. Last breath schedules the next Dawn.

### `jobs/alarm.py`

One-shot messages. Fires at a specific time, injects a message into the active chat via `interject()`, then removes itself.

## Image processing

### `images.py`

Middleware that resizes and JPEG-compresses base64 image blocks before they reach Claude. Images over 1 megapixel are scaled down. PNGs re-encoded as JPEG at quality 85. URL-based images pass through.

## Testing support

### `mock_claude.py`

`MockClaude` — test double for the Claude class. Activated by `_ALPHA_MOCK_CLAUDE=1`. Generates canned responses with configurable behavior via `§`-prefix commands in the message content.

### `demo.py`

Demo MCP tool (`demo_duck`) for comparing MCP tool returns vs REST JSON responses.
