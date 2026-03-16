import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useRef } from "react";
import ChatPage from "./pages/ChatPage";
import DevContextMeter from "./pages/DevContextMeter";
import DevStatusBar from "./pages/DevStatusBar";
import { SidebarProvider } from "@/components/ui/sidebar";
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
        }>;
        const chatList: ChatMeta[] = raw.map((c) => ({
          id: c.chatId,
          title: c.title,
          state: c.state as ChatState,
          updatedAt: c.updatedAt,
          sessionUuid: c.sessionUuid || undefined,
          tokenCount: c.tokenCount,
          contextWindow: c.contextWindow,
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
        };
        actions.updateChatState(
          eChatId,
          data.state as ChatState,
          data.title,
          data.updatedAt,
          data.sessionUuid || undefined,
          data.tokenCount,
          data.contextWindow,
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

        // Fallback: text-stash reconciliation for events without an ID
        // (e.g., claude echoes from --replay-user-messages).
        const reconciled = actions.reconcileUserMessage(eChatId, umContent);

        if (!reconciled) {
          // Check if this is a stale echo — if the stash is already null
          // (cleared by ID-based reconciliation), this echo is redundant.
          // Only create a new message for genuine remote/replay messages.
          const stash = useWorkshopStore.getState()._pendingSendText;
          if (stash === null && !umData.id) {
            // Stash already consumed — this is a claude echo arriving late.
            // Drop it silently to avoid duplicating the message.
            break;
          }

          // Genuine new message: interjection, replay, or remote
          actions.addRemoteUserMessage(eChatId, umContent);
          const aid = actions.addRemoteAssistantPlaceholder(eChatId);
          assistantIdMapRef.current[eChatId] = aid;
        }
        break;
      }

      // -- Message streaming events --
      // All streaming actions pass eChatId so the store can accumulate
      // deltas in the messageCache when the target chat is in the background.
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
        actions.appendThinking(aid, event.data as string, eChatId);
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

      case "done": {
        if (eChatId) assistantIdMapRef.current[eChatId] = null;
        break;
      }

      case "interrupted": {
        if (eChatId) assistantIdMapRef.current[eChatId] = null;
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

  // Intercept replay sends to initialize buffer + show loading state
  const wrappedSend = useCallback((msg: ClientMessage) => {
    if (msg.type === "replay" && msg.chatId) {
      replayBuffersRef.current[msg.chatId] = [];
      useWorkshopStore.getState().setReplaying(true);
    }
    return send(msg);
  }, [send]);

  // ---- Hydrate sidebar on connect ----
  useEffect(() => {
    if (connected) {
      send({ type: "list-chats" });
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
      </Routes>
    </BrowserRouter>
  );
}

export default App;
