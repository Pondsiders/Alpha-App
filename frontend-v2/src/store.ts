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
}

export const useStore = create<AppState>()(
  immer((set) => ({
    connected: false,
    chats: {},
    currentChatId: null,

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

    appendTextDelta: (chatId, messageId, delta) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        const msg = chat.messages.find(
          (m) => m.role === "assistant" && m.data.id === messageId,
        );
        if (!msg || msg.role !== "assistant") return;
        const lastPart = msg.data.parts[msg.data.parts.length - 1];
        if (lastPart && lastPart.type === "text") {
          lastPart.text += delta;
        } else {
          msg.data.parts.push({ type: "text", text: delta });
        }
      }),

    appendThinkingDelta: (chatId, messageId, delta) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        const msg = chat.messages.find(
          (m) => m.role === "assistant" && m.data.id === messageId,
        );
        if (!msg || msg.role !== "assistant") return;
        const lastPart = msg.data.parts[msg.data.parts.length - 1];
        if (lastPart && lastPart.type === "thinking") {
          lastPart.thinking += delta;
        } else {
          msg.data.parts.push({ type: "thinking", thinking: delta });
        }
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
    return {
      id: data.id,
      role: "assistant",
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
