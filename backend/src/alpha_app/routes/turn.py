"""turn.py — Turn handling: new turns and interjections.

A new turn resurrects dead chats, sends the message, and streams
the response. An interjection feeds a message into an already-busy
subprocess (full duplex).
"""

import asyncio

import logfire
from fastapi import WebSocket

from alpha_app.chat import MODEL, Chat, ChatState
from alpha_app.db import persist_chat
from alpha_app.routes.broadcast import broadcast
from alpha_app.routes.spans import build_prompt_preview, format_input_messages
from alpha_app.routes.streaming import stream_chat_events


async def handle_new_turn(
    ws: WebSocket,
    connections: set,
    chat: Chat,
    content: list[dict],
    turn_input_messages: dict[str, list],
    streaming_tasks: dict[str, asyncio.Task],
) -> None:
    """Handle a new turn: resurrect if needed, send, stream events.

    Runs as a background task so the WebSocket read loop stays hot.
    State changes and streaming events broadcast to ALL connections.
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
            # Resurrect if DEAD
            resurrected = False
            if chat.state == ChatState.DEAD:
                if not chat.session_uuid:
                    await broadcast(connections, {
                        "type": "error",
                        "chatId": chat_id,
                        "data": "Chat is dead with no session to resume",
                    })
                    await broadcast(connections, {"type": "done", "chatId": chat_id})
                    return

                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": "starting",
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })
                await chat.resurrect(system_prompt=ws.app.state.system_prompt)
                resurrected = True
                await broadcast(connections, {
                    "type": "chat-state",
                    "chatId": chat_id,
                    "data": {
                        "state": chat.state.value,
                        "sessionUuid": chat.session_uuid or "",
                        "tokenCount": chat.token_count,
                        "contextWindow": chat.context_window,
                    },
                })

            chat.set_trace_context(logfire.get_context())

            await chat.send(content)

            span.set_attribute("chat.title", chat.title)
            span.set_attribute("chat.resurrected", resurrected)

            # Notify state change: IDLE -> BUSY (all clients)
            await broadcast(connections, {
                "type": "chat-state",
                "chatId": chat_id,
                "data": {
                    "state": chat.state.value,
                    "title": chat.title,
                    "updatedAt": chat.updated_at,
                    "sessionUuid": chat.session_uuid or "",
                    "tokenCount": chat.token_count,
                    "contextWindow": chat.context_window,
                },
            })

            await persist_chat(chat)

            # Stream events to all connections
            await stream_chat_events(connections, chat, span)

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
    """Handle an interjection: feed to BUSY subprocess, echo to other clients."""
    await chat.send(content)

    # Echo user message to other connections (sender has it optimistically)
    await broadcast(connections, {
        "type": "user-message",
        "chatId": chat.id,
        "data": {"content": content},
    }, exclude=ws)

    # Append to the turn's input messages for the Logfire span
    if chat.id in turn_input_messages:
        turn_input_messages[chat.id].extend(format_input_messages(content))
