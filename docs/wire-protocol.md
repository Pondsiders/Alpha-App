---
title: Wire Protocol
description: How the Alpha-App frontend and backend talk to each other over a single multiplexed WebSocket.
outline: deep
---

# Wire Protocol

Alpha-App communicates over a single multiplexed WebSocket. All messages are JSON objects.

The protocol is asymmetric: clients send **commands** to the server; the server sends **events** to every connected client.

## Connection

The WebSocket endpoint is `/ws`. On connection, the server sends one event:

- [`app-state`](#app-state) — global application state including the full chat list.

Every WebSocket connection — initial load and every reconnect — triggers the same unconditional `app-state` push.

## Envelope

### Client → Server: Commands (unicast)

Commands flow from one client to the server.

```json
{
  "command": "join-chat",
  "id": "req_1",
  "chatId": "xyz"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `command` | always | The command name. |
| `id` | when a response is expected | Correlation token. The server echoes this on the response event. Omit for fire-and-forget. |
| `chatId` | when scoped to a chat | Which chat this command targets. |
| *(other fields)* | per command | Command-specific payload fields live at the top level. No nested `payload` or `params` object. |

### Server → Client: Events (broadcast)

Every event the server emits is sent to every connected client.

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
| `id` | when responding to a command | Echoed from the command that triggered this event. |
| `chatId` | when scoped to a chat | Which chat this event belongs to. Absent on global events (only [`app-state`](#app-state) today). |
| *(other fields)* | per event | Event-specific payload fields live at the top level. |

### Correlation

If a command includes an `id`, the server **MUST** eventually emit an event that echoes that `id`. The response is either a success event (e.g., [`chat-loaded`](#chat-loaded) in response to [`join-chat`](#join-chat)) or an [`error`](#errors) event.

If a command omits `id`, no correlated response is expected.

### Errors

```json
{
  "event": "error",
  "id": "req_1",
  "chatId": "xyz",
  "code": "not-found",
  "message": "Chat xyz not found"
}
```

::: info Error codes are domain strings, not numbers
Examples: `"not-found"`, `"invalid-state"`, `"subprocess-died"`, `"context-exceeded"`.
:::

Errors carry `chatId` when the error is scoped to a chat, and `id` when they correlate to a command.

## Commands

### `join-chat`

Load a chat's full history and metadata.

```json
{ "command": "join-chat", "id": "req_1", "chatId": "hellopixel01" }
```

**Response:** [`chat-loaded`](#chat-loaded).

### `create-chat`

Create a new conversation.

```json
{ "command": "create-chat", "id": "req_3" }
```

**Response:** [`chat-created`](#chat-created).

### `send`

Send a user message to Claude.

```json
{
  "command": "send",
  "id": "req_4",
  "chatId": "xyz",
  "messageId": "V1StGXR8_Z5jdHi6B-myT",
  "content": [{ "type": "text", "text": "Hello" }]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `messageId` | always | Frontend-minted nanoid for the user message. The server stamps it onto the broadcast [`user-message`](#user-message) echo. |
| `content` | always | Anthropic-shaped content blocks. |

**Response:** the [turn lifecycle](#turn-lifecycle).

### `interrupt`

Stop Claude mid-response.

```json
{ "command": "interrupt", "chatId": "xyz" }
```

## Events

### Application state

#### `app-state`

The global state of the world. Sent on every WebSocket connect, and broadcast whenever the global state changes (chat created, deleted, renamed, etc.).

::: details Example payload
```json
{
  "event": "app-state",
  "chats": [
    {
      "chatId": "fN-8oovTiSotGEH1w242V",
      "createdAt": "2026-05-08T16:50:09.130260Z",
      "lastActive": "2026-05-08T16:50:09.130269Z",
      "state": "pending",
      "tokenCount": 0,
      "contextWindow": 1000000
    }
  ],
  "version": "0.0.0"
}
```
:::

::: tip Timestamps are ISO 8601 strings
Every datetime on the wire is an ISO 8601 string with timezone (`"2026-05-08T16:50:09.130260Z"`), not a unix integer.
:::

| Field | Description |
|-------|-------------|
| `chats` | Full chat list. |
| `version` | App version string. |

`app-state` is the only event that omits `chatId`.

### Chat lifecycle

#### `chat-loaded`

Full message history and metadata for one chat. Emitted in response to [`join-chat`](#join-chat).

::: details Example payload
```json
{
  "event": "chat-loaded",
  "id": "req_1",
  "chatId": "hellopixel01",
  "createdAt": "2026-05-08T16:50:09.130260Z",
  "lastActive": "2026-05-08T16:50:09.130269Z",
  "state": "ready",
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

A new chat exists. Emitted in response to [`create-chat`](#create-chat), or unsolicited when the server creates a chat on its own (Dawn, etc.).

```json
{
  "event": "chat-created",
  "id": "req_3",
  "chatId": "abc123",
  "createdAt": "2026-05-08T16:50:09.130260Z",
  "lastActive": "2026-05-08T16:50:09.130269Z",
  "state": "pending",
  "tokenCount": 0,
  "contextWindow": 1000000,
  "archived": false
}
```

#### `chat-state`

A chat's runtime state — its position in the turn lifecycle plus current context-window utilization. Broadcast whenever any of those values change.

```json
{
  "event": "chat-state",
  "chatId": "xyz",
  "state": "processing",
  "tokenCount": 165000,
  "contextWindow": 1000000
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | `"pending" \| "ready" \| "preprocessing" \| "processing" \| "postprocessing"` | The chat's position in the turn lifecycle. |
| `tokenCount` | `number` | Current context size in tokens. |
| `contextWindow` | `number` | Maximum context window size for the model backing this chat. |

**State values:**

`"pending"`
: No Claude subprocess. The chat exists but has been reaped or never spawned.

`"ready"`
: Subprocess alive and idle, awaiting input.

`"preprocessing"`
: Backend has the message; recall, timestamping, and normalization are in flight. Claude has not received it yet.

`"processing"`
: Claude has the message and is generating.

`"postprocessing"`
: Post-turn work (reflection, etc.) is running.

### Turn lifecycle

A *turn* is a user message → Claude's response.

```
send (command)
  └→ turn-started              edge: the turn has begun
  └→ chat-state {preprocessing}
  └→ user-message              preprocessed echo (with memories, timestamp)
  └→ chat-state {processing}
  └→ thinking-delta (0..n)     extended thinking fragments
  └→ text-delta (0..n)         text response fragments
  └→ tool-call-start           Claude decided to call a tool
  └→ tool-call-delta (0..n)    JSON args streaming
  └→ tool-call-result          tool finished, here's the result
     (steps above can repeat — Claude can think, text, tool, text, tool, text)
  └→ assistant-message         the complete finished message
  └→ turn-complete             edge: the turn has ended
  └→ chat-state {ready}        (or {postprocessing} if a post-turn cycle runs)
```

::: info Preprocessing
Before a user message is sent to Claude, the server *preprocesses* it — attaches a timestamp, recalls relevant memories, normalizes content. The [`user-message`](#user-message) event echoes the preprocessed version.
:::

#### `turn-started`

A turn has begun. Emitted after a [`send`](#send) command, before any other turn-lifecycle events.

```json
{
  "event": "turn-started",
  "chatId": "xyz"
}
```

#### `user-message`

A preprocessed human-authored user message, broadcast after a [`send`](#send).

::: details Example payload
```json
{
  "event": "user-message",
  "chatId": "xyz",
  "messageId": "V1StGXR8_Z5jdHi6B-myT",
  "content": [
    { "type": "text", "text": "Hello there" }
  ],
  "memories": [
    { "id": 16617, "content": "...", "score": 0.85 }
  ],
  "timestamp": "2026-04-06T22:45:00Z"
}
```
:::

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `messageId` | `string` | always | Echoed from the [`send`](#send) command. |
| `content` | `list[block]` | always | Content blocks in Messages API format. |
| `memories` | `list[memory] \| null` | always | Recalled memories attached during preprocessing (null if none). |
| `timestamp` | `string` | always | ISO 8601 UTC timestamp, set during preprocessing. |

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

Tool finished executing. Complete args and result.

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

The complete finished assistant message with all parts assembled. Sent at the end of the turn, before [`turn-complete`](#turn-complete).

::: details Example payload
```json
{
  "event": "assistant-message",
  "chatId": "xyz",
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

Claude finished responding. A [`chat-state`](#chat-state) `{ready, ...}` event with updated token counts follows immediately.

```json
{
  "event": "turn-complete",
  "chatId": "xyz"
}
```

## Validation

Both sides validate every incoming message against a schema.

::: warning Validation explodes; it does not paper over
Missing required fields are a hard failure, not a silent default. If a field is required, its absence is a bug to be caught, not a gap to be papered over.
:::

**Backend (Python):** Pydantic models per command name. Wire-shape failures are uncaught exceptions; FastAPI closes the WebSocket. The wire `error` event is reserved for *domain* failures.

**Frontend (TypeScript):** Zod schemas per event name. Invalid events throw.

## Design Principles {#design-principles}

1. **Commands are unicast; events are broadcast.** {#principle-asymmetric}
2. **Flat payloads.** No nested `data`, `metadata`, or `params` objects. {#principle-flat}
3. **Required fields are required.** Validation explodes on missing fields. {#principle-required}
4. **`id` is a correlation token.** {#principle-id}
5. **`chatId` is the relevance scope.** Events without `chatId` are global. {#principle-chatid}
6. **Domain error codes.** `"not-found"`, not `-32601`. {#principle-error-codes}
7. **Extensible by addition.** New commands and events are added by defining a name + schema. The envelope doesn't change. {#principle-extensible}
