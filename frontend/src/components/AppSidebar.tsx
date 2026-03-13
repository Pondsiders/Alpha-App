/**
 * AppSidebar — Chat list sidebar with indicator lights.
 *
 * Phase 2: Driven by Zustand store (chats map populated via WebSocket).
 * Indicator dots: green=IDLE, amber-pulse=BUSY/STARTING, gray=DEAD.
 * "New Chat" navigates to /chat which triggers auto-create in Layout.
 */

import { useCallback, useEffect, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";

import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarGroup,
  SidebarGroupContent,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

import { useWorkshopStore, type ChatState } from "@/store";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const now = Date.now() / 1000;
  const diff = now - epochSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 172800) return "yesterday";
  return `${Math.floor(diff / 86400)}d ago`;
}

// ---------------------------------------------------------------------------
// Indicator Light
// ---------------------------------------------------------------------------

const DOT_COLORS: Record<ChatState, string> = {
  idle: "var(--theme-success)",
  busy: "var(--theme-primary)",
  starting: "var(--theme-primary)",
  dead: "var(--theme-muted)",
};

const DOT_ANIMATIONS: Record<ChatState, string> = {
  idle: "",
  busy: "animate-pulse",
  starting: "animate-pulse",
  dead: "",
};

const STATE_LABELS: Record<ChatState, { text: string; color: string }> = {
  idle: { text: "Idle", color: "var(--theme-success)" },
  busy: { text: "Streaming", color: "var(--theme-primary)" },
  starting: { text: "Starting", color: "var(--theme-primary)" },
  dead: { text: "Inactive", color: "var(--theme-muted)" },
};

function ChatIndicator({ state, chatId }: { state: ChatState; chatId: string }) {
  const label = STATE_LABELS[state];
  return (
    <HoverCard openDelay={300} closeDelay={100}>
      <HoverCardTrigger asChild>
        <span
          className={`shrink-0 w-2 h-2 rounded-full cursor-default ${DOT_ANIMATIONS[state]}`}
          style={{ backgroundColor: DOT_COLORS[state] }}
          aria-label={state}
        />
      </HoverCardTrigger>
      <HoverCardContent side="right" align="start" className="w-auto min-w-[140px]">
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted shrink-0">State</span>
            <span className="text-[11px]" style={{ color: label.color }}>
              {label.text}
            </span>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted shrink-0">Chat</span>
            <span className="text-[11px] font-mono" style={{ overflowWrap: "anywhere" }}>
              {chatId}
            </span>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}

// ---------------------------------------------------------------------------
// AppSidebar
// ---------------------------------------------------------------------------

interface AppSidebarProps {
  onNewChat: () => void;
}

export function AppSidebar({ onNewChat }: AppSidebarProps) {
  const { chatId } = useParams<{ chatId?: string }>();
  const navigate = useNavigate();
  const { setOpenMobile, setOpen, isMobile } = useSidebar();

  const chats = useWorkshopStore((s) => s.chats);
  const activeChatId = useWorkshopStore((s) => s.activeChatId);
  const isEmpty = !activeChatId;

  // Auto-open sidebar when in empty state (no active chat)
  useEffect(() => {
    if (isEmpty) {
      setOpen(true);
    }
  }, [isEmpty, setOpen]);

  // Sort by updatedAt descending
  const sortedChats = useMemo(
    () => Object.values(chats).sort((a, b) => b.updatedAt - a.updatedAt),
    [chats]
  );

  // New Chat guard: disable if there's already a pending (zero-message) chat.
  // Include "starting" so rapid double-clicks are also blocked before the
  // subprocess finishes warming up.
  const hasUnusedChat = useMemo(
    () => sortedChats.some((c) => (c.state === "idle" || c.state === "starting") && !c.title),
    [sortedChats]
  );

  const handleChatClick = useCallback(
    (id: string) => {
      navigate(`/chat/${id}`);
      if (isMobile) setOpenMobile(false);
    },
    [navigate, isMobile, setOpenMobile]
  );

  const handleNewChat = useCallback(() => {
    onNewChat();
    if (isMobile) setOpenMobile(false);
  }, [onNewChat, isMobile, setOpenMobile]);

  return (
    <Sidebar side="left" variant="sidebar" collapsible="offcanvas">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={handleNewChat}
              tooltip="New chat"
              disabled={hasUnusedChat}
              className={isEmpty && !hasUnusedChat ? "animate-breathe" : undefined}
            >
              <Plus size={16} />
              <span>New chat</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {sortedChats.map((chat) => (
                <SidebarMenuItem key={chat.id}>
                  <SidebarMenuButton
                    onClick={() => handleChatClick(chat.id)}
                    isActive={chatId === chat.id}
                    tooltip={chat.title || "New chat"}
                  >
                    <ChatIndicator state={chat.state} chatId={chat.id} />
                    <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                      {chat.title || (
                        <span className="italic text-muted">New chat</span>
                      )}
                    </span>
                    <span className="text-xs text-muted/50 shrink-0 group-data-[collapsible=icon]:hidden">
                      {formatRelative(chat.updatedAt)}
                    </span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarRail />
    </Sidebar>
  );
}
