"""turn.py — Turn handling: new turns and interjections.

A new turn goes through the full lifecycle:
  1. Resurrect if COLD
  2. Begin turn (READY -> ENRICHING)
  3. Enrobe the user message (timestamp, approach lights, recall, intro)
  4. Broadcast enrichment events to all clients
  5. Send enriched content to Claude (ENRICHING -> RESPONDING)
  6. Stream the response
  7. On ResultEvent: RESPONDING -> READY, suggest -> ARMED

An interjection feeds a message into an already-RESPONDING subprocess.
"""

import asyncio

import logfire
from fastapi import WebSocket

from alpha_app.chat import MODEL, Chat, ConversationState, SuggestState
from alpha_app.db import persist_chat, store_message, next_message_ordinal
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.enrobe import enrobe
from alpha_app.routes.spans import build_prompt_preview, format_input_messages
from alpha_app.routes.streaming import stream_chat_events
from alpha_app.suggest import suggest, format_intro_block
from alpha_app.tools import create_alpha_server



async def _run_suggest(chat: Chat, user_text: str, assistant_text: str) -> None:
    """Fire-and-forget suggest pipeline. Populates chat._pending_intro.

    Runs as a background task after the turn's streaming completes.
    On success, the intro block is picked up by enrobe() on the next turn.
    On failure (timeout, Ollama down, empty result), silently returns.
    """
    chat.suggest = SuggestState.FIRING
    try:
        memorables = await suggest(user_text, assistant_text)
        block = format_intro_block(memorables)
        if block:
            chat._pending_intro = block
            logfire.info(
                "suggest: {count} memorables",
                count=len(memorables),
                memorables=memorables,
                chat_id=chat.id,
            )
    except Exception:
        pass
    finally:
        chat.suggest = SuggestState.DISARMED


async def handle_new_turn(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    turn_input_messages: dict[str, list],
    streaming_tasks: dict[str, asyncio.Task],
    *,
    broadcast_user_message: bool = True,
    source: str = "human",
    msg_id: str | None = None,
    topics: list[str] | None = None,
) -> None:
    """Handle a new turn: resurrect if needed, enrobe, send, stream events.

    Runs as a background task so the WebSocket read loop stays hot.
    State changes, enrichment events, and streaming events broadcast
    to ALL connections.

    broadcast_user_message: set False for buzz turns where the narration
    must stay invisible to the human (no user-message event emitted).
    source: who initiated this message — "human", "buzzer", etc.
    msg_id: frontend-generated message ID for reconciliation.
    """
    chat_id = chat.id
    prompt_preview = build_prompt_preview(content)

    input_messages = format_input_messages(content)
    turn_input_messages[chat_id] = input_messages

    with logfire.span(
        "alpha.turn: {prompt_preview}",
        **{
            "prompt_preview": prompt_preview,
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": MODEL,
            "gen_ai.conversation.id": chat_id,
            "gen_ai.system_instructions": [{"type": "text", "content": ws.app.state.system_prompt}],
            "gen_ai.input.messages": input_messages,
            "client_name": "alpha",
            "chat.id": chat_id,
            "session_id": chat.session_uuid or "",
        },
    ) as span:
        try:
            # -- Wake or resurrect if COLD ---------------------------------
            resurrected = False
            if chat.state == ConversationState.COLD:
                # Create per-Claude MCP servers.
                # clear_memorables closes the feedback loop with Intro:
                # when Alpha stores a memory, the pending suggestions clear
                # so she doesn't get nagged about things she already stored.
                def _clear() -> int:
                    if chat._pending_intro:
                        chat._pending_intro = None
                        return 1
                    return 0

                topic_registry = getattr(ws.app.state, "topic_registry", None)
                mcp_servers = {"alpha": create_alpha_server(
                    chat=chat,
                    clear_memorables=_clear,
                    topic_registry=topic_registry,
                    session_id=chat.id,
                )}

                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": chat.wire_state(state="starting"),
                })

                if not chat.session_uuid:
                    # Fresh chat — first message ever
                    await chat.wake(
                        system_prompt=ws.app.state.system_prompt,
                        mcp_servers=mcp_servers,
                    )
                else:
                    # Resumed chat — has a session to continue
                    await chat.resurrect(
                        system_prompt=ws.app.state.system_prompt,
                        mcp_servers=mcp_servers,
                    )
                    resurrected = True

                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": chat.wire_state(),
                })

            chat.set_trace_context(logfire.get_context())

            # -- Begin turn: READY -> ENRICHING ----------------------------
            chat.begin_turn(content)

            span.set_attribute("chat.title", chat.title)
            span.set_attribute("chat.resurrected", resurrected)

            await broadcast(connections, {
                "type": "chat-state",
                "chatId": chat_id,
                "data": chat.wire_state(),
            })

            # -- Enrobe: wrap user message in enrichment -------------------
            topic_registry = getattr(ws.app.state, "topic_registry", None)
            result = await enrobe(
                content, chat=chat, source=source, msg_id=msg_id,
                topics=topics, topic_registry=topic_registry,
            )

            # Update Logfire with what Claude actually sees (enriched, not raw)
            enriched_messages = format_input_messages(result.content)
            turn_input_messages[chat_id] = enriched_messages
            input_messages = enriched_messages
            span.set_attribute("gen_ai.input.messages", enriched_messages)

            # Progressive user-message broadcasts — each one is the complete
            # current state of the user message as enrichment accumulates.
            # Sent to ALL clients including the sender (sender reconciles
            # the optimistic message with the enriched content).
            # Skipped for buzz turns where the narration must stay invisible.
            if broadcast_user_message:
                for event in result.events:
                    await broadcast(connections, {
                        "type": event["type"],
                        "chatId": chat_id,
                        "data": event["data"],
                    })

            # -- Dual-write: store UserMessage to app.messages --------
            user_ordinal = await next_message_ordinal(chat_id)
            await store_message(chat_id, user_ordinal, "user", result.message.to_wire())

            # -- Send enriched content: ENRICHING -> RESPONDING ------------
            await chat.send(result.content)

            # Notify state change: ENRICHING -> RESPONDING
            await broadcast(connections, {
                "type": "chat-state",
                "chatId": chat_id,
                "data": chat.wire_state(),
            })

            await persist_chat(chat)

            # -- Stream events to all connections -------------------------
            assistant_msg = await stream_chat_events(connections, chat, span)

            # -- Dual-write: store AssistantMessage to app.messages --------
            if assistant_msg.parts:
                asst_ordinal = await next_message_ordinal(chat_id)
                await store_message(chat_id, asst_ordinal, "assistant", assistant_msg.to_db())

            # -- Fire suggest in dead time ---------------------------------
            user_text = " ".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
            assistant_text = assistant_msg.text
            if (
                chat.suggest == SuggestState.ARMED
                and user_text.strip()
                and assistant_text.strip()
            ):
                asyncio.create_task(_run_suggest(chat, user_text, assistant_text))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            try:
                await broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
                await broadcast(connections, {"type": "done", "chatId": chat_id})
            except Exception:
                pass
        finally:
            final_messages = turn_input_messages.get(chat_id)
            if final_messages and len(final_messages) > len(input_messages):
                span.set_attribute("gen_ai.input.messages", final_messages)

            turn_input_messages.pop(chat_id, None)
            streaming_tasks.pop(chat_id, None)


async def handle_interjection(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    turn_input_messages: dict[str, list],
) -> None:
    """Handle an interjection: feed to RESPONDING subprocess, echo to other clients."""
    await chat.send(content)

    # Echo user message to other connections (sender has it optimistically).
    # Interjections are raw user input — no enrichment, just the content.
    await broadcast(connections, {
        "type": "user-message",
        "chatId": chat.id,
        "data": {"content": content, "source": "human"},
    }, exclude=ws)

    # Append to the turn's input messages for the Logfire span
    if chat.id in turn_input_messages:
        turn_input_messages[chat.id].extend(format_input_messages(content))
