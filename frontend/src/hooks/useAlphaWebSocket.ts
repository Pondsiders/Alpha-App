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
import { useStore, type Message, type UserMessage, type AssistantMessage } from "@/store";
import { useWebSocket } from "@/lib/useWebSocket";
import {
  Commands,
  parseEvent,
  type Command,
  type ServerEvent,
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
          // Wire summaries are already shaped exactly like Chat (modulo the
          // locally-held messages array). Pass them through to the store.
          setChatList(event.chats);
          break;
        }

        case "chat-loaded": {
          upsertChat({
            chatId: event.chatId,
            createdAt: event.createdAt,
            lastActive: event.lastActive,
            state: event.state,
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
            chatId: event.chatId,
            createdAt: event.createdAt,
            lastActive: event.lastActive,
            // A freshly created chat has no subprocess yet — it starts in
            // pending and the first send wakes it.
            state: "pending",
            tokenCount: 0,
            contextWindow: 1_000_000,
          });
          setCurrentChatId(event.chatId);
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
            // Append in arrival order. Text-deltas now carry an explicit
            // messageId so out-of-order rendering is impossible — there's
            // no need to defer non-human messages until animation finishes.
            appendMessage(event.chatId, enriched);
          }
          break;
        }

        case "thinking-delta": {
          // The backend stamps messageId on every delta — find or create
          // that exact placeholder. No inferring from "last message."
          ensureAssistantMessage(event.chatId, event.messageId);
          // Seed the Zustand part on the FIRST delta so SequentialParts
          // sees a thinking part exists. Subsequent deltas only update
          // the streaming ref (one Zustand write per part lifetime).
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
          // Seed the Zustand part on the FIRST delta so SequentialParts
          // sees a text part exists. AnimatedText reads from the streaming
          // ref for live text, so Zustand only carries the seed value
          // (replaced wholesale at assistant-message seal).
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
          // assistant-message is a FINALIZATION event, not a creation event.
          // Find the placeholder by exact messageId. If we streamed it,
          // the placeholder already exists — finalize in place (same ID,
          // no remount, animation continues naturally and drains the
          // streaming ref to completion). If not (late joiner, page
          // reload), create the message fresh with no animation.

          const chatForAssist = useStore.getState().chats[event.chatId];
          const existingIdx = chatForAssist?.messages.findIndex(
            (m) => m.role === "assistant" && (m.data as AssistantMessage).id === event.messageId,
          ) ?? -1;

          if (existingIdx >= 0) {
            // Finalize in place. DON'T clear the streaming ref — AnimatedText
            // is still reading from it. The ref has the complete text from
            // accumulated deltas. Seal the message so isStreaming flips false.
            // The ref gets cleared when AnimatedText's drain catches up and
            // calls onDone.
            useStore.setState((state) => {
              const c = state.chats[event.chatId];
              if (c) {
                const existing = c.messages[existingIdx].data as AssistantMessage;
                existing.parts = event.content as AssistantMessage["parts"];
                existing.sealed = true;
              }
            });
          } else {
            // Late joiner / page reload — no streaming happened.
            // Create the message fresh. No animation needed.
            appendMessage(event.chatId, {
              role: "assistant",
              data: {
                id: event.messageId,
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
          // The signal that Claude finished. State and token-count updates
          // arrive on a chat-state event that follows; nothing for the
          // consumer to do here.
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
      setChatState,
      replaceLastUserMessage,
      ensureAssistantMessage,
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
      send(Commands.joinChat({ chatId: currentChatId }));
    }
  }, [connected, currentChatId, send]);

  return { send, connected };
}
