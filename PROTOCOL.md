# Wire Protocol

Alpha-App communicates over a single multiplexed WebSocket. All messages are JSON objects. The protocol is asymmetric: clients send **commands**, the server sends **events**.

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

If a command includes an `id`, the server MUST eventually respond with an event that echoes that `id`. The response is either:
- A success event (e.g., `chat-loaded` in response to `join-chat`)
- An `error` event

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

Error codes are domain-specific strings, not numbers. Examples: `"not-found"`, `"invalid-state"`, `"subprocess-died"`, `"context-exceeded"`. The `id` field, if present, correlates the error to the command that caused it. Errors without `id` are unsolicited (e.g., a subprocess crash).

## Commands

### `join-chat`
Load a chat's full history and metadata.

```json
{ "command": "join-chat", "id": "req_1", "chatId": "hellopixel01" }
```

Response: `chat-loaded` event.

### `list-chats`
Get all chats for the sidebar.

```json
{ "command": "list-chats", "id": "req_2" }
```

Response: `chat-list` event.

### `create-chat`
Create a new conversation.

```json
{ "command": "create-chat", "id": "req_3" }
```

Response: `chat-created` event.

### `send`
Send a user message to Claude.

```json
{ "command": "send", "id": "req_4", "chatId": "xyz", "content": [{ "type": "text", "text": "Hello" }] }
```

Response: `send-ack` event (confirms receipt). Then streaming events flow: `text-delta`, `thinking-delta`, `tool-call`, and finally `turn-complete`.

### `interrupt`
Stop Claude mid-response. Fire-and-forget (no `id` needed).

```json
{ "command": "interrupt", "chatId": "xyz" }
```

### `buzz`
The duck button. Injects a system message.

```json
{ "command": "buzz", "id": "req_5", "chatId": "xyz" }
```

Response: `buzz-ack` event.

## Events

### Chat lifecycle

#### `chat-list`
Response to `list-chats`. Full sidebar data.

```json
{
  "event": "chat-list",
  "id": "req_2",
  "chats": [
    {
      "chatId": "hellopixel01",
      "title": "Hello, world",
      "createdAt": 1775345137,
      "updatedAt": 1775345137,
      "state": "dead",
      "tokenCount": 0,
      "contextWindow": 1000000
    }
  ]
}
```

#### `chat-loaded`
Response to `join-chat`. Full message history + metadata.

```json
{
  "event": "chat-loaded",
  "id": "req_1",
  "chatId": "hellopixel01",
  "title": "Hello, world",
  "createdAt": 1775345137,
  "updatedAt": 1775345137,
  "state": "dead",
  "tokenCount": 0,
  "contextWindow": 1000000,
  "messages": [
    { "role": "user", "data": { ... } },
    { "role": "assistant", "data": { ... } }
  ]
}
```

#### `chat-created`
A new chat exists. Can be a response to `create-chat` (with `id`) or unsolicited (Dawn created one).

```json
{
  "event": "chat-created",
  "id": "req_3",
  "chatId": "abc123",
  "title": "",
  "createdAt": 1775345200
}
```

#### `chat-state`
A chat's state changed (idle, busy, dead).

```json
{
  "event": "chat-state",
  "chatId": "xyz",
  "state": "busy"
}
```

### Turn lifecycle

A turn is a user message → Claude's response. The full event sequence:

```
send (command)
  └→ send-ack                    "got it, enriching"
  └→ user-message                enriched echo (with memories, timestamp)
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

#### `send-ack`
Response to `send`. Means "I received it, enrichment is running, Claude is about to respond."

```json
{
  "event": "send-ack",
  "id": "req_4",
  "chatId": "xyz"
}
```

#### `user-message`
The enriched user message echoed back from the server. Includes memories, timestamp, and any other enrichment. This is the authoritative version — the frontend's optimistic local copy gets replaced by this.

```json
{
  "event": "user-message",
  "chatId": "xyz",
  "messageId": "msg_1",
  "content": [
    { "type": "text", "text": "Hello there" }
  ],
  "memories": [
    { "id": 16617, "content": "...", "score": 0.85 }
  ],
  "timestamp": "Mon Apr 6 2026, 3:45 PM"
}
```

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
The complete finished assistant message with all parts assembled. Sent at the very end of Claude's response, before `turn-complete`.

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

#### `turn-complete`
Claude finished responding. Updated token counts for the context meter.

```json
{
  "event": "turn-complete",
  "chatId": "xyz",
  "tokenCount": 165000,
  "contextWindow": 1000000,
  "percent": 16.5
}
```

### Context

#### `context-update`
Token counts changed outside of a turn (e.g., after compaction or system events).

```json
{
  "event": "context-update",
  "chatId": "xyz",
  "tokenCount": 165000,
  "contextWindow": 1000000,
  "percent": 16.5
}
```

## Validation

Both sides validate every incoming message against a schema.

**Backend (Python):** Pydantic models per command name. Invalid commands get an `error` event response. Missing required fields are a hard failure, not a silent default.

**Frontend (TypeScript):** Zod schemas per event name. Invalid events throw, not silently degrade. No `?? 0`, no `?? Date.now()`. If a field is required, its absence is a bug to be caught, not a gap to be papered over.

## Design Principles

1. **Commands and events are different shapes.** Don't force symmetry on an asymmetric protocol.
2. **Flat payloads.** No nested `data`, `metadata`, or `params` objects. Fields live at the top level of the message.
3. **Required fields are required.** Validation explodes on missing fields. Silent defaults hide bugs.
4. **`id` means "I expect a response."** Absent `id` means fire-and-forget.
5. **`chatId` means "this belongs to a chat."** Events without `chatId` are global (e.g., `chat-list`).
6. **Domain error codes.** `"not-found"`, not `-32601`. The codes mean something to us.
7. **Extensible by addition.** New commands and events are added by defining a name + schema. The envelope doesn't change.
