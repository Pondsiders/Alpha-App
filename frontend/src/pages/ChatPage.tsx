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
import { ArrowUp, Square, Copy, Check, Plus } from "lucide-react";
import { ToolFallback } from "../components/ToolFallback";
import { MemoryNote } from "../components/tools/MemoryNote";
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
  useMessage,
  SimpleImageAttachmentAdapter,
} from "@assistant-ui/react";
import type {
  ThreadMessageLike,
  AppendMessage,
} from "@assistant-ui/react";
import { MarkdownText } from "../components/MarkdownText";
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
  onNewChat: () => void;
}

// -----------------------------------------------------------------------------
// Message Components
// -----------------------------------------------------------------------------

const UserMessage = () => {
  const message = useMessage();

  // Look up our source tag from the store (assistant-ui doesn't carry custom fields)
  const source = useWorkshopStore((s) =>
    s.messages.find((m) => m.id === message.id)?.source
  );

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
    <MessagePrimitive.Root data-testid="user-message" className="flex flex-col items-end mb-4 gap-2">
      {/* Image bubble(s) — separate from text */}
      {imageParts.map((img, i) => (
        <div
          key={i}
          className="rounded-2xl overflow-hidden border border-border max-w-[50%]"
        >
          <img
            src={img.image}
            alt="Attached image"
            className="w-full h-auto"
          />
        </div>
      ))}
      {/* Text bubble */}
      {textContent && (
        <div className="px-4 py-3 bg-user-bubble rounded-2xl max-w-[75%] text-text break-words whitespace-pre-wrap">
          {textContent}
        </div>
      )}
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

const AssistantMessage = () => {
  const message = useMessage();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const rawText = (message.content as Array<{ type: string; text?: string }>)
      .filter((p) => p.type === "text" && p.text)
      .map((p) => p.text!)
      .join("\n\n");
    await navigator.clipboard.writeText(rawText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <MessagePrimitive.Root data-testid="assistant-message" className="mb-6 pl-2 pr-12 group/assistant">
      <div className="text-text leading-relaxed flex flex-col gap-3">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            Reasoning: ThinkingBlock,
            tools: {
              by_name: {
                mcp__cortex__store: MemoryNote,
              },
              Fallback: ToolFallback,
            },
          }}
        />
      </div>
      <div className="mt-1 opacity-0 group-hover/assistant:opacity-100 transition-opacity">
        <button
          onClick={handleCopy}
          className="text-muted hover:text-text p-1 rounded bg-transparent border-none cursor-pointer transition-colors"
          aria-label={copied ? "Copied" : "Copy message"}
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
    </MessagePrimitive.Root>
  );
};

// -----------------------------------------------------------------------------
// Convert Message to ThreadMessageLike
// -----------------------------------------------------------------------------

const convertMessage = (message: Message): ThreadMessageLike => {
  const content = message.content.map((part) => {
    if (part.type === "thinking") {
      return { type: "reasoning" as const, text: part.thinking };
    }
    return part;
  });

  return {
    id: message.id,
    role: message.role,
    content,
    createdAt: message.createdAt,
  };
};

// -----------------------------------------------------------------------------
// Empty State — shown when no chat is active
// -----------------------------------------------------------------------------

function EmptyState({ onNewChat, connected }: { onNewChat: () => void; connected: boolean }) {
  return (
    <div className="h-full flex flex-col bg-background">
      <StatusBar />
      {/* Main content — centered New Chat button */}
      <div className="flex-1 flex items-center justify-center">
        <button
          onClick={onNewChat}
          disabled={!connected}
          className="flex items-center gap-2 px-6 py-3 rounded-2xl bg-primary text-[#1c1c1c] font-medium text-base
                     border-2 border-primary/30 disabled:opacity-50 disabled:cursor-default
                     cursor-pointer animate-breathe"
        >
          <Plus size={18} strokeWidth={2.5} />
          New Chat
        </button>
      </div>
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
  const activeChatId = useWorkshopStore((s) => s.activeChatId);
  const activeChat = useWorkshopStore((s) =>
    s.activeChatId ? s.chats[s.activeChatId] : null
  );
  const approachLights = useWorkshopStore((s) =>
    s.activeChatId ? s.approachLights[s.activeChatId] ?? EMPTY_LIGHTS : EMPTY_LIGHTS
  );

  // isRunning is derived from chat state — no more global boolean
  const isRunning = activeChat?.state === "busy" || activeChat?.state === "starting";

  const addUserMessage = useWorkshopStore((s) => s.addUserMessage);
  const addAssistantPlaceholder = useWorkshopStore((s) => s.addAssistantPlaceholder);
  const appendToAssistant = useWorkshopStore((s) => s.appendToAssistant);
  const setMessages = useWorkshopStore((s) => s.setMessages);

  // Keep refs for stable access in callbacks
  const activeChatIdRef = useRef(activeChatId);
  activeChatIdRef.current = activeChatId;
  const sendRef = useRef(send);
  sendRef.current = send;

  // ---- Load messages when active chat changes ----
  useEffect(() => {
    if (!activeChatId) return;

    // If we already have messages (restored from cache by setActiveChatId), skip
    if (useWorkshopStore.getState().messages.length > 0) return;

    // Request replay over WebSocket — events arrive through the same
    // handlers as live streaming (user-message, text-delta, tool-call, done)
    send({ type: "replay", chatId: activeChatId });
  }, [activeChatId, send]);

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
      addUserMessage(text, storeImages.length > 0 ? storeImages : undefined);

      // Check if this is an interjection (chat is busy — assistant still streaming)
      const activeChat = useWorkshopStore.getState().chats[chatId];
      const isBusy = activeChat?.state === "busy";

      if (!isBusy) {
        // Normal turn — create placeholder for assistant response
        const assistantId = addAssistantPlaceholder();
        assistantIdMapRef.current[chatId] = assistantId;
      }
      // Interjection: no new placeholder — the existing assistant message keeps accumulating

      // Send via WebSocket with chatId
      const sent = sendRef.current({
        type: "send",
        chatId,
        content:
          backendContent.length === 1 && backendContent[0].type === "text"
            ? (backendContent[0] as { text: string }).text // Simple string for text-only
            : backendContent,
      });

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

  const adapters = useMemo(
    () => ({ attachments: new SimpleImageAttachmentAdapter() }),
    []
  );

  const runtime = useExternalStoreRuntime({
    messages,
    setMessages,
    // Always false — we manage duplex ourselves. Setting true would block
    // ComposerPrimitive.Send, but we need sends to work mid-stream for
    // interjections. Our own `isRunning` (derived from chat state) controls
    // the stop button and interjection logic in onNew.
    isRunning: false,
    onNew,
    onCancel,
    convertMessage,
    adapters,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="h-full flex flex-col bg-background">
        <StatusBar />
        <ThreadPrimitive.Root className="flex-1 flex flex-col overflow-hidden chat-font">
          <ThreadPrimitive.Viewport className="flex-1 flex flex-col overflow-y-scroll p-6">
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
                }}
              />

              {/* Approach lights — stage directions, not bubbles */}
              {approachLights.map((light, i) => (
                <ApproachLight key={`${light.level}-${i}`} {...light} />
              ))}

            </div>

            <div aria-hidden="true" className="h-4" />
          </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>

        <footer className="px-6 py-4 bg-background chat-font">
          <div className="max-w-3xl mx-auto">
            <ComposerPrimitive.Root className="flex flex-col gap-3 p-4 bg-composer rounded-2xl shadow-[0_0.25rem_1.25rem_rgba(0,0,0,0.4),0_0_0_0.5px_rgba(108,106,96,0.15)]">
              {/* Attachment previews */}
              <ComposerAttachments />

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
    return <EmptyState onNewChat={props.onNewChat} connected={props.connected} />;
  }

  return <ThreadView {...props} />;
}
