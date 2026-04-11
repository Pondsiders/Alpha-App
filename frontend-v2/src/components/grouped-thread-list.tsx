/**
 * GroupedThreadList — Week-grouped, fully-expanded sidebar thread list.
 *
 * Reads chats from the Zustand store (populated by the WebSocket handler
 * in useAlphaWebSocket), groups them by week then circadian day, and
 * renders each thread as a plain button whose click calls setCurrentChatId.
 *
 * Zero assistant-ui framework machinery. No provider hierarchy, no thread
 * list runtime, no derived contexts. Just: read the store, render buttons,
 * wire onClick.
 *
 * Single-thread days render as a plain day-name item (clickable).
 * Multi-thread days render as a non-clickable day label plus an always-
 * visible list of time entries underneath. No collapse affordance —
 * everything the sidebar can show, it shows. The circadian architecture
 * means most days are single-chat anyway; multi-chat days are the
 * exception worth spreading out visually.
 */

import { useMemo } from "react";

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuItem,
  SidebarMenuButton,
  SidebarMenuSub,
  SidebarMenuSubItem,
  SidebarMenuSubButton,
} from "@/components/ui/sidebar";
import { ChatHoverCard } from "@/components/ChatHoverCard";

import { useStore, type Chat } from "@/store";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DayBucket {
  dayLabel: string; // "Friday", "Thursday", etc.
  dateLabel: string; // PSO-8601 date for tooltip (also used as key)
  threads: Chat[];
}

interface WeekBucket {
  weekLabel: string; // "This Week", "Last Week", "Mar 15–21"
  days: DayBucket[];
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

/** Get the Monday (start of week) for a given date */
function getWeekStart(d: Date): Date {
  const clone = new Date(d);
  const day = clone.getDay();
  const diff = day === 0 ? -6 : 1 - day; // Monday = start
  clone.setDate(clone.getDate() + diff);
  clone.setHours(0, 0, 0, 0);
  return clone;
}

/** Format a week label */
function getWeekLabel(weekStart: Date, now: Date): string {
  const nowWeekStart = getWeekStart(now);
  const diff = Math.round(
    (nowWeekStart.getTime() - weekStart.getTime()) / (7 * 24 * 60 * 60 * 1000),
  );
  if (diff === 0) return "This Week";
  if (diff === 1) return "Last Week";
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);
  const startStr = weekStart.toLocaleDateString("en-US", {
    timeZone: LA,
    month: "short",
    day: "numeric",
  });
  const endStr = weekEnd.toLocaleDateString("en-US", {
    timeZone: LA,
    day: "numeric",
  });
  return `${startStr}–${endStr}`;
}

// ---------------------------------------------------------------------------
// Grouping logic
// ---------------------------------------------------------------------------

function groupChats(chats: Chat[]): WeekBucket[] {
  const now = new Date();

  // Bucket by circadian day
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

  // Sort days newest first
  const sortedDays = Array.from(dayMap.values()).sort(
    (a, b) => b.circadianStart.getTime() - a.circadianStart.getTime(),
  );

  // Bucket days into weeks
  const weekMap = new Map<
    string,
    { weekStart: Date; days: typeof sortedDays }
  >();
  for (const day of sortedDays) {
    const ws = getWeekStart(day.circadianStart);
    const key = ws.toISOString();
    if (!weekMap.has(key)) {
      weekMap.set(key, { weekStart: ws, days: [] });
    }
    weekMap.get(key)!.days.push(day);
  }

  // Sort weeks newest first, build output
  return Array.from(weekMap.values())
    .sort((a, b) => b.weekStart.getTime() - a.weekStart.getTime())
    .map(({ weekStart, days }) => ({
      weekLabel: getWeekLabel(weekStart, now),
      days: days.map((d) => ({
        dayLabel: getDayName(d.circadianStart),
        dateLabel: toPSO8601(d.circadianStart.getTime() / 1000),
        threads: d.threads.sort((a, b) => b.createdAt - a.createdAt),
      })),
    }));
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

/**
 * Sub-menu button for a thread inside a multi-thread day.
 *
 * Has its own useStore subscription so only this one component re-renders
 * when the active chat changes — not the entire sidebar.
 */
function ThreadSubItem({ chat }: { chat: Chat }) {
  const setCurrentChatId = useStore((s) => s.setCurrentChatId);
  const isActive = useStore((s) => s.currentChatId === chat.id);

  return (
    <ChatHoverCard chat={chat}>
      <SidebarMenuSubButton
        className="cursor-pointer data-active:bg-primary data-active:text-primary-foreground"
        isActive={isActive}
        onClick={() => setCurrentChatId(chat.id)}
      >
        {toTimeOnly(chat.createdAt)}
      </SidebarMenuSubButton>
    </ChatHoverCard>
  );
}

/** Menu button for a day that has exactly one chat — click goes to it. */
function SingleThreadDayItem({
  dayLabel,
  chat,
}: {
  dayLabel: string;
  chat: Chat;
}) {
  const setCurrentChatId = useStore((s) => s.setCurrentChatId);
  const isActive = useStore((s) => s.currentChatId === chat.id);

  return (
    <SidebarMenuItem>
      <ChatHoverCard chat={chat}>
        <SidebarMenuButton
          className="w-full cursor-pointer data-active:bg-primary data-active:text-primary-foreground"
          isActive={isActive}
          onClick={() => setCurrentChatId(chat.id)}
        >
          {dayLabel}
        </SidebarMenuButton>
      </ChatHoverCard>
    </SidebarMenuItem>
  );
}

/**
 * Non-collapsible menu for a day that has multiple chats. Day label
 * uses the same visual layout as SidebarMenuButton (default size) but
 * is a non-interactive <div> with muted text color — so it reads as
 * "a day item, but this one's a header not a link."
 */
function MultiThreadDayItem({ day }: { day: DayBucket }) {
  return (
    <SidebarMenuItem>
      <div
        className="flex h-8 w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm text-muted-foreground select-none cursor-default"
      >
        {day.dayLabel}
      </div>
      <SidebarMenuSub>
        {day.threads.map((t) => (
          <SidebarMenuSubItem key={t.id}>
            <ThreadSubItem chat={t} />
          </SidebarMenuSubItem>
        ))}
      </SidebarMenuSub>
    </SidebarMenuItem>
  );
}

/** Dispatcher: single-thread or multi-thread based on day contents. */
function DayItem({ day }: { day: DayBucket }) {
  if (day.threads.length === 1) {
    return <SingleThreadDayItem dayLabel={day.dayLabel} chat={day.threads[0]} />;
  }
  return <MultiThreadDayItem day={day} />;
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export function GroupedThreadList() {
  // Subscribe to the chats map. Immer gives us structural sharing, so this
  // reference only changes when the map itself actually changes.
  const chatsMap = useStore((s) => s.chats);

  // Project to array once per change, not on every render.
  const weeks = useMemo(() => {
    const chats = Object.values(chatsMap);
    return groupChats(chats);
  }, [chatsMap]);

  return (
    <div className="flex flex-col">
      {weeks.map((week) => (
        <SidebarGroup key={week.weekLabel}>
          <SidebarGroupLabel>{week.weekLabel}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {week.days.map((day) => (
                <DayItem key={day.dateLabel} day={day} />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </div>
  );
}
