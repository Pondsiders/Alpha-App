# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Frontend-v2 is the next-generation React chat UI for Alpha-App, built on [assistant-ui](https://github.com/assistant-ui/assistant-ui) primitives. It replaces the original `frontend/` with a cleaner architecture: Zustand store as single source of truth, Zod-validated WebSocket protocol, and assistant-ui's `useExternalStoreRuntime` for rendering.

Currently **Phase 1** (read-only): receives and displays chats/messages from the backend. Phase 2 will wire the composer to send messages back.

## Commands

```bash
npm run dev          # Vite dev server (TLS, port 443 on primer)
npm run build        # tsc type-check + Vite production build
npm run lint         # ESLint
```

No test suite yet — frontend-tests/ in the parent repo covers the original frontend.

## Architecture

### Data Flow

```
WebSocket events → useAlphaWebSocket → Zustand store → RuntimeProvider (convertMessage) → assistant-ui primitives
```

All UI-driving state lives in the Zustand store (`store.ts`). Components are pure renderers with no local state for chat data.

### Store (`store.ts`)

Zustand + Immer middleware. Shape: `{ connected, chats: Record<id, Chat>, currentChatId }`.

- **Chat**: `{ id, title, messages[], isRunning, tokenCount, contextWindow, ... }`
- **Message**: tagged union `{ role: "user"|"assistant"|"system", data: {...} }` — mirrors backend format exactly
- Actions: `setChatList`, `upsertChat`, `setMessages`, `appendMessage`, `appendTextDelta`, `appendThinkingDelta`, `setIsRunning`, `setTokenCount`
- Selectors: `selectCurrentChat`, `selectChatList` (sorted by updatedAt)
- Each component subscribes to only what it reads via `useStore(selector)`

### WebSocket Layer

Two-layer hook architecture:

1. **`lib/useWebSocket.ts`** — Generic transport. Manages connect/reconnect (exponential backoff: 1s→16s), JSON parsing. Returns `{ send, connected }`.
2. **`hooks/useAlphaWebSocket.ts`** — App-specific event routing. Dispatches server events to Zustand actions. Sends `join-chat` when `currentChatId` changes.

### Protocol (`lib/protocol.ts`)

Zod v4 schemas for all WebSocket messages. Discriminated unions for `Command` (client→server) and `ServerEvent` (server→client). Invalid events are logged and ignored.

Key commands: `join-chat`, `create-chat`, `send`, `interrupt`, `buzz`
Key events: `app-state`, `chat-loaded`, `chat-state`, `text-delta`, `thinking-delta`, `tool-call-start/delta/result`, `assistant-message`, `turn-complete`, `context-update`

### Render Boundary (`RuntimeProvider.tsx`)

Bridges the store to assistant-ui. Reads `selectCurrentChat`, converts messages via `convertMessage()` (pure function: backend shapes → `ThreadMessageLike`), and passes to `useExternalStoreRuntime`. The `onNew` handler is a Phase 2 stub.

### Components

- **`assistant-ui/thread.tsx`** — Main chat view. ThreadPrimitive.Root + Viewport + messages + composer.
- **`assistant-ui/markdown-text.tsx`** — react-markdown + remark-gfm + react-shiki (Vitesse Dark). Copy buttons on code blocks. User messages get smaller prose.
- **`assistant-ui/tool-fallback.tsx`** — Generic tool-call UI. Status dot, arg summary, collapsible result. Smart arg extraction (file_path, query, command, etc.).
- **`grouped-thread-list.tsx`** — Sidebar thread list grouped by circadian day (6 AM LA boundary) then by week. No assistant-ui machinery.
- **`ChatInfo.tsx`** / **`ContextMeter.tsx`** — Header widgets. Context meter colors: <65% muted, 65–75% amber, >75% red.

### Theming (`themes/alpha.css`)

OKLch color space. Dark mode preferred. Amber primary. All semantic tokens (primary, destructive, muted, accent, etc.) plus sidebar and chart variants. Imported by `index.css`. Typography via Inter Variable + @tailwindcss/typography.

## Key Conventions

- Path alias `@/*` maps to `src/*` (Vite + TypeScript)
- Vite dev server proxies `/api` and `/ws` to the backend at `primer:18020`
- TLS certs from `/Pondside/Basement/Files/certs/` (Tailscale host: `primer.tail8bd569.ts.net`)
- shadcn/ui components in `components/ui/` — configured via `components.json`
- `cn()` utility = `clsx` + `tailwind-merge` (in `lib/utils.ts`)
- Backend message formats stored as-is in the store; conversion to assistant-ui shapes happens only at the render boundary
- Tailwind v4 with `@theme` inline blocks (no `tailwind.config.js`)

## Phase 2 TODOs

These are marked throughout the code:
- Wire `onNew` in RuntimeProvider to send messages via WebSocket
- Handle `text-delta` and `thinking-delta` events (need message ID propagation from `send-ack`)
- Handle `tool-call-start`, `tool-call-delta`, `tool-call-result` events
- Register custom tool UIs via assistant-ui runtime
