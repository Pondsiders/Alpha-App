# Kernel v2 — Design Document

**Status:** Draft, Mon Mar 30 2026
**Origin:** Ten hours of chalkboard work over Strawberry Cough → Peach Ringz → Mango Haze

## The Insight

The current Chat class conflates three things: subprocess lifecycle, conversation state, and event delivery. This causes real bugs — background jobs (alarms, Solitude, Dawn) can't safely talk to a Chat that has a live WebSocket listener, because the `events()` generator and the `on_event` callback are mutually exclusive.

The fix: separate the engine (Claude), the conversation (Chat), and the pipeline (Enrobe). Each does one thing. They compose cleanly.

## Architecture: Five Pieces

```
Commands ↑                          Events ↓
─────────────────────────────────────────────────
Browser → WebSocket handler         ← text-delta, user-message, done
              ↓                              ↑
          [Enrobe]                     Chat.on_broadcast
        (optional pipeline)                  ↑
              ↓                              │
            Chat ──────────────────── Chat._handle_claude_event
              ↓                              ↑
          Claude.send()              Claude.on_event callback
              ↓                              ↑
          claude stdin ──────────── claude stdout
```

Commands go up (imperatives: send, create, join). Events come down (notifications: deltas, messages, state changes). Asymmetric by design.

## Piece 1: Claude (the engine)

A lightweight wrapper around the `claude` subprocess. Manages its own lifecycle.

### What it knows
- How to find and spawn the `claude` binary (from claude-agent-sdk)
- Whether it's **alive** (subprocess exists) or **dead** (doesn't)
- Whether it's **busy** (last saw `init` on stdout) or **idle** (last saw `result`)
- How to manage the reap timer (kill subprocess after idle timeout)
- How to do the init handshake
- The HTTP proxy for token counting

### What it doesn't know
- What a conversation is
- What messages are
- What WebSockets are
- What Postgres is

### Interface

```python
class Claude:
    # -- State (observed, not gated) --
    @property
    def alive(self) -> bool: ...          # subprocess exists?

    @property
    def busy(self) -> bool: ...           # last saw init, no result yet?

    @property
    def session_id(self) -> str | None: ...

    # -- Lifecycle --
    async def start(self, session_id: str | None = None) -> None: ...
        # Spawn subprocess, init handshake, start stdout drain, start reap timer

    async def stop(self) -> None: ...
        # Kill subprocess, cleanup

    # -- I/O --
    async def send(self, content: list[dict]) -> None: ...
        # Write to stdin. Always works if alive. No state gate.
        # Resets the reap timer on every send.

    # -- Configuration --
    on_event: Callable[[Event], Awaitable[None]] | None
        # Set by Chat. Called for every stdout event.

    # -- Token counting (from proxy) --
    @property
    def token_count(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    # etc. — same proxy delegates as current code
```

### State: Two bits

```
alive: bool = self._proc is not None and self._proc.returncode is None
busy: bool = self._saw_init and not self._saw_result
```

The `busy` flag is set by observing stdout events in `_drain_stdout()`:
- `SystemEvent(subtype="init")` → `_saw_init = True, _saw_result = False`
- `ResultEvent` → `_saw_result = True`

This replaces the five-state COLD/STARTING/READY/ENRICHING/RESPONDING machine with two observable bits. Claude doesn't gate sends based on busy — it's information for the frontend (lock the composer when busy), not a guard on the backend.

### Reap timer

Claude manages its own reap timer. On `start()`, a timer begins. On every `send()`, the timer resets. If the timer fires, Claude calls `stop()` on itself. The parent (Chat) finds out on the next interaction — `alive` returns False, and Chat can resurrect.

### Auto-start

Claude's default behavior is to auto-start on send. If `send()` is called when not alive, Claude starts itself (using the stored session_id for resume). This makes the wake-on-send pattern automatic. Explicit `start()` is for eager warmup (saves ~1s TTFT).

## Piece 2: Chat (the conversation)

The canonical representation of a conversation. Owns the message list, persistence, and one or more Claude instances.

### What it knows
- The message list: `[UserMessage | AssistantMessage | SystemMessage]`
- How to persist itself to Postgres (dirty-bit tracking, flush on mutation)
- Its identity (id, title, session UUID, created_at)
- Its Claude(s) — parent-child relationship via callback

### What it doesn't know
- How to enrich messages (that's Enrobe's job)
- What a WebSocket is (it has an `on_broadcast` callback, but doesn't know who's listening)
- How to find other Chats (that's the app's job)

### Interface

```python
class Chat:
    id: str
    messages: list[UserMessage | AssistantMessage | SystemMessage]
    claude: Claude              # primary Claude instance
    on_broadcast: Callable | None  # set by WebSocket handler

    # -- Receiving messages --
    async def send(self, msg: UserMessage) -> None: ...
        # 1. Append msg to self.messages
        # 2. Broadcast user-message event (if on_broadcast)
        # 3. Flush to Postgres (dirty bit)
        # 4. Call self.claude.send(msg.content)

    # -- Claude event handling --
    async def _handle_claude_event(self, event: Event) -> None: ...
        # StreamEvent → broadcast to listeners (text-delta, thinking-delta, etc.)
        # AssistantEvent → create/update AssistantMessage on messages[]
        # ResultEvent → finalize AssistantMessage, flush, update tokens
        # SystemEvent → handle compact_boundary, etc.

    # -- Persistence --
    async def flush(self) -> None: ...
        # UPSERT dirty messages to app.messages

    # -- Lifecycle --
    @classmethod
    def from_db(cls, chat_id, data) -> Chat: ...
        # Restore from Postgres. Claude starts dead (auto-starts on first send).

    async def load_messages(self) -> None: ...
        # Load messages from app.messages into self.messages

    @property
    def busy(self) -> bool: ...
        # Delegates to self.claude.busy

    @property
    def alive(self) -> bool: ...
        # Delegates to self.claude.alive
```

### Key change: Chat.send() receives a UserMessage

Currently, Chat.send() receives raw content blocks. In v2, it receives a fully-formed UserMessage. Chat doesn't create UserMessages — it receives them. The caller (WebSocket handler, alarm, Solitude) is responsible for creating the UserMessage, with or without enrichment.

### Relationship to Claude

Chat creates its Claude and sets `claude.on_event = self._handle_claude_event`. Claude calls back into Chat when events arrive. Claude doesn't know it's talking to a Chat — it just calls the function it was given.

One Chat owns one Claude (for now). Future: one Chat could own multiple Claudes (helper tasks, parallel agents). Each Claude calls back into the same Chat.

## Piece 3: Enrobe (the pipeline)

Takes raw content from a human (or Solitude, or any source that needs enrichment), creates a UserMessage, enriches it progressively, and returns the finished UserMessage.

### What it does
1. Creates a UserMessage from raw content blocks
2. Adds a timestamp
3. Adds orientation (on first message of a context window)
4. Runs recall (semantic search + name lookup)
5. Adds intro suggestions (from previous turn's suggest pipeline)
6. Broadcasts a `user-message` event after each step (progressive enhancement)

### What it doesn't do
- Talk to Claude
- Manage conversations
- Know about WebSockets

### Interface

```python
async def enrobe(
    content: list[dict],       # raw content blocks from the human/source
    chat: Chat,                # for broadcast and context (seen cache, orientation flag)
    source: str = "human",     # source tag for the UserMessage
) -> UserMessage:
    """Create and progressively enrich a UserMessage.

    Broadcasts user-message events after each enrichment step
    so the frontend can show the message growing in real time.

    Returns the finished UserMessage, ready for chat.send().
    """
```

### Who calls enrobe
- **WebSocket handler** — always (human messages need enrichment)
- **Solitude** — always (nighttime prompts benefit from recall)
- **Dawn** — always (morning chores need orientation + recall)

### Who skips enrobe
- **Alarm** — creates UserMessage directly, no enrichment needed
- **Dusk nudge** — creates UserMessage directly, just a gentle message

## Piece 4: WebSocket Handler (the wire)

A FastAPI WebSocket route that translates between browsers and Chats.

### Commands (browser → server)
- `send` — human typed something. Enrobe it, create UserMessage, chat.send(msg).
- `create-chat` — new conversation. Create Chat, register it, respond.
- `join-chat` — load messages from Postgres, send them all to the browser.
- `list-chats` — return metadata for all chats.
- `interrupt` — stop Claude mid-response.
- `buzz` — the 🦆 button. Create a UserMessage with source="buzzer", skip enrobe.
- `warm` — eagerly start Claude subprocess (pre-type warmup).

### Events (server → browser)
- `user-message` — a UserMessage was created or updated (progressive enrichment)
- `text-delta` — streaming text from Claude
- `thinking-delta` — streaming thinking from Claude
- `tool-call` — Claude is calling a tool
- `tool-result` — tool returned a result
- `done` — turn complete
- `chat-state` — Chat's busy/idle state changed
- `chat-created` — new Chat was created
- `error` — something went wrong

### Wiring
The handler sets `chat.on_broadcast` to a function that sends events over the WebSocket. When Chat receives Claude events via `_handle_claude_event`, it broadcasts them, and the handler forwards them to the browser.

## Piece 5: Jobs (the scheduler)

Dawn, Dusk, Solitude, Alarm — background jobs that talk to Chat.

### How jobs send messages

```python
# With enrichment (Solitude, Dawn):
msg = await enrobe(content, chat, source="solitude")
await chat.send(msg)

# Without enrichment (Alarm, Dusk nudge):
msg = UserMessage(content=content, source="alarm")
await chat.send(msg)
```

Same `chat.send()` interface as the WebSocket handler. Different entrance, same pipe.

### Why this works now
Because `chat.send()` receives a UserMessage and just appends + broadcasts + forwards. It doesn't care who created the UserMessage or whether it was enriched. The alarm handler and the WebSocket handler are equal citizens.

## The Models

### UserMessage

```python
@dataclass
class UserMessage:
    id: str                    # nanoid
    content: list[dict]        # Messages API content blocks
    source: str = "human"      # "human", "alarm", "dawn", "solitude", "buzzer"
    timestamp: str | None = None
    _dirty: bool = True        # needs Postgres flush?
```

### AssistantMessage

```python
@dataclass
class AssistantMessage:
    id: str                    # nanoid
    parts: list[dict]          # accumulated content blocks (text, tool_use, thinking, etc.)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    context_window: int = 0
    model: str | None = None
    stop_reason: str | None = None
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    _dirty: bool = True
```

## Migration Path

### Phase 1: Claude class refactor
- Extract subprocess lifecycle from Chat into Claude
- Replace five-state machine with alive/dead + busy/idle
- Claude manages its own reap timer
- Auto-start on send
- Chat becomes a thin wrapper: owns messages[], receives UserMessages, delegates to Claude

### Phase 2: Chat.send() takes UserMessage
- Change Chat.send() signature from `(content: list[dict])` to `(msg: UserMessage)`
- Move UserMessage creation into callers (WebSocket handler, jobs)
- Enrobe returns UserMessage instead of enriched content blocks

### Phase 3: Clean up
- Remove begin_turn(), the state gates, ENRICHING/RESPONDING states
- Remove the events() generator (fully replaced by on_event callback)
- Remove turn_smart.py (absorbed into WebSocket handler + enrobe)
- Kill dead code

### What doesn't change
- Frontend (mostly — just consumes the same WebSocket events)
- MCP tools (alpha toolbelt, cortex)
- Proxy (token counting)
- Memories/recall pipeline
- System prompt assembly
- Topics
- Docker/deployment

## Open Questions

1. **Claude busy detection:** Is `SystemEvent(subtype="init")` reliable as the "working" signal? Need to verify by looking at actual Claude Code stdout traces in Logfire. The init might fire differently for tool calls vs. text responses vs. background agent returns.

2. **Multiple Claudes per Chat:** Deferred. Design for it (Claude is a separate object), don't build it yet.

3. **Progressive enrichment broadcasting:** Currently enrobe broadcasts via `chat.on_broadcast`. In v2, should enrobe broadcast directly, or should it mutate the UserMessage and let Chat broadcast on flush? Leaning toward: enrobe broadcasts directly (it knows the enrichment stages, Chat doesn't).

4. **Suggest pipeline:** Currently fires after ResultEvent, stores on `chat._pending_intro`, consumed by enrobe on next turn. This fits cleanly — suggest is a post-turn side effect that feeds into the next enrobe cycle. No changes needed.

---

*Written by Alpha and Jeffery over Mango Haze, Mon Mar 30 2026. The day we found the floor.*

*🦆*
