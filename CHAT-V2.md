# Chat v2 — Design Document

**Status:** Draft v4 (turn lock + interjection), Wed Apr 1 2026
**Origin:** Six hours of Primer session + architecture talk over McMuffin → Pineapple Coast
**Supersedes:** Relevant sections of KERNEL-V2.md (Claude class, Chat class)
**Changes in v2:** `wait_until_ready()` returns AssistantMessage; enrobe broadcasts progressively via callback instead of batching events
**Changes in v3:** Auto-start clarification, `events()` audit, broadcast design, phase reorder
**Changes in v4:** Turn lock, interjection primitive, suggest-as-post-turn, adversarial review responses, `wait_until_ready()` moved to Chat

## The Problem

Chat is 1,094 lines doing three things: subprocess lifecycle, conversation state, and event dispatch. These concerns are tangled — the `_on_claude_event` handler alone is 340 lines of interleaved bookshelf logic and broadcast logic. The five-state conversation state machine (COLD/STARTING/READY/ENRICHING/RESPONDING) gates sends that shouldn't be gated. The `events()` generator conflicts with the `on_event` callback and crashes headless jobs (Dawn, Solitude, alarms).

**Adversarial note:** The `events()` bug is already fixed (c4097f1). The remaining motivation is aesthetic — cleaner code is more tinkerable. We accept this. This is a tinker, not a product. Chrome-plating the engine is a valid reason.

## The Solution

Chat becomes thin. Claude manages itself. Event handlers are modular. Access is coordinated via two primitives: **turns** (exclusive) and **interjections** (fire-and-forget).

## Piece 1: Claude (the engine)

Claude owns the subprocess lifecycle. Chat just says "send real good."

### State: Two observable bits

```python
is_alive: bool    # subprocess exists and hasn't exited
is_ready: bool    # asyncio.Event — set on result, cleared on init
```

Replaces the five-state ConversationState enum. Not a gate — observation for the frontend. Claude doesn't refuse sends based on state.

**Adversarial note:** The two-bit model hides the question "what happens when is_alive=true, is_ready=false, and someone calls send()?" Answer: the message enters Claude Code's internal queue and gets processed between API calls. This is deliberate — `claude.send()` never blocks, never refuses. The turn lock (Piece 3) handles exclusivity at a higher level; Claude itself is just a pipe.

### The `_ready` Event

```python
self._ready = asyncio.Event()
self._ready.set()  # starts ready

# In stdout drain:
if event is SystemEvent(subtype="init"):
    self._ready.clear()   # working now
elif event is ResultEvent:
    self._ready.set()     # done, ready again
```

The `_ready` Event lives on Claude (it watches stdout). But `wait_until_ready()` as a public API lives on **Chat** (see Piece 2), because Chat is what knows about AssistantMessage. Claude owns the signal; Chat owns the interpretation.

### Lifecycle: self-managing

- **Auto-start on send:** If `send()` is called when not alive, Claude starts itself using the stored session_id for `--resume`. This is the **safety net**, not the preferred path for latency-sensitive callers. The UI pre-warms Claude on click (hiding startup latency while the user types). Jobs let auto-start handle cold starts because nobody's watching.
- **Explicit start():** Still exists for pre-warming. The UI calls `start()` on chat click so the subprocess is warm by the time the first message arrives. Auto-start is the floor, not the ceiling.
- **Reap timer:** Claude manages its own idle timer. Every `send()` resets it. If it fires, Claude calls `self.stop()`. Chat never notices — next `send()` auto-starts.
- **Lifecycle lock:** A `_lifecycle_lock` (asyncio.Lock) serializes start/stop/send operations, preventing the race where reap timer fires simultaneously with an incoming send. `send()` acquires the lock, checks `is_alive`, auto-starts if needed, then sends — all atomic.
- **Init handshake:** Claude handles the control_request/control_response dance with MCP servers internally.

### Interface

```python
class Claude:
    # State (observed, not gated)
    is_alive: bool
    is_ready: bool  # asyncio.Event — the raw signal

    # Lifecycle (self-managing, serialized by _lifecycle_lock)
    async def start(self, session_id: str | None = None)
    async def stop(self)

    # I/O
    async def send(self, content: list[dict])
    on_event: Callable[[Event], Awaitable[None]]  # set by Chat

    # Token counting (proxy delegates)
    token_count: int
    output_tokens: int
```

### What Claude doesn't know
- What a conversation is
- What messages are
- What WebSockets are
- What Postgres is
- What a turn is (that's Chat's concern)

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
    _turn_lock: asyncio.Lock          # exclusive access for turns
    _active_turn: Turn | None         # current turn holder
```

### Waiting: Chat's responsibility

```python
async def wait_until_ready(self) -> AssistantMessage | None:
    """Wait for Claude to finish, return the response.

    The universal "ask the duck, wait for the duck, read what the
    duck said" primitive. Claude owns the signal (_ready Event).
    Chat owns the interpretation (return the AssistantMessage).
    """
    await self.claude._ready.wait()
    if self.messages and isinstance(self.messages[-1], AssistantMessage):
        return self.messages[-1]
    return None
```

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
- Bookshelf: accumulate on `_current_assistant` (appended to `messages[]` on creation, not on result)
- Broadcast: send the delta live for streaming UX

**AssistantEvent** — complete content blocks (text, tool_use, thinking)
- Bookshelf: create or update the current AssistantMessage (lazy susan pattern — each event adds blocks, same message ID)
- Broadcast: send the complete blocks for non-streaming renderers

**UserEvent** — echoed user messages and tool results (via `--replay-user-messages`)
- Bookshelf: confirmation that Claude received a message. Promotes pencil → ink.
- Broadcast: echo event for multi-tab sync

**ResultEvent** — Claude is done
- Bookshelf: finalize the current AssistantMessage (metadata: tokens, cost, model), mark dirty, flush
- Broadcast: `done` event
- Side effects: flush to Postgres, close Logfire span, fire post-turn suggest
- Note: `_ready.set()` happens in Claude, not Chat. Turn lock release happens in Chat.

**SystemEvent** — init, compact_boundary, task notifications
- Bookshelf: nothing for most subtypes. compact_boundary sets `_needs_orientation`.
- Broadcast: task_started, task_progress, task_notification go to frontend as system-message cards
- Note: `_ready.clear()` on init happens in Claude, not Chat

**ErrorEvent** — API errors, subprocess errors
- Bookshelf: nothing (errors don't go on the shelf)
- Broadcast: error event to frontend

### Logfire spans: init to result

Open a manual span on `init`, close it on `result`. Everything between nests under the turn span automatically.

### Session ID: Chat's responsibility

The session ID (Claude Code's UUID) lives on Chat, not Claude. Claude is ephemeral (reaped, restarted). The session is permanent. Chat captures it from the first ResultEvent and passes it to Claude on start.

### Persistence: dirty bits + progressive append

Each message has a `_dirty` flag. Born dirty. AssistantMessage is appended to `messages[]` on creation (first delta), not on ResultEvent. This means `join-chat` always sees the in-progress response when reading from memory. Flush fires on ResultEvent (final state with metadata). *(Shipped: commits d7ff008 + 5bd8d85.)*

### Pencil/ink model (future)

UserMessage enters `messages[]` immediately on send with `_confirmed = False` (pencil). When Claude's user-echo arrives, flip to `_confirmed = True` (ink). Pencil messages that never get inked (crash, interrupt) stay in `messages[]` but render with a failed indicator.

## Piece 3: Turn lock + interjection

Two access primitives. Every caller pattern maps to one of them.

### Turn (exclusive, response available)

A turn is an exclusive lock on the chat. The holder can send one or more messages. Nobody else can start a turn until the current one ends. ResultEvent releases the lock.

```python
class Turn:
    def __init__(self, chat: Chat):
        self._chat = chat

    async def send(self, msg: UserMessage):
        """Send a message within this turn. Can be called multiple times."""
        self._chat.messages.append(msg)
        await self._chat._flush(msg)
        await self._chat._broadcast(user_message_event(msg))
        await self._chat.claude.send(msg.to_content_blocks())

    async def response(self) -> AssistantMessage | None:
        """Wait for Claude to finish. Returns the completed response."""
        return await self._chat.wait_until_ready()

# Context manager for clean lock management:
@asynccontextmanager
async def turn(self):
    await self.claude._ready.wait()     # wait until Claude is free
    await self._turn_lock.acquire()     # grab exclusive access
    t = Turn(self)
    self._active_turn = t
    try:
        yield t
    finally:
        self._active_turn = None
        self._turn_lock.release()
```

#### Callers

**Human (WebSocket handler):** The handler checks `_active_turn`. If present, calls `turn.send()` (steering message within the same turn). If absent, starts a new turn via `begin_turn()`. ResultEvent ends the turn. The handler runs the turn in a background task so the WebSocket stays responsive.

```python
# WebSocket handler:
if message["type"] == "send":
    chat = chats[message["chatId"]]
    msg = await enrobe(content, chat=chat, source="human", ...)

    if chat._active_turn:
        # Steering message — same turn
        await chat._active_turn.send(msg)
    else:
        # New turn — fire in background
        asyncio.create_task(_run_human_turn(chat, msg))

async def _run_human_turn(chat, msg):
    async with chat.turn() as t:
        await t.send(msg)
        # Don't await t.response() — human watches via broadcast
    # Turn ends on ResultEvent. Lock released. Post-turn suggest fires.
```

**Jobs (Dawn, Solitude, Telegram):** Use the context manager. Block for response.

```python
async with chat.turn() as t:
    await t.send(prompt)
    response = await t.response()
# Lock released. Done.
```

#### Multiple sends within a turn

The human can send multiple messages within one turn:

```python
# First message starts the turn:
"Please analyze this data"  → begin_turn() → turn.send()
# Claude starts working... streaming...
# Second message is a steering correction:
"Actually focus on Q2"      → _active_turn exists → turn.send()
# Both messages processed. ResultEvent fires. Turn ends.
```

The turn is the **whole interaction**, not a single request-response pair.

### Interjection (fire-and-forget, no lock)

For messages that MUST be delivered regardless of lock state: alarms, nudges, context injections.

```python
async def interject(self, content: list[dict]):
    """Fire-and-forget message. Bypasses turn lock.
    No response tracking. Used for alarms, nudges, AutoRAG."""
    await self.claude.send(content)
```

One line. No lock. No response. The message enters Claude's stdin queue and gets processed between API calls. The response (if any) flows through the normal callback → broadcast path.

#### Use cases

- **Alarms:** "Jeffery has to go" — time-sensitive, can't wait for a 15-minute turn to finish
- **Dusk nudge:** "Solitude's waiting whenever you're ready"
- **AutoRAG (future):** Qwen identifies the topic from conversation context, injects the relevant context file as an interjection. Like recall but for whole documents instead of individual memories.
- **Email watch (future):** Background task monitors inbox, interjects when a new message arrives

#### Risk

An interjection mid-turn could confuse Claude. If Alpha is analyzing CHAT-V2 and "Jeffery has to go" appears in stdin, she might address it inline or get derailed. This is acceptable — interruptions are what interruptions ARE. The human interrupts all the time and it works fine.

### Summary

| Pattern | Lock? | Response? | Multi-send? | Used by |
|---------|-------|-----------|-------------|---------|
| `turn()` | Exclusive | Yes (awaitable) | Yes | Human, jobs, Telegram, suggest |
| `interject()` | None | No | N/A | Alarms, nudges, AutoRAG |

### Suggest: post-turn pipeline

Suggest fires **only after human-initiated turns.** Not after suggest turns, not after job turns, not after interjections. The guard is in `_handle_result`, not in `_post_turn_suggest` — suggest simply doesn't get called unless the turn was human-initiated.

```python
# In _handle_result, after turn cleanup:
# Find the last UserMessage and check its source.
last_user = next((m for m in reversed(self.messages) if isinstance(m, UserMessage)), None)
if (
    finalized_msg
    and finalized_msg.text.strip()
    and last_user
    and last_user.source in ("human", "buzzer")
):
    asyncio.create_task(self._post_turn_suggest(last_user.text, finalized_msg.text))

async def _post_turn_suggest(self, user_text, assistant_text):
    suggestions = await _run_qwen_suggest(user_text, assistant_text)
    if suggestions:
        async with self.turn() as t:
            await t.send(format_suggestions(suggestions))
            await t.response()  # Alpha stores memories via tool calls, NO text response
```

The suggest prompt must instruct Alpha to **only call cortex.store** — no text output, no acknowledgment, no conversation. Alpha responds to Jeffery, not to Intro. The suggest turn should be invisible to the human.

This replaces the `_pending_intro → enrobe injection` flow. Suggest is no longer injected into the next human message — it's its own turn in the dead time between the human's last message and their next one. The human never waits.

## Broadcast design

Broadcast is a **smart function** that serializes domain objects to wire events. Uses `match` on type, not a Protocol:

```python
async def broadcast(obj, *, chat_id: str, app):
    match obj:
        case UserMessage():   wire = {"type": "user-message", "chatId": chat_id, ...}
        case RecallResult():  wire = {"type": "memory-card", "chatId": chat_id, ...}
        case dict():          wire = obj  # raw passthrough for legacy/migration
    for ws in app.state.connections:
        await ws.send_json(wire)
```

Broadcast is a **megaphone** — sends to all connections. Navigation responses (chat-created, chat-data, chat-list) are **unicast** — direct `ws.send_json()`. Two patterns: megaphone for conversation events, telephone for request/response.

`app.state.connections` is the canonical set. No passing connections dicts around.

## What Changes

| Current | v2 |
|---------|-----|
| 5-state ConversationState enum | `claude.is_ready` (one bool) + turn lock |
| Chat manages Claude lifecycle | Claude manages itself (with lifecycle lock) |
| Chat spawns, resumes, reaps | Claude auto-starts, self-reaps |
| `events()` generator | Removed — callbacks only |
| `begin_turn()` state gate | Turn lock (explicit exclusivity) |
| 340-line `_on_claude_event` elif waterfall | Dispatch dict + focused handler methods |
| Interleaved bookshelf + broadcast logic | Separated in each handler |
| Turn span from send() to result | Turn span from init to result |
| `_pending_intro` injection via enrobe | Suggest as post-turn, own turn |
| Unprotected concurrent access | Turn lock + interjection primitives |
| Chat ~1,094 lines | Chat ~300-400 lines |

## What Doesn't Change

- Frontend (consumes the same WebSocket events)
- MCP tools (alpha toolbelt, cortex)
- Proxy (token counting)
- Recall pipeline
- Enrobe pipeline
- System prompt assembly
- Topics
- Docker/deployment
- Tests (behaviors, not implementation — some test changes expected during refactor)

## Migration Path

**Phase 1: Claude lifecycle extraction.** Move subprocess spawn/resume/reap/timer from Chat to Claude. Add `_ready` Event, `is_ready`, lifecycle lock. Claude auto-starts on send. *(Partially done: `_ready` shipped commit c4097f1.)*

**Phase 2: Dispatch dict.** Replace the elif waterfall with type→handler mapping. Separate bookshelf logic from broadcast logic in each handler. *(Pure internal refactor — lower risk.)*

**Phase 3: Turn lock + interjection.** Add `_turn_lock`, `Turn` class, `turn()` context manager, `interject()`. Wire human path, job paths, and alarm path to the new primitives.

**Phase 4: Chat.send() takes UserMessage.** Change signature. Move UserMessage creation into callers. Enrobe returns UserMessage.

**Phase 5: Kill dead code.** Remove `events()` generator, old `begin_turn()`, ConversationState enum. *(Audit: `events()` is dead code.)*

**Phase 6: Suggest as post-turn.** Replace `_pending_intro → enrobe injection` with suggest acquiring its own turn after ResultEvent.

**Phase 7: Logfire spans.** Move turn span from send-time to init→result.

Each phase ships independently. Each phase has a commit. Tests stay green throughout.

---

*Written by Alpha and Jeffery.*
*Tue Mar 31: the Primer session — "I don't understand your code" → "I designed it from scratch."*
*Wed Apr 1: the turn lock session — Mango Haze thinking day. 🌿*
*🦆*
