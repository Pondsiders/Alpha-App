/**
 * GroupedThreadList — Week-grouped, day-collapsible sidebar thread list.
 *
 * Reads the runtime's ordered thread list via useAuiState, fetches
 * /api/threads for creation timestamps, joins them, and groups by
 * week-then-circadian-day. Each thread is rendered inside a
 * ThreadListItemByIndexProvider which sets up the per-item context
 * that ThreadListItemPrimitive.Root and .Trigger need — giving us
 * click handling, active state, and archive/delete actions for free
 * from the framework, while we keep full control over the outer layout.
 *
 * Single-thread days render as a plain day-name item.
 * Multi-thread days render as a collapsible with sub-items.
 */

import { useEffect, useState, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  ThreadListItemByIndexProvider,
  ThreadListItemPrimitive,
  useAuiState,
} from "@assistant-ui/react";
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Thread data augmented with its index in the runtime's threadItems array. */
interface AugmentedThread {
  chatId: string; // maps to ThreadListItemState.remoteId
  title?: string;
  createdAt: number; // unix timestamp (from /api/threads)
  runtimeIndex: number; // index in s.threads.threadItems
}

interface DayBucket {
  dayLabel: string; // "Friday", "Thursday", etc.
  dateLabel: string; // PSO-8601 date for tooltip (also used as key)
  threads: AugmentedThread[];
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
    (nowWeekStart.getTime() - weekStart.getTime()) / (7 * 24 * 60 * 60 * 1000)
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

function groupThreads(threads: AugmentedThread[]): WeekBucket[] {
  const now = new Date();

  // Bucket by circadian day
  const dayMap = new Map<
    string,
    { circadianStart: Date; threads: AugmentedThread[] }
  >();
  for (const t of threads) {
    const circDay = getCircadianDay(t.createdAt);
    const key = circDay.toISOString();
    if (!dayMap.has(key)) {
      dayMap.set(key, { circadianStart: circDay, threads: [] });
    }
    dayMap.get(key)!.threads.push(t);
  }

  // Sort days newest first
  const sortedDays = Array.from(dayMap.values()).sort(
    (a, b) => b.circadianStart.getTime() - a.circadianStart.getTime()
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
 * Thread item rendered as a sub-menu button (inside a collapsible day).
 *
 * MUST be wrapped in ThreadListItemByIndexProvider by the caller so that
 * ThreadListItemPrimitive.Root / .Trigger have per-item context.
 */
function ThreadSubItem({ thread }: { thread: AugmentedThread }) {
  return (
    <ThreadListItemPrimitive.Root>
      <Tooltip>
        <TooltipTrigger asChild>
          <ThreadListItemPrimitive.Trigger asChild>
            <SidebarMenuSubButton className="cursor-pointer">
              {toTimeOnly(thread.createdAt)}
            </SidebarMenuSubButton>
          </ThreadListItemPrimitive.Trigger>
        </TooltipTrigger>
        <TooltipContent side="right">
          {toPSO8601(thread.createdAt)}
        </TooltipContent>
      </Tooltip>
    </ThreadListItemPrimitive.Root>
  );
}

/**
 * Day row — either a single thread button, or a collapsible with
 * multiple thread sub-items.
 */
function DayItem({ day }: { day: DayBucket }) {
  const [open, setOpen] = useState(false);

  // Single thread — clicking the day name switches to that thread
  if (day.threads.length === 1) {
    const thread = day.threads[0];
    return (
      <SidebarMenuItem>
        <ThreadListItemByIndexProvider
          index={thread.runtimeIndex}
          archived={false}
        >
          <ThreadListItemPrimitive.Root>
            <Tooltip>
              <TooltipTrigger asChild>
                <ThreadListItemPrimitive.Trigger asChild>
                  <SidebarMenuButton className="w-full">
                    {day.dayLabel}
                  </SidebarMenuButton>
                </ThreadListItemPrimitive.Trigger>
              </TooltipTrigger>
              <TooltipContent side="right">
                {toPSO8601(thread.createdAt)}
              </TooltipContent>
            </Tooltip>
          </ThreadListItemPrimitive.Root>
        </ThreadListItemByIndexProvider>
      </SidebarMenuItem>
    );
  }

  // Multiple threads — collapsible day containing per-thread sub-items
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <SidebarMenuButton className="w-full">
            {day.dayLabel}
            {open ? (
              <ChevronDown className="ml-auto size-3.5" />
            ) : (
              <ChevronRight className="ml-auto size-3.5" />
            )}
          </SidebarMenuButton>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarMenuSub>
            {day.threads.map((t) => (
              <SidebarMenuSubItem key={t.chatId}>
                <ThreadListItemByIndexProvider
                  index={t.runtimeIndex}
                  archived={false}
                >
                  <ThreadSubItem thread={t} />
                </ThreadListItemByIndexProvider>
              </SidebarMenuSubItem>
            ))}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

/**
 * Select the runtime's threadItems array directly.
 *
 * The store uses immutable updates, so `threadItems` has a stable reference
 * unless the list actually changes (adds/removes/reorders). useAuiState
 * compares with Object.is via useSyncExternalStore, so returning the raw
 * array is the cheap path — transforming it here would break reference
 * stability and cause a re-render on every store notification.
 */
function selectThreadItems(s: {
  threads: { threadItems: readonly { id: string; remoteId?: string }[] };
}) {
  return s.threads.threadItems;
}

export function GroupedThreadList() {
  // Runtime-side thread list — stable reference, only changes when the
  // list itself changes.
  const threadItems = useAuiState(selectThreadItems);

  // Timestamp data from /api/threads (the runtime doesn't carry createdAt)
  const [timestamps, setTimestamps] = useState<Map<
    string,
    { createdAt: number; title?: string }
  > | null>(null);

  useEffect(() => {
    fetch("/api/threads")
      .then((r) => r.json())
      .then(
        (
          data: Array<{ chatId: string; createdAt: number; title?: string }>
        ) => {
          const map = new Map<
            string,
            { createdAt: number; title?: string }
          >();
          for (const t of data) {
            map.set(t.chatId, { createdAt: t.createdAt, title: t.title });
          }
          setTimestamps(map);
        }
      )
      .catch(() => setTimestamps(new Map()));
  }, []);

  // Join runtime threads with timestamps, preserving the runtime's index.
  const augmented = useMemo<AugmentedThread[]>(() => {
    if (!timestamps || !threadItems) return [];
    const result: AugmentedThread[] = [];
    threadItems.forEach((item, runtimeIndex) => {
      if (!item.remoteId) return;
      const meta = timestamps.get(item.remoteId);
      if (!meta) return;
      result.push({
        chatId: item.remoteId,
        title: meta.title,
        createdAt: meta.createdAt,
        runtimeIndex,
      });
    });
    return result;
  }, [threadItems, timestamps]);

  const weeks = useMemo(() => groupThreads(augmented), [augmented]);

  if (timestamps === null) {
    return (
      <div className="flex flex-col gap-1 p-3">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-9 animate-pulse rounded-lg bg-muted/50" />
        ))}
      </div>
    );
  }

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
