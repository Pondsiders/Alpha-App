/**
 * AppSidebar — Flat chat list with week dividers.
 *
 * Each chat shows its creation timestamp in PSO-8601 format.
 * Divided by week: "This Week", "Last Week", then date ranges.
 * New Chat button: small amber roundrect with + icon, left-aligned.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

import { useWorkshopStore, type ChatMeta } from "@/store";

// ---------------------------------------------------------------------------
// PSO-8601 formatting
// ---------------------------------------------------------------------------

/** Short format for sidebar buttons: "Wednesday, 7:22 AM" */
function formatShort(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString("en-US", {
    timeZone: "America/Los_Angeles",
    weekday: "long",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

/** Full PSO-8601 for tooltip: "Wed Mar 25 2026, 7:22 AM" */
function formatFull(epochSeconds: number): string {
  if (!epochSeconds) return "";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString("en-US", {
    timeZone: "America/Los_Angeles",
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

// ---------------------------------------------------------------------------
// Week grouping
// ---------------------------------------------------------------------------

/** Get the Monday of the week containing a date (Pondside local time). */
function weekStart(epochSeconds: number): string {
  if (!epochSeconds) return "0000-01-01";
  const d = new Date(epochSeconds * 1000);
  // Get local date in LA
  const laDate = new Date(
    d.toLocaleString("en-US", { timeZone: "America/Los_Angeles" })
  );
  const day = laDate.getDay(); // 0=Sun, 1=Mon, ...
  const diff = day === 0 ? 6 : day - 1; // Days since Monday
  laDate.setDate(laDate.getDate() - diff);
  laDate.setHours(0, 0, 0, 0);
  return laDate.toISOString().slice(0, 10); // YYYY-MM-DD
}

function weekLabel(weekStartStr: string, isThisWeek: boolean, isLastWeek: boolean): string {
  if (isThisWeek) return "This Week";
  if (isLastWeek) return "Last Week";
  // Format as "Mar 8–14, 2026"
  const [y, m, d] = weekStartStr.split("-").map(Number);
  const start = new Date(y, m - 1, d);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  const startStr = start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const endStr = end.getDate().toString();
  const yearStr = start.getFullYear().toString();
  return `${startStr}–${endStr}, ${yearStr}`;
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

  // Only force-open sidebar when truly empty (no chats at all after
  // initial load). Avoid overriding localStorage on every mount —
  // the brief isEmpty state before WebSocket populates chats was
  // clobbering the user's saved preference.
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  useEffect(() => {
    if (Object.keys(chats).length > 0) setInitialLoadDone(true);
  }, [chats]);
  useEffect(() => {
    if (initialLoadDone && isEmpty) setOpen(true);
  }, [initialLoadDone, isEmpty, setOpen]);

  // Sort by updatedAt descending, group by week
  const grouped = useMemo(() => {
    const sorted = Object.values(chats).sort((a, b) => b.updatedAt - a.updatedAt);

    const now = Date.now() / 1000;
    const thisWeekStr = weekStart(now);
    const lastWeekEpoch = now - 7 * 86400;
    const lastWeekStr = weekStart(lastWeekEpoch);

    const groups: { key: string; label: string; chats: ChatMeta[] }[] = [];
    const seen = new Map<string, ChatMeta[]>();

    for (const chat of sorted) {
      const ws = weekStart(chat.createdAt);
      if (!seen.has(ws)) {
        const arr: ChatMeta[] = [];
        seen.set(ws, arr);
        groups.push({
          key: ws,
          label: weekLabel(ws, ws === thisWeekStr, ws === lastWeekStr),
          chats: arr,
        });
      }
      seen.get(ws)!.push(chat);
    }
    return groups;
  }, [chats]);

  // New Chat guard
  const hasUnusedChat = useMemo(
    () =>
      Object.values(chats).some(
        (c) => (c.state === "idle" || c.state === "starting") && !c.title
      ),
    [chats]
  );

  const [isPending, setIsPending] = useState(false);
  useEffect(() => {
    if (hasUnusedChat) setIsPending(false);
  }, [hasUnusedChat]);

  const handleChatClick = useCallback(
    (id: string) => {
      navigate(`/chat/${id}`);
      if (isMobile) setOpenMobile(false);
    },
    [navigate, isMobile, setOpenMobile]
  );

  const handleNewChat = useCallback(() => {
    setIsPending(true);
    onNewChat();
    if (isMobile) setOpenMobile(false);
  }, [onNewChat, isMobile, setOpenMobile]);

  return (
    <Sidebar side="left" variant="sidebar" collapsible="offcanvas" className="border-border/50">
      <SidebarHeader className="h-12 items-start justify-center pl-5 pr-3 border-b border-border shrink-0">
        <button
          onClick={handleNewChat}
          disabled={isPending || hasUnusedChat}
          className="flex items-center gap-2 text-sm transition-colors hover:opacity-80 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span
            className="flex items-center justify-center w-8 h-8 rounded-lg shrink-0"
            style={{
              backgroundColor: "var(--theme-primary)",
              color: "var(--theme-bg)",
            }}
          >
            <Plus size={16} strokeWidth={2.5} />
          </span>
          <span className="font-medium">New chat</span>
        </button>
      </SidebarHeader>

      <SidebarContent>
        {grouped.map((group) => (
          <SidebarGroup key={group.key} className="py-1">
            <SidebarGroupLabel className="text-[10px] text-muted/40 uppercase tracking-wider px-3 pb-0.5">
              {group.label}
            </SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.chats.map((chat) => {
                  const ts = chat.createdAt;
                  return (
                    <SidebarMenuItem key={chat.id} className="pl-2">
                      <TooltipProvider delayDuration={400}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <SidebarMenuButton
                              onClick={() => handleChatClick(chat.id)}
                              isActive={chatId === chat.id}
                              className="text-[13px]"
                              style={chatId === chat.id ? {
                                borderLeft: "2px solid var(--theme-primary)",
                                paddingLeft: "8px",
                              } : undefined}
                            >
                              <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                                {formatShort(ts)}
                              </span>
                            </SidebarMenuButton>
                          </TooltipTrigger>
                          <TooltipContent side="right" className="text-xs">
                            {formatFull(ts)}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>

      <SidebarRail className="after:!bg-border/50 hover:after:!bg-border" />
    </Sidebar>
  );
}
