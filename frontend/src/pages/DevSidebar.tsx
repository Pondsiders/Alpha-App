/**
 * DevSidebar — Three sidebar mockups using shadcn Sidebar components.
 * Real chat data, three labeling strategies side by side.
 */

import { useState } from "react";
import { Plus, ChevronDown, ChevronRight } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarProvider,
  SidebarInset,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// Real chat data (Apr 2 2026 snapshot)
const CHATS = [
  { id: "dKQZorU_0SdP", createdAt: 1775138174 },
  { id: "BFeY0ZWtT93L", createdAt: 1775134800 },
  { id: "HQSd21j-LBWR", createdAt: 1775086200 },
  { id: "aV1pO9g1Z69K", createdAt: 1774962000 },
  { id: "solitude", createdAt: 1774846800 },
  { id: "E8jSEVUwetkK", createdAt: 1774789200 },
  { id: "xzliXSkPUl63", createdAt: 1774702800 },
  { id: "PsWzH6FLHfOW", createdAt: 1774616400 },
  { id: "Z9gHJKF-dEUF", createdAt: 1774530000 },
  { id: "M9HpTbKO9kfl", createdAt: 1774448771 },
  { id: "P04SMcvcjWH0", createdAt: 1774359821 },
  { id: "SexpAQjOwf5c", createdAt: 1774303548 },
  { id: "MxzgDgLU_Nkq", createdAt: 1774275879 },
  { id: "U0w3T5kF5Y2n", createdAt: 1774207565 },
  { id: "LotovMaK65Q0", createdAt: 1774152479 },
  { id: "wFwZCO4x-dfh", createdAt: 1774102836 },
  { id: "3CLgAiB7kxO0", createdAt: 1774016877 },
  { id: "KOrvUA3dgfRl", createdAt: 1773930779 },
  { id: "XTnjK-rZz04d", createdAt: 1773845543 },
  { id: "EVxzZ8BOCJoE", createdAt: 1773757754 },
  { id: "WuVb-bCIXi9W", createdAt: 1773669415 },
  { id: "l53_2LwYvbw_", createdAt: 1773590380 },
  { id: "c7b902qw3WJb", createdAt: 1773604808 },
  { id: "F99W3T-YoicG", createdAt: 1773584171 },
  { id: "smIwv5_AtlFY", createdAt: 1773438562 },
  { id: "xpVfuEOIn8Qc", createdAt: 1773434120 },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const LA = "America/Los_Angeles";

function toLAWeekday(ts: number) {
  return new Date(ts * 1000).toLocaleDateString("en-US", { timeZone: LA, weekday: "long" });
}
function toLATime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString("en-US", { timeZone: LA, hour: "numeric", minute: "2-digit" });
}
function toPSO8601(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("en-US", {
    timeZone: LA, weekday: "short", month: "short", day: "numeric", year: "numeric",
  }) + ", " + d.toLocaleTimeString("en-US", { timeZone: LA, hour: "numeric", minute: "2-digit" });
}
function toPSO8601DateOnly(ts: number) {
  return new Date(ts * 1000).toLocaleDateString("en-US", {
    timeZone: LA, weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
}
function dayKey(ts: number) {
  return new Date(ts * 1000).toLocaleDateString("en-US", { timeZone: LA });
}

function relativeDay(ts: number, nowTs: number): string {
  const chatDate = new Date(ts * 1000).toLocaleDateString("en-US", { timeZone: LA });
  const todayDate = new Date(nowTs * 1000).toLocaleDateString("en-US", { timeZone: LA });
  const [cM, cD, cY] = chatDate.split("/").map(Number);
  const [tM, tD, tY] = todayDate.split("/").map(Number);
  const chat = new Date(cY, cM - 1, cD);
  const today = new Date(tY, tM - 1, tD);
  const diff = Math.round((today.getTime() - chat.getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return toLAWeekday(ts);
  if (diff < 14) return "Last " + toLAWeekday(ts);
  return toPSO8601DateOnly(ts);
}

function weekLabel(ts: number, now: number) {
  const getSunday = (t: number) => {
    const d = new Date(t * 1000);
    d.setDate(d.getDate() - d.getDay());
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  };
  const thisWeek = getSunday(now);
  const chatWeek = getSunday(ts);
  if (chatWeek === thisWeek) return "This Week";
  if (chatWeek === thisWeek - 7 * 86400000) return "Last Week";
  const sunday = new Date(chatWeek);
  const saturday = new Date(chatWeek + 6 * 86400000);
  const fmt = (d: Date) => d.toLocaleDateString("en-US", { timeZone: LA, month: "short", day: "numeric" });
  return `${fmt(sunday)} – ${fmt(saturday)}`;
}

// ---------------------------------------------------------------------------
// Data grouping
// ---------------------------------------------------------------------------

type ChatItem = typeof CHATS[0];
type Day = { key: string; weekday: string; items: ChatItem[] };
type Week = { label: string; days: Day[] };

const now = Date.now() / 1000;

function buildDays(): Day[] {
  const map: Record<string, ChatItem[]> = {};
  for (const c of CHATS) {
    const k = dayKey(c.createdAt);
    if (!map[k]) map[k] = [];
    map[k].push(c);
  }
  return Object.entries(map).map(([k, items]) => ({
    key: k,
    weekday: toLAWeekday(items[0].createdAt),
    items: items.sort((a, b) => b.createdAt - a.createdAt),
  }));
}

function buildWeeks(days: Day[]): Week[] {
  const map: Record<string, Day[]> = {};
  const order: string[] = [];
  for (const day of days) {
    const lbl = weekLabel(day.items[0].createdAt, now);
    if (!map[lbl]) { map[lbl] = []; order.push(lbl); }
    map[lbl].push(day);
  }
  return order.map((lbl) => ({ label: lbl, days: map[lbl] }));
}

const days = buildDays();
const weeks = buildWeeks(days);

// ---------------------------------------------------------------------------
// Shared: collapsible day item
// ---------------------------------------------------------------------------

function DayItem({
  day, label, active, setActive,
}: {
  day: Day; label: string; active: string | null; setActive: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  if (day.items.length === 1) {
    const c = day.items[0];
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={active === c.id}
              onClick={() => setActive(c.id)}
            >
              {label}
            </SidebarMenuButton>
          </SidebarMenuItem>
        </TooltipTrigger>
        <TooltipContent side="right">{toPSO8601(c.createdAt)}</TooltipContent>
      </Tooltip>
    );
  }

  // Multi-chat day
  const anyActive = day.items.some((c) => active === c.id);
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        isActive={anyActive}
        onClick={() => setOpen(!open)}
      >
        {label}
        {open
          ? <ChevronDown className="ml-auto size-3.5 text-muted-foreground" />
          : <ChevronRight className="ml-auto size-3.5 text-muted-foreground" />
        }
      </SidebarMenuButton>
      {open && (
        <SidebarMenuSub>
          {day.items.map((c) => (
            <Tooltip key={c.id}>
              <TooltipTrigger asChild>
                <SidebarMenuSubItem>
                  <SidebarMenuSubButton
                    isActive={active === c.id}
                    onClick={() => setActive(c.id)}
                  >
                    {toLATime(c.createdAt)}
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              </TooltipTrigger>
              <TooltipContent side="right">{toPSO8601(c.createdAt)}</TooltipContent>
            </Tooltip>
          ))}
        </SidebarMenuSub>
      )}
    </SidebarMenuItem>
  );
}

// ---------------------------------------------------------------------------
// 1. FLAT — PSO-8601
// ---------------------------------------------------------------------------

function FlatSidebar() {
  const [active, setActive] = useState<string | null>("dKQZorU_0SdP");
  return (
    <Sidebar collapsible="none" className="border-r">
      <SidebarHeader className="p-4">
        <span className="text-sm font-semibold">Flat — PSO-8601</span>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {CHATS.map((c) => (
                <Tooltip key={c.id}>
                  <TooltipTrigger asChild>
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        isActive={active === c.id}
                        onClick={() => setActive(c.id)}
                      >
                        <span className="truncate">{toPSO8601(c.createdAt)}</span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  </TooltipTrigger>
                  <TooltipContent side="right">{c.id}</TooltipContent>
                </Tooltip>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton className="text-muted-foreground">
              <Plus className="size-4" /> New Chat (dev)
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

// ---------------------------------------------------------------------------
// 2. GROUPED — week headers + weekday names
// ---------------------------------------------------------------------------

function GroupedSidebar() {
  const [active, setActive] = useState<string | null>("dKQZorU_0SdP");
  return (
    <Sidebar collapsible="none" className="border-r">
      <SidebarHeader className="p-4">
        <span className="text-sm font-semibold">Grouped — by week</span>
      </SidebarHeader>
      <SidebarContent>
        {weeks.map((week) => (
          <SidebarGroup key={week.label}>
            <SidebarGroupLabel>{week.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {week.days.map((day) => (
                  <DayItem
                    key={day.key}
                    day={day}
                    label={day.weekday}
                    active={active}
                    setActive={setActive}
                  />
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton className="text-muted-foreground">
              <Plus className="size-4" /> New Chat (dev)
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

// ---------------------------------------------------------------------------
// 3. RELATIVE — human-friendly day names, no headers
// ---------------------------------------------------------------------------

function RelativeSidebar() {
  const [active, setActive] = useState<string | null>("dKQZorU_0SdP");
  return (
    <Sidebar collapsible="none" className="border-r">
      <SidebarHeader className="p-4">
        <span className="text-sm font-semibold">Relative — human-friendly</span>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {days.map((day) => (
                <DayItem
                  key={day.key}
                  day={day}
                  label={relativeDay(day.items[0].createdAt, now)}
                  active={active}
                  setActive={setActive}
                />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton className="text-muted-foreground">
              <Plus className="size-4" /> New Chat (dev)
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DevSidebar() {
  return (
    <TooltipProvider>
      <SidebarProvider defaultOpen={true}>
        <div className="h-dvh bg-background text-foreground flex">
          <FlatSidebar />
          <GroupedSidebar />
          <RelativeSidebar />
          <SidebarInset className="flex-1 flex items-center justify-center">
            <p className="text-lg text-muted-foreground">← Click around. Which feels better?</p>
          </SidebarInset>
        </div>
      </SidebarProvider>
    </TooltipProvider>
  );
}
