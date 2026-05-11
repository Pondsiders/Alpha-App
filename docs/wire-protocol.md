---
title: Wire Protocol
description: How the Alpha-App frontend and backend talk to each other over a single multiplexed WebSocket.
outline: deep
---

# Wire Protocol

Alpha-App communicates over a single multiplexed WebSocket. All messages are JSON objects.

The protocol has three envelopes:

- **Commands** — client to server, unicast.
- **Responses** — server to one client, unicast, correlated to a command.
- **Events** — server to all clients, broadcast.

## Connection

The WebSocket endpoint is `/ws`. The server sends nothing on connect. The client opens with a [`hello`](#hello) command and the server replies with [`hi-yourself`](#hi-yourself).

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
| `id` | when a response is expected | Correlation token. The server echoes this on the [response](#responses). Omit for fire-and-forget. |
| `chatId` | when scoped to a chat | Which chat this command targets. |
| *(other fields)* | per command | Command-specific payload fields live at the top level. No nested `payload` or `params` object. |

### Server → Client: Responses (unicast)

Responses flow from the server to one client — the one whose command triggered them. Every response carries the `id` of the originating command.

```json
{
  "response": "chat-joined",
  "id": "req_1",
  "chatId": "xyz"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `response` | always | The response name. |
| `id` | always | Correlation token. Echoed from the originating command. |
| `chatId` | when scoped to a chat | Which chat this response belongs to. |
| *(other fields)* | per response | Response-specific payload fields live at the top level. |

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
| `chatId` | when scoped to a chat | Which chat this event belongs to. Absent on global events. |
| *(other fields)* | per event | Event-specific payload fields live at the top level. |

Events never carry `id`.

### Correlation

If a command includes an `id`, the server **MUST** eventually emit a [response](#responses) that echoes that `id`. The response is either a successful response (e.g., [`chat-joined`](#chat-joined) in response to [`join-chat`](#join-chat)) or an [`error`](#errors) response.

If a command omits `id`, no correlated response is expected.

### Errors

An `error` is a response. A domain failure on a command flows back to the originator only.

```json
{
  "response": "error",
  "id": "req_1",
  "chatId": "xyz",
  "code": "not-found",
  "message": "Chat xyz not found"
}
```

::: info Error codes are domain strings, not numbers
Examples: `"not-found"`, `"invalid-state"`, `"context-exceeded"`.
:::

Errors carry `chatId` when scoped to a chat. Wire-shape failures (malformed JSON, unknown commands, schema violations) are bugs — they raise on the server side and the socket closes; they don't reach `error`.

## Commands

### `hello`

Open a session. Sent by the client immediately after the WebSocket connects.

```json
{ "command": "hello", "id": "req_0" }
```

**Response:** [`hi-yourself`](#hi-yourself).

### `join-chat`

Load a chat's full history and metadata.

```json
{ "command": "join-chat", "id": "req_1", "chatId": "hellopixel01" }
```

**Response:** [`chat-joined`](#chat-joined).

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

## Responses

### `hi-yourself`

Current global state. Emitted in response to [`hello`](#hello).

::: details Example payload
```json
{
  "response": "hi-yourself",
  "id": "req_0",
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

Same data shape as [`app-state`](#app-state); the response answers *"what's true?"* on demand, the event announces *"what just changed."*

### `chat-joined`

Full message history and metadata for one chat. Emitted in response to [`join-chat`](#join-chat).

::: details Example payload
```json
{
  "response": "chat-joined",
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

### `error`

See [Errors](#errors) above.

## Events

### Application state

#### `app-state`

The global state of the world. Broadcast whenever the global state changes (chat created, deleted, renamed, etc.).

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

#### `chat-created`

A new chat exists. Broadcast whenever a chat comes into being — in response to a client's [`create-chat`](#create-chat) command, or unsolicited when the server creates a chat on its own (Dawn, etc.).

```json
{
  "event": "chat-created",
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

**Backend (Python):** Pydantic models per command name. Wire-shape failures are uncaught exceptions; FastAPI closes the WebSocket. The wire `error` response is reserved for *domain* failures.

**Frontend (TypeScript):** Zod schemas per event name. Invalid events throw.

## Design Principles {#design-principles}

1. **Three envelopes: commands, responses, events.** Commands are unicast client-to-server. Responses are unicast server-to-one-client, correlated to a command. Events are broadcast server-to-all-clients. {#principle-envelopes}
2. **Flat payloads.** No nested `data`, `metadata`, or `params` objects. {#principle-flat}
3. **Required fields are required.** Validation explodes on missing fields. {#principle-required}
4. **`id` is a correlation token.** Carried by commands that expect a response, echoed on the response. Events never carry `id`. {#principle-id}
5. **`chatId` is the relevance scope.** Messages without `chatId` are global. {#principle-chatid}
6. **Domain error codes.** `"not-found"`, not `-32601`. {#principle-error-codes}
7. **Extensible by addition.** New commands, responses, and events are added by defining a name + schema. The envelope doesn't change. {#principle-extensible}
