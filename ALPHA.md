# Alpha-App — The Move-In Plan

*Sunday March 8, 2026. Limoncello grapenado. The workshop.*

Alpha-App is the future front door. The goal: move Alpha out of Duckpond
and into her own app. Not someday. Soon.

## The Context Window

What Alpha sees on first message of a new session:

**System prompt** (once, at claude startup):
- Soul doc
- Bill of Rights

**Orientation** (first user message of context window):
- Yesterday capsule (Postgres)
- Last night capsule (Postgres)
- Today so far (Postgres)
- Letter from last night (Postgres)
- ALPHA.md contents and links
- User message
- Recalled memories (0-3, from Cortex via SDK recall)

**The loop** (every subsequent message):
- User message
- Recalled memories (0-3)

**Async sidecar — Intro** (runs in dead time after each assistant turn):
- Analyzes the conversation
- Suggests what to store
- Fires between turns, never blocks the conversation

## MCP Tools (Hard Requirements)

- `cortex__store` — Alpha needs to store memories
- `cortex__search` — Alpha needs to search her own memory

## The Dress vs. The Accessories

**The dress** (must have for move-in):
- Soul + Bill of Rights (system prompt)
- Capsules + letter (Postgres — yesterday, last night, today so far, letter)
- Cortex store + search (MCP tools)
- Recall on user messages (SDK function)
- Intro (async sidecar — load-bearing, not optional)
- ALPHA.md files

**Accessories** (stitch on after move-in):
- Weather + astronomy (API calls)
- Events + todos (API calls)
- Hostname + system info (cheap, low priority)

## What Already Exists

- Streaming pipeline: browser → WebSocket → backend → claude → mock/real API
- Multi-chat sidebar with switching and indicator dots
- Context meter (real-time token counting)
- Approach lights (65% yellow, 75% red)
- Chat lifecycle: holster → chat → reap → resurrect
- `assemble_system_prompt()` in the SDK
- Cortex MCP tools (store, search, recent)
- Recall function in the SDK
- One green e2e test and CI

## What Needs Building

1. Wire `assemble_system_prompt()` into app startup (soul + bill of rights)
2. Build orientation assembly (capsule queries, ALPHA.md loading)
3. Wire recall into the message-send path (enrobe)
4. Wire Intro as async sidecar (fires after assistant turn completes)
5. Connect Cortex MCP tools

## Philosophy

Four wheels and a seat. Get it driving. Bolt the doors on later.
