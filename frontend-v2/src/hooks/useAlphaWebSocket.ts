/**
 * useAlphaWebSocket — wires the WebSocket transport to the Zustand store.
 *
 * This is the application-specific layer on top of the generic useWebSocket
 * hook. It knows the event shapes the backend produces and routes them to
 * the correct store actions. Call it once at the top of the app tree (from
 * App.tsx). It returns the `send` function and the connected flag for any
 * component that wants to push messages to the server.
 *
 * Phase 1 implementation: focused on rendering seeded data. Handles
 * chat-list and chat-data fully; other event types (streaming deltas, done,
 * errors) are stubbed with TODO comments for Phase 2.
 */

import { useCallback, useEffect, useRef } from "react";
import { useStore, type Chat, type Message } from "@/store";
import {
  useWebSocket,
  type ServerEvent,
  type ClientMessage,
} from "@/lib/useWebSocket";

/**
 * Shape of chat metadata in the chat-list event (matches backend's
 * list_chats() return: wire_state entries from db.py).
 */
interface ChatListEntry {
  chatId: string;
  title?: string;
  state?: string;
  createdAt?: number;
  updatedAt?: number;
  sessionUuid?: string;
  tokenCount?: number;
  contextWindow?: number;
}

/**
 * Shape of the chat-data payload (the "gimme the fucking chat" response).
 * Matches backend's join-chat response.
 */
interface ChatDataPayload {
  chatId: string;
  messages: Array<{
    role: "user" | "assistant" | "system";
    data: Record<string, unknown>;
  }>;
  title?: string;
  createdAt?: number;
  updatedAt?: number;
  tokenCount?: number;
  contextWindow?: number;
}

export function useAlphaWebSocket() {
  // Pull stable action references from the store. Zustand guarantees these
  // don't change across renders (actions live in module scope).
  const setConnected = useStore((s) => s.setConnected);
  const setChatList = useStore((s) => s.setChatList);
  const setMessages = useStore((s) => s.setMessages);
  const upsertChat = useStore((s) => s.upsertChat);
  const appendMessage = useStore((s) => s.appendMessage);
  const appendTextDelta = useStore((s) => s.appendTextDelta);
  const appendThinkingDelta = useStore((s) => s.appendThinkingDelta);
  const setIsRunning = useStore((s) => s.setIsRunning);
  const setTokenCount = useStore((s) => s.setTokenCount);

  // Dispatcher — the one place that maps server events to store actions.
  // Wrapped in a ref so useWebSocket's onEvent callback always sees the
  // latest version without re-establishing the connection.
  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        // -- Phase 1: things we need for rendering seeded data --

        case "chat-list": {
          // Backend sends { chats: ChatListEntry[] } or ChatListEntry[] directly.
          // Be tolerant of both shapes.
          const raw = event.data as
            | ChatListEntry[]
            | { chats: ChatListEntry[] }
            | undefined;
          if (!raw) return;
          const list = Array.isArray(raw) ? raw : raw.chats;
          if (!list) return;
          const chats: Omit<Chat, "messages" | "isRunning">[] = list.map(
            (c) => ({
              id: c.chatId,
              title: c.title ?? "",
              createdAt: c.createdAt ?? Date.now() / 1000,
              updatedAt: c.updatedAt ?? Date.now() / 1000,
              tokenCount: c.tokenCount ?? 0,
              contextWindow: c.contextWindow ?? 1_000_000,
              sessionUuid: c.sessionUuid,
            }),
          );
          setChatList(chats);
          break;
        }

        case "chat-data": {
          // The "gimme the fucking chat" response: full history for one chat.
          const payload = event.data as ChatDataPayload | undefined;
          if (!payload) return;
          const chatId = payload.chatId ?? event.chatId;
          if (!chatId) {
            console.warn("[Alpha WS] chat-data without chatId", event);
            return;
          }

          // Upsert metadata first so the chat exists in the store.
          upsertChat({
            id: chatId,
            title: payload.title ?? "",
            createdAt: payload.createdAt ?? Date.now() / 1000,
            updatedAt: payload.updatedAt ?? Date.now() / 1000,
            tokenCount: payload.tokenCount ?? 0,
            contextWindow: payload.contextWindow ?? 1_000_000,
          });

          // Then replace messages. Backend already sends tagged {role, data}
          // pairs that match our Message union at runtime; TypeScript needs
          // an unknown stepping-stone because the payload type uses
          // Record<string, unknown> for the data field.
          setMessages(chatId, payload.messages as unknown as Message[]);
          break;
        }

        case "chat-created": {
          // Server-initiated new chat (e.g., Dawn at 6 AM).
          const data = event.data as ChatListEntry | undefined;
          if (!data) return;
          upsertChat({
            id: data.chatId,
            title: data.title ?? "",
            createdAt: data.createdAt ?? Date.now() / 1000,
            updatedAt: data.updatedAt ?? Date.now() / 1000,
            tokenCount: data.tokenCount ?? 0,
            contextWindow: data.contextWindow ?? 1_000_000,
          });
          break;
        }

        // -- Phase 2: interactive send/receive (stubs for now) --

        case "text-delta": {
          // TODO: Phase 2. Append streaming text to the active assistant msg.
          const data = event.data as
            | { messageId: string; delta: string }
            | undefined;
          if (!event.chatId || !data) return;
          appendTextDelta(event.chatId, data.messageId, data.delta);
          break;
        }

        case "thinking-delta": {
          // TODO: Phase 2. Append streaming thinking to the active msg.
          const data = event.data as
            | { messageId: string; delta: string }
            | undefined;
          if (!event.chatId || !data) return;
          appendThinkingDelta(event.chatId, data.messageId, data.delta);
          break;
        }

        case "user-message": {
          // TODO: Phase 2. Backend echoes the user message with enrichment.
          const data = event.data as Record<string, unknown> | undefined;
          if (!event.chatId || !data) return;
          appendMessage(event.chatId, {
            role: "user",
            data: data as Message extends { role: "user"; data: infer U }
              ? U
              : never,
          });
          break;
        }

        case "assistant-message": {
          // TODO: Phase 2. Coalesced assistant message at turn end.
          const data = event.data as Record<string, unknown> | undefined;
          if (!event.chatId || !data) return;
          appendMessage(event.chatId, {
            role: "assistant",
            data: data as Message extends { role: "assistant"; data: infer A }
              ? A
              : never,
          });
          break;
        }

        case "chat-state": {
          // TODO: Phase 2. Update running/idle state, token count, etc.
          const data = event.data as
            | { state?: string; tokenCount?: number }
            | undefined;
          if (!event.chatId || !data) return;
          if (data.state !== undefined) {
            setIsRunning(event.chatId, data.state === "busy");
          }
          if (data.tokenCount !== undefined) {
            setTokenCount(event.chatId, data.tokenCount);
          }
          break;
        }

        case "context-update": {
          // TODO: Phase 2. Token meter updates during streaming.
          const data = event.data as
            | { tokenCount?: number }
            | undefined;
          if (!event.chatId || data?.tokenCount === undefined) return;
          setTokenCount(event.chatId, data.tokenCount);
          break;
        }

        case "done": {
          // TODO: Phase 2. End of turn — clear isRunning.
          if (!event.chatId) return;
          setIsRunning(event.chatId, false);
          break;
        }

        case "interrupted": {
          if (!event.chatId) return;
          setIsRunning(event.chatId, false);
          break;
        }

        case "error":
        case "exception": {
          // TODO: Phase 2. Surface errors via Sonner toast.
          console.error("[Alpha WS] backend error:", event);
          break;
        }

        // -- Events we don't need yet or at all --

        case "tool-call":
        case "tool-use-start":
        case "tool-use-delta":
        case "tool-result":
        case "approach-light":
        case "enrichment-timestamp":
        case "system-message":
        case "agent-started":
        case "agent-progress":
        case "agent-done":
        case "replay-done":
          // TODO: Phase 2 or later. Not blocking for pixelfucking.
          break;

        default: {
          // Exhaustiveness check — TypeScript will error here if we miss
          // a case from the ServerEvent type union.
          const _exhaustive: never = event.type;
          console.warn("[Alpha WS] unhandled event type:", _exhaustive, event);
        }
      }
    },
    [
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

  // Track connection state in the store too, so any component can read it.
  const handleConnectionChange = useCallback(
    (connected: boolean) => {
      setConnected(connected);
    },
    [setConnected],
  );

  const { send, connected } = useWebSocket({
    onEvent: handleEvent,
    onConnectionChange: handleConnectionChange,
  });

  // On connect, ask the backend for the chat list. Using a ref to track
  // whether we've already requested it for this connection so we don't
  // spam the server on every re-render.
  const requestedRef = useRef(false);
  useEffect(() => {
    if (connected && !requestedRef.current) {
      requestedRef.current = true;
      send({ type: "list-chats" });
    } else if (!connected) {
      requestedRef.current = false;
    }
  }, [connected, send]);

  // When the user switches to a chat (setCurrentChatId), send join-chat
  // to the backend. The server responds with a chat-data event containing
  // the full message history, which our handler writes to the store via
  // setMessages. The Thread component then re-renders with the loaded
  // conversation.
  //
  // Subscribe directly to currentChatId so this effect only fires when
  // the selection actually changes — not on every store update.
  const currentChatId = useStore((s) => s.currentChatId);
  useEffect(() => {
    if (connected && currentChatId) {
      send({ type: "join-chat", chatId: currentChatId });
    }
  }, [connected, currentChatId, send]);

  return { send, connected };
}

// Re-export the ClientMessage type so callers can build messages without
// reaching into lib.
export type { ClientMessage };
