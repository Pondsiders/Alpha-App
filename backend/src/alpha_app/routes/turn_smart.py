"""turn_smart.py — Simplified turn handling for Smart Chat.

The callback in Chat._on_claude_event handles streaming and AssistantMessage
assembly. This module handles the input side: resurrect, enrobe, send.

No streaming loop. No stream_chat_events(). Send returns immediately.
Claude's response flows through the callback to the WebSocket.
"""

import asyncio

import logfire
from fastapi import WebSocket

from alpha_app.chat import MODEL, Chat, ConversationState, SuggestState
from alpha_app.db import persist_chat
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.enrobe import enrobe
from alpha_app.routes.spans import build_prompt_preview, format_input_messages
from alpha_app.suggest import suggest, format_intro_block
from alpha_app.tools import create_alpha_server


async def _run_suggest(chat: Chat, user_text: str, assistant_text: str) -> None:
    """Fire-and-forget suggest pipeline. Populates chat._pending_intro."""
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


async def handle_send(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    *,
    broadcast_user_message: bool = True,
    source: str = "human",
    msg_id: str | None = None,
    topics: list[str] | None = None,
) -> None:
    """Handle a user message: resurrect, enrobe, send, return.

    No streaming loop — Claude's response flows through Chat._on_claude_event
    to the WebSocket via the on_broadcast callback.

    This function is FIRE AND FORGET from the WebSocket handler's perspective.
    Errors are broadcast to the client, not raised.
    """
    chat_id = chat.id
    prompt_preview = build_prompt_preview(content)

    # Open the turn span manually — it closes in the callback on ResultEvent.
    # This span lives across the full turn: user input → Claude response.
    span = logfire.span(
        "alpha.turn: {prompt_preview}",
        **{
            "prompt_preview": prompt_preview,
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.request.model": MODEL,
            "gen_ai.conversation.id": chat_id,
            "gen_ai.system_instructions": [
                {"type": "text", "content": getattr(ws.app.state, "system_prompt", "")}
            ],
            "gen_ai.input.messages": format_input_messages(content),
            "client_name": "alpha",
            "chat.id": chat_id,
            "session_id": chat.session_uuid or "",
        },
    )
    span.__enter__()
    chat._turn_span = span

    try:
            # -- Resurrect if COLD ----------------------------------------
            if chat.state == ConversationState.COLD:
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
                    await chat.wake(
                        system_prompt=ws.app.state.system_prompt,
                        mcp_servers=mcp_servers,
                    )
                else:
                    await chat.resurrect(
                        system_prompt=ws.app.state.system_prompt,
                        mcp_servers=mcp_servers,
                    )

                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": chat.wire_state(),
                })

            chat.set_trace_context(logfire.get_context())

            # -- Title extraction -----------------------------------------
            if not chat.title and content:
                for block in content:
                    if block.get("type") == "text" and block.get("text"):
                        chat.title = block["text"][:80]
                        break

            chat._cancel_reap_timer()
            chat.updated_at = __import__("time").time()

            # -- Enrobe ---------------------------------------------------
            topic_registry = getattr(ws.app.state, "topic_registry", None)
            result = await enrobe(
                content, chat=chat, source=source, msg_id=msg_id,
                topics=topics, topic_registry=topic_registry,
            )

            # Update Logfire with enriched content
            enriched_messages = format_input_messages(result.content)
            span.set_attribute("gen_ai.input.messages", enriched_messages)

            # -- Broadcast user message -----------------------------------
            if broadcast_user_message:
                for event in result.events:
                    await broadcast(connections, {
                        "type": event["type"],
                        "chatId": chat_id,
                        "data": event["data"],
                    })

            # -- Add to Chat.messages[] and persist -------------------------
            chat.messages.append(result.message)  # Born dirty
            await chat.flush()  # Write UserMessage to Postgres immediately

            # -- Send to Claude (returns immediately) ----------------------
            # Reset output token accumulator for this turn
            chat.reset_output_tokens()
            await chat.send(result.content)

            await broadcast(connections, {
                "type": "chat-state",
                "chatId": chat_id,
                "data": chat.wire_state(),
            })

            # Response flows through Chat._on_claude_event → on_broadcast.
            # We don't wait for it here. That's the whole point.

    except asyncio.CancelledError:
        # Close the span on cancellation
        if chat._turn_span:
            chat._turn_span.__exit__(None, None, None)
            chat._turn_span = None
    except Exception as e:
        logfire.error("turn failed: {error}", error=str(e))
        # Close the span with error info
        if chat._turn_span:
            chat._turn_span.set_attribute("error.type", type(e).__name__)
            chat._turn_span.__exit__(type(e), e, e.__traceback__)
            chat._turn_span = None
        try:
            await broadcast(connections, {"type": "error", "chatId": chat_id, "data": str(e)})
        except Exception:
            pass
