/**
 * ChatPage — The main conversation view for Alpha.
 *
 * Supports text and image attachments (paste, drag-drop, or file picker).
 * Uses WebSocket for bidirectional communication with the backend.
 * Uses Zustand for state management and useExternalStoreRuntime to bridge
 * to assistant-ui primitives.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowUp, Square, Copy, Check } from "lucide-react";
import { ToolFallback } from "../components/ToolFallback";
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
  AssistantIf,
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
  type JSONValue,
  type ToolCallPart,
} from "../store";
import { StatusBar } from "@/components/StatusBar";
import { useWebSocket, type ServerEvent } from "@/lib/useWebSocket";

// -----------------------------------------------------------------------------
// Message Components
// -----------------------------------------------------------------------------

const UserMessage = () => {
  const message = useMessage();

  // Separate image and text parts for individual bubbles
  const imageParts = (message.content as Array<{ type: string; image?: string }>)
    .filter((p) => p.type === "image" && !!p.image) as Array<{ type: "image"; image: string }>;
  const textContent = (message.content as Array<{ type: string; text?: string }>)
    .filter((p) => p.type === "text" && p.text?.trim())
    .map((p) => p.text!)
    .join("\n");

  return (
    <MessagePrimitive.Root className="flex flex-col items-end mb-4 gap-2">
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
    <details className="mb-3 group">
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
    <MessagePrimitive.Root className="mb-6 pl-2 pr-12 group/assistant">
      <div className="text-text leading-relaxed">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            Reasoning: ThinkingBlock,
            tools: {
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
// Thread View
// -----------------------------------------------------------------------------

interface ThreadViewProps {
  onSessionCreated?: () => void;
}

function ThreadView({ onSessionCreated }: ThreadViewProps) {
  const messages = useWorkshopStore((s) => s.messages);
  const isRunning = useWorkshopStore((s) => s.isRunning);
  const sessionId = useWorkshopStore((s) => s.sessionId);

  const addUserMessage = useWorkshopStore((s) => s.addUserMessage);
  const addAssistantPlaceholder = useWorkshopStore((s) => s.addAssistantPlaceholder);
  const appendToAssistant = useWorkshopStore((s) => s.appendToAssistant);
  const appendThinking = useWorkshopStore((s) => s.appendThinking);
  const addToolCall = useWorkshopStore((s) => s.addToolCall);
  const updateToolResult = useWorkshopStore((s) => s.updateToolResult);
  const setMessages = useWorkshopStore((s) => s.setMessages);
  const setSessionId = useWorkshopStore((s) => s.setSessionId);
  const setRunning = useWorkshopStore((s) => s.setRunning);

  // Track the current assistant message being streamed into.
  // This ref bridges the gap between onNew (where the placeholder is created)
  // and onEvent (where streaming deltas arrive asynchronously).
  const currentAssistantIdRef = useRef<string | null>(null);

  // Keep stable refs to callbacks the WebSocket handler needs
  const onSessionCreatedRef = useRef(onSessionCreated);
  onSessionCreatedRef.current = onSessionCreated;

  // Store action refs for the WebSocket event handler
  const actionsRef = useRef({
    appendToAssistant,
    appendThinking,
    addToolCall,
    updateToolResult,
    setSessionId,
    setRunning,
  });
  actionsRef.current = {
    appendToAssistant,
    appendThinking,
    addToolCall,
    updateToolResult,
    setSessionId,
    setRunning,
  };

  // WebSocket event handler — dispatches server events to Zustand
  const onEvent = useCallback((event: ServerEvent) => {
    const assistantId = currentAssistantIdRef.current;
    const actions = actionsRef.current;

    switch (event.type) {
      case "thinking-delta":
        if (assistantId) actions.appendThinking(assistantId, event.data as string);
        break;

      case "text-delta":
        if (assistantId) actions.appendToAssistant(assistantId, event.data as string);
        break;

      case "tool-call": {
        if (!assistantId) break;
        const tc = event.data as ToolCallPart;
        actions.addToolCall(assistantId, {
          toolCallId: tc.toolCallId,
          toolName: tc.toolName,
          args: tc.args,
          argsText: tc.argsText,
        });
        break;
      }

      case "tool-result": {
        if (!assistantId) break;
        const { toolCallId, result, isError } = event.data as {
          toolCallId: string;
          result: JSONValue;
          isError?: boolean;
        };
        actions.updateToolResult(assistantId, toolCallId, result, isError);
        break;
      }

      case "session-id":
        actions.setSessionId(event.data as string);
        onSessionCreatedRef.current?.();
        break;

      case "error":
        console.error("[Alpha WS] Error:", event.data);
        if (assistantId) {
          actions.appendToAssistant(assistantId, `Error: ${event.data}`);
        }
        break;

      case "done":
        currentAssistantIdRef.current = null;
        actions.setRunning(false);
        break;

      case "interrupted":
        currentAssistantIdRef.current = null;
        actions.setRunning(false);
        break;
    }
  }, []);

  const { send, connected } = useWebSocket({ onEvent });

  // Keep a ref to `send` and `sessionId` for use in onNew
  const sendRef = useRef(send);
  sendRef.current = send;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const onNew = useCallback(
    async (appendMessage: AppendMessage) => {
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

      console.log("[Alpha] Sending via WebSocket, blocks:", backendContent.length);

      // Add user message to store (optimistic)
      addUserMessage(text, storeImages.length > 0 ? storeImages : undefined);

      // Create placeholder for assistant response
      const assistantId = addAssistantPlaceholder();
      currentAssistantIdRef.current = assistantId;
      setRunning(true);

      // Send via WebSocket
      const sent = sendRef.current({
        type: "send",
        content: backendContent.length === 1 && backendContent[0].type === "text"
          ? (backendContent[0] as { text: string }).text  // Simple string for text-only
          : backendContent,
        sessionId: sessionIdRef.current,
      });

      if (!sent) {
        appendToAssistant(assistantId, "Error: Not connected to server");
        currentAssistantIdRef.current = null;
        setRunning(false);
      }
    },
    [addUserMessage, addAssistantPlaceholder, appendToAssistant, setRunning]
  );

  const onCancel = useCallback(async () => {
    sendRef.current({ type: "interrupt" });
    currentAssistantIdRef.current = null;
    setRunning(false);
  }, [setRunning]);

  const adapters = useMemo(
    () => ({ attachments: new SimpleImageAttachmentAdapter() }),
    []
  );

  const runtime = useExternalStoreRuntime({
    messages,
    setMessages,
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
        <ThreadPrimitive.Root className="flex-1 flex flex-col overflow-hidden chat-font">
          <ThreadPrimitive.Viewport className="flex-1 flex flex-col overflow-y-scroll p-6">
            <div className="max-w-3xl mx-auto w-full flex-1">
              {messages.length === 0 && !isRunning && (
                <div className="flex-1 flex items-center justify-center h-full">
                  <p className="text-muted text-xl">
                    {connected
                      ? "How can I help you today?"
                      : "Connecting..."}
                  </p>
                </div>
              )}

              <ThreadPrimitive.Messages
                components={{
                  UserMessage,
                  AssistantMessage,
                }}
              />

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

                <AssistantIf condition={({ thread }) => !thread.isRunning}>
                  <ComposerPrimitive.Send className="w-9 h-9 flex items-center justify-center bg-primary border-none rounded-lg text-white cursor-pointer">
                    <ArrowUp size={20} strokeWidth={2.5} />
                  </ComposerPrimitive.Send>
                </AssistantIf>

                <AssistantIf condition={({ thread }) => thread.isRunning}>
                  <ComposerPrimitive.Cancel className="w-9 h-9 flex items-center justify-center bg-primary border-none rounded-lg text-white cursor-pointer">
                    <Square size={16} fill="white" />
                  </ComposerPrimitive.Cancel>
                </AssistantIf>
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

interface ChatPageProps {
  onSessionCreated?: () => void;
}

export default function ChatPage({ onSessionCreated }: ChatPageProps) {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const loadSession = useWorkshopStore((s) => s.loadSession);
  const reset = useWorkshopStore((s) => s.reset);

  useEffect(() => {
    if (!sessionId) {
      reset();
      return;
    }

    // Backend fetch — loads in background, no loading screen.
    // ThreadView renders immediately; messages appear when ready.
    const controller = new AbortController();

    fetch(`/api/sessions/${sessionId}`, { signal: controller.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`Session not found`);
        return r.json();
      })
      .then((data) => {
        const messages: Message[] = (data.messages || []).map(
          (m: { role: string; content: unknown }, i: number) => ({
            id: `loaded-${i}`,
            role: m.role as "user" | "assistant",
            content: Array.isArray(m.content)
              ? m.content
              : [{ type: "text", text: String(m.content) }],
            createdAt: new Date(),
          })
        );
        loadSession(sessionId, messages);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        console.error("[Alpha] Failed to load session:", err.message);
        navigate("/chat");
      });

    return () => controller.abort();
  }, [sessionId, loadSession, reset, navigate]);

  return <ThreadView onSessionCreated={onSessionCreated} />;
}
