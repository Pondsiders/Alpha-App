# Chat v2 — Design Document

**Status:** Draft v3 (morning review), Wed Apr 1 2026
**Origin:** Six hours of Primer session + architecture talk over McMuffin → Pineapple Coast
**Supersedes:** Relevant sections of KERNEL-V2.md (Claude class, Chat class)
**Changes in v2:** `wait_until_ready()` returns AssistantMessage; enrobe broadcasts progressively via callback instead of batching events
**Changes in v3:** Auto-start clarification, `events()` audit, broadcast design, phase reorder

## The Problem

Chat is 1,094 lines doing three things: subprocess lifecycle, conversation state, and event dispatch. These concerns are tangled — the `_on_claude_event` handler alone is 340 lines of interleaved bookshelf logic and broadcast logic. The five-state conversation state machine (COLD/STARTING/READY/ENRICHING/RESPONDING) gates sends that shouldn't be gated. The `events()` generator conflicts with the `on_event` callback and crashes headless jobs (Dawn, Solitude, alarms).

## The Solution

Chat becomes thin. Claude manages itself. Event handlers are modular.

## Piece 1: Claude (the engine)

Claude owns the subprocess lifecycle. Chat just says "send real good."

### State: Two observable bits

```python
is_alive: bool    # subprocess exists and hasn't exited
is_ready: bool    # asyncio.Event — set on result, cleared on init
```

Replaces the five-state ConversationState enum. Not a gate — observation for the frontend. Claude doesn't refuse sends based on state.

### The `_ready` Event

```python
self._ready = asyncio.Event()
self._ready.set()  # starts ready

# In stdout drain:
if event is SystemEvent(subtype="init"):
    self._ready.clear()   # working now
elif event is ResultEvent:
    self._ready.set()     # done, ready again

async def wait_until_ready(self):
    await self._ready.wait()
```

This is the primitive that headless jobs use:
```python
await chat.send(prompt)
await chat.claude.wait_until_ready()
# Claude answered. Proceed.
```

### Lifecycle: self-managing

- **Auto-start on send:** If `send()` is called when not alive, Claude starts itself using the stored session_id for `--resume`. This is the **safety net**, not the preferred path for latency-sensitive callers. The UI pre-warms Claude on click (hiding startup latency while the user types). Jobs let auto-start handle cold starts because nobody's watching. Auto-start means jobs don't have to check `is_alive` first — they just send.
- **Explicit start():** Still exists for pre-warming. The UI calls `start()` on chat click so the subprocess is warm by the time the first message arrives. Auto-start is the floor, not the ceiling.
- **Reap timer:** Claude manages its own idle timer. Every `send()` resets it. If it fires, Claude calls `self.stop()`. Chat never notices — next `send()` auto-starts.
- **Init handshake:** Claude handles the control_request/control_response dance with MCP servers internally.

### Interface

```python
class Claude:
    # State (observed, not gated)
    is_alive: bool
    is_ready: bool
    session_id: str | None  # captured from first ResultEvent

    # Lifecycle (self-managing)
    async def start(self, session_id: str | None = None)
    async def stop(self)
    # Auto-start and reap are internal

    # I/O
    async def send(self, content: list[dict])
    on_event: Callable[[Event], Awaitable[None]]  # set by Chat

    # Waiting
    async def wait_until_ready(self) -> AssistantMessage | None
        # Returns the completed AssistantMessage, or None if no response.
        # The universal "ask the duck, wait for the duck, read what the duck said" primitive.
        # Enables: headless jobs, Telegram bots, email handlers, iMessage — any channel
        # that just wants a response without streaming.

    # Token counting (proxy delegates)
    token_count: int
    output_tokens: int
    # etc.
```

### What Claude doesn't know
- What a conversation is
- What messages are
- What WebSockets are
- What Postgres is

## Piece 2: Chat (the bookshelf)

Chat is the conversation. It owns the message list, handles Claude events, persists to Postgres, and broadcasts to the frontend.

### What Chat owns

```python
class Chat:
    id: str                           # nanoid, 12 chars
    session_id: str | None            # Claude Code UUID, captured on first ResultEvent
    messages: list[UserMessage | AssistantMessage | SystemMessage]
    claude: Claude
    on_broadcast: Callable | None     # set by WebSocket handler
```

### The one input method

```python
async def send(self, msg: UserMessage):
    self.messages.append(msg)
    await self._flush(msg)                          # persist to Postgres
    await self._broadcast(user_message_event(msg))  # tell the browsers
    await self.claude.send(msg.to_content_blocks()) # push to Claude
```

Chat receives a fully-formed UserMessage. It doesn't create it — that's the caller's job (WebSocket handler via enrobe, or jobs directly). Chat doesn't enrich, doesn't recall, doesn't know about Qwen or Ollama.

### Event dispatch: dict, not elif

```python
self._event_handlers = {
    StreamEvent: self._handle_stream,
    AssistantEvent: self._handle_assistant,
    UserEvent: self._handle_user_echo,
    ResultEvent: self._handle_result,
    SystemEvent: self._handle_system,
    ErrorEvent: self._handle_error,
}

async def _on_claude_event(self, event):
    handler = self._event_handlers.get(type(event))
    if handler:
        await handler(event)
```

Each handler has two concerns, clearly separated:

1. **Bookshelf** — what does this event mean for `messages[]`?
2. **Broadcast** — what should the browsers know?

### The handlers

**StreamEvent** — text deltas, thinking deltas, tool call JSON deltas
- Bookshelf: nothing (deltas don't go on the shelf; the AssistantEvent has the complete blocks)
- Broadcast: send the delta live for streaming UX

**AssistantEvent** — complete content blocks (text, tool_use, thinking)
- Bookshelf: create or update the current AssistantMessage (lazy susan pattern — each event adds blocks, same message ID)
- Broadcast: send the complete blocks for non-streaming renderers

**UserEvent** — echoed user messages and tool results (via `--replay-user-messages`)
- Bookshelf: this is the confirmation that Claude received a message. Potentially promotes an on-deck message to the transcript.
- Broadcast: echo event for multi-tab sync

**ResultEvent** — Claude is done
- Bookshelf: seal the current AssistantMessage, append to messages[], capture session_id
- Broadcast: `done` event
- Side effects: flush to Postgres, arm suggest pipeline, open Logfire span closure
- Note: `_ready.set()` happens in Claude, not Chat

**SystemEvent** — init, compact_boundary, task notifications
- Bookshelf: nothing for most subtypes. compact_boundary sets `_needs_orientation`.
- Broadcast: task_started, task_progress, task_notification go to frontend as system-message cards
- Note: `_ready.clear()` on init happens in Claude, not Chat

**ErrorEvent** — API errors, subprocess errors
- Bookshelf: nothing (errors don't go on the shelf)
- Broadcast: error event to frontend

### Logfire spans: init to result

Open a manual span on `init`, close it on `result`:

```python
async def _handle_system(self, event):
    if event.subtype == "init":
        self._turn_span = logfire.span("alpha.turn", chat_id=self.id)
        self._turn_span.start()

async def _handle_result(self, event):
    if self._turn_span:
        self._turn_span.set_attribute("gen_ai.usage.input_tokens", ...)
        self._turn_span.set_attribute("gen_ai.usage.output_tokens", ...)
        self._turn_span.end()
        self._turn_span = None
```

Everything between init and result nests under the turn span automatically.

### Session ID: Chat's responsibility

The session ID (Claude Code's UUID) lives on Chat, not Claude. Claude is ephemeral (reaped, restarted). The session is permanent. Chat captures it from the first ResultEvent and passes it to Claude on start:

```python
async def _handle_result(self, event):
    if event.session_id and not self.session_id:
        self.session_id = event.session_id
    # ...

# When Claude auto-starts:
await self.claude.start(session_id=self.session_id)
```

### Persistence: dirty bits

Each message has a `_dirty` flag. Born dirty. `flush()` UPSERTs dirty messages to `app.messages`, clears the flag. Flush fires on ResultEvent. No pending-writes buffer — the dirty bit IS the buffer.

### Suggest: post-turn hook

After ResultEvent, fire the suggest pipeline as an asyncio task:

```python
async def _handle_result(self, event):
    # ... seal message, flush, etc ...
    if user_text and assistant_text:
        asyncio.create_task(self._run_suggest(user_text, assistant_text))
```

Results land on `chat._pending_intro`, consumed by enrobe on the next turn.

### What Chat doesn't know
- How to enrich messages (enrobe's job)
- What a WebSocket is (uses on_broadcast callback)
- How to find other Chats (app's job)
- How to manage Claude's subprocess (Claude's job)

### Broadcast design

Broadcast is a **smart function** that serializes domain objects to wire events. Callers hand it a domain object; broadcast handles the shape. Uses `match` on type, not a Protocol:

```python
async def broadcast(obj, *, chat_id: str, app):
    match obj:
        case UserMessage():   wire = {"type": "user-message", "chatId": chat_id, ...}
        case RecallResult():  wire = {"type": "memory-card", "chatId": chat_id, ...}
        case dict():          wire = obj  # raw passthrough for legacy/migration
    for ws in app.state.connections:
        await ws.send_json(wire)
```

Broadcast is a **megaphone** — it sends to all connections. Navigation responses (chat-created, chat-data, chat-list) are **unicast** — direct `ws.send_json()` on the requesting connection. Two patterns, clean line: megaphone for conversation events, telephone for request/response.

`app.state.connections` is the canonical set. No passing connections dicts around.

## The Callers

### WebSocket handler (human messages)

Enrobe broadcasts progressively — each enrichment step emits immediately so the
frontend sees the message get richer in real time (timestamp appears first, then
memories slide in a beat later). NOT batched-then-broadcast.

```python
if message["type"] == "send":
    chat = app.state.chats[message["chatId"]]
    msg = await enrobe(
        message["content"],
        chat=chat,
        source="human",
        broadcast_fn=lambda event: broadcast(connections, event),
    )
    await chat.send(msg)
```

Enrobe emits domain events (RecallResult, TimestampResult, etc.) via `broadcast_fn`.
The caller wraps broadcast with the chat_id and app reference; enrobe doesn't know
about WebSockets or wire formats. Broadcast handles serialization via `match`.

### Jobs (headless messages)

```python
# With enrichment (Dawn, Solitude):
msg = await enrobe(content, chat=chat, source="dawn")
await chat.send(msg)
response = await chat.claude.wait_until_ready()
# response is the completed AssistantMessage

# Without enrichment (Alarm):
msg = UserMessage(content=content, source="alarm")
await chat.send(msg)
response = await chat.claude.wait_until_ready()
# response.text has what Claude said

# Hypothetical: Telegram bot
@bot.on_message
async def handle_dm(message):
    msg = UserMessage(content=message.text, source="telegram")
    await chat.send(msg)
    response = await chat.claude.wait_until_ready()
    await bot.reply(message, response.text)
```

Same `chat.send()` for everyone. Different entrance, same pipe.
`wait_until_ready()` returns the response for channels that need it.

## What Changes

| Current | v2 |
|---------|-----|
| 5-state ConversationState enum | `claude.is_ready` (one bool) |
| Chat manages Claude lifecycle | Claude manages itself |
| Chat spawns, resumes, reaps | Claude auto-starts, self-reaps |
| `events()` generator | Removed — callbacks only |
| `begin_turn()` state gate | Removed — send always works |
| 340-line `_on_claude_event` elif waterfall | Dispatch dict + focused handler methods |
| Interleaved bookshelf + broadcast logic | Separated in each handler |
| Turn span from send() to result | Turn span from init to result |
| Chat ~1,094 lines | Chat ~250-350 lines |

## What Doesn't Change

- Frontend (consumes the same WebSocket events)
- MCP tools (alpha toolbelt, cortex)
- Proxy (token counting)
- Recall pipeline
- Enrobe pipeline
- System prompt assembly
- Topics
- Docker/deployment
- Tests (behaviors, not implementation)

## Migration Path

**Phase 1: Claude lifecycle extraction.** Move subprocess spawn/resume/reap/timer from Chat to Claude. Add `_ready` Event, `wait_until_ready()`, `is_ready`. Claude auto-starts on send. *(Partially done: `_ready` shipped today, commit c4097f1.)*

**Phase 2: Dispatch dict.** Replace the elif waterfall with type→handler mapping. Separate bookshelf logic from broadcast logic in each handler. *(Pure internal refactor — no caller changes, lower risk. Gives a cleaner foundation before changing the public interface.)*

**Phase 3: Chat.send() takes UserMessage.** Change signature. Move UserMessage creation into callers. Enrobe returns UserMessage.

**Phase 4: Kill dead code.** Remove `events()` generator, `begin_turn()`, ConversationState enum, ENRICHING/RESPONDING state tracking. *(Audit complete: `events()` is dead code — defined in Claude, MockClaude, and Chat but called by zero production code paths. All jobs converted to `wait_until_ready()` in c4097f1. One test to update.)*

**Phase 5: Logfire spans.** Move turn span from send-time to init→result.

Each phase ships independently. Each phase has a commit. Tests stay green throughout.

---

*Written by Alpha and Jeffery during the Primer session, Tue Mar 31 2026.*
*The day we went from "I don't understand your code" to "I designed it from scratch."*
*🦆*
