# Smart Chat — Architectural Refactor

Branch: `smart-chat` (worktree at Alpha-App-smartchat)
Started: Tue Mar 24, 2026, Blue Dream afternoon

## The Problem

Claude Code is a persistent subprocess with a continuous stdout stream. Our current
architecture treats it as a request-response system: send a message, consume events
until ResultEvent, then stop reading. Between turns, nobody reads stdout. Background
events (agent completions, system messages) queue up and play back out of order on
the next send — the desync bug (#43).

## The Solution

**Chat becomes the model of the conversation.** Not a subprocess wrapper — the actual
authoritative list of messages.

### Key Principles

1. **Claude's stdout is always drained.** A background task reads forever. No gap
   between turns. Events flow through a callback, not a generator.

2. **Chat.messages[] is the truth.** UserMessages from Chat.send(), AssistantMessages
   from the stdout drain. The list IS the conversation.

3. **Modal UI.** I talk or you talk, never both. Send → Stop while responding.
   Esc also stops. The frontend always knows who's talking.

4. **Streaming is eye candy.** Stream events provide real-time typing. The coalesced
   AssistantMessage at ResultEvent is the record. Deltas are ephemeral.

5. **"Gimme the fucking chat" on connect.** One payload, full messages list. No replay.

### Phase 1: Continuous drain + callback (DONE — 818b444)

- Claude._drain_stdout() as a background task
- on_event callback replaces events() generator
- ClaudeState.RUNNING replaces READY
- use_proxy flag, stdin/stdout Logfire tracing
- events() generator kept as deprecated compat path
- 135 tests pass

### Phase 2: Smart Chat — Chat as conversation model

Chat gets:
- `messages: list[UserMessage | AssistantMessage]` — the canonical conversation
- `_accumulator: AssistantMessage | None` — in-progress response being built
- `_on_event(event)` — internal callback wired to Claude's on_event

The callback does:
- **StreamEvent** → broadcast delta to WebSocket (ephemeral eye candy)
- **AssistantEvent** → update accumulator with content blocks
- **UserEvent** (echo) → push UserMessage onto messages[], broadcast
- **ResultEvent** → finalize accumulator, push onto messages[], broadcast coalesced
  message, persist to Postgres, start reap timer, arm suggest
- **SystemEvent** → handle compact_boundary (reset orientation), broadcast

Chat.send() does:
- Enrobe the content (timestamp, orientation, recall, intro)
- Push UserMessage onto messages[]
- Write to Claude's stdin
- Return immediately (no blocking on events)

The `stream_chat_events()` function in streaming.py is absorbed into Chat's callback.
The `handle_new_turn()` function in turn.py becomes: resurrect if needed → enrobe →
chat.send(). No streaming loop, no awaiting events.

wake() and resurrect() pass on_event=self._on_event to _make_claude().

### Phase 3: Kill dead code

Remove:
- Chat.events() generator
- Chat.begin_turn() state transition (ENRICHING is dead — send() goes straight to stdin)
- ConversationState.ENRICHING, RESPONDING (only COLD, STARTING, RUNNING remain)
- streaming.py's stream_chat_events() — absorbed into Chat callback
- The approach light check (already disabled, just remove the dead code)
- --replay-user-messages flag (we don't need user echoes from stdout)
- turn.py simplified: resurrect → enrobe → chat.send(), return
- ws.py simplified: handle_send → handle_new_turn (no streaming_tasks dict)

### Frontend (Phase 4, separate)

- Modal composer: locked during response, Send → Stop, Esc stops
- join-chat returns Chat.messages[] as one payload
- No replay buffer, no event archaeology
- Stream deltas rendered as work-in-progress, replaced by truth on result

### Protocol Probe Results

Documented in claude_protocol_probe_results.md. Key findings:

1. User echoes arrive MID-STREAM for the first message (between stream events)
2. Queued messages: user echoes arrive in a burst BEFORE streaming for the response
3. Claude Code processes queued messages as ONE combined turn (one ResultEvent)
4. message_start is the first streaming event — but user echo may come before or after it
5. Result is the reliable end signal

### What the callback needs to handle

The tricky part: stream events arrive BEFORE the user echo for the first message.
Chat needs to handle events in whatever order they arrive:

- If stream events arrive before user echo: start accumulating into _accumulator,
  broadcast deltas. When user echo arrives, insert UserMessage at the right position.
  (But in modal mode, the frontend already HAS the user message — it rendered it
  optimistically when the user hit Send.)

- If user echo arrives first (normal for queued messages): push UserMessage, then
  start accumulating.

The modal UI helps enormously: the frontend never needs to reconcile the echo with
an optimistic message because there's only one thing happening at a time.

### Test Strategy

- Protocol probe script validates stdout event ordering
- Exercise script (claude_exercise.py) validates the callback flow
- Existing 135 unit tests should still pass (compat path)
- New tests for Smart Chat: accumulator behavior, callback firing, messages list
- Manual testing on port 18011 (worktree) alongside main on 18010
