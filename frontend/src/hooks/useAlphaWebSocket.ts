/**
 * useAlphaWebSocket — wires the WebSocket transport to the Zustand store.
 *
 * Speaks the three-envelope protocol defined in docs/wire-protocol.md.
 * - Client sends commands:  { command: "hello", id: "..." }
 * - Server sends responses: { response: "hi-yourself", id: "...", ... }
 * - Server sends events:    { event: "text-delta", chatId: "...", ... }
 *
 * On connect the server waits silently. The client opens with `hello` and
 * the server replies with `hi-yourself` carrying the current chat list.
 * If `?lastChat=` is in the WebSocket URL, the client then sends
 * `join-chat` to hydrate that chat's messages.
 *
 * Every incoming message is validated through Zod (lib/protocol.ts).
 * Invalid messages throw — no silent defaults, no ?? 0.
 */

import { useCallback, useEffect, useRef } from "react";
import { nanoid } from "nanoid";
import { useStore, type Message, type UserMessage, type AssistantMessage } from "@/store";
import { useWebSocket } from "@/lib/useWebSocket";
import {
  Commands,
  parseMessage,
  type Command,
  type ServerEvent,
  type ServerResponse,
} from "@/lib/protocol";
import {
  pushTextDelta,
  pushThinkingDelta,
} from "@/lib/streamingText";

export function useAlphaWebSocket() {
  const setConnected = useStore((s) => s.setConnected);
  const setCurrentChatId = useStore((s) => s.setCurrentChatId);
  const setChatList = useStore((s) => s.setChatList);
  const setMessages = useStore((s) => s.setMessages);
  const upsertChat = useStore((s) => s.upsertChat);
  const appendMessage = useStore((s) => s.appendMessage);
  const appendTextDelta = useStore((s) => s.appendTextDelta);
  const appendThinkingDelta = useStore((s) => s.appendThinkingDelta);
  const setChatState = useStore((s) => s.setChatState);
  const setWsSend = useStore((s) => s.setWsSend);
  const replaceLastUserMessage = useStore((s) => s.replaceLastUserMessage);
  const ensureAssistantMessage = useStore((s) => s.ensureAssistantMessage);

  const handleResponse = useCallback(
    (response: ServerResponse) => {
      switch (response.response) {
        case "hi-yourself": {
          setChatList(response.chats);
          break;
        }

        case "chat-joined": {
          upsertChat({
            chatId: response.chatId,
            createdAt: response.createdAt,
            lastActive: response.lastActive,
            state: response.state,
            tokenCount: response.tokenCount,
            contextWindow: response.contextWindow,
          });
          setMessages(response.chatId, response.messages as unknown as Message[]);
          setCurrentChatId(response.chatId);
          try { localStorage.setItem("alpha-lastChatId", response.chatId); } catch { /* noop */ }
          break;
        }

        case "chat-created": {
          setCurrentChatId(response.chatId);
          try { localStorage.setItem("alpha-lastChatId", response.chatId); } catch { /* noop */ }
          break;
        }

        case "received":
        case "interrupted": {
          break;
        }

        case "error": {
          console.error(`[Alpha WS] error (${response.code}):`, response.message);
          break;
        }
      }
    },
    [setChatList, upsertChat, setMessages, setCurrentChatId],
  );

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.event) {
        case "app-state": {
          setChatList(event.chats);
          break;
        }

        case "chat-state": {
          setChatState(event.chatId, {
            state: event.state,
            tokenCount: event.tokenCount,
            contextWindow: event.contextWindow,
          });
          break;
        }

        case "turn-started": {
          break;
        }

        case "user-message": {
          // Server echo carries enrichment (memories, timestamp).
          // Reconcile by messageId: find the optimistic message and replace it.
          // If not found, append.
          const enriched: Message = {
            role: "user",
            data: {
              id: event.messageId,
              content: event.content as UserMessage["content"],
              memories: event.memories ?? [],
              timestamp: event.timestamp,
            },
          };

          const chatState = useStore.getState().chats[event.chatId];
          const existingIdx = chatState?.messages.findIndex(
            (m: Message) => m.role === "user" && m.data.id === event.messageId,
          ) ?? -1;

          if (existingIdx >= 0) {
            replaceLastUserMessage(event.chatId, enriched);
          } else {
            appendMessage(event.chatId, enriched);
          }
          break;
        }

        case "thinking-delta": {
          ensureAssistantMessage(event.chatId, event.messageId);
          const thinkChat = useStore.getState().chats[event.chatId];
          const thinkMsg = thinkChat?.messages.find(
            (m) => m.role === "assistant" && (m.data as AssistantMessage).id === event.messageId,
          );
          const hasThinkingPart = thinkMsg?.role === "assistant"
            && (thinkMsg.data as AssistantMessage).parts.some((p) => p.type === "thinking");
          if (!hasThinkingPart) {
            appendThinkingDelta(event.chatId, event.messageId, event.delta);
          }
          pushThinkingDelta(event.chatId, event.messageId, event.delta);
          break;
        }

        case "text-delta": {
          ensureAssistantMessage(event.chatId, event.messageId);
          const chat = useStore.getState().chats[event.chatId];
          const msg = chat?.messages.find(
            (m) => m.role === "assistant" && (m.data as AssistantMessage).id === event.messageId,
          );
          const hasTextPart = msg?.role === "assistant"
            && (msg.data as AssistantMessage).parts.some((p) => p.type === "text");
          if (!hasTextPart) {
            appendTextDelta(event.chatId, event.messageId, event.delta);
          }
          pushTextDelta(event.chatId, event.messageId, event.delta);
          break;
        }

        case "tool-call-start":
        case "tool-call-delta":
        case "tool-call-result": {
          // TODO: Phase 2. Render tool calls.
          break;
        }

        case "assistant-message": {
          const chatForAssist = useStore.getState().chats[event.chatId];
          const existingIdx = chatForAssist?.messages.findIndex(
            (m) => m.role === "assistant" && (m.data as AssistantMessage).sealed === false,
          ) ?? -1;

          if (existingIdx >= 0) {
            useStore.setState((state) => {
              const c = state.chats[event.chatId];
              if (c) {
                const existing = c.messages[existingIdx].data as AssistantMessage;
                existing.parts = event.content as AssistantMessage["parts"];
                existing.sealed = true;
              }
            });
          } else {
            appendMessage(event.chatId, {
              role: "assistant",
              data: {
                id: nanoid(),
                parts: event.content as AssistantMessage["parts"],
                sealed: true,
                input_tokens: 0,
                output_tokens: 0,
                cache_creation_tokens: 0,
                cache_read_tokens: 0,
                context_window: 1_000_000,
                model: null,
                stop_reason: null,
                cost_usd: 0,
                duration_ms: 0,
                inference_count: 0,
              },
            });
          }
          break;
        }

        case "turn-complete": {
          break;
        }
      }
    },
    [
      setChatList,
      upsertChat,
      setCurrentChatId,
      setChatState,
      appendMessage,
      appendTextDelta,
      appendThinkingDelta,
      replaceLastUserMessage,
      ensureAssistantMessage,
    ],
  );

  const handleRawMessage = useCallback(
    (raw: unknown) => {
      let message: ReturnType<typeof parseMessage>;
      try {
        message = parseMessage(raw);
      } catch (err) {
        console.error("[Alpha WS] invalid message from server:", err, raw);
        return;
      }
      if (message.kind === "response") {
        handleResponse(message.payload);
      } else {
        handleEvent(message.payload);
      }
    },
    [handleResponse, handleEvent],
  );

  const { send: rawSend, connected } = useWebSocket({
    onEvent: handleRawMessage,
    onConnectionChange: useCallback(
      (isConnected: boolean) => setConnected(isConnected),
      [setConnected],
    ),
  });

  // Typed send: accepts a Command object, serializes to JSON.
  const send = useCallback(
    (cmd: Command): boolean => rawSend(cmd),
    [rawSend],
  );

  // Expose send on the store so any component can send commands.
  useEffect(() => {
    setWsSend(send as unknown as (cmd: Record<string, unknown>) => void);
    return () => setWsSend(null);
  }, [send, setWsSend]);

  // On every connect (first or reconnect): send hello, then if we have a
  // remembered chat id, send join-chat for it. The server's hi-yourself
  // response populates the sidebar; chat-joined hydrates the chosen chat.
  useEffect(() => {
    if (!connected) return;
    send(Commands.hello({ id: nanoid() }));
    let lastChat: string | null = null;
    try { lastChat = localStorage.getItem("alpha-lastChatId"); } catch { /* noop */ }
    if (lastChat) {
      send(Commands.joinChat({ id: nanoid(), chatId: lastChat }));
    }
  }, [connected, send]);

  // When the user switches chats mid-session (sidebar click), join it.
  const currentChatId = useStore((s) => s.currentChatId);
  const prevChatIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (currentChatId === prevChatIdRef.current) return;
    const isFirstLoad = prevChatIdRef.current === null;
    prevChatIdRef.current = currentChatId;
    if (isFirstLoad) return;

    if (connected && currentChatId) {
      send(Commands.joinChat({ id: nanoid(), chatId: currentChatId }));
    }
  }, [connected, currentChatId, send]);

  return { send, connected };
}
