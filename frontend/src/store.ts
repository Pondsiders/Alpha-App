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
import type { ChatStateValue } from "@/lib/protocol";

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
  /** True once assistant-message finalizes this message. Prevents
   *  ensureAssistantMessage from reusing it for the next turn's deltas. */
  sealed?: boolean;
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

/**
 * One chat as the frontend tracks it. Mirrors `ChatSummary` from the wire
 * (see backend/src/alpha/ws/events.py and docs/wire-protocol.md), plus
 * the locally-held `messages` array.
 *
 * Field shapes match the wire exactly: `chatId` not `id`, ISO-8601 string
 * timestamps not unix seconds, `state` from the five-value FSM. The
 * composer-input rule is a function of `state` alone — see the `Chat`
 * docstring in backend/src/alpha/chat.py for the full state machine.
 */
export interface Chat {
  chatId: string;
  createdAt: string; // ISO 8601 with offset
  lastActive: string; // ISO 8601 with offset
  state: ChatStateValue;
  tokenCount: number;
  contextWindow: number;
  messages: Message[];
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
   * Replace the entire chat list. Used on the connect-time `app-state`
   * event. Preserves `messages` for any chats already in the store (so
   * switching back and forth doesn't blow away loaded history).
   */
  setChatList: (chats: Omit<Chat, "messages">[]) => void;

  /** Upsert a single chat (used on `chat-created`, `chat-state`, etc). */
  upsertChat: (chat: Partial<Chat> & { chatId: string }) => void;

  /** Replace all messages for a chat. Used on `chat-loaded`. */
  setMessages: (chatId: string, messages: Message[]) => void;

  /** Append a new message to a chat (user send, or server-initiated). */
  appendMessage: (chatId: string, message: Message) => void;

  /** Replace the last user message with an enriched version from the server. */
  replaceLastUserMessage: (chatId: string, message: Message) => void;

  /**
   * Find an assistant message by ID, or create one with that exact ID
   * appended to the end of the message list. The ID comes from the
   * backend's text-delta / thinking-delta event — no inference, no
   * "look at the last message and guess." Returns the ID unchanged.
   */
  ensureAssistantMessage: (chatId: string, messageId: string) => string;

  /** Append a text delta to the last assistant message (streaming). */
  appendTextDelta: (chatId: string, messageId: string, delta: string) => void;

  /** Append a thinking delta to the last assistant message (streaming). */
  appendThinkingDelta: (
    chatId: string,
    messageId: string,
    delta: string,
  ) => void;

  /** Update a chat's lifecycle state. Used on `chat-state` events. */
  setChatState: (
    chatId: string,
    fields: { state: ChatStateValue; tokenCount: number; contextWindow: number },
  ) => void;

  /** WebSocket send function — set by useAlphaWebSocket on connect. */
  wsSend: ((cmd: Record<string, unknown>) => void) | null;
  setWsSend: (fn: ((cmd: Record<string, unknown>) => void) | null) => void;
}

export const useStore = create<AppState>()(
  immer((set, get) => ({
    connected: false,
    chats: {},
    currentChatId: null,

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
          const existing = state.chats[c.chatId];
          next[c.chatId] = {
            ...c,
            messages: existing?.messages ?? [],
          };
        }
        state.chats = next;
      }),

    upsertChat: (patch) =>
      set((state) => {
        const existing = state.chats[patch.chatId];
        if (existing) {
          Object.assign(existing, patch);
        } else {
          // Brittle-as-fuck: a chat needs every wire field to exist. Caller
          // must supply createdAt, lastActive, and state — there's no honest
          // default for any of them. Missing fields explode loudly here.
          if (
            patch.createdAt === undefined ||
            patch.lastActive === undefined ||
            patch.state === undefined
          ) {
            throw new Error(
              `upsertChat: new chat ${patch.chatId} missing required wire fields`,
            );
          }
          state.chats[patch.chatId] = {
            chatId: patch.chatId,
            createdAt: patch.createdAt,
            lastActive: patch.lastActive,
            state: patch.state,
            tokenCount: patch.tokenCount ?? 0,
            contextWindow: patch.contextWindow ?? 1_000_000,
            messages: patch.messages ?? [],
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

    ensureAssistantMessage: (chatId: string, messageId: string): string => {
      // Use get() / set() from the immer callback closure above instead of
      // useStore.getState() / useStore.setState() — the latter creates a
      // circular type reference that collapses the whole store to `any`.
      const chat = get().chats[chatId];
      if (!chat) return messageId;

      // Look for an existing assistant message with this exact ID anywhere
      // in the chat. The backend assigns one msg-<uuid> per assistant
      // message and stamps it on every delta, so two text-deltas with the
      // same messageId belong to the same placeholder regardless of where
      // it currently sits in the message list.
      const existing = chat.messages.find(
        (m) => m.role === "assistant" && (m.data as AssistantMessage).id === messageId,
      );
      if (existing) {
        return messageId;
      }

      // Not found — create a placeholder with the exact ID. Mark it
      // `sealed: false` so convertMessage routes it through the streaming
      // path. The backend's assistant-message event will eventually flip
      // sealed → true to finalize.
      set((s) => {
        const c = s.chats[chatId];
        if (c) {
          c.messages.push({
            role: "assistant",
            data: {
              id: messageId,
              parts: [],
              sealed: false,
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
      return messageId;
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

    setChatState: (chatId, fields) =>
      set((state) => {
        const chat = state.chats[chatId];
        if (chat) {
          chat.state = fields.state;
          chat.tokenCount = fields.tokenCount;
          chat.contextWindow = fields.contextWindow;
        }
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

/** Get the ordered list of chats (newest first by lastActive).
 *  ISO 8601 strings sort lexicographically when same-precision; ours are. */
export const selectChatList = (s: AppState): Chat[] =>
  Object.values(s.chats).sort((a, b) => b.lastActive.localeCompare(a.lastActive));

/** Derive the composer-input rule from a chat's state. The composer
 *  accepts input when the chat is `pending`, `ready`, or
 *  `postprocessing`; locked when `preprocessing` or `processing`. See the
 *  `Chat` docstring in backend/src/alpha/chat.py for the full state machine. */
export function isComposerOpen(chat: Chat | null | undefined): boolean {
  if (!chat) return false;
  return (
    chat.state === "pending" ||
    chat.state === "ready" ||
    chat.state === "postprocessing"
  );
}

/** Inverse of isComposerOpen for assistant-ui's `isRunning` semantics. */
export function isChatBusy(chat: Chat | null | undefined): boolean {
  if (!chat) return false;
  return chat.state === "preprocessing" || chat.state === "processing";
}

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
    // Three-state `sealed`:
    //   false     → currently streaming (placeholder accepting deltas)
    //   true      → finalized by assistant-message after streaming
    //   undefined → loaded from history (treat as sealed; backend's
    //               to_wire() doesn't carry this frontend-only field)
    // Only `sealed === false` is "streaming" — otherwise the message is
    // a static, finished one and goes through the non-animated render path.
    const isStreaming = data.sealed === false;
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
