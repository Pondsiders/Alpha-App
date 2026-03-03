/**
 * Workshop Store — Zustand state management for Alpha.
 *
 * Single source of truth for conversation state.
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

// -----------------------------------------------------------------------------
// Store Interface
// -----------------------------------------------------------------------------

interface WorkshopState {
  sessionId: string | null;
  messages: Message[];
  isRunning: boolean;
  contextPercent: number;
  model: string | null;
  tokenCount: number;
  tokenLimit: number;
}

interface WorkshopActions {
  addUserMessage: (content: string, attachments?: Array<{ type: "image"; image: string }>) => string;
  addAssistantPlaceholder: () => string;
  appendToAssistant: (messageId: string, text: string) => void;
  appendThinking: (messageId: string, thinking: string) => void;
  addToolCall: (messageId: string, toolCall: Omit<ToolCallPart, "type">) => void;
  updateToolResult: (
    messageId: string,
    toolCallId: string,
    result: JSONValue,
    isError?: boolean
  ) => void;
  setMessages: (messages: readonly Message[] | Message[]) => void;
  setSessionId: (sessionId: string | null) => void;
  setRunning: (running: boolean) => void;
  setContextPercent: (percent: number) => void;
  setModel: (model: string | null) => void;
  setTokens: (count: number, limit: number) => void;
  reset: () => void;
  loadSession: (sessionId: string, messages: Message[]) => void;
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
  sessionId: null,
  messages: [],
  isRunning: false,
  contextPercent: 0,
  model: null,
  tokenCount: 0,
  tokenLimit: 0,
};

export const useWorkshopStore = create<WorkshopStore>()(
  immer((set) => ({
    ...initialState,

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

    appendToAssistant: (messageId, text) => {
      set((state) => {
        const message = state.messages.find((m) => m.id === messageId);
        if (!message || message.role !== "assistant") return;

        const lastPart = message.content[message.content.length - 1];
        if (lastPart?.type === "text") {
          lastPart.text += text;
        } else {
          message.content.push({ type: "text", text });
        }
      });
    },

    appendThinking: (messageId, thinking) => {
      set((state) => {
        const message = state.messages.find((m) => m.id === messageId);
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

    addToolCall: (messageId, toolCall) => {
      set((state) => {
        const message = state.messages.find((m) => m.id === messageId);
        if (!message || message.role !== "assistant") return;
        message.content.push({ type: "tool-call", ...toolCall });
      });
    },

    updateToolResult: (messageId, toolCallId, result, isError = false) => {
      set((state) => {
        const message = state.messages.find((m) => m.id === messageId);
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

    setSessionId: (sessionId) => {
      set((state) => {
        state.sessionId = sessionId;
      });
    },

    setRunning: (running) => {
      set((state) => {
        state.isRunning = running;
      });
    },

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

    reset: () => {
      set(initialState);
    },

    loadSession: (sessionId, messages) => {
      set((state) => {
        state.sessionId = sessionId;
        state.messages = messages;
      });
    },
  }))
);
