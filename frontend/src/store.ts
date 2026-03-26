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
  /** True while input_json_delta events are still arriving. */
  streaming?: boolean;
};
export type SystemNotificationPart = {
  type: "system-notification";
  text: string;
  source: string;   // "task_notification", "post_turn", etc.
  taskId?: string;
  status?: string;
};
export type ContentPart = TextPart | ThinkingPart | ImagePart | ToolCallPart | SystemNotificationPart;

/** Who initiated this message. Undefined = human (the default). */
export type MessageSource = "human" | "intro" | "infrastructure";

/** A recalled memory surfaced by the enrichment pipeline. */
export interface RecalledMemory {
  id: number;
  content: string;
  score: number;
  created_at: string;
}

/** A temporal capsule (yesterday, last night, today, letter). */
export interface CapsuleData {
  key: string;
  title: string;
  content: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: ContentPart[];
  createdAt: Date;
  source?: MessageSource;
  // Enrichment fields — populated by progressive user-message events
  timestamp?: string;
  memories?: RecalledMemory[];
  capsules?: CapsuleData[];
}

export type ChatState = "starting" | "idle" | "busy" | "dead";

export interface ApproachLight {
  level: "yellow" | "red";
  text: string;
}

export interface ChatMeta {
  id: string;
  title: string;
  state: ChatState;
  updatedAt: number;
  createdAt: number;  // Unix epoch seconds — set once at creation, never updated
  sessionUuid?: string;
  tokenCount?: number;
  contextWindow?: number;
  topics?: Record<string, string>;  // { "alpha-app": "on", "intake": "off" }
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

  // Approach lights (per-chat)
  approachLights: Record<string, ApproachLight[]>;

  // Stash for reconciliation — the raw text the user just submitted.
  // Set by addUserMessage, consumed by reconcileUserMessage.
  _pendingSendText: string | null;

  // Pending echo queue — tracks messages we sent so we can match their
  // claude echoes. Each entry records whether it was an interjection.
  // When the echo arrives, we find-and-splice the matching entry:
  //   isInterjection=true  → create new assistant placeholder (turn boundary)
  //   isInterjection=false → drop (original prompt echo, already rendered)
  _pendingEchos: Array<{ text: string; isInterjection: boolean }>;

  // Replay — when true, message list doesn't render (store mutates silently)
  isReplaying: boolean;

  // Agent progress — transient state for active subagent rendering.
  // Keyed by toolUseId (ties back to the Agent tool-call part).
  // Ephemeral — not persisted. Cleared on agent-done.
  agentProgress: Record<string, {
    taskId: string;
    prompt?: string;
    description?: string;
    lastToolName?: string;
    toolUses?: number;
    durationMs?: number;
    status?: string;
    summary?: string;
    done?: boolean;
  }>;
}

interface WorkshopActions {
  // Chat management
  setChats: (chatList: ChatMeta[]) => void;
  addChat: (chat: ChatMeta) => void;
  updateChatState: (chatId: string, state: ChatState, title?: string, updatedAt?: number, sessionUuid?: string, tokenCount?: number, contextWindow?: number, topics?: Record<string, string>) => void;
  setActiveChatId: (chatId: string | null) => void;

  // Messages
  addUserMessage: (content: string, attachments?: Array<{ type: "image"; image: string }>, source?: MessageSource) => string;
  addAssistantPlaceholder: () => string;
  appendToAssistant: (messageId: string, text: string, chatId?: string) => void;
  appendThinking: (messageId: string, thinking: string, chatId?: string) => void;
  addToolCall: (messageId: string, toolCall: Omit<ToolCallPart, "type">, chatId?: string) => void;
  addStreamingToolCall: (messageId: string, toolCallId: string, toolName: string, chatId?: string) => void;
  appendToolUseDelta: (messageId: string, toolCallId: string, partialJson: string, chatId?: string) => void;
  updateToolResult: (
    messageId: string,
    toolCallId: string,
    result: JSONValue,
    isError?: boolean,
    chatId?: string,
  ) => void;
  setMessages: (messages: readonly Message[] | Message[]) => void;

  // System messages (task notifications, post-turn, etc.)
  addSystemMessage: (chatId: string, text: string, source: string, extra?: Record<string, string>) => void;

  // Remote messages (echoed from other connections via the switch)
  addRemoteUserMessage: (chatId: string, content: ContentPart[], serverId?: string) => string;
  addRemoteAssistantPlaceholder: (chatId: string) => string;

  // Reconciliation — merge enrobed echo into optimistic user message
  reconcileUserMessage: (chatId: string, echoContent: ContentPart[]) => boolean;

  // ID-based reconciliation — update a user message by its ID and enrichment
  updateUserMessageById: (chatId: string, messageId: string, wireData: {
    content?: ContentPart[];
    timestamp?: string;
    memories?: RecalledMemory[];
    orientation?: { capsules?: CapsuleData[] };
  }) => boolean;

  // Cache
  cacheActiveMessages: () => void;
  loadFromCache: (chatId: string) => boolean;
  loadMessages: (chatId: string, messages: Message[]) => void;

  // Connection
  setConnected: (connected: boolean) => void;

  // Replay
  setReplaying: (replaying: boolean) => void;

  // Context meter
  setContextPercent: (percent: number) => void;
  setModel: (model: string | null) => void;
  setTokens: (count: number, limit: number) => void;
  updateChatTokens: (chatId: string, tokenCount: number, contextWindow: number) => void;

  // Pending echo queue
  pushPendingEcho: (text: string, isInterjection: boolean) => void;
  matchPendingEcho: (echoText: string) => { isInterjection: boolean } | null;

  // Approach lights
  addApproachLight: (chatId: string, level: "yellow" | "red", text: string) => void;

  // Agent progress (transient)
  updateAgentStarted: (toolUseId: string, data: { taskId: string; prompt?: string; description?: string }) => void;
  updateAgentProgress: (toolUseId: string, data: { description?: string; lastToolName?: string; toolUses?: number; durationMs?: number }) => void;
  updateAgentDone: (toolUseId: string, data: { status?: string; summary?: string; toolUses?: number; durationMs?: number }) => void;

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
  approachLights: {},
  _pendingSendText: null,
  _pendingEchos: [],
  isReplaying: false,
  agentProgress: {},
};

export const useWorkshopStore = create<WorkshopStore>()(
  immer((set, get) => ({
    ...initialState,

    // -- Chat management ------------------------------------------------------

    setChats: (chatList) => {
      set((state) => {
        const map: Record<string, ChatMeta> = {};
        for (const chat of chatList) {
          // Merge with existing chat data to preserve fields (like topics)
          // that chat-list doesn't carry but chat-state does.
          const existing = state.chats[chat.id];
          map[chat.id] = existing ? { ...existing, ...chat } : chat;
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

    updateChatState: (chatId, chatState, title, updatedAt, sessionUuid?, tokenCount?, contextWindow?, topics?) => {
      set((state) => {
        const chat = state.chats[chatId];
        if (!chat) return;
        chat.state = chatState;
        if (title !== undefined) chat.title = title;
        if (updatedAt !== undefined) chat.updatedAt = updatedAt;
        if (sessionUuid !== undefined) chat.sessionUuid = sessionUuid;
        if (tokenCount !== undefined) chat.tokenCount = tokenCount;
        if (contextWindow !== undefined) chat.contextWindow = contextWindow;
        if (topics !== undefined) chat.topics = topics;

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

    addUserMessage: (content, attachments, source) => {
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
          source,
        });
        // Stash the raw text for reconciliation with the echo
        state._pendingSendText = content.trim() || null;
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

        // Append to the LAST content part if it's a thinking block.
        // If something else was inserted since (tool-call, text), start a new
        // thinking block so interleaved thinking renders in stream order.
        const last = message.content[message.content.length - 1];
        if (last?.type === "thinking") {
          (last as ThinkingPart).thinking += thinking;
        } else {
          message.content.push({ type: "thinking", thinking });
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
        // If a streaming placeholder exists for this toolCallId, finalize it.
        const existing = message.content.find(
          (p): p is ToolCallPart =>
            p.type === "tool-call" && p.toolCallId === toolCall.toolCallId
        );
        if (existing) {
          existing.args = toolCall.args;
          existing.argsText = toolCall.argsText;
          existing.streaming = false;
        } else {
          message.content.push({ type: "tool-call", ...toolCall });
        }
      });
    },

    addStreamingToolCall: (messageId, toolCallId, toolName, chatId?) => {
      set((state) => {
        let message = state.messages.find((m) => m.id === messageId);
        if (!message && chatId && chatId !== state.activeChatId) {
          const cached = state.messageCache[chatId];
          if (cached) message = cached.find((m) => m.id === messageId);
        }
        if (!message || message.role !== "assistant") return;
        message.content.push({
          type: "tool-call",
          toolCallId,
          toolName,
          args: {},
          argsText: "",
          streaming: true,
        });
      });
    },

    appendToolUseDelta: (messageId, toolCallId, partialJson, chatId?) => {
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
          toolCall.argsText += partialJson;
        }
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

    addRemoteUserMessage: (chatId, content, serverId?) => {
      const id = serverId || generateId();
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

    addSystemMessage: (chatId, text, source, extra) => {
      set((state) => {
        const msg: Message = {
          id: generateId(),
          role: "system" as const,
          content: [{
            type: "system-notification",
            text,
            source,
            ...(extra || {}),
          } as SystemNotificationPart],
          createdAt: new Date(),
        };
        if (chatId === state.activeChatId) {
          state.messages.push(msg);
        } else {
          if (!state.messageCache[chatId]) state.messageCache[chatId] = [];
          state.messageCache[chatId].push(msg);
        }
      });
    },

    // -- Reconciliation -------------------------------------------------------

    reconcileUserMessage: (chatId, echoContent) => {
      // Stash-based reconciliation. When the user sends a message, addUserMessage
      // stashes the raw text. When a user-message echo arrives, we check if ANY
      // text block in the echo matches the stash. If so, update the message.
      // Simple. No "last block" logic. Reed that bends.

      const stash = get()._pendingSendText;
      if (!stash) return false;

      // Does ANY text block in the echo contain the stashed string?
      const hasMatch = Array.isArray(echoContent) && echoContent.some(
        (p: unknown) => {
          if (typeof p !== "object" || p === null) return false;
          const block = p as Record<string, unknown>;
          return block.type === "text" && typeof block.text === "string"
            && block.text.trim() === stash.trim();
        }
      );
      if (!hasMatch) return false;

      // Find the most recent user message (scan from end)
      const messages = chatId === get().activeChatId
        ? get().messages
        : get().messageCache[chatId] || [];

      let targetIdx = -1;
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === "user") {
          targetIdx = i;
          break;
        }
      }
      if (targetIdx === -1) return false;

      // Replace content with the full enrobed echo.
      set((state) => {
        const arr = chatId === state.activeChatId
          ? state.messages
          : (state.messageCache[chatId] || []);
        const msg = arr[targetIdx];
        if (msg && msg.role === "user") {
          msg.content = echoContent as ContentPart[];
        }
      });

      return true;
    },

    // ID-based reconciliation — find a user message by ID and update its
    // content + enrichment fields (timestamp, memories, capsules).
    // Also clears _pendingSendText so the claude echo can't clobber the update.
    updateUserMessageById: (chatId, messageId, wireData) => {
      const messages = chatId === get().activeChatId
        ? get().messages
        : get().messageCache[chatId] || [];

      const target = messages.find((m) => m.id === messageId && m.role === "user");
      if (!target) return false;

      set((state) => {
        const arr = chatId === state.activeChatId
          ? state.messages
          : (state.messageCache[chatId] || []);
        const msg = arr.find((m) => m.id === messageId && m.role === "user");
        if (msg) {
          if (wireData.content) msg.content = wireData.content as ContentPart[];
          if (wireData.timestamp !== undefined) msg.timestamp = wireData.timestamp;
          if (wireData.memories) msg.memories = wireData.memories;
          if (wireData.orientation?.capsules) msg.capsules = wireData.orientation.capsules;
        }
        state._pendingSendText = null;
      });

      return true;
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

    // -- Replay ---------------------------------------------------------------

    setReplaying: (replaying) => {
      set((state) => {
        state.isReplaying = replaying;
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

    // -- Approach lights ------------------------------------------------------

    addApproachLight: (chatId, level, text) => {
      set((state) => {
        if (!state.approachLights[chatId]) {
          state.approachLights[chatId] = [];
        }
        state.approachLights[chatId].push({ level, text });
      });
    },

    // -- Pending echo queue ---------------------------------------------------

    pushPendingEcho: (text, isInterjection) => {
      set((state) => {
        state._pendingEchos.push({ text, isInterjection });
      });
    },

    matchPendingEcho: (echoText) => {
      const echos = get()._pendingEchos;
      const idx = echos.findIndex((e) => e.text === echoText);
      if (idx === -1) return null;
      const match = echos[idx];
      // Remove the matched entry
      set((state) => {
        state._pendingEchos.splice(idx, 1);
      });
      return { isInterjection: match.isInterjection };
    },

    // -- Agent progress (transient) -------------------------------------------

    updateAgentStarted: (toolUseId, data) => {
      set((state) => {
        state.agentProgress[toolUseId] = {
          taskId: data.taskId,
          prompt: data.prompt,
          description: data.description,
        };
      });
    },

    updateAgentProgress: (toolUseId, data) => {
      set((state) => {
        const existing = state.agentProgress[toolUseId];
        if (existing) {
          if (data.description) existing.description = data.description;
          if (data.lastToolName) existing.lastToolName = data.lastToolName;
          if (data.toolUses !== undefined) existing.toolUses = data.toolUses;
          if (data.durationMs !== undefined) existing.durationMs = data.durationMs;
        }
      });
    },

    updateAgentDone: (toolUseId, data) => {
      set((state) => {
        const existing = state.agentProgress[toolUseId];
        if (existing) {
          existing.done = true;
          if (data.status) existing.status = data.status;
          if (data.summary) existing.summary = data.summary;
          if (data.toolUses !== undefined) existing.toolUses = data.toolUses;
          if (data.durationMs !== undefined) existing.durationMs = data.durationMs;
        }
      });
    },

    // -- Reset ----------------------------------------------------------------

    reset: () => {
      set(initialState);
    },
  }))
);
