/**
 * useAlphaWebSocket — wires the WebSocket transport to the Zustand store.
 *
 * Speaks the command/event protocol defined in PROTOCOL.md.
 * Client sends commands: { command: "join-chat", chatId: "xyz" }
 * Server sends events:  { event: "app-state", chats: [...] }
 *
 * On connect, the server immediately sends app-state + chat-loaded.
 * No client request needed for startup — the server pushes.
 * The ?lastChat= query param hints which chat to restore.
 *
 * Every incoming event is validated through Zod (lib/protocol.ts).
 * Invalid events throw — no silent defaults, no ?? 0.
 */

import { useCallback, useEffect, useRef } from "react";
import { useStore, type Chat, type Message, type UserMessage, type AssistantMessage } from "@/store";
import { useWebSocket } from "@/lib/useWebSocket";
import {
  parseEvent,
  type Command,
  type ServerEvent,
} from "@/lib/protocol";

export function useAlphaWebSocket() {
  const setConnected = useStore((s) => s.setConnected);
  const setCurrentChatId = useStore((s) => s.setCurrentChatId);
  const setChatList = useStore((s) => s.setChatList);
  const setMessages = useStore((s) => s.setMessages);
  const upsertChat = useStore((s) => s.upsertChat);
  const appendMessage = useStore((s) => s.appendMessage);
  const appendTextDelta = useStore((s) => s.appendTextDelta);
  const appendThinkingDelta = useStore((s) => s.appendThinkingDelta);
  const setIsRunning = useStore((s) => s.setIsRunning);
  const setTokenCount = useStore((s) => s.setTokenCount);
  const setWsSend = useStore((s) => s.setWsSend);
  const replaceLastUserMessage = useStore((s) => s.replaceLastUserMessage);
  const ensureAssistantMessage = useStore((s) => s.ensureAssistantMessage);

  const handleRawEvent = useCallback(
    (raw: unknown) => {
      // Validate through Zod — throws on invalid shape.
      let event: ServerEvent;
      try {
        event = parseEvent(raw);
      } catch (err) {
        console.error("[Alpha WS] invalid event from server:", err, raw);
        return;
      }

      switch (event.event) {
        // -- Chat lifecycle --

        case "app-state": {
          const chats: Omit<Chat, "messages" | "isRunning">[] = event.chats.map(
            (c) => ({
              id: c.chatId,
              title: c.title,
              createdAt: c.createdAt,
              updatedAt: c.updatedAt,
              tokenCount: c.tokenCount,
              contextWindow: c.contextWindow,
            }),
          );
          setChatList(chats);
          break;
        }

        case "chat-loaded": {
          upsertChat({
            id: event.chatId,
            title: event.title,
            createdAt: event.createdAt,
            updatedAt: event.updatedAt,
            tokenCount: event.tokenCount,
            contextWindow: event.contextWindow,
          });
          setMessages(event.chatId, event.messages as unknown as Message[]);
          // Select this chat and persist to localStorage for next startup.
          setCurrentChatId(event.chatId);
          try { localStorage.setItem("alpha-lastChatId", event.chatId); } catch { /* noop */ }

          break;
        }

        case "chat-created": {
          upsertChat({
            id: event.chatId,
            title: event.title,
            createdAt: event.createdAt,
            updatedAt: event.createdAt, // No updatedAt on creation
            tokenCount: 0,
            contextWindow: 1_000_000,
          });
          setCurrentChatId(event.chatId);
          break;
        }

        case "chat-state": {
          if (event.chatId) {
            setIsRunning(event.chatId, event.state === "busy");
          }
          break;
        }

        // -- Turn lifecycle --

        case "send-ack": {
          // Acknowledged. Could set a "sending" state here if we want.
          break;
        }

        case "user-message": {
          // Server echo carries enrichment (memories, timestamp, source).
          // Reconcile by messageId: find the optimistic message and replace it.
          // If not found (reflection turns, narrator, etc.), append.
          const enriched: Message = {
            role: "user",
            data: {
              id: event.messageId,
              source: event.source,
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
            // Found optimistic message — replace with enriched version
            replaceLastUserMessage(event.chatId, enriched);
          } else {
            // New message (reflection, narrator, etc.) — append
            appendMessage(event.chatId, enriched);
          }
          break;
        }

        case "thinking-delta": {
          // TODO: wire to appendThinkingDelta once we have message IDs in deltas
          break;
        }

        case "text-delta": {
          const aid = ensureAssistantMessage(event.chatId);
          appendTextDelta(event.chatId, aid, event.delta);
          break;
        }

        case "tool-call-start": {
          // TODO: Phase 2. Show tool call beginning.
          break;
        }

        case "tool-call-delta": {
          // TODO: Phase 2. Stream tool call args.
          break;
        }

        case "tool-call-result": {
          // TODO: Phase 2. Show tool result.
          break;
        }

        case "assistant-message": {
          // Replace the streaming placeholder with the complete message.
          // The placeholder (from ensureAssistantMessage) is the last message
          // if we've been streaming. The complete version has all parts + metadata.
          const chatForAssist = useStore.getState().chats[event.chatId];
          const lastMsg = chatForAssist?.messages[chatForAssist.messages.length - 1];
          const isStreamingPlaceholder =
            lastMsg?.role === "assistant" &&
            (lastMsg.data as AssistantMessage).id.startsWith("ast-");

          // Build the complete message using the backend-shaped AssistantMessage.
          // Token accounting fields are zero here — they get filled in by a
          // subsequent turn-complete event. The parts array is the
          // authoritative full content from the backend.
          const completeAssistant: Message = {
            role: "assistant",
            data: {
              id: event.messageId,
              parts: event.content as AssistantMessage["parts"],
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
          };

          if (isStreamingPlaceholder) {
            // Replace in place via direct setState
            useStore.setState((state) => {
              const c = state.chats[event.chatId];
              if (c && c.messages.length > 0) {
                c.messages[c.messages.length - 1] = completeAssistant;
              }
            });
          } else {
            appendMessage(event.chatId, completeAssistant);
          }
          break;
        }

        case "turn-complete": {
          // Don't set isRunning=false here — let chat-state handle it.
          // The suggest pipeline fires a second turn after the main response,
          // and setting false between them would disengage scroll follow.
          setTokenCount(event.chatId, event.tokenCount);
          break;
        }

        // -- Context --

        case "context-update": {
          setTokenCount(event.chatId, event.tokenCount);
          break;
        }

        // -- Errors --

        case "error": {
          console.error(`[Alpha WS] error (${event.code}):`, event.message);
          break;
        }
      }
    },
    [
      setCurrentChatId,
      setChatList,
      setMessages,
      upsertChat,
      appendMessage,
      appendTextDelta,
      appendThinkingDelta,
      setIsRunning,
      setTokenCount,
    ],
  );

  const handleConnectionChange = useCallback(
    (connected: boolean) => setConnected(connected),
    [setConnected],
  );

  const { send: rawSend, connected } = useWebSocket({
    onEvent: handleRawEvent,
    onConnectionChange: handleConnectionChange,
  });

  // Typed send: accepts a Command object, serializes to JSON. The raw
  // transport layer takes `unknown` and JSON-serializes — protocol shape
  // is validated on the consumer side (here), not the transport.
  const send = useCallback(
    (cmd: Command): boolean => rawSend(cmd),
    [rawSend],
  );

  // Expose send on the store so any component can send commands. The
  // store types wsSend as (cmd: Record<string, unknown>) => void because
  // the store itself is protocol-agnostic; we cast through `unknown`
  // because Command is narrower than Record<string, unknown>.
  useEffect(() => {
    setWsSend(send as unknown as (cmd: Record<string, unknown>) => void);
    return () => setWsSend(null);
  }, [send, setWsSend]);

  // When the user switches chats manually (sidebar click), join it.
  // On startup the server sends chat-loaded automatically — this effect
  // only fires for subsequent switches (currentChatId changes after the
  // initial app-state/chat-loaded pair).
  const currentChatId = useStore((s) => s.currentChatId);
  const prevChatIdRef = useRef<string | null>(null);
  useEffect(() => {
    // Skip the first set (from the server's auto-loaded chat).
    if (currentChatId === prevChatIdRef.current) return;
    const isFirstLoad = prevChatIdRef.current === null;
    prevChatIdRef.current = currentChatId;
    if (isFirstLoad) return; // Server already sent this chat.

    if (connected && currentChatId) {
      send({ command: "join-chat", chatId: currentChatId });
    }
  }, [connected, currentChatId, send]);

  return { send, connected };
}
