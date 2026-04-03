/**
 * GroupedThreadList — Week-grouped, day-collapsible sidebar thread list.
 *
 * Fetches thread data from /api/threads and renders it grouped by week,
 * then by circadian day (6 AM to 6 AM). Uses ThreadListItemPrimitive
 * from assistant-ui for individual items (active state + switching for free).
 *
 * Single-thread days render as a plain day-name item.
 * Multi-thread days render as a collapsible with sub-items.
 */

import { useEffect, useState, useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  ThreadListItemPrimitive,
  ThreadListPrimitive,
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

interface ThreadData {
  chatId: string;
  title?: string;
  createdAt: number; // unix timestamp
  updatedAt: number;
}

interface DayBucket {
  dayLabel: string; // "Friday", "Thursday", etc.
  dateLabel: string; // PSO-8601 date for tooltip
  threads: ThreadData[];
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
  // Convert to LA time string, parse back
  const laStr = d.toLocaleString("en-US", { timeZone: LA });
  const la = new Date(laStr);
  // If before 6 AM, the circadian day started yesterday
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
  // "Mar 15–21" format
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

function groupThreads(threads: ThreadData[]): WeekBucket[] {
  const now = new Date();

  // Bucket by circadian day
  const dayMap = new Map<string, { circadianStart: Date; threads: ThreadData[] }>();
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
  const weekMap = new Map<string, { weekStart: Date; days: typeof sortedDays }>();
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

/** Format creation time as PSO-8601 time only: "7:09 AM" */
function toTimeOnly(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("en-US", {
    timeZone: LA,
    hour: "numeric",
    minute: "2-digit",
  });
}

function ThreadItem({ thread }: { thread: ThreadData }) {
  return (
    <ThreadListItemPrimitive.Root threadId={thread.chatId}>
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

function DayItem({ day }: { day: DayBucket }) {
  const [open, setOpen] = useState(false);

  // Single thread — just show the day name, clicking switches to that thread
  if (day.threads.length === 1) {
    return (
      <SidebarMenuItem>
        <ThreadListItemPrimitive.Root
          threadId={day.threads[0].chatId}
          className="w-full"
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <ThreadListItemPrimitive.Trigger asChild>
                <SidebarMenuButton className="w-full">
                  {day.dayLabel}
                </SidebarMenuButton>
              </ThreadListItemPrimitive.Trigger>
            </TooltipTrigger>
            <TooltipContent side="right">
              {toPSO8601(day.threads[0].createdAt)}
            </TooltipContent>
          </Tooltip>
        </ThreadListItemPrimitive.Root>
      </SidebarMenuItem>
    );
  }

  // Multiple threads — collapsible
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
                <ThreadItem thread={t} />
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

export function GroupedThreadList() {
  const [threads, setThreads] = useState<ThreadData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/threads")
      .then((r) => r.json())
      .then((data) => {
        setThreads(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const weeks = useMemo(() => groupThreads(threads), [threads]);

  if (loading) {
    return (
      <div className="flex flex-col gap-1 p-3">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-9 animate-pulse rounded-lg bg-muted/50" />
        ))}
      </div>
    );
  }

  return (
    <ThreadListPrimitive.Root className="flex flex-col">
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
    </ThreadListPrimitive.Root>
  );
}
