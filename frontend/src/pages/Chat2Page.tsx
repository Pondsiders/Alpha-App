/**
 * Chat2Page — Full implementation of the assistant-ui Shadcn example.
 *
 * Sidebar + Header + Thread. Stock components. Our WebSocket backend.
 * This is the reference implementation that will eventually become our ChatPage.
 *
 * Source: https://github.com/assistant-ui/assistant-ui/blob/main/apps/docs/components/examples/shadcn.tsx
 */

import "../chat2.css";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams } from "react-router-dom";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import type { ThreadMessageLike, AppendMessage } from "@assistant-ui/react";
import { Thread } from "@/components/assistant-ui/thread";
import { ThreadList } from "@/components/assistant-ui/thread-list";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { MenuIcon, PanelLeftIcon, ShareIcon } from "lucide-react";
import type { FC } from "react";

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------

interface UserMsg {
  role: "user";
  id: string;
  text: string;
  source?: string;
}

interface AssistantMsg {
  role: "assistant";
  id: string;
  parts: Array<{ type: string; text?: string; [k: string]: unknown }>;
}

type Msg = UserMsg | AssistantMsg;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractUserText(data: Record<string, unknown>): string {
  const contentArr = data.content as Array<{ type: string; text?: string }> | undefined;
  if (Array.isArray(contentArr)) {
    return contentArr
      .filter((b) => b.type === "text")
      .map((b) => b.text || "")
      .join("\n");
  }
  return (data.text as string) || "";
}

// ---------------------------------------------------------------------------
// Layout: Sidebar
// ---------------------------------------------------------------------------

const Logo: FC = () => (
  <div className="flex items-center gap-2 px-2 font-medium text-sm">
    <span className="text-lg">🦆</span>
    <span className="text-foreground/90">Alpha</span>
  </div>
);

const Sidebar: FC<{ collapsed?: boolean }> = ({ collapsed }) => (
  <aside
    className={`flex h-full flex-col bg-muted/30 transition-all duration-200 ${
      collapsed ? "w-0 overflow-hidden opacity-0" : "w-65 opacity-100"
    }`}
  >
    <div className="flex h-14 shrink-0 items-center px-4">
      <Logo />
    </div>
    <div className="flex-1 overflow-y-auto p-3">
      <ThreadList />
    </div>
  </aside>
);

const MobileSidebar: FC = () => (
  <Sheet>
    <SheetTrigger asChild>
      <Button
        variant="ghost"
        size="icon"
        className="size-9 shrink-0 md:hidden"
      >
        <MenuIcon className="size-4" />
        <span className="sr-only">Toggle menu</span>
      </Button>
    </SheetTrigger>
    <SheetContent side="left" className="w-70 p-0">
      <div className="flex h-14 items-center px-4">
        <Logo />
      </div>
      <div className="p-3">
        <ThreadList />
      </div>
    </SheetContent>
  </Sheet>
);

// ---------------------------------------------------------------------------
// Layout: Header
// ---------------------------------------------------------------------------

const Header: FC<{
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}> = ({ sidebarCollapsed, onToggleSidebar }) => (
  <header className="flex h-14 shrink-0 items-center gap-2 px-4">
    <MobileSidebar />
    <TooltipIconButton
      variant="ghost"
      size="icon"
      tooltip={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
      side="bottom"
      onClick={onToggleSidebar}
      className="hidden size-9 md:flex"
    >
      <PanelLeftIcon className="size-4" />
    </TooltipIconButton>
    <div className="text-sm text-muted-foreground">Alpha · Opus 4.6</div>
    <TooltipIconButton
      variant="ghost"
      size="icon"
      tooltip="Share"
      side="bottom"
      className="ml-auto size-9"
    >
      <ShareIcon className="size-4" />
    </TooltipIconButton>
  </header>
);

// ---------------------------------------------------------------------------
// Chat2Page
// ---------------------------------------------------------------------------

export default function Chat2Page() {
  const { chatId } = useParams<{ chatId: string }>();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [connected, setConnected] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // --- WebSocket connection ---
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      if (chatId) {
        ws.send(JSON.stringify({ type: "join-chat", chatId }));
      }
    };

    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleEvent(msg);
      } catch {
        // ignore parse errors
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [chatId]);

  // --- Event handler ---
  const handleEvent = useCallback((event: Record<string, unknown>) => {
    const type = event.type as string;

    if (type === "chat-data") {
      const payload = (event.data as Record<string, unknown>) || {};
      const loaded = (payload.messages as Array<{ role: string; data: Record<string, unknown> }>) || [];
      const parsed: Msg[] = [];
      for (const m of loaded) {
        if (m.role === "user" || m.role === "system") {
          parsed.push({
            role: "user",
            id: (m.data.id as string) || `u-${parsed.length}`,
            text: extractUserText(m.data),
            source: (m.data.source as string) || (m.role === "system" ? "system" : "human"),
          });
        } else if (m.role === "assistant") {
          parsed.push({
            role: "assistant",
            id: (m.data.id as string) || `a-${parsed.length}`,
            parts: (m.data.parts as AssistantMsg["parts"]) || [{ type: "text", text: "" }],
          });
        }
      }
      setMessages(parsed);
      setIsRunning(false);
      setIsLoading(false);
    }

    if (type === "user-message") {
      const data = event.data as Record<string, unknown>;
      const text = extractUserText(data);
      const id = (data.id as string) || `u-${Date.now()}`;
      const source = (data.source as string) || "human";

      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "user" && last.text === text && last.source === source) {
          return prev;
        }
        return [...prev, { role: "user", id, text, source }];
      });
    }

    if (type === "text-delta") {
      const text = (event.data as string) || "";
      setIsRunning(true);
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant") {
          const updated = { ...last, parts: [...last.parts] };
          const lastPart = updated.parts[updated.parts.length - 1];
          if (lastPart?.type === "text") {
            updated.parts[updated.parts.length - 1] = {
              ...lastPart,
              text: (lastPart.text || "") + text,
            };
          } else {
            updated.parts.push({ type: "text", text });
          }
          return [...prev.slice(0, -1), updated];
        } else {
          return [
            ...prev,
            {
              role: "assistant" as const,
              id: `a-${Date.now()}`,
              parts: [{ type: "text", text }],
            },
          ];
        }
      });
    }

    if (type === "result" || type === "done") {
      setIsRunning(false);
    }
  }, []);

  // --- Convert to ThreadMessageLike ---
  const convertMessage = useCallback((msg: Msg): ThreadMessageLike => {
    if (msg.role === "assistant") {
      const content: ThreadMessageLike["content"] = [];
      for (const part of msg.parts) {
        if (part.type === "text") {
          content.push({ type: "text" as const, text: part.text || "" });
        }
      }
      if (content.length === 0) {
        content.push({ type: "text" as const, text: "" });
      }
      return { role: "assistant", content };
    }
    return { role: "user", content: [{ type: "text" as const, text: msg.text }] };
  }, []);

  // --- Send handler ---
  const onNew = useCallback(
    async (appendMessage: AppendMessage) => {
      if (!chatId || !wsRef.current) return;
      const textParts = appendMessage.content.filter(
        (p): p is { type: "text"; text: string } => p.type === "text"
      );
      const text = textParts.map((p) => p.text).join("\n");
      if (!text.trim()) return;

      const tempId = `u-${Date.now()}`;
      setMessages((prev) => [...prev, { role: "user", id: tempId, text, source: "human" }]);
      setIsRunning(true);

      wsRef.current.send(
        JSON.stringify({
          type: "send",
          chatId,
          content: [{ type: "text", text }],
        })
      );
    },
    [chatId]
  );

  // --- Thread list adapter ---
  const threadListAdapter = useMemo(
    () => ({
      threadId: chatId ?? "default",
      threads: chatId
        ? [{ id: chatId, status: "regular" as const, title: "Current Chat" }]
        : [],
      archivedThreads: [],
      onSwitchToNewThread: () => {},
      onSwitchToThread: () => {},
    }),
    [chatId]
  );

  // --- Scroll to bottom on initial load ---
  // ExternalStore doesn't fire thread.initialize, so we scroll manually.
  // useEffect fires after React commits the render with messages.
  const hasScrolledRef = useRef(false);
  useEffect(() => {
    if (isLoading || messages.length === 0 || hasScrolledRef.current) return;
    hasScrolledRef.current = true;
    // Wait for the Thread component to render all messages
    const timer = setTimeout(() => {
      const viewport = document.querySelector(".aui-thread-viewport");
      if (viewport) {
        viewport.scrollTo({ top: viewport.scrollHeight, behavior: "instant" });
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [isLoading, messages.length]);

  // --- Runtime ---
  const runtime = useExternalStoreRuntime({
    messages,
    isRunning,
    isLoading,
    convertMessage,
    onNew,
    adapters: {
      threadList: threadListAdapter,
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TooltipProvider>
        <div className="flex h-dvh w-full bg-background text-foreground">
          {/* Desktop sidebar */}
          <div className="hidden md:block">
            <Sidebar collapsed={sidebarCollapsed} />
          </div>

          {/* Main area */}
          <div className="flex flex-1 flex-col overflow-hidden">
            <Header
              sidebarCollapsed={sidebarCollapsed}
              onToggleSidebar={() => setSidebarCollapsed(!sidebarCollapsed)}
            />

            {!connected && !isLoading && (
              <div className="mx-4 mb-2 rounded bg-destructive text-destructive-foreground px-3 py-1 text-sm text-center">
                Disconnected
              </div>
            )}

            <main className="flex-1 overflow-hidden">
              <Thread />
            </main>
          </div>
        </div>
      </TooltipProvider>
    </AssistantRuntimeProvider>
  );
}
