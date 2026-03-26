/**
 * ChatPage — The main conversation view for Alpha.
 *
 * Phase 2: Multi-chat aware. Reads activeChatId from store, sends chatId
 * with all messages. WebSocket owned by Layout (App.tsx), send passed as prop.
 *
 * Supports text and image attachments (paste, drag-drop, or file picker).
 * Uses Zustand for state management and useExternalStoreRuntime to bridge
 * to assistant-ui primitives.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { ArrowUp, Square, Copy } from "lucide-react";
import { ToolFallback } from "../components/ToolFallback";
import { MemoryTray } from "../components/MemoryTray";
import { MemoryCard } from "../components/MemoryCard";
import { MemoryNote } from "../components/tools/MemoryNote";
import { MemoryStore } from "../components/tools/MemoryStore";
import { BashResult } from "../components/tools/BashResult";
import { ReadResult } from "../components/tools/ReadResult";
import { EditResult } from "../components/tools/EditResult";
import { WriteResult } from "../components/tools/WriteResult";
import { GrepResult } from "../components/tools/GrepResult";
import { TodoResult } from "../components/tools/TodoResult";
import { AgentResult } from "../components/tools/AgentResult";
import { animated } from "../components/AnimatedTool";
import { ToolGroup } from "../components/ToolGroup";
import { SystemMessage as SystemMessageComponent } from "../components/SystemMessage";
import { TopicBar } from "../components/TopicBar";
import {
  ComposerAttachments,
  ComposerAddAttachment,
} from "../components/Attachment";
import {
  useExternalStoreRuntime,
  AssistantRuntimeProvider,
  ThreadPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ActionBarPrimitive,
  useMessage,
  SimpleImageAttachmentAdapter,
} from "@assistant-ui/react";
import type {
  ThreadMessageLike,
  AppendMessage,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useWorkshopStore,
  type Message,
} from "../store";
import { StatusBar } from "@/components/StatusBar";
import { ApproachLight } from "@/components/ApproachLight";
import type { ApproachLight as ApproachLightType } from "@/store";
import type { ClientMessage } from "@/lib/useWebSocket";

// Stable empty array — avoids infinite re-renders with Zustand's Object.is check.
// `?? []` creates a new reference every call; Zustand sees new !== old and re-renders.
const EMPTY_LIGHTS: ApproachLightType[] = [];

// -----------------------------------------------------------------------------
// Props
// -----------------------------------------------------------------------------

interface ChatPageProps {
  send: (msg: ClientMessage) => boolean;
  connected: boolean;
  assistantIdMapRef: React.MutableRefObject<Record<string, string | null>>;
}

// -----------------------------------------------------------------------------
// Message Components
// -----------------------------------------------------------------------------

const UserMessage = () => {
  const message = useMessage();

  // Look up enrichment fields from the store (assistant-ui doesn't carry custom fields)
  const storeMsg = useWorkshopStore((s) =>
    s.messages.find((m) => m.id === message.id)
  );
  const source = storeMsg?.source;
  const memories = storeMsg?.memories;

  // Separate image and text parts for individual bubbles
  const imageParts = (message.content as Array<{ type: string; image?: string }>)
    .filter((p) => p.type === "image" && !!p.image) as Array<{ type: "image"; image: string }>;
  const textContent = (message.content as Array<{ type: string; text?: string }>)
    .filter((p) => p.type === "text" && p.text?.trim())
    .map((p) => p.text!)
    .join("\n");

  // Non-human messages render as stage directions, not bubbles
  if (source && source !== "human") {
    return (
      <MessagePrimitive.Root data-testid="system-message" className="my-3 flex items-center gap-3">
        <div className="flex-1 h-px bg-muted/20" />
        <span className="text-xs italic text-muted/60 whitespace-nowrap select-none">
          {textContent || "…"}
        </span>
        <div className="flex-1 h-px bg-muted/20" />
      </MessagePrimitive.Root>
    );
  }

  return (
    <MessagePrimitive.Root data-testid="user-message" className="flex flex-col items-end mb-4">
      {/* Constraining wrapper — all user message parts share the same max width */}
      <div className="flex flex-col items-end max-w-[75%]">
        {/* Rich bubble — one unified container for text + attachments + memories */}
        <div className="bg-user-bubble rounded-2xl text-text max-w-full">
          {/* Text */}
          {textContent && (
            <div className="px-4 py-3 break-words overflow-x-auto markdown-text">
              <Markdown remarkPlugins={[remarkGfm]}>{textContent}</Markdown>
            </div>
          )}
          {/* Attachment shelf — horizontal scroll strip inside the bubble */}
          {imageParts.length > 0 && (
            <div className="flex gap-2 overflow-x-auto px-4 pb-3" style={{ direction: "rtl" }}>
              {[...imageParts].reverse().map((img, i) => (
                <div key={i} className="shrink-0 rounded-lg overflow-hidden border border-border/50 max-w-48" style={{ direction: "ltr" }}>
                  <img src={img.image} alt={`Attachment ${i + 1}`} className="w-full h-auto max-h-36 object-cover" />
                </div>
              ))}
            </div>
          )}
          {/* Memory shelf — horizontal cards, RTL for right-alignment, scroll-to-left on mount */}
          {memories && memories.length > 0 && (
            <div
              className="flex gap-2 overflow-x-auto px-4 pb-4 pt-2"
              style={{ direction: "rtl" }}
              ref={(el) => { if (el) el.scrollLeft = -el.scrollWidth; }}
            >
              {[...memories].reverse().map((m) => (
                <div key={m.id} style={{ direction: "ltr" }} className="shrink-0">
                  <MemoryCard memory={m} flat />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};

const ThinkingBlock = ({ text, status }: { text: string; status: unknown }) => {
  const isStreaming = (status as { type?: string })?.type === "running";

  return (
    <details data-testid="thinking-block" className="group">
      <summary className="cursor-pointer text-muted italic select-none list-none flex items-center gap-2 text-[13px]">
        <span className="text-muted/60 group-open:rotate-90 transition-transform inline-block">{"\u25B6"}</span>
        {isStreaming ? "Thinking..." : "Thought"}
      </summary>
      <div className="mt-2 pl-4 border-l-2 border-muted/20 text-muted italic leading-relaxed whitespace-pre-wrap text-[13px]">
        {text}
      </div>
    </details>
  );
};

// Stable component map — MUST be outside the component or in useMemo.
// An inline object literal in JSX is recreated every render, which breaks
// Parts' internal memoization and forces child unmount/remount.
// Native assistant-ui markdown text with smooth streaming + GFM
const NativeMarkdownText = () => (
  <MarkdownTextPrimitive
    remarkPlugins={[remarkGfm]}
    className="markdown-text"
    smooth
  />
);

const ASSISTANT_PARTS_COMPONENTS = {
  Text: NativeMarkdownText,
  Reasoning: ThinkingBlock,
  ToolGroup,
  tools: {
    by_name: {
      mcp__cortex__store: MemoryNote,
      mcp__alpha__store: MemoryStore,
      Bash: BashResult,
      Read: ReadResult,
      Edit: EditResult,
      Write: WriteResult,
      Grep: GrepResult,
      Glob: GrepResult,
      TodoWrite: TodoResult,
      Agent: AgentResult,
    },
    Fallback: ToolFallback,
  },
};

// -- Streaming cursor — thread-level "I'm working" indicator ----------------

const StreamingCursor = () => {
  const isBusy = useWorkshopStore((s) => {
    const chat = s.activeChatId ? s.chats[s.activeChatId] : null;
    return chat?.state === "busy" || chat?.state === "starting";
  });

  return (
    <div
      className={`flex items-center h-5 pl-2 transition-opacity duration-200 ${
        isBusy ? "opacity-100" : "opacity-0"
      }`}
    >
      <span
        className={`w-2 h-2 rounded-full ${isBusy ? "animate-pulse-dot" : ""}`}
        style={{ backgroundColor: "var(--theme-primary)" }}
      />
    </div>
  );
};

// -- Assistant message --------------------------------------------------------

const AssistantMessage = () => {

  return (
    <MessagePrimitive.Root data-testid="assistant-message" className="relative pl-2 pr-12 mb-8 group/assistant">
      <div className="text-text leading-relaxed flex flex-col gap-5">
        <MessagePrimitive.Parts components={ASSISTANT_PARTS_COMPONENTS} />
      </div>
      {/* Copy button — inside the message root so hover zone is contiguous. */}
      <div className="mt-2 opacity-0 group-hover/assistant:opacity-100 transition-opacity duration-150">
        <ActionBarPrimitive.Root>
          <ActionBarPrimitive.Copy asChild>
            <button
              className="text-muted/40 hover:text-text p-1 rounded bg-transparent border-none cursor-pointer transition-colors"
              aria-label="Copy message"
            >
              <Copy size={14} />
            </button>
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
};

// -----------------------------------------------------------------------------
// Convert Message to ThreadMessageLike
// -----------------------------------------------------------------------------

// Part types that assistant-ui knows how to render per role.
// Anything else gets filtered with a console warning — scream but don't die.
const KNOWN_USER_PARTS = new Set(["text", "image", "file", "audio"]);
const KNOWN_ASSISTANT_PARTS = new Set(["text", "reasoning", "tool-call", "file", "audio"]);

const convertMessage = (message: Message): ThreadMessageLike => {
  // System messages — convert to text-only (ThreadSystemMessage constraint).
  if (message.role === "system") {
    const text = message.content
      .map((part) => {
        if (part.type === "system-notification") return part.text;
        if (part.type === "text") return part.text;
        return "";
      })
      .filter(Boolean)
      .join("\n");
    return {
      id: message.id,
      role: "system" as const,
      content: [{ type: "text" as const, text }],
      createdAt: message.createdAt,
    };
  }

  const knownParts = message.role === "user" ? KNOWN_USER_PARTS : KNOWN_ASSISTANT_PARTS;

  const content = message.content
    .map((part) => {
      if (part.type === "thinking") {
        return { type: "reasoning" as const, text: part.thinking };
      }
      return part;
    })
    .filter((part) => {
      if (knownParts.has(part.type)) return true;
      console.warn(
        `[convertMessage] Filtering unsupported ${message.role} part type: ${part.type}`,
        part,
      );
      return false;
    });

  return {
    id: message.id,
    role: message.role,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    content: content as any,
    createdAt: message.createdAt,
  };
};

// -----------------------------------------------------------------------------
// Empty State — shown when no chat is active
// -----------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="h-full flex flex-col bg-background">
      <StatusBar />
      <div className="flex-1" />
      {/* Grayed-out composer (visual affordance only — not interactive) */}
      <footer className="px-6 py-4 bg-background chat-font">
        <div className="max-w-3xl mx-auto opacity-30 pointer-events-none select-none">
          <div className="flex flex-col gap-3 p-4 bg-composer rounded-2xl shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.15)]">
            <div className="w-full py-2 text-muted/60 italic text-[18px]">Message Alpha...</div>
            <div className="flex justify-end items-center gap-3">
              {/* Attachment button placeholder */}
              <div className="w-9 h-9 rounded-lg border border-border" />
              {/* Send button placeholder */}
              <div className="w-9 h-9 rounded-lg bg-primary" />
            </div>
          </div>
          <p className="text-right text-muted mt-2 text-[11px]">
            Alpha remembers everything. Except when she doesn't. 🦆
          </p>
        </div>
      </footer>
    </div>
  );
}

// -----------------------------------------------------------------------------
// Thread View
// -----------------------------------------------------------------------------

function ThreadView({ send, connected, assistantIdMapRef }: ChatPageProps) {
  const messages = useWorkshopStore((s) => s.messages);
  const isReplaying = useWorkshopStore((s) => s.isReplaying);
  const activeChatId = useWorkshopStore((s) => s.activeChatId);
  const activeChat = useWorkshopStore((s) =>
    s.activeChatId ? s.chats[s.activeChatId] : null
  );
  const approachLights = useWorkshopStore((s) =>
    s.activeChatId ? s.approachLights[s.activeChatId] ?? EMPTY_LIGHTS : EMPTY_LIGHTS
  );

  // isRunning is derived from chat state — no more global boolean
  const isRunning = activeChat?.state === "busy" || activeChat?.state === "starting";

  // ---- Topics ----
  // Topic states come from chat-state events as { "alpha-app": "on", "intake": "off" }.
  // The backend's TopicRegistry scans the filesystem; Chat tracks injection.
  const backendTopics = activeChat?.topics ?? {};
  const [armedTopics, setArmedTopics] = useState<Set<string>>(new Set());

  const topicPills = Object.entries(backendTopics).map(([name, backendState]) => ({
    name,
    state: backendState === "on" ? "on" as const
      : armedTopics.has(name) ? "armed" as const
      : "off" as const,
  }));

  const handleTopicToggle = useCallback((name: string) => {
    setArmedTopics((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  }, []);

  const addUserMessage = useWorkshopStore((s) => s.addUserMessage);
  const addAssistantPlaceholder = useWorkshopStore((s) => s.addAssistantPlaceholder);
  const appendToAssistant = useWorkshopStore((s) => s.appendToAssistant);
  const setMessages = useWorkshopStore((s) => s.setMessages);

  // Keep refs for stable access in callbacks
  const activeChatIdRef = useRef(activeChatId);
  activeChatIdRef.current = activeChatId;
  const sendRef = useRef(send);
  sendRef.current = send;
  const armedTopicsRef = useRef(armedTopics);
  armedTopicsRef.current = armedTopics;

  // ---- Load messages when active chat changes or connection is established ----
  useEffect(() => {
    if (!activeChatId) return;
    if (!connected) return; // Wait until the WebSocket is open

    // If we already have messages (restored from cache by setActiveChatId), skip
    if (useWorkshopStore.getState().messages.length > 0) return;

    // Request the whole chat in one shot — join-chat returns all messages
    // + metadata (including topics) as a single payload.
    send({ type: "join-chat", chatId: activeChatId });
  }, [activeChatId, connected, send]);

  // ---- Send handler ----
  const onNew = useCallback(
    async (appendMessage: AppendMessage) => {
      const chatId = activeChatIdRef.current;
      if (!chatId) return;

      const textParts = appendMessage.content.filter(
        (p): p is { type: "text"; text: string } => p.type === "text"
      );
      const text = textParts.map((p) => p.text).join("\n");

      // Extract image attachments (paste, drag-drop, file picker)
      const rawAttachments = (appendMessage as Record<string, unknown>).attachments as
        | Array<{ type: string; content?: Array<{ type: string; image?: string }> }>
        | undefined;

      const storeImages: Array<{ type: "image"; image: string }> = [];
      const backendContent: Record<string, unknown>[] = [];

      // Text block
      if (text.trim()) {
        backendContent.push({ type: "text", text });
      }

      // Image blocks from attachments
      if (rawAttachments) {
        for (const att of rawAttachments) {
          if (att.type !== "image" || !att.content) continue;
          for (const part of att.content) {
            if (part.type === "image" && part.image?.startsWith("data:")) {
              storeImages.push({ type: "image", image: part.image });
              const [header, data] = part.image.split(",");
              const mediaType = header.split(":")[1].split(";")[0];
              backendContent.push({
                type: "image",
                source: { type: "base64", media_type: mediaType, data },
              });
            }
          }
        }
      }

      // Nothing to send
      if (!text.trim() && storeImages.length === 0) return;

      console.log("[Alpha] Sending to chat %s, blocks: %d", chatId, backendContent.length);

      // Add user message to store (optimistic) — always, even for interjections
      const userMsgId = addUserMessage(text, storeImages.length > 0 ? storeImages : undefined);

      // Check if this is an interjection (chat is busy — assistant still streaming)
      const activeChat = useWorkshopStore.getState().chats[chatId];
      const isBusy = activeChat?.state === "busy";

      // Push to pending echo queue so we can match the claude echo later.
      // Normal sends: echo gets dropped (already rendered optimistically).
      // Interjections: echo triggers a new assistant placeholder (turn boundary).
      if (text.trim()) {
        useWorkshopStore.getState().pushPendingEcho(text.trim(), isBusy);
      }

      if (!isBusy) {
        // Normal turn — create placeholder for assistant response
        const assistantId = addAssistantPlaceholder();
        assistantIdMapRef.current[chatId] = assistantId;
      }
      // Interjection: no new placeholder here — created when the echo arrives

      // Send via WebSocket with chatId + messageId + armed topics
      const topicsToSend = Array.from(armedTopicsRef.current);
      const sent = sendRef.current({
        type: "send",
        chatId,
        messageId: userMsgId,
        content:
          backendContent.length === 1 && backendContent[0].type === "text"
            ? (backendContent[0] as { text: string }).text // Simple string for text-only
            : backendContent,
        ...(topicsToSend.length > 0 && { topics: topicsToSend }),
      });
      // Don't clear armed state here — let it stay amber until
      // chat-state arrives with injectedTopics, then pill goes green.
      // The pill builder prefers "on" over "armed", so the transition
      // is smooth: armed (amber) → on (green) with no flash to off.

      if (!sent) {
        const existingId = assistantIdMapRef.current[chatId];
        if (existingId) {
          appendToAssistant(existingId, "\n\nError: Not connected to server");
        }
        if (!isBusy) {
          assistantIdMapRef.current[chatId] = null;
        }
      }
    },
    [addUserMessage, addAssistantPlaceholder, appendToAssistant, assistantIdMapRef]
  );

  const onCancel = useCallback(async () => {
    const chatId = activeChatIdRef.current;
    if (chatId) {
      sendRef.current({ type: "interrupt", chatId });
      assistantIdMapRef.current[chatId] = null;
    }
  }, [assistantIdMapRef]);

  // Buzz — the nonverbal hello. Sends a signal (not a message) to the backend.
  // The backend constructs a narration message that Alpha sees but the human doesn't.
  const onBuzz = useCallback(() => {
    const chatId = activeChatIdRef.current;
    if (!chatId) return;

    // Create assistant placeholder — the response needs somewhere to land
    const assistantId = addAssistantPlaceholder();
    assistantIdMapRef.current[chatId] = assistantId;

    // Send the buzz — no content, no user message, just a knock on the door
    sendRef.current({ type: "buzz", chatId });
  }, [addAssistantPlaceholder, assistantIdMapRef]);

  // Custom adapter that gives each pasted image a unique ID.
  // SimpleImageAttachmentAdapter uses file.name as the ID, but clipboard
  // pastes always produce the same filename (e.g. "image.png"), causing
  // the second paste to overwrite the first. Unique IDs fix #41.
  const adapters = useMemo(() => {
    const base = new SimpleImageAttachmentAdapter();
    let counter = 0;
    return {
      attachments: {
        accept: base.accept,
        async add(state: { file: File }) {
          const result = await base.add(state);
          return { ...result, id: `paste-${Date.now()}-${counter++}` };
        },
        send: base.send.bind(base),
        remove: base.remove.bind(base),
      },
    };
  }, []);

  const runtime = useExternalStoreRuntime({
    messages,
    setMessages,
    // Modal: when running, assistant-ui disables Send and shows Stop.
    // No interjections — messages queue in Claude Code.
    isRunning,
    onNew,
    onCancel,
    convertMessage,
    adapters,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="h-full flex flex-col bg-background">
        <StatusBar />
        {isReplaying ? (
          /* During replay, don't render messages — the store mutates silently.
             One render happens when isReplaying flips to false. */
          <div className="flex-1 flex items-center justify-center chat-font">
            <p className="text-primary/20 text-2xl font-light tracking-wide select-none animate-pulse">
              Alpha
            </p>
          </div>
        ) : (
          <ThreadPrimitive.Root className="flex-1 flex flex-col overflow-hidden chat-font relative">
            <ThreadPrimitive.Viewport
              className="flex-1 flex flex-col overflow-y-scroll overflow-x-hidden p-6"
              autoScroll
              scrollToBottomOnInitialize
              scrollToBottomOnThreadSwitch
            >
              <div className="max-w-3xl mx-auto w-full flex-1">
                {messages.length === 0 && !isRunning && (
                  <div className="flex-1 flex items-center justify-center h-full">
                    <p className="text-primary/40 text-2xl font-light tracking-wide select-none">
                      {connected ? "Alpha" : "Connecting..."}
                    </p>
                  </div>
                )}

                <ThreadPrimitive.Messages
                  components={{
                    UserMessage,
                    AssistantMessage,
                    SystemMessage: SystemMessageComponent,
                  }}
                />

                {/* Cursor — thread-level "I'm working" indicator.
                    Visible when the chat is busy, sits after all messages. */}
                <StreamingCursor />

                {/* Approach lights — stage directions, not bubbles */}
                {approachLights.map((light, i) => (
                  <ApproachLight key={`${light.level}-${i}`} {...light} />
                ))}


              </div>

              <div aria-hidden="true" className="h-4" />
            </ThreadPrimitive.Viewport>

            {/* Scroll-to-bottom — hidden when at bottom (disabled), visible when scrolled up */}
            <ThreadPrimitive.ScrollToBottom
              className="absolute bottom-2 left-1/2 -translate-x-1/2 z-10
                         rounded-full bg-surface/80 backdrop-blur border border-border/50
                         px-3 py-1.5 text-xs text-muted hover:text-foreground
                         shadow-md transition-all cursor-pointer
                         disabled:hidden"
            >
              ↓ Scroll to bottom
            </ThreadPrimitive.ScrollToBottom>
          </ThreadPrimitive.Root>
        )}

        <footer className="px-6 py-4 bg-background chat-font">
          <div className="max-w-3xl mx-auto">
            <ComposerPrimitive.Root className="flex flex-col gap-3 p-4 bg-composer rounded-2xl shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.15)]">
              {/* Attachment previews */}
              <ComposerAttachments />

              {/* Topic pills — above the input, always visible */}
              <TopicBar topics={topicPills} onToggle={handleTopicToggle} />

              <ComposerPrimitive.Input
                placeholder="Message Alpha..."
                className="w-full py-2 bg-transparent border-none text-text outline-none resize-none"
              />
              <div className="flex justify-end items-center gap-3">
                {/* Attach image button */}
                <ComposerAddAttachment />

                {/* Buzz — the nonverbal hello. Only on fresh chats. */}
                {messages.length === 0 && connected && !isRunning && activeChatId && (
                  <button
                    data-testid="buzz-button"
                    onClick={onBuzz}
                    className="w-9 h-9 flex items-center justify-center bg-transparent border border-border rounded-lg cursor-pointer hover:border-primary/60 transition-colors text-base"
                    title="Say hi"
                  >
                    🦆
                  </button>
                )}

                {/* Send — always visible. Works as interjection when busy (duplex). */}
                <ComposerPrimitive.Send
                  data-testid="send-button"
                  className="w-9 h-9 flex items-center justify-center bg-primary border-none rounded-lg text-white cursor-pointer disabled:opacity-40 disabled:cursor-default"
                >
                  <ArrowUp size={20} strokeWidth={2.5} />
                </ComposerPrimitive.Send>

                {/* Stop — visible while streaming. Regular button because we told
                    assistant-ui isRunning=false for duplex send support. */}
                {isRunning && (
                  <button
                    data-testid="stop-button"
                    onClick={onCancel}
                    className="w-9 h-9 flex items-center justify-center bg-primary border-none rounded-lg text-white cursor-pointer"
                  >
                    <Square size={16} fill="white" />
                  </button>
                )}
              </div>
            </ComposerPrimitive.Root>
            <p className="text-right text-muted mt-2 text-[11px]">
              Alpha remembers everything. Except when she doesn't. 🦆
            </p>
          </div>
        </footer>
      </div>
    </AssistantRuntimeProvider>
  );
}

// -----------------------------------------------------------------------------
// ChatPage (route handler)
// -----------------------------------------------------------------------------

export default function ChatPage(props: ChatPageProps) {
  const activeChatId = useWorkshopStore((s) => s.activeChatId);

  if (!activeChatId) {
    return <EmptyState />;
  }

  return <ThreadView {...props} />;
}
