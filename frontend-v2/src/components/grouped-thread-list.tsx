/**
 * GroupedThreadList — Day-grouped sidebar with time-based chat items.
 *
 * Every chat appears as its creation time (e.g. "2:36 PM") under a
 * day-of-week group header. Tooltip shows the full PSO-8601 date.
 * Dropdown menu on the ··· action button shows Chat ID and Session UUID
 * with copy buttons — the chat's passport, one click away.
 *
 * No week grouping. No single-vs-multi day distinction. Every day is
 * a header, every chat is a time item. Consistency beats compactness.
 */

import { useState, useMemo } from "react";
import { CheckIcon, CopyIcon, EllipsisIcon } from "lucide-react";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarMenuAction,
} from "@/components/ui/sidebar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { useIsMobile } from "@/hooks/use-mobile";
import { useStore, type Chat } from "@/store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DayBucket {
  dayLabel: string; // "Saturday", "Friday", etc.
  dateKey: string; // ISO key for React key
  threads: Chat[];
}

// ---------------------------------------------------------------------------
// Time helpers (PSO-8601, America/Los_Angeles, 6 AM circadian boundary)
// ---------------------------------------------------------------------------

const LA = "America/Los_Angeles";

/** Get the circadian day start (6 AM LA time) for a given unix timestamp */
function getCircadianDay(ts: number): Date {
  const d = new Date(ts * 1000);
  const laStr = d.toLocaleString("en-US", { timeZone: LA });
  const la = new Date(laStr);
  if (la.getHours() < 6) {
    la.setDate(la.getDate() - 1);
  }
  la.setHours(6, 0, 0, 0);
  return la;
}

/** Format a unix timestamp as PSO-8601: "Fri Apr 3 2026, 7:09 AM" */
function toPSO8601(ts: number): string {
  const d = new Date(ts * 1000);
  return (
    d.toLocaleDateString("en-US", {
      timeZone: LA,
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    }) +
    ", " +
    d.toLocaleTimeString("en-US", {
      timeZone: LA,
      hour: "numeric",
      minute: "2-digit",
    })
  );
}

/** Format creation time as PSO-8601 time only: "7:09 AM" */
function toTimeOnly(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-US", {
    timeZone: LA,
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Get weekday name for a circadian day */
function getDayName(circadianStart: Date): string {
  return circadianStart.toLocaleDateString("en-US", {
    timeZone: LA,
    weekday: "long",
  });
}

// ---------------------------------------------------------------------------
// Grouping logic (days only, no week buckets)
// ---------------------------------------------------------------------------

function groupChatsByDay(chats: Chat[]): DayBucket[] {
  const dayMap = new Map<
    string,
    { circadianStart: Date; threads: Chat[] }
  >();

  for (const c of chats) {
    const circDay = getCircadianDay(c.createdAt);
    const key = circDay.toISOString();
    if (!dayMap.has(key)) {
      dayMap.set(key, { circadianStart: circDay, threads: [] });
    }
    dayMap.get(key)!.threads.push(c);
  }

  return Array.from(dayMap.values())
    .sort((a, b) => b.circadianStart.getTime() - a.circadianStart.getTime())
    .map(({ circadianStart, threads }) => ({
      dayLabel: getDayName(circadianStart),
      dateKey: circadianStart.toISOString(),
      threads: threads.sort((a, b) => b.createdAt - a.createdAt),
    }));
}

// ---------------------------------------------------------------------------
// CopyRow — a dropdown menu item that copies text to clipboard
// ---------------------------------------------------------------------------

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault(); // keep menu open
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API requires HTTPS
    }
  };

  return (
    <DropdownMenuItem
      className="flex items-start gap-3 cursor-pointer px-3 py-2.5"
      onClick={handleCopy}
    >
      <div className="flex-1 min-w-0 space-y-1">
        <p className="text-xs font-medium leading-none text-muted-foreground">
          {label}
        </p>
        <code className="text-sm text-foreground break-all leading-snug block">
          {value}
        </code>
      </div>
      {copied ? (
        <CheckIcon className="size-3.5 shrink-0 text-muted-foreground mt-0.5" />
      ) : (
        <CopyIcon className="size-3.5 shrink-0 text-muted-foreground mt-0.5" />
      )}
    </DropdownMenuItem>
  );
}

// ---------------------------------------------------------------------------
// ChatItem — one chat in the sidebar
// ---------------------------------------------------------------------------

function ChatItem({ chat }: { chat: Chat }) {
  const setCurrentChatId = useStore((s) => s.setCurrentChatId);
  const isActive = useStore((s) => s.currentChatId === chat.id);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const isMobile = useIsMobile();

  return (
    <Tooltip open={dropdownOpen ? false : undefined}>
      <TooltipTrigger asChild>
        <SidebarMenuItem>
          <SidebarMenuButton
            className="cursor-pointer group-hover/menu-item:bg-sidebar-accent group-hover/menu-item:text-sidebar-accent-foreground data-active:bg-primary data-active:text-primary-foreground"
            isActive={isActive}
            onClick={() => setCurrentChatId(chat.id)}
          >
            {toTimeOnly(chat.createdAt)}
          </SidebarMenuButton>

      <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
        <DropdownMenuTrigger asChild>
          <SidebarMenuAction
            showOnHover
            className="peer-data-[active=true]/menu-button:opacity-100 peer-data-[active=true]/menu-button:text-primary-foreground peer-data-[active=true]/menu-button:group-hover/menu-item:text-sidebar-accent-foreground"
          >
            <EllipsisIcon />
            <span className="sr-only">Chat details</span>
          </SidebarMenuAction>
        </DropdownMenuTrigger>
        <DropdownMenuContent side={isMobile ? "bottom" : "right"} align="start" className="w-72">
          <CopyRow label="Chat ID" value={chat.id} />
          {chat.sessionUuid && (
            <>
              <DropdownMenuSeparator />
              <CopyRow label="Session ID" value={chat.sessionUuid} />
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
        </SidebarMenuItem>
      </TooltipTrigger>
      <TooltipContent side="right" className="text-xs tabular-nums">
        {toPSO8601(chat.createdAt)}
      </TooltipContent>
    </Tooltip>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function GroupedThreadList() {
  const chatsMap = useStore((s) => s.chats);

  const days = useMemo(() => {
    const chats = Object.values(chatsMap);
    return groupChatsByDay(chats);
  }, [chatsMap]);

  return (
    <div className="flex flex-col">
      {days.map((day) => (
        <SidebarGroup key={day.dateKey}>
          <SidebarGroupLabel>{day.dayLabel}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {day.threads.map((chat) => (
                <ChatItem key={chat.id} chat={chat} />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </div>
  );
}
