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
} from "./store";

// ---------------------------------------------------------------------------
// Replay buffering — build full Message[] from buffered events in one pass
// ---------------------------------------------------------------------------

const REPLAY_BUFFERED_EVENTS = new Set([
  "user-message", "text-delta", "thinking-delta", "tool-call", "tool-result", "done",
]);

function processReplayBuffer(events: ServerEvent[]): Message[] {
  const messages: Message[] = [];
  let currentAssistant: Message | null = null;

  for (const event of events) {
    switch (event.type) {
      case "user-message": {
        const data = event.data as { content: ContentPart[] };
        messages.push({
          id: generateId(),
          role: "user",
          content: data.content || [],
          createdAt: new Date(),
        });
        currentAssistant = null;
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
      case "done": {
        currentAssistant = null;
        break;
      }
    }
  }

  return messages;
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
        const umData = event.data as { id?: string; content?: ContentPart[]; source?: string };
        const umContent = umData.content || [];

        // Tool results = internal plumbing. Ignore for now.
        const isToolResult = Array.isArray(umContent) && umContent.some(
          (b: unknown) => typeof b === "object" && b !== null && (b as Record<string, unknown>).type === "tool_result"
        );
        if (isToolResult) break;

        // ID-based reconciliation: if the event carries a message ID that
        // matches an existing message, update it in place. This handles
        // progressive enrichment (timestamp → memories → capsules).
        if (umData.id) {
          const updated = actions.updateUserMessageById(eChatId, umData.id, umContent);
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
        // Flush the replay buffer: build the full message list and render once.
        if (!eChatId) break;
        const buffer = replayBuffersRef.current[eChatId];
        if (buffer !== undefined) {
          delete replayBuffersRef.current[eChatId];
          const msgs = processReplayBuffer(buffer);
          actions.loadMessages(eChatId, msgs);
        }
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

  // Intercept replay sends to initialize the buffer for that chatId
  const wrappedSend = useCallback((msg: ClientMessage) => {
    if (msg.type === "replay" && msg.chatId) {
      replayBuffersRef.current[msg.chatId] = [];
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
