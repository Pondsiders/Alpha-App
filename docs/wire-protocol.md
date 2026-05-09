---
title: Wire Protocol
description: How the Alpha-App frontend and backend talk to each other over a single multiplexed WebSocket.
outline: deep
---

# Wire Protocol

Alpha-App communicates over a single multiplexed WebSocket. All messages are JSON objects.

::: tip The shape of the protocol
The protocol is **asymmetric**: clients send **commands**, the server sends **events**. Commands optionally expect a response; events flow freely whether anyone asked for them or not. Don't force symmetry on a protocol that isn't symmetric.
:::

## Connection

The WebSocket endpoint is `/ws`. The client MAY include a `lastChat` query parameter suggesting which chat to restore:

```http
GET /ws?lastChat=abc123 HTTP/1.1
Upgrade: websocket
```

On connection, the server immediately sends two events — no client command required:

1. [`app-state`](#app-state) — global application state including the full chat list.
2. [`chat-loaded`](#chat-loaded) — the full message history for one chat.

If `lastChat` is present and the chat exists, the server sends that chat. Otherwise it sends the most recent chat. If no chats exist at all, only `app-state` is sent (no `chat-loaded`).

The client renders from these two events. No startup handshake, no request/response dance. The server pushes; the client receives and renders.

::: tip Reconnect is the same as first connect
Every WebSocket connection — initial load *and* every reconnect after a transient drop — triggers the same two-event push. So when the client reconnects after a laptop wake, a network blip, or a server restart, it automatically receives the current `app-state` (picking up any chats Dawn or other background work created while the socket was closed) and the current `chat-loaded` for the active chat.

The client does not need to issue a re-sync command on reconnect. The server resyncs unconditionally.
:::

## Envelope

### Client → Server: Commands

```json
{
  "command": "join-chat",
  "id": "req_1",
  "chatId": "xyz",
  "content": [...]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `command` | always | The command name. |
| `id` | when a response is expected | Correlation ID. The server echoes this on the response event. Omit for fire-and-forget commands. |
| `chatId` | when scoped to a chat | Which chat this command targets. |
| *(other fields)* | per command | Command-specific payload fields live at the top level. No nested `payload` or `params` object. |

### Server → Client: Events

```json
{
  "event": "text-delta",
  "chatId": "xyz",
  "delta": "Hello there"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `event` | always | The event name. |
| `id` | when responding to a command | Echoed from the command that triggered this event. Absent on unsolicited events (streaming, broadcasts, server-initiated). |
| `chatId` | when scoped to a chat | Which chat this event belongs to. |
| *(other fields)* | per event | Event-specific payload fields live at the top level. |

### Correlation

If a command includes an `id`, the server **MUST** eventually respond with an event that echoes that `id`. The response is either:

- A success event (e.g., [`chat-loaded`](#chat-loaded) in response to [`join-chat`](#join-chat))
- An [`error`](#errors) event

If a command omits `id`, no response is expected or sent.

### Errors

```json
{
  "event": "error",
  "id": "req_1",
  "code": "not-found",
  "message": "Chat xyz not found"
}
```

::: info Error codes are domain strings, not numbers
Examples: `"not-found"`, `"invalid-state"`, `"subprocess-died"`, `"context-exceeded"`. The codes mean something to us.

The `id` field, if present, correlates the error to the command that caused it. Errors without `id` are unsolicited (e.g., a subprocess crash).
:::

## Commands

### `join-chat`

Load a chat's full history and metadata.

```json
{ "command": "join-chat", "id": "req_1", "chatId": "hellopixel01" }
```

**Response:** [`chat-loaded`](#chat-loaded) event.

### `create-chat`

Create a new conversation.

```json
{ "command": "create-chat", "id": "req_3" }
```

**Response:** [`chat-created`](#chat-created) event.

### `send`

Send a user message to Claude.

```json
{
  "command": "send",
  "id": "req_4",
  "chatId": "xyz",
  "content": [{ "type": "text", "text": "Hello" }]
}
```

**Response:** [`send-ack`](#send-ack) event (confirms receipt). Then a preprocessed [`user-message`](#user-message) is echoed back, and streaming events flow: [`text-delta`](#text-delta), [`thinking-delta`](#thinking-delta), [`tool-call-start`](#tool-call-start), and finally [`turn-complete`](#turn-complete). See [Turn lifecycle](#turn-lifecycle) for the full sequence.

### `interrupt`

Stop Claude mid-response. Fire-and-forget (no `id` needed).

```json
{ "command": "interrupt", "chatId": "xyz" }
```

## Events

### Application state

#### `app-state`

Sent by the server immediately on WebSocket connect. Also broadcast to all clients when global state changes (chat created, deleted, renamed, etc.). Never requested by the client — the server pushes it.

::: details Example payload
```json
{
  "event": "app-state",
  "chats": [
    {
      "chatId": "fN-8oovTiSotGEH1w242V",
      "createdAt": "2026-05-08T16:50:09.130260Z",
      "lastActive": "2026-05-08T16:50:09.130269Z",
      "state": "dead",
      "tokenCount": 0,
      "contextWindow": 1000000
    }
  ],
  "version": "0.0.0"
}
```
:::

::: tip Timestamps are ISO 8601 strings
Every datetime on the wire is an ISO 8601 string with timezone (`"2026-05-08T16:50:09.130260Z"`), not a unix integer. Pydantic emits `datetime` fields this way by default in `mode="json"`; matches `Date(isoString)` on the JS side.
:::

| Field | Description |
|-------|-------------|
| `chats` | Full chat list for the sidebar. Chats are identified by `createdAt`, not by topic; the sidebar renders them as a date-sorted list (Apple Mail's date-column shape, not Gmail's subject-column shape). |
| `version` | App version string. Client can compare to detect stale frontends. |

### Chat lifecycle

#### `chat-loaded`

Full message history + metadata for one chat. Sent in two situations:

1. Immediately after [`app-state`](#app-state) on connect (the startup chat).
2. In response to a [`join-chat`](#join-chat) command (switching chats mid-session).

::: details Example payload
```json
{
  "event": "chat-loaded",
  "id": "req_1",
  "chatId": "hellopixel01",
  "createdAt": 1775345137,
  "lastActive": 1775345137,
  "state": "dead",
  "tokenCount": 0,
  "contextWindow": 1000000,
  "messages": [
    { "role": "user", "data": { ... } },
    { "role": "assistant", "data": { ... } }
  ]
}
```
:::

#### `chat-created`

A new chat exists. Can be a response to [`create-chat`](#create-chat) (with `id`) or unsolicited (Dawn created one).

```json
{
  "event": "chat-created",
  "id": "req_3",
  "chatId": "abc123",
  "createdAt": 1775345200
}
```

#### `chat-state`

A chat's runtime state — its lifecycle status plus current context-window utilization. Sent whenever any of those values change. The server can send `chat-state` updates frequently; clients should treat each one as the new authoritative state for the chat.

```json
{
  "event": "chat-state",
  "chatId": "xyz",
  "state": "busy",
  "tokenCount": 165000,
  "contextWindow": 1000000,
  "percent": 16.5
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | `"idle" \| "busy" \| "dead"` | Lifecycle status. `idle` means ready for input; `busy` means a turn is in flight; `dead` means the chat's subprocess is gone (resurrectable but not currently running). |
| `tokenCount` | `number` | Current context size in tokens (input + cache_read + cache_creation). |
| `contextWindow` | `number` | Maximum context window size for the model backing this chat. |
| `percent` | `number` | Convenience: `tokenCount / contextWindow * 100`, rounded for display. |

::: info `chat-state` is the single source of truth for the context meter
There is no separate `context-update` event. Token counts ride along on every `chat-state`. Send updates whenever they would change — at the start and end of a turn, after compaction, or any other moment the displayed meter should move.
:::

### Turn lifecycle

A *turn* is a user message → Claude's response. The full event sequence:

```
send (command)
  └→ send-ack                    "got it, preprocessing"
  └→ user-message                preprocessed echo (with memories, timestamp)
  └→ chat-state {busy}
  └→ thinking-delta (0..n)       extended thinking fragments
  └→ text-delta (0..n)           text response fragments
  └→ tool-call-start             Claude decided to call a tool
  └→ tool-call-delta (0..n)      JSON args streaming
  └→ tool-call-result            tool finished, here's the result
     (steps above can repeat — Claude can think, text, tool, text, tool, text)
  └→ assistant-message           the complete finished message
  └→ turn-complete               done, updated token counts
  └→ chat-state {idle}
```

::: info Preprocessing
Before a user message is sent to Claude, the server *preprocesses* it — attaches a timestamp, recalls relevant memories, normalizes content. The [`user-message`](#user-message) event echoes the **preprocessed** version back to the client, replacing whatever optimistic local copy the frontend rendered when the user hit send.
:::

#### `send-ack`

Response to [`send`](#send). Means "I received it, preprocessing is running, Claude is about to respond."

```json
{
  "event": "send-ack",
  "id": "req_4",
  "chatId": "xyz"
}
```

#### `user-message`

The preprocessed user message echoed back from the server. Includes source, memories, timestamp, and any other preprocessing output.

::: tip Authoritative
This is the authoritative version — the frontend's optimistic local copy gets replaced by this.
:::

::: details Example payload
```json
{
  "event": "user-message",
  "chatId": "xyz",
  "messageId": "msg_1",
  "source": "human",
  "content": [
    { "type": "text", "text": "Hello there" }
  ],
  "memories": [
    { "id": 16617, "content": "...", "score": 0.85 }
  ],
  "timestamp": "Mon Apr 6 2026, 3:45 PM"
}
```
:::

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | `"human" \| "reflection"` | always | Who initiated this user message. Determines how the frontend renders it and whether it blocks input. |
| `content` | `list[block]` | always | Content blocks in Messages API format. |
| `memories` | `list[memory] \| null` | always | Recalled memories attached during preprocessing (null if none). |
| `timestamp` | `string` | always | PSO-8601 formatted creation time, e.g. `"Fri Apr 10 2026, 11:27 AM"`. Set during preprocessing. |

**Source values and their semantics:**

`"human"`
: Initiated by Jeffery typing in the composer. **Blocks input** (composer shows stop button, new sends rejected until turn completes).

`"reflection"`
: Injected by the backend as a post-turn reflection prompt. **Does not block input** (composer stays idle). If a new human message arrives mid-reflection, the backend interrupts the reflection and the human wins.

The `blocks_input` property is derivable from `source` on both backend and frontend. Sources not in the blocking set are interruptible.

#### `thinking-delta`

A fragment of Claude's extended thinking.

```json
{
  "event": "thinking-delta",
  "chatId": "xyz",
  "delta": "Let me consider..."
}
```

#### `text-delta`

A fragment of Claude's text response.

```json
{
  "event": "text-delta",
  "chatId": "xyz",
  "delta": "Hello there"
}
```

#### `tool-call-start`

Claude has decided to call a tool. Name known, args still streaming.

```json
{
  "event": "tool-call-start",
  "chatId": "xyz",
  "toolCallId": "tc_1",
  "name": "store"
}
```

#### `tool-call-delta`

A JSON fragment of the tool call arguments being assembled.

```json
{
  "event": "tool-call-delta",
  "chatId": "xyz",
  "toolCallId": "tc_1",
  "delta": "{\"memory\": \"Mon Apr 6"
}
```

#### `tool-call-result`

Tool finished executing. Complete args + result.

```json
{
  "event": "tool-call-result",
  "chatId": "xyz",
  "toolCallId": "tc_1",
  "name": "store",
  "args": { "memory": "Mon Apr 6 2026..." },
  "result": "Memory stored (id: 16618)"
}
```

#### `assistant-message`

The complete finished assistant message with all parts assembled. Sent at the very end of Claude's response, before [`turn-complete`](#turn-complete).

::: details Example payload
```json
{
  "event": "assistant-message",
  "chatId": "xyz",
  "messageId": "msg_2",
  "content": [
    { "type": "thinking", "text": "Let me consider..." },
    { "type": "text", "text": "Hello there! Here's what I found..." },
    { "type": "tool-call", "name": "store", "args": { ... }, "result": "..." },
    { "type": "text", "text": "Memory stored." }
  ]
}
```
:::

#### `turn-complete`

Claude finished responding. The signal that the turn is over. A [`chat-state`](#chat-state) `{idle, ...}` event with updated token counts follows immediately.

```json
{
  "event": "turn-complete",
  "chatId": "xyz"
}
```

## Validation

Both sides validate every incoming message against a schema.

::: warning Validation explodes; it does not paper over
Missing required fields are a hard failure, not a silent default. No `?? 0`, no `?? Date.now()`. If a field is required, its absence is a bug to be caught, not a gap to be papered over.
:::

**Backend (Python):** Pydantic models per command name. **Wire-shape failures are bugs, not protocol cases.** If an inbound message can't be parsed as JSON, or can't be validated against any known command shape, the backend raises an uncaught exception. FastAPI closes the WebSocket; the frontend reconnects (the connection is the recovery primitive). The exception lands in Logfire under the request span — that's the canonical pane of glass for debugging. The wire `error` event is reserved for *domain* failures — operations that were valid commands but can't be done (`not-found`, `invalid-state`, `subprocess-died`, `context-exceeded`).

**Frontend (TypeScript):** Zod schemas per event name. Invalid events throw, not silently degrade.

## Design Principles {#design-principles}

1. **Commands and events are different shapes.** Don't force symmetry on an asymmetric protocol. {#principle-asymmetric}
2. **Flat payloads.** No nested `data`, `metadata`, or `params` objects. Fields live at the top level of the message. {#principle-flat}
3. **Required fields are required.** Validation explodes on missing fields. Silent defaults hide bugs. {#principle-required}
4. **`id` means "I expect a response."** Absent `id` means fire-and-forget. {#principle-id}
5. **`chatId` means "this belongs to a chat."** Events without `chatId` are global (e.g., the chat list in [`app-state`](#app-state)). {#principle-chatid}
6. **Domain error codes.** `"not-found"`, not `-32601`. The codes mean something to us. {#principle-error-codes}
7. **Extensible by addition.** New commands and events are added by defining a name + schema. The envelope doesn't change. {#principle-extensible}
