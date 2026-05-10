/**
 * App — Main layout using shadcn Sidebar + assistant-ui Thread.
 *
 * Uses the real shadcn SidebarProvider for collapsible sidebar with
 * keyboard shortcut (Ctrl+B), mobile sheet, cookie persistence.
 * Thread fills the main content area.
 */

import { useState } from "react";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { Thread } from "@/components/assistant-ui/thread";
import { GroupedThreadList } from "@/components/grouped-thread-list";
import { ContextRing, contextColorClass } from "@/components/ContextRing";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarProvider,
  SidebarTrigger,
  SidebarInset,
} from "@/components/ui/sidebar";
import { RuntimeProvider } from "./RuntimeProvider";
import { useAlphaWebSocket } from "@/hooks/useAlphaWebSocket";
import { useStore } from "@/store";
import { Plus } from "lucide-react";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { Commands } from "@/lib/protocol";

// -- New Chat Button ----------------------------------------------------------

function NewChatButton() {
  const wsSend = useStore((s) => s.wsSend);
  const connected = useStore((s) => s.connected);

  return (
    <SidebarMenuButton
      className="w-full cursor-pointer text-muted-foreground hover:text-foreground"
      disabled={!connected || !wsSend}
      onClick={() => wsSend?.(Commands.createChat())}
    >
      <Plus className="size-4" />
      <span>New Chat</span>
    </SidebarMenuButton>
  );
}

// -- App Sidebar --------------------------------------------------------------

function AppSidebar() {
  return (
    <Sidebar>
      <SidebarHeader className="h-14 !flex-row !items-center justify-start px-4">
        <div className="flex items-center gap-2 font-medium text-sm">
          <Avatar className="size-7">
            <AvatarImage src="/alpha.png" alt="Alpha" />
            <AvatarFallback>🦆</AvatarFallback>
          </Avatar>
          <span className="text-foreground/90">Alpha</span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <GroupedThreadList />
      </SidebarContent>
      <SidebarFooter className="p-2">
        <NewChatButton />
      </SidebarFooter>
    </Sidebar>
  );
}

// -- Floating Context Ring ----------------------------------------------------
//
// Sits in the top-right corner of the chat area. Shows context usage as
// a Lucide-styled progress ring. HoverCard opens with the full details
// (model, tokens, percentage) for when you need the numbers.
//
// Threshold treatment:
//   <80%  — neutral tile (bg-card), ring is muted, hover brightens to white
//   80-90% — amber tile (bg-primary/15), ring is amber, hover saturates
//   >=90% — red tile (bg-destructive/15), ring is red, hover saturates
// In warning states, the tile itself carries the signal color so the ring
// can keep its threshold color on hover instead of losing it.

function contextTileClasses(percent: number): string {
  if (percent >= 90) {
    return "bg-destructive/15 text-destructive hover:bg-destructive/25";
  }
  if (percent >= 80) {
    return "bg-primary/15 text-primary hover:bg-primary/25";
  }
  return "bg-card text-muted-foreground hover:bg-muted hover:text-foreground";
}

// Shared tile class suffix for both floating buttons.
const FLOATING_TILE = "size-9 rounded-lg shadow-md transition-colors";

function FloatingContextRing() {
  // Mock data — will come from the runtime/store when backend is connected
  const mockPercent = 48.3;
  const mockTokenCount = 483_000;
  const mockTokenLimit = 1_000_000;
  const [open, setOpen] = useState(false);

  const display = mockPercent.toFixed(1) + "%";
  const tokenDisplay =
    `${mockTokenCount.toLocaleString()} / ${mockTokenLimit.toLocaleString()}`;

  return (
    <HoverCard openDelay={200} closeDelay={100} open={open} onOpenChange={setOpen}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className={`flex items-center justify-center ${FLOATING_TILE} ${contextTileClasses(mockPercent)}`}
          aria-label={`Context ${display}`}
          onClick={() => setOpen((o) => !o)}
        >
          <ContextRing percent={mockPercent} className="size-4" />
        </button>
      </HoverCardTrigger>
      <HoverCardContent side="bottom" align="end" className="w-auto min-w-[180px]">
        <div className="space-y-2">
          <div className="space-y-0.5">
            <span className="text-[10px] text-muted-foreground tracking-wide">Context</span>
            <div className={`text-xs tabular-nums ${contextColorClass(mockPercent)}`}>
              {display}
            </div>
          </div>
          <div className="space-y-0.5">
            <span className="text-[10px] text-muted-foreground tracking-wide">Tokens</span>
            <div className="text-xs tabular-nums text-foreground">{tokenDisplay}</div>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}

// -- App ----------------------------------------------------------------------

export default function App() {
  // Open the WebSocket on mount and wire events to the Zustand store.
  // Must run inside a component (it uses hooks). One call, everything
  // else takes care of itself.
  useAlphaWebSocket();

  const [sidebarOpen, setSidebarOpen] = useState(() => {
    try { return localStorage.getItem("alpha-sidebarOpen") !== "false"; } catch { return true; }
  });

  return (
    <RuntimeProvider>
      <TooltipProvider>
        <SidebarProvider
          open={sidebarOpen}
          onOpenChange={(open) => { setSidebarOpen(open); try { localStorage.setItem("alpha-sidebarOpen", String(open)); } catch { /* noop */ } }}
        >
          <AppSidebar />
          <SidebarInset
            style={{
              ["--thread-max-width" as string]: "44rem",
              ["--composer-radius" as string]: "24px",
              ["--composer-padding" as string]: "10px",
            }}
          >
            <main className="relative flex-1 overflow-hidden">
              {/* Floating sidebar trigger — top-left corner, over the chat */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <SidebarTrigger className={`absolute left-3 top-3 z-20 ${FLOATING_TILE} bg-card text-muted-foreground hover:bg-muted hover:text-foreground dark:hover:bg-muted`} />
                </TooltipTrigger>
                <TooltipContent side="bottom">Toggle sidebar</TooltipContent>
              </Tooltip>
              {/* Floating context ring — top-right corner, with hover details */}
              <div className="absolute right-3 top-3 z-20">
                <FloatingContextRing />
              </div>
              <Thread />
            </main>
          </SidebarInset>
        </SidebarProvider>
      </TooltipProvider>
    </RuntimeProvider>
  );
}
