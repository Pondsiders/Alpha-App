/**
 * store.ts — Zustand store for frontend-v2.
 *
 * The single source of truth for everything the UI renders. The WebSocket
 * handler writes to this store. The ExternalStoreRuntime reads from it.
 * No framework state management. No repository merges. No race conditions.
 *
 * Message format mirrors the backend's UserMessage.to_db() and
 * AssistantMessage.to_db() exactly — we store what the backend sends.
 * Conversion to assistant-ui's ThreadMessageLike happens at the render
 * boundary via `convertMessage`, not here.
 */

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import type { ThreadMessageLike } from "@assistant-ui/react";

// ---------------------------------------------------------------------------
// Backend message formats (must match backend/src/alpha_app/models.py)
// ---------------------------------------------------------------------------

/** A single content block in a user message. */
export type UserContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; image: string } // already display-format: data URI or URL
  | { type: string; [key: string]: unknown }; // pass-through for unknown types

/** User message as persisted by backend (UserMessage.to_db() → to_wire() shape). */
export interface UserMessage {
  id: string;
  source: string; // "human", "buzzer", "intro", "approach-light"
  content: UserContentBlock[];
  timestamp: string | null;
  memories?: unknown[] | null;
  topics?: string[] | null;
}

/** A single assistant-message part: text, thinking, or tool call. */
export type AssistantPart =
  | { type: "text"; text: string }
  | { type: "thinking"; thinking: string }
  | {
      type: "tool-call";
      toolCallId: string;
      toolName: string;
      args: unknown;
      argsText?: string;
      result?: unknown;
    };

/** Assistant message as persisted by backend (AssistantMessage.to_db() shape). */
export interface AssistantMessage {
  id: string;
  parts: AssistantPart[];
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  context_window: number;
  model: string | null;
  stop_reason: string | null;
  cost_usd: number;
  duration_ms: number;
  inference_count: number;
}

/** System message (task notifications, etc.) — placeholder for later. */
export interface SystemMessage {
  id: string;
  text: string;
  source: string;
  timestamp?: string | null;
}

/** A tagged message — role tells us which variant `data` is. */
export type Message =
  | { role: "user"; data: UserMessage }
  | { role: "assistant"; data: AssistantMessage }
  | { role: "system"; data: SystemMessage };

// ---------------------------------------------------------------------------
// Chat — metadata + messages for one conversation
// ---------------------------------------------------------------------------

export interface Chat {
  id: string;
  title: string;
  createdAt: number; // unix seconds
  updatedAt: number; // unix seconds
  messages: Message[];
  isRunning: boolean;
  tokenCount: number;
  contextWindow: number;
  sessionUuid?: string;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface AppState {
  // -- Connection state --
  connected: boolean;

  // -- Chats --
  /** Map of chatId → Chat. Keyed lookup is cheap and stable-by-key. */
  chats: Record<string, Chat>;
  /** The chat currently displayed in the Thread component. */
  currentChatId: string | null;

  // -- Actions (setters) --
  setConnected: (connected: boolean) => void;
  setCurrentChatId: (id: string | null) => void;

  /**
   * Replace the entire chat list. Used on initial `chat-list` event from
   * the WebSocket. Preserves `messages` for any chats already in the store
   * (so switching back and forth doesn't blow away loaded history).
   */
  setChatList: (chats: Omit<Chat, "messages" | "isRunning">[]) => void;

  /** Upsert a single chat (used when Dawn creates a new one at 6 AM, etc). */
  upsertChat: (chat: Partial<Chat> & { id: string }) => void;

  /** Replace all messages for a chat. Used on `chat-data` event. */
  setMessages: (chatId: string, messages: Message[]) => void;

  /** Append a new message to a chat (user send, or server-initiated). */
  appendMessage: (chatId: string, message: Message) => void;

  /** Replace the last user message with an enriched version from the server. */
  replaceLastUserMessage: (chatId: string, message: Message) => void;

  /** Ensure an in-progress assistant message exists. Returns its ID. */
  ensureAssistantMessage: (chatId: string) => string;

  /** Append a text delta to the last assistant message (streaming). */
  appendTextDelta: (chatId: string, messageId: string, delta: string) => void;

  /** Append a thinking delta to the last assistant message (streaming). */
  appendThinkingDelta: (
    chatId: string,
    messageId: string,
    delta: string,
  ) => void;

  /** Set the running flag for a chat. */
  setIsRunning: (chatId: string, isRunning: boolean) => void;

  /** Update token accounting for a chat. */
  setTokenCount: (chatId: string, tokenCount: number) => void;

  /** True when a streaming assistant message is still animating text. */
  isAssistantAnimating: boolean;
  setIsAssistantAnimating: (v: boolean) => void;

  /** WebSocket send function — set by useAlphaWebSocket on connect. */
  wsSend: ((cmd: Record<string, unknown>) => void) | null;
  setWsSend: (fn: ((cmd: Record<string, unknown>) => void) | null) => void;
}

export const useStore = create<AppState>()(
  immer((set, get) => ({
    connected: false,
    chats: {},
    currentChatId: null,
    isAssistantAnimating: false,

    setIsAssistantAnimating: (v) =>
      set((state) => {
        state.isAssistantAnimating = v;
      }),

    wsSend: null,

    setConnected: (connected) =>
      set((state) => {
        state.connected = connected;
      }),

    setCurrentChatId: (id) =>
      set((state) => {
        state.currentChatId = id;
      }),

    setChatList: (incoming) =>
      set((state) => {
        const next: Record<string, Chat> = {};
        for (const c of incoming) {
          const existing = state.chats[c.id];
          next[c.id] = {
            ...c,
            messages: existing?.messages ?? [],
            isRunning: existing?.isRunning ?? false,
          };
        }
        state.chats = next;
      }),

    upsertChat: (patch) =>
      set((state) => {
        const existing = state.chats[patch.id];
        if (existing) {
          Object.assign(existing, patch);
        } else {
          state.chats[patch.id] = {
            id: patch.id,
            title: patch.title ?? "",
            createdAt: patch.createdAt ?? Date.now() / 1000,
            updatedAt: patch.updatedAt ?? Date.now() / 1000,
            messages: patch.messages ?? [],
            isRunning: patch.isRunning ?? false,
            tokenCount: patch.tokenCount ?? 0,
            contextWindow: patch.contextWindow ?? 1_000_000,
            sessionUuid: patch.sessionUuid,
          };
        }
      }),

    setMessages: (chatId, messages) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (chat) chat.messages = messages;
      }),

    appendMessage: (chatId, message) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (chat) chat.messages.push(message);
      }),

    replaceLastUserMessage: (chatId, message) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        const messageId = (message.data as any)?.id;
        if (messageId) {
          // Match by ID — the correct way
          const idx = chat.messages.findIndex(
            (m) => m.role === "user" && (m.data as any).id === messageId
          );
          if (idx >= 0) {
            chat.messages[idx] = message;
            return;
          }
        }
        // Fallback: replace last user message by position
        for (let i = chat.messages.length - 1; i >= 0; i--) {
          if (chat.messages[i].role === "user") {
            chat.messages[i] = message;
            return;
          }
        }
        chat.messages.push(message);
      }),

    ensureAssistantMessage: (chatId: string): string => {
      // Use get() / set() from the immer callback closure above instead of
      // useStore.getState() / useStore.setState() — the latter creates a
      // circular type reference that collapses the whole store to `any`.
      const chat = get().chats[chatId];
      if (!chat) return "";

      // Check if the last message is already an in-progress assistant message
      const last = chat.messages[chat.messages.length - 1];
      if (last?.role === "assistant") {
        return (last.data as AssistantMessage).id;
      }

      // Create a new streaming assistant message
      const id = `ast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      set((s) => {
        const c = s.chats[chatId];
        if (c) {
          c.messages.push({
            role: "assistant",
            data: {
              id,
              parts: [],
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
            } as AssistantMessage,
          });
        }
      });
      return id;
    },

    appendTextDelta: (chatId, messageId, delta) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        const msg = chat.messages.find(
          (m) => m.role === "assistant" && m.data.id === messageId,
        );
        if (!msg || msg.role !== "assistant") return;
        const parts = msg.data.parts;
        for (let i = parts.length - 1; i >= 0; i--) {
          if (parts[i].type === "text") {
            (parts[i] as { text: string }).text += delta;
            return;
          }
        }
        parts.push({ type: "text", text: delta });
      }),

    appendThinkingDelta: (chatId, messageId, delta) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        const msg = chat.messages.find(
          (m) => m.role === "assistant" && m.data.id === messageId,
        );
        if (!msg || msg.role !== "assistant") return;
        const parts = msg.data.parts;
        for (let i = parts.length - 1; i >= 0; i--) {
          if (parts[i].type === "thinking") {
            (parts[i] as { thinking: string }).thinking += delta;
            return;
          }
        }
        parts.push({ type: "thinking", thinking: delta });
      }),

    setIsRunning: (chatId, isRunning) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (chat) chat.isRunning = isRunning;
      }),

    setTokenCount: (chatId, tokenCount) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (chat) chat.tokenCount = tokenCount;
      }),

    setWsSend: (fn) =>
      set((state) => {
        state.wsSend = fn as any; // Immer can't proxy functions, cast is safe
      }),
  })),
);

// ---------------------------------------------------------------------------
// Selectors — stable references for common reads
// ---------------------------------------------------------------------------

/** Get the current chat object, or null if nothing is selected. */
export const selectCurrentChat = (s: AppState): Chat | null =>
  s.currentChatId ? (s.chats[s.currentChatId] ?? null) : null;

/** Get the ordered list of chats (newest first by updatedAt). */
export const selectChatList = (s: AppState): Chat[] =>
  Object.values(s.chats).sort((a, b) => b.updatedAt - a.updatedAt);

// ---------------------------------------------------------------------------
// convertMessage — our format → assistant-ui's ThreadMessageLike
// ---------------------------------------------------------------------------

/**
 * Convert one of our backend-format messages to the assistant-ui
 * ThreadMessageLike format for rendering.
 *
 * This is the boundary between "our world" (backend shapes, Postgres,
 * WebSocket events) and "assistant-ui's world" (Thread primitive, parts,
 * role/content taxonomy). Keep the mapping explicit and visible.
 */
export function convertMessage(msg: Message): ThreadMessageLike {
  if (msg.role === "user") {
    const data = msg.data;
    return {
      id: data.id,
      role: "user",
      content: data.content.map((block) => {
        if (block.type === "text") {
          return { type: "text" as const, text: (block as { text: string }).text };
        }
        if (block.type === "image") {
          return {
            type: "image" as const,
            image: (block as { image: string }).image,
          };
        }
        // Pass through unknown types — assistant-ui may handle or ignore.
        return block as never;
      }),
    };
  }

  if (msg.role === "assistant") {
    const data = msg.data;
    // Streaming placeholders have ast- prefix IDs. They're still generating.
    const isStreaming = data.id?.startsWith("ast-");
    return {
      id: data.id,
      role: "assistant",
      status: isStreaming
        ? { type: "running" as const }
        : { type: "complete" as const, reason: "stop" as const },
      content: data.parts.map((part) => {
        if (part.type === "text") {
          return { type: "text" as const, text: part.text };
        }
        if (part.type === "thinking") {
          // assistant-ui calls this "reasoning"
          return { type: "reasoning" as const, text: part.thinking };
        }
        if (part.type === "tool-call") {
          return {
            type: "tool-call" as const,
            toolCallId: part.toolCallId,
            toolName: part.toolName,
            args: part.args,
            result: part.result,
          } as never;
        }
        return part as never;
      }),
    };
  }

  // System messages render as system text — minimal for now.
  return {
    id: msg.data.id,
    role: "system",
    content: [{ type: "text", text: msg.data.text }],
  };
}
