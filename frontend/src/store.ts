/**
 * Workshop Store — Zustand state management for Alpha.
 *
 * Phase 2: Multi-chat aware. Chats map, active chat, message caching.
 * isRunning is derived from the active chat's state, not stored directly.
 */

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------

export type JSONValue =
  | string
  | number
  | boolean
  | null
  | JSONValue[]
  | { [key: string]: JSONValue };
export type JSONObject = { [key: string]: JSONValue };

export type TextPart = { type: "text"; text: string };
export type ThinkingPart = { type: "thinking"; thinking: string };
export type ImagePart = { type: "image"; image: string };
export type ToolCallPart = {
  type: "tool-call";
  toolCallId: string;
  toolName: string;
  args: JSONObject;
  argsText: string;
  result?: JSONValue;
  isError?: boolean;
};
export type ContentPart = TextPart | ThinkingPart | ImagePart | ToolCallPart;

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: ContentPart[];
  createdAt: Date;
}

export type ChatState = "starting" | "idle" | "busy" | "dead";

export interface ChatMeta {
  id: string;
  title: string;
  state: ChatState;
  updatedAt: number;
  sessionUuid?: string;
  tokenCount?: number;
  contextWindow?: number;
}

// -----------------------------------------------------------------------------
// Store Interface
// -----------------------------------------------------------------------------

interface WorkshopState {
  // Multi-chat
  chats: Record<string, ChatMeta>;
  activeChatId: string | null;

  // Messages (active chat)
  messages: Message[];
  messageCache: Record<string, Message[]>;

  // Connection
  connected: boolean;

  // Context meter
  contextPercent: number;
  model: string | null;
  tokenCount: number;
  tokenLimit: number;
}

interface WorkshopActions {
  // Chat management
  setChats: (chatList: ChatMeta[]) => void;
  addChat: (chat: ChatMeta) => void;
  updateChatState: (chatId: string, state: ChatState, title?: string, updatedAt?: number, sessionUuid?: string, tokenCount?: number, contextWindow?: number) => void;
  setActiveChatId: (chatId: string | null) => void;

  // Messages
  addUserMessage: (content: string, attachments?: Array<{ type: "image"; image: string }>) => string;
  addAssistantPlaceholder: () => string;
  appendToAssistant: (messageId: string, text: string, chatId?: string) => void;
  appendThinking: (messageId: string, thinking: string, chatId?: string) => void;
  addToolCall: (messageId: string, toolCall: Omit<ToolCallPart, "type">, chatId?: string) => void;
  updateToolResult: (
    messageId: string,
    toolCallId: string,
    result: JSONValue,
    isError?: boolean,
    chatId?: string,
  ) => void;
  setMessages: (messages: readonly Message[] | Message[]) => void;

  // Remote messages (echoed from other connections via the switch)
  addRemoteUserMessage: (chatId: string, content: ContentPart[]) => string;
  addRemoteAssistantPlaceholder: (chatId: string) => string;

  // Cache
  cacheActiveMessages: () => void;
  loadFromCache: (chatId: string) => boolean;
  loadMessages: (chatId: string, messages: Message[]) => void;

  // Connection
  setConnected: (connected: boolean) => void;

  // Context meter
  setContextPercent: (percent: number) => void;
  setModel: (model: string | null) => void;
  setTokens: (count: number, limit: number) => void;
  updateChatTokens: (chatId: string, tokenCount: number, contextWindow: number) => void;

  // Reset
  reset: () => void;
}

export type WorkshopStore = WorkshopState & WorkshopActions;

// -----------------------------------------------------------------------------
// ID Generation
// -----------------------------------------------------------------------------

let messageIdCounter = 0;
export const generateId = () => `msg-${Date.now()}-${++messageIdCounter}`;

// -----------------------------------------------------------------------------
// Store
// -----------------------------------------------------------------------------

const initialState: WorkshopState = {
  chats: {},
  activeChatId: null,
  messages: [],
  messageCache: {},
  connected: false,
  contextPercent: 0,
  model: null,
  tokenCount: 0,
  tokenLimit: 0,
};

export const useWorkshopStore = create<WorkshopStore>()(
  immer((set, get) => ({
    ...initialState,

    // -- Chat management ------------------------------------------------------

    setChats: (chatList) => {
      set((state) => {
        const map: Record<string, ChatMeta> = {};
        for (const chat of chatList) {
          map[chat.id] = chat;
        }
        state.chats = map;

        // Restore token state for the active chat.
        // Handles the race where setActiveChatId fires before chat-list arrives.
        // (setActiveChatId always fires first — React effects run before the
        //  WebSocket network round-trip completes, so activeChatId is set by
        //  the time the chat-list response arrives here.)
        if (state.activeChatId) {
          const active = map[state.activeChatId];
          if (active) {
            const tc = active.tokenCount ?? 0;
            const cw = active.contextWindow ?? 200_000;
            state.tokenCount = tc;
            state.tokenLimit = cw;
            state.contextPercent = cw > 0
              ? Math.round((tc / cw) * 1000) / 10
              : 0;
          }
        }
      });
    },

    addChat: (chat) => {
      set((state) => {
        state.chats[chat.id] = chat;
      });
    },

    updateChatState: (chatId, chatState, title, updatedAt, sessionUuid?, tokenCount?, contextWindow?) => {
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        chat.state = chatState;
        if (title !== undefined) chat.title = title;
        if (updatedAt !== undefined) chat.updatedAt = updatedAt;
        if (sessionUuid !== undefined) chat.sessionUuid = sessionUuid;
        if (tokenCount !== undefined) chat.tokenCount = tokenCount;
        if (contextWindow !== undefined) chat.contextWindow = contextWindow;

        // Update global meter if this is the active chat
        if (chatId === state.activeChatId && tokenCount !== undefined && contextWindow !== undefined) {
          state.tokenCount = tokenCount;
          state.tokenLimit = contextWindow;
          state.contextPercent = contextWindow > 0
            ? Math.round((tokenCount / contextWindow) * 1000) / 10
            : 0;
        }
      });
    },

    setActiveChatId: (chatId) => {
      set((state) => {
        // Cache current messages before switching
        const prevId = state.activeChatId;
        if (prevId && state.messages.length > 0) {
          state.messageCache[prevId] = [...state.messages];
        }

        state.activeChatId = chatId;

        // Restore from cache or clear (instant swap per KERNEL.md)
        if (chatId && state.messageCache[chatId]) {
          state.messages = [...state.messageCache[chatId]];
        } else {
          state.messages = [];
        }

        // Restore token state from the new chat's metadata
        if (chatId) {
          const chat = state.chats[chatId];
          if (chat) {
            const tc = chat.tokenCount ?? 0;
            const cw = chat.contextWindow ?? 200_000;
            state.tokenCount = tc;
            state.tokenLimit = cw;
            state.contextPercent = cw > 0
              ? Math.round((tc / cw) * 1000) / 10
              : 0;
          }
        }
      });
    },

    // -- Messages -------------------------------------------------------------

    addUserMessage: (content, attachments) => {
      const id = generateId();
      const parts: ContentPart[] = [];

      // Images first (render as separate bubbles above text)
      if (attachments) {
        for (const att of attachments) {
          if (att.type === "image") {
            parts.push({ type: "image", image: att.image });
          }
        }
      }

      // Text (only if non-empty)
      if (content.trim()) {
        parts.push({ type: "text", text: content });
      }

      set((state) => {
        state.messages.push({
          id,
          role: "user",
          content: parts,
          createdAt: new Date(),
        });
      });
      return id;
    },

    addAssistantPlaceholder: () => {
      const id = generateId();
      set((state) => {
        state.messages.push({
          id,
          role: "assistant",
          content: [],
          createdAt: new Date(),
        });
      });
      return id;
    },

    appendToAssistant: (messageId, text, chatId?) => {
      set((state) => {
        // Look in active messages first, then fall back to background chat cache
        let message = state.messages.find((m) => m.id === messageId);
        if (!message && chatId && chatId !== state.activeChatId) {
          const cached = state.messageCache[chatId];
          if (cached) message = cached.find((m) => m.id === messageId);
        }
        if (!message || message.role !== "assistant") return;

        const lastPart = message.content[message.content.length - 1];
        if (lastPart?.type === "text") {
          lastPart.text += text;
        } else {
          message.content.push({ type: "text", text });
        }
      });
    },

    appendThinking: (messageId, thinking, chatId?) => {
      set((state) => {
        let message = state.messages.find((m) => m.id === messageId);
        if (!message && chatId && chatId !== state.activeChatId) {
          const cached = state.messageCache[chatId];
          if (cached) message = cached.find((m) => m.id === messageId);
        }
        if (!message || message.role !== "assistant") return;

        const thinkingPart = message.content.find(
          (p): p is { type: "thinking"; thinking: string } => p.type === "thinking"
        );
        if (thinkingPart) {
          thinkingPart.thinking += thinking;
        } else {
          message.content.unshift({ type: "thinking", thinking });
        }
      });
    },

    addToolCall: (messageId, toolCall, chatId?) => {
      set((state) => {
        let message = state.messages.find((m) => m.id === messageId);
        if (!message && chatId && chatId !== state.activeChatId) {
          const cached = state.messageCache[chatId];
          if (cached) message = cached.find((m) => m.id === messageId);
        }
        if (!message || message.role !== "assistant") return;
        message.content.push({ type: "tool-call", ...toolCall });
      });
    },

    updateToolResult: (messageId, toolCallId, result, isError = false, chatId?) => {
      set((state) => {
        let message = state.messages.find((m) => m.id === messageId);
        if (!message && chatId && chatId !== state.activeChatId) {
          const cached = state.messageCache[chatId];
          if (cached) message = cached.find((m) => m.id === messageId);
        }
        if (!message) return;

        const toolCall = message.content.find(
          (p): p is ToolCallPart =>
            p.type === "tool-call" && p.toolCallId === toolCallId
        );
        if (toolCall) {
          toolCall.result = result;
          toolCall.isError = isError;
        }
      });
    },

    setMessages: (messages) => {
      set((state) => {
        state.messages = [...messages];
      });
    },

    // -- Remote messages (echoed from other connections via the switch) --------

    addRemoteUserMessage: (chatId, content) => {
      const id = generateId();
      set((state) => {
        const msg: Message = {
          id,
          role: "user" as const,
          content,
          createdAt: new Date(),
        };
        if (chatId === state.activeChatId) {
          state.messages.push(msg);
        } else {
          if (!state.messageCache[chatId]) state.messageCache[chatId] = [];
          state.messageCache[chatId].push(msg);
        }
      });
      return id;
    },

    addRemoteAssistantPlaceholder: (chatId) => {
      const id = generateId();
      set((state) => {
        const msg: Message = {
          id,
          role: "assistant" as const,
          content: [],
          createdAt: new Date(),
        };
        if (chatId === state.activeChatId) {
          state.messages.push(msg);
        } else {
          if (!state.messageCache[chatId]) state.messageCache[chatId] = [];
          state.messageCache[chatId].push(msg);
        }
      });
      return id;
    },

    // -- Cache ----------------------------------------------------------------

    cacheActiveMessages: () => {
      const { activeChatId, messages } = get();
      if (activeChatId && messages.length > 0) {
        set((state) => {
          state.messageCache[activeChatId] = [...messages];
        });
      }
    },

    loadFromCache: (chatId) => {
      const cached = get().messageCache[chatId];
      if (cached && cached.length > 0) {
        set((state) => {
          state.messages = [...cached];
        });
        return true;
      }
      return false;
    },

    loadMessages: (chatId, messages) => {
      set((state) => {
        state.messages = messages;
        state.messageCache[chatId] = [...messages];
      });
    },

    // -- Connection -----------------------------------------------------------

    setConnected: (connected) => {
      set((state) => {
        state.connected = connected;
      });
    },

    // -- Context meter --------------------------------------------------------

    setContextPercent: (percent) => {
      set((state) => {
        state.contextPercent = percent;
      });
    },

    setModel: (model) => {
      set((state) => {
        state.model = model;
      });
    },

    setTokens: (count, limit) => {
      set((state) => {
        state.tokenCount = count;
        state.tokenLimit = limit;
      });
    },

    updateChatTokens: (chatId, tokenCount, contextWindow) => {
      set((state) => {
        // Update per-chat metadata
        const chat = state.chats[chatId];
        if (chat) {
          chat.tokenCount = tokenCount;
          chat.contextWindow = contextWindow;
        }

        // Update global meter if this is the active chat
        if (chatId === state.activeChatId) {
          state.tokenCount = tokenCount;
          state.tokenLimit = contextWindow;
          state.contextPercent = contextWindow > 0
            ? Math.round((tokenCount / contextWindow) * 1000) / 10
            : 0;
        }
      });
    },

    // -- Reset ----------------------------------------------------------------

    reset: () => {
      set(initialState);
    },
  }))
);
