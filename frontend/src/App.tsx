import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import ChatPage from "./pages/ChatPage";
import DevContextMeter from "./pages/DevContextMeter";
import DevStatusBar from "./pages/DevStatusBar";
import DevTopics from "./pages/DevTopics";
import DevMemoryStore from "./pages/DevMemoryStore";
import DevMemoryCards from "./pages/DevMemoryCards";
import DevTools from "./pages/DevTools";
import { SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { AppSidebar } from "@/components/AppSidebar";
import { useWebSocket, type ServerEvent, type ClientMessage } from "@/lib/useWebSocket";
import {
  useWorkshopStore,
  generateId,
  type ChatMeta,
  type ChatState,
  type ContentPart,
  type JSONObject,
  type JSONValue,
  type Message,
  type ToolCallPart,
  type RecalledMemory,
  type CapsuleData,
  type MessageSource,
} from "./store";

// ---------------------------------------------------------------------------
// Replay buggering — build full Message[] from buffered events in one pass.
// Pure JavaScript, no Zustand, no immer. Fast.
// ---------------------------------------------------------------------------

const REPLAY_BUFFERED_EVENTS = new Set([
  "user-message", "assistant-message", "text-delta", "thinking-delta",
  "tool-call", "tool-result", "done",
  "chat-state", "context-update",  // Ephemeral runtime state — stale on replay
]);

interface ReplayResult {
  messages: Message[];
  tokenCount?: number;
  contextWindow?: number;
}

function processReplayBuffer(events: ServerEvent[]): ReplayResult {
  const messages: Message[] = [];
  let currentAssistant: Message | null = null;
  let lastTokenCount: number | undefined;
  let lastContextWindow: number | undefined;

  for (const event of events) {
    switch (event.type) {
      case "user-message": {
        const data = event.data as {
          id?: string;
          content?: ContentPart[];
          timestamp?: string;
          memories?: RecalledMemory[];
          orientation?: { capsules?: CapsuleData[] };
        };

        // Progressive enrichment: multiple user-message events may arrive
        // for the same message (timestamp first, then memories). Deduplicate
        // by matching on data.id — last snapshot wins.
        const existingIdx = data.id
          ? messages.findIndex((m) => m.id === data.id)
          : -1;

        if (existingIdx >= 0) {
          // Update in place — don't create a new message, don't reset assistant
          const existing = messages[existingIdx];
          if (data.content) existing.content = data.content as ContentPart[];
          if (data.timestamp !== undefined) existing.timestamp = data.timestamp;
          if (data.memories) existing.memories = data.memories;
          if (data.orientation?.capsules) existing.capsules = data.orientation.capsules;
        } else if (!data.id && messages.length > 0 && messages[messages.length - 1]?.role === "user") {
          // ID-less user-message after an existing user message = claude echo.
          // Skip it — the labeled version already captured this turn.
          // (Don't reset currentAssistant either.)
        } else {
          // New user message
          messages.push({
            id: data.id || generateId(),
            role: "user",
            content: data.content || [],
            createdAt: new Date(),
            timestamp: data.timestamp,
            memories: data.memories,
            capsules: data.orientation?.capsules,
          });
          currentAssistant = null;
        }
        break;
      }
      case "text-delta": {
        if (!currentAssistant) {
          currentAssistant = { id: generateId(), role: "assistant", content: [], createdAt: new Date() };
          messages.push(currentAssistant);
        }
        const text = event.data as string;
        const last = currentAssistant.content[currentAssistant.content.length - 1];
        if (last?.type === "text") {
          (last as { type: "text"; text: string }).text += text;
        } else {
          currentAssistant.content.push({ type: "text", text });
        }
        break;
      }
      case "thinking-delta": {
        if (!currentAssistant) {
          currentAssistant = { id: generateId(), role: "assistant", content: [], createdAt: new Date() };
          messages.push(currentAssistant);
        }
        const thinking = event.data as string;
        const last = currentAssistant.content[currentAssistant.content.length - 1];
        if (last?.type === "thinking") {
          (last as { type: "thinking"; thinking: string }).thinking += thinking;
        } else {
          currentAssistant.content.push({ type: "thinking", thinking });
        }
        break;
      }
      case "tool-call": {
        if (!currentAssistant) {
          currentAssistant = { id: generateId(), role: "assistant", content: [], createdAt: new Date() };
          messages.push(currentAssistant);
        }
        const tc = event.data as {
          toolCallId: string;
          toolName: string;
          args: JSONObject;
          argsText: string;
        };
        currentAssistant.content.push({
          type: "tool-call",
          toolCallId: tc.toolCallId,
          toolName: tc.toolName,
          args: tc.args,
          argsText: tc.argsText,
        });
        break;
      }
      case "tool-result": {
        if (!currentAssistant) break;
        const { toolCallId, result, isError } = event.data as {
          toolCallId: string;
          result: JSONValue;
          isError?: boolean;
        };
        const toolCall = currentAssistant.content.find(
          (p): p is ToolCallPart => p.type === "tool-call" && p.toolCallId === toolCallId
        );
        if (toolCall) {
          toolCall.result = result;
          toolCall.isError = isError;
        }
        break;
      }
      case "assistant-message": {
        // Coalesced assistant message — complete, all parts in order.
        // This is the primary replay path. Deltas are ephemeral (not stored).
        // Also carries context window info for the meter.
        const amData = event.data as {
          tokenCount?: number;
          contextWindow?: number;
          parts?: Array<{
            type: string;
            text?: string;
            thinking?: string;
            toolCallId?: string;
            toolName?: string;
            args?: JSONObject;
            argsText?: string;
          }>;
        };
        const amParts = amData.parts || [];
        const amContent: ContentPart[] = [];
        for (const part of amParts) {
          if (part.type === "text" && part.text) {
            amContent.push({ type: "text", text: part.text });
          } else if (part.type === "thinking" && part.thinking) {
            amContent.push({ type: "thinking", thinking: part.thinking });
          } else if (part.type === "tool-call" && part.toolCallId) {
            amContent.push({
              type: "tool-call",
              toolCallId: part.toolCallId,
              toolName: part.toolName || "",
              args: (part.args || {}) as JSONObject,
              argsText: part.argsText || "",
            });
          }
        }
        messages.push({
          id: generateId(),
          role: "assistant",
          content: amContent,
          createdAt: new Date(),
        });
        if (amData.tokenCount !== undefined) lastTokenCount = amData.tokenCount;
        if (amData.contextWindow !== undefined) lastContextWindow = amData.contextWindow;
        currentAssistant = null;
        break;
      }
      case "done": {
        currentAssistant = null;
        break;
      }
    }
  }

  return { messages, tokenCount: lastTokenCount, contextWindow: lastContextWindow };
}

// ---------------------------------------------------------------------------
// Layout — owns the WebSocket, dispatches all events to the store
// ---------------------------------------------------------------------------

function Layout() {
  const navigate = useNavigate();
  const { chatId } = useParams<{ chatId?: string }>();

  // Stable refs for use inside the event callback
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;

  // Store actions — wrapped in a ref so the event callback stays stable
  const actionsRef = useRef({
    setChats: useWorkshopStore.getState().setChats,
    addChat: useWorkshopStore.getState().addChat,
    updateChatState: useWorkshopStore.getState().updateChatState,
    setActiveChatId: useWorkshopStore.getState().setActiveChatId,
    setConnected: useWorkshopStore.getState().setConnected,
    appendToAssistant: useWorkshopStore.getState().appendToAssistant,
    appendThinking: useWorkshopStore.getState().appendThinking,
    addToolCall: useWorkshopStore.getState().addToolCall,
    addStreamingToolCall: useWorkshopStore.getState().addStreamingToolCall,
    appendToolUseDelta: useWorkshopStore.getState().appendToolUseDelta,
    updateToolResult: useWorkshopStore.getState().updateToolResult,
    updateChatTokens: useWorkshopStore.getState().updateChatTokens,
    addRemoteUserMessage: useWorkshopStore.getState().addRemoteUserMessage,
    addRemoteAssistantPlaceholder: useWorkshopStore.getState().addRemoteAssistantPlaceholder,
    addApproachLight: useWorkshopStore.getState().addApproachLight,
    loadMessages: useWorkshopStore.getState().loadMessages,
    reconcileUserMessage: useWorkshopStore.getState().reconcileUserMessage,
    updateUserMessageById: useWorkshopStore.getState().updateUserMessageById,
    setReplaying: useWorkshopStore.getState().setReplaying,
  });
  // Keep the ref fresh (store actions are stable with immer, but belt & suspenders)
  actionsRef.current = {
    setChats: useWorkshopStore.getState().setChats,
    addChat: useWorkshopStore.getState().addChat,
    updateChatState: useWorkshopStore.getState().updateChatState,
    setActiveChatId: useWorkshopStore.getState().setActiveChatId,
    setConnected: useWorkshopStore.getState().setConnected,
    appendToAssistant: useWorkshopStore.getState().appendToAssistant,
    appendThinking: useWorkshopStore.getState().appendThinking,
    addToolCall: useWorkshopStore.getState().addToolCall,
    addStreamingToolCall: useWorkshopStore.getState().addStreamingToolCall,
    appendToolUseDelta: useWorkshopStore.getState().appendToolUseDelta,
    updateToolResult: useWorkshopStore.getState().updateToolResult,
    updateChatTokens: useWorkshopStore.getState().updateChatTokens,
    addRemoteUserMessage: useWorkshopStore.getState().addRemoteUserMessage,
    addRemoteAssistantPlaceholder: useWorkshopStore.getState().addRemoteAssistantPlaceholder,
    addApproachLight: useWorkshopStore.getState().addApproachLight,
    loadMessages: useWorkshopStore.getState().loadMessages,
    reconcileUserMessage: useWorkshopStore.getState().reconcileUserMessage,
    updateUserMessageById: useWorkshopStore.getState().updateUserMessageById,
    setReplaying: useWorkshopStore.getState().setReplaying,
  };

  // Shared assistant ID map — Layout reads, ChatPage writes
  const assistantIdMapRef = useRef<Record<string, string | null>>({});

  // Replay buffer — accumulates events per chatId until replay-done
  const replayBuffersRef = useRef<Record<string, ServerEvent[]>>({});

  // Tool-use index → toolCallId map — needed because input_json_delta events
  // carry an index (position in content blocks) not a toolCallId. Keyed by
  // chatId, then index → toolCallId. Cleared on done.
  const toolIndexMapRef = useRef<Record<string, Record<number, string>>>({});

  // Guard for auto-create at /chat
  const createPendingRef = useRef(false);

  // ---- Event handler (stable — no deps, uses refs) ----
  const onEvent = useCallback((event: ServerEvent) => {
    const eChatId = event.chatId;
    const actions = actionsRef.current;

    // Buffer message-content events during replay — apply all at once on replay-done
    if (eChatId && eChatId in replayBuffersRef.current && REPLAY_BUFFERED_EVENTS.has(event.type)) {
      replayBuffersRef.current[eChatId].push(event);
      return;
    }

    switch (event.type) {
      // -- Meta events --
      case "chat-list": {
        const raw = event.data as Array<{
          chatId: string;
          title: string;
          state: string;
          updatedAt: number;
          sessionUuid?: string;
          tokenCount?: number;
          contextWindow?: number;
          topics?: Record<string, string>;
        }>;
        const chatList: ChatMeta[] = raw.map((c) => ({
          id: c.chatId,
          title: c.title,
          state: c.state as ChatState,
          updatedAt: c.updatedAt,
          sessionUuid: c.sessionUuid || undefined,
          tokenCount: c.tokenCount,
          contextWindow: c.contextWindow,
          // topics intentionally omitted — chat-list is for sidebar data only.
          // Topic state arrives via chat-state events after replay.
        }));
        actions.setChats(chatList);
        // If the URL chatId isn't in the list, the stored chat no longer exists —
        // clear localStorage and fall through to the empty state.
        const currentChatId = chatIdRef.current;
        if (currentChatId && !chatList.find((c) => c.id === currentChatId)) {
          localStorage.removeItem("alpha.activeChatUrl");
          navigateRef.current("/chat", { replace: true });
        }
        break;
      }

      case "chat-created": {
        const data = event.data as { state: string };
        actions.addChat({
          id: eChatId!,
          title: "",
          state: data.state as ChatState,
          updatedAt: Date.now() / 1000,
        });
        // If we triggered this (auto-create at /chat), navigate to the new chat
        if (createPendingRef.current) {
          createPendingRef.current = false;
          actions.setActiveChatId(eChatId!);
          navigateRef.current(`/chat/${eChatId}`, { replace: true });
        }
        break;
      }

      case "chat-state": {
        if (!eChatId) break;
        if (useWorkshopStore.getState().isReplaying) break;
        const data = event.data as {
          state: string;
          title?: string;
          updatedAt?: number;
          sessionUuid?: string;
          tokenCount?: number;
          contextWindow?: number;
          topics?: Record<string, string>;
        };
        actions.updateChatState(
          eChatId,
          data.state as ChatState,
          data.title,
          data.updatedAt,
          data.sessionUuid || undefined,
          data.tokenCount,
          data.contextWindow,
          data.topics,
        );
        break;
      }

      // -- User message echo --
      // With --replay-user-messages, claude echoes ALL user messages back
      // on stdout: initial prompt, tool results, and interjections.
      // Backend broadcasts everything. Frontend discriminates.
      case "user-message": {
        if (!eChatId) break;
        const umData = event.data as {
          id?: string;
          content?: ContentPart[];
          source?: string;
          timestamp?: string;
          memories?: Array<{ id: number; content: string; score: number; created_at: string }>;
          orientation?: { capsules?: Array<{ key: string; title: string; content: string }> };
        };
        const umContent = umData.content || [];

        // Tool results = internal plumbing. Ignore for now.
        const isToolResult = Array.isArray(umContent) && umContent.some(
          (b: unknown) => typeof b === "object" && b !== null && (b as Record<string, unknown>).type === "tool_result"
        );
        if (isToolResult) break;

        // ID-based reconciliation: if the event carries a message ID that
        // matches an existing message, update it in place with all enrichment.
        if (umData.id) {
          const updated = actions.updateUserMessageById(eChatId, umData.id, {
            content: umData.content,
            timestamp: umData.timestamp,
            memories: umData.memories,
            orientation: umData.orientation,
          });
          if (updated) break;
        }

        // FIRST: check the pending echo queue — did WE send this?
        // This must run BEFORE stash reconciliation because the stash will
        // match interjection echoes and consume them, preventing the queue
        // from ever seeing them.
        const echoText = Array.isArray(umContent)
          ? umContent
              .filter((b: unknown) => typeof b === "object" && b !== null && (b as Record<string, unknown>).type === "text")
              .map((b: unknown) => ((b as Record<string, unknown>).text as string) || "")
              .join(" ")
              .trim()
          : "";

        const echoMatch = echoText ? useWorkshopStore.getState().matchPendingEcho(echoText) : null;

        if (echoMatch) {
          if (echoMatch.isInterjection) {
            // Interjection echo: new placeholder for the new response.
            const aid = actions.addRemoteAssistantPlaceholder(eChatId);
            assistantIdMapRef.current[eChatId] = aid;
          }
          // Either way, it's our echo — drop it.
          break;
        }

        // SECOND: text-stash reconciliation for events without an ID
        // (enrobe echoes, claude echoes we didn't queue).
        const reconciled = actions.reconcileUserMessage(eChatId, umContent);

        if (!reconciled) {
          const stash = useWorkshopStore.getState()._pendingSendText;
          if (stash === null && !umData.id) {
            break;
          }

          // Genuine new message: replay, remote, or other browser.
          actions.addRemoteUserMessage(eChatId, umContent, umData.id);
          const aid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = aid;
        }
        break;
      }

      // -- Message streaming events --
      // Text deltas go straight to the store. assistant-ui's useSmooth
      // handles display animation (character-by-character via rAF).
      // No more TypeOnBuffer, no more DOM manipulation, no more flash.
      case "text-delta": {
        if (!eChatId) break;
        let aid = assistantIdMapRef.current[eChatId];
        if (!aid) {
          aid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = aid;
        }
        actions.appendToAssistant(aid, event.data as string, eChatId);
        break;
      }

      case "thinking-delta": {
        if (!eChatId) break;
        let aid = assistantIdMapRef.current[eChatId];
        if (!aid) {
          aid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = aid;
        }
        // Thinking renders immediately — no type-on buffer.
        // It's collapsed behind a disclosure triangle anyway, and buffering it
        // causes interleaving with text deltas (both drain at 2 chars/frame,
        // creating dozens of tiny alternating thinking/text blocks).
        actions.appendThinking(aid, event.data as string, eChatId);
        break;
      }

      case "tool-use-start": {
        if (!eChatId) break;
        let tusAid = assistantIdMapRef.current[eChatId];
        if (!tusAid) {
          tusAid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = tusAid;
        }
        const tus = event.data as {
          toolCallId: string;
          toolName: string;
          index: number;
        };
        // Register index → toolCallId mapping for subsequent deltas
        if (!toolIndexMapRef.current[eChatId]) {
          toolIndexMapRef.current[eChatId] = {};
        }
        toolIndexMapRef.current[eChatId][tus.index] = tus.toolCallId;
        actions.addStreamingToolCall(tusAid, tus.toolCallId, tus.toolName, eChatId);
        break;
      }

      case "tool-use-delta": {
        if (!eChatId) break;
        const tud = event.data as { index: number; partialJson: string };
        const toolCallId = toolIndexMapRef.current[eChatId]?.[tud.index];
        if (!toolCallId) break;
        const tudAid = assistantIdMapRef.current[eChatId];
        if (!tudAid) break;
        actions.appendToolUseDelta(tudAid, toolCallId, tud.partialJson, eChatId);
        break;
      }

      case "tool-call": {
        if (!eChatId) break;
        let aid = assistantIdMapRef.current[eChatId];
        if (!aid) {
          aid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = aid;
        }
        const tc = event.data as {
          toolCallId: string;
          toolName: string;
          args: JSONObject;
          argsText: string;
        };
        actions.addToolCall(aid, {
          toolCallId: tc.toolCallId,
          toolName: tc.toolName,
          args: tc.args,
          argsText: tc.argsText,
        }, eChatId);
        break;
      }

      case "tool-result": {
        if (!eChatId) break;
        const aid = assistantIdMapRef.current[eChatId];
        if (!aid) break;
        const { toolCallId, result, isError } = event.data as {
          toolCallId: string;
          result: JSONValue;
          isError?: boolean;
        };
        actions.updateToolResult(aid, toolCallId, result, isError, eChatId);
        break;
      }

      case "context-update": {
        if (!eChatId) break;
        if (useWorkshopStore.getState().isReplaying) break;
        const ctx = event.data as { tokenCount: number; tokenLimit: number };
        actions.updateChatTokens(eChatId, ctx.tokenCount, ctx.tokenLimit);
        break;
      }

      case "approach-light": {
        if (!eChatId) break;
        const alData = event.data as { level: "yellow" | "red"; text: string };
        actions.addApproachLight(eChatId, alData.level, alData.text);
        break;
      }

      case "error": {
        console.error("[Alpha WS] Error:", event.data);
        if (eChatId) {
          const aid = assistantIdMapRef.current[eChatId];
          if (aid) actions.appendToAssistant(aid, `Error: ${event.data}`, eChatId);
        }
        break;
      }

      case "exception": {
        const ex = event.data as {
          exceptionType: string;
          metadata?: Record<string, unknown>;
        };
        console.warn("[Alpha WS] Exception:", ex.exceptionType, ex.metadata);

        if (ex.exceptionType === "context-loss-detected") {
          const meta = ex.metadata as {
            previousTokens: number;
            currentTokens: number;
            tokensLost: number;
          };
          const lostK = Math.round(meta.tokensLost / 1000);
          toast.error("Context truncated", {
            description: `~${lostK}K tokens of conversation lost on resume.`,
            duration: 10000,
          });
        } else if (ex.exceptionType === "api-error") {
          const meta = ex.metadata as { status: number; body?: string };
          const statusMessages: Record<number, string> = {
            429: "Rate limited",
            529: "API overloaded",
            500: "Internal server error",
            502: "Bad gateway",
            503: "Service unavailable",
          };
          const title = statusMessages[meta.status] || `API error ${meta.status}`;
          toast.error(title, {
            description: meta.body?.slice(0, 120) || `HTTP ${meta.status}`,
            duration: 8000,
          });
        }
        break;
      }

      case "done": {
        // DON'T clear assistantIdMapRef — `done` fires after each internal
        // subprocess turn (tool call cycle), not just at the end of the whole
        // response. Clearing it here caused tool calls to split into a new
        // assistant message. The ref gets cleared when chat-state transitions
        // to idle (handled by the assistant-message or chat-state events).
        break;
      }

      case "interrupted": {
        if (eChatId) {
          delete toolIndexMapRef.current[eChatId];
          assistantIdMapRef.current[eChatId] = null;
        }
        break;
      }

      case "chat-data": {
        // The "gimme the fucking chat" response. One payload, all messages + metadata.
        if (!eChatId) break;
        const chatData = event.data as {
          messages: Array<{ role: string; data: Record<string, unknown> }>;
          metadata: {
            state: string;
            title?: string;
            updatedAt?: number;
            sessionUuid?: string;
            tokenCount?: number;
            contextWindow?: number;
            topics?: Record<string, string>;
          };
        };

        // Convert backend messages to store Message objects
        const storeMessages: Message[] = chatData.messages.map((msg, idx) => {
          const d = msg.data as Record<string, unknown>;
          if (msg.role === "user") {
            // UserMessage wire format: { id, content, timestamp, memories, source, ... }
            const rawContent = (d.content as Array<{ type: string; text?: string; image?: string }>) || [];
            const parts: ContentPart[] = rawContent.map((b) => {
              if (b.type === "image" && b.image) return { type: "image" as const, image: b.image };
              return { type: "text" as const, text: b.text || "" };
            });
            return {
              id: (d.id as string) || `replay-u-${idx}`,
              role: "user" as const,
              content: parts,
              createdAt: new Date(),
              source: (d.source as MessageSource) || "human",
              timestamp: d.timestamp as string | undefined,
              memories: d.memories as RecalledMemory[] | undefined,
              capsules: d.orientation
                ? ((d.orientation as Record<string, unknown>).capsules as CapsuleData[] | undefined)
                : undefined,
            };
          } else {
            // AssistantMessage — two formats:
            // New (to_db): { id, parts: [{type, text?, thinking?, toolCallId?, ...}], ... }
            // Old (pre-AssistantMessage): { content: [{ type: "text", text: "..." }] }
            const rawParts = (d.parts as Array<{
              type: string;
              text?: string;
              thinking?: string;
              toolCallId?: string;
              toolName?: string;
              args?: JSONObject;
              argsText?: string;
              result?: JSONValue;
              isError?: boolean;
            }>) || (d.content as Array<{ type: string; text?: string }>) || [];

            const parts: ContentPart[] = [];
            for (const p of rawParts) {
              if (p.type === "text" && p.text) {
                parts.push({ type: "text", text: p.text });
              } else if (p.type === "thinking" && p.thinking) {
                parts.push({ type: "thinking", thinking: p.thinking });
              } else if (p.type === "tool-call" && p.toolCallId) {
                const toolPart: ToolCallPart = {
                  type: "tool-call",
                  toolCallId: p.toolCallId,
                  toolName: p.toolName || "",
                  args: (p.args || {}) as JSONObject,
                  argsText: p.argsText || "",
                };
                // Preserve tool results from the database (added by streaming.py)
                if (p.result !== undefined) toolPart.result = p.result;
                if (p.isError) toolPart.isError = p.isError;
                parts.push(toolPart);
              }
            }
            return {
              id: (d.id as string) || `replay-a-${idx}`,
              role: "assistant" as const,
              content: parts,
              createdAt: new Date(),
            };
          }
        });

        // Load messages into store
        actions.loadMessages(eChatId, storeMessages);

        // Ensure the chat exists in the store, then update with metadata.
        // chat-data often arrives BEFORE chat-list, so the chat may not
        // exist in state.chats yet. addChat creates it if missing.
        const md = chatData.metadata;
        actions.addChat({
          id: eChatId,
          title: md.title || "",
          state: md.state as ChatState,
          updatedAt: md.updatedAt || 0,
          sessionUuid: md.sessionUuid || undefined,
          tokenCount: md.tokenCount,
          contextWindow: md.contextWindow,
          topics: md.topics,
        });

        // Sync context meter from loaded metadata.
        // Force-update the global meter directly — can't rely on
        // updateChatTokens' activeChatId gate because setActiveChatId
        // runs in a useEffect that may not have fired yet.
        if (md.tokenCount !== undefined && md.contextWindow !== undefined) {
          actions.updateChatTokens(eChatId, md.tokenCount, md.contextWindow);
          // Also set the global meter directly in case activeChatId isn't set yet
          useWorkshopStore.setState({
            tokenCount: md.tokenCount,
            tokenLimit: md.contextWindow,
            contextPercent: md.contextWindow > 0
              ? Math.round((md.tokenCount / md.contextWindow) * 1000) / 10
              : 0,
          });
        }

        actions.setReplaying(false);
        break;
      }

      case "replay-done": {
        // Flush the replay buffer: build the full message list in pure JS
        // (no Zustand, no immer) and render once. Fast buggering.
        if (eChatId) {
          const buffer = replayBuffersRef.current[eChatId];
          if (buffer !== undefined) {
            delete replayBuffersRef.current[eChatId];
            const { messages: msgs, tokenCount, contextWindow } = processReplayBuffer(buffer);
            actions.loadMessages(eChatId, msgs);
            // Sync context meter from the last assistant-message's token info
            if (tokenCount !== undefined && contextWindow !== undefined) {
              actions.updateChatTokens(eChatId, tokenCount, contextWindow);
            }
          }
        }
        actions.setReplaying(false);
        break;
      }
    }
  }, []);

  // ---- WebSocket connection ----
  const setConnected = useWorkshopStore((s) => s.setConnected);
  const onConnectionChange = useCallback(
    (c: boolean) => setConnected(c),
    [setConnected]
  );
  const { send, connected } = useWebSocket({ onEvent, onConnectionChange });

  // Intercept replay/join-chat sends to initialize buffer + show loading state
  const wrappedSend = useCallback((msg: ClientMessage) => {
    if (msg.type === "replay" && msg.chatId) {
      replayBuffersRef.current[msg.chatId] = [];
      useWorkshopStore.getState().setReplaying(true);
    }
    if (msg.type === "join-chat" && msg.chatId) {
      useWorkshopStore.getState().setReplaying(true);
    }
    return send(msg);
  }, [send]);

  // ---- Hydrate sidebar + rejoin active chat on (re)connect ----
  // Uses a ref to track whether we've already joined this connection cycle,
  // preventing double-sends on initial load (ChatPage also sends join-chat).
  const lastConnectedRef = useRef(false);
  useEffect(() => {
    if (connected && !lastConnectedRef.current) {
      lastConnectedRef.current = true;
      send({ type: "list-chats" });
      const chatId = useWorkshopStore.getState().activeChatId;
      if (chatId) {
        send({ type: "join-chat", chatId });
      }
    } else if (!connected) {
      lastConnectedRef.current = false;
    }
  }, [connected, send]);

  // ---- Create-chat callback (used by sidebar New Chat button) ----
  const handleCreateChat = useCallback(() => {
    if (!createPendingRef.current) {
      createPendingRef.current = true;
      send({ type: "create-chat" });
    }
  }, [send]);

  // ---- Persist active chat URL to localStorage ----
  useEffect(() => {
    if (chatId) {
      localStorage.setItem("alpha.activeChatUrl", `/chat/${chatId}`);
      // Reset create guard when we've landed on a chat
      createPendingRef.current = false;
    }
  }, [chatId]);

  // ---- Sync URL chatId ↔ store activeChatId ----
  const setActiveChatId = useWorkshopStore((s) => s.setActiveChatId);
  useEffect(() => {
    setActiveChatId(chatId ?? null);
  }, [chatId, setActiveChatId]);

  return (
    <SidebarProvider>
      <AppSidebar onNewChat={handleCreateChat} />
      <main className="flex-1 flex flex-col min-w-0 h-svh">
        <ChatPage
          send={wrappedSend}
          connected={connected}
          assistantIdMapRef={assistantIdMapRef}
        />
      </main>
    </SidebarProvider>
  );
}

// ---------------------------------------------------------------------------
// RootRedirect — restore last active chat from localStorage, or empty state
// ---------------------------------------------------------------------------

function RootRedirect() {
  const stored = localStorage.getItem("alpha.activeChatUrl");
  return <Navigate to={stored || "/chat"} replace />;
}

// ---------------------------------------------------------------------------
// App — routing
// ---------------------------------------------------------------------------

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/chat" element={<Layout />} />
        <Route path="/chat/:chatId" element={<Layout />} />
        <Route path="/dev/context-meter" element={<DevContextMeter />} />
        <Route path="/dev/status-bar" element={<DevStatusBar />} />
        <Route path="/dev/topics" element={<DevTopics />} />
        <Route path="/dev/memory-store" element={<DevMemoryStore />} />
        <Route path="/dev/memory-cards" element={<DevMemoryCards />} />
        <Route path="/dev/tools" element={<DevTools />} />
      </Routes>
      <Toaster position="top-center" richColors />
    </BrowserRouter>
  );
}

export default App;
