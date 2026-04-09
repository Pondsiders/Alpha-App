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
import { ChatInfo } from "@/components/ChatInfo";
import { ContextMeter } from "@/components/ContextMeter";
import { TooltipProvider } from "@/components/ui/tooltip";
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

// -- New Chat Button ----------------------------------------------------------

function NewChatButton() {
  const wsSend = useStore((s) => s.wsSend);
  const connected = useStore((s) => s.connected);

  return (
    <SidebarMenuButton
      className="w-full cursor-pointer text-muted-foreground hover:text-foreground"
      disabled={!connected || !wsSend}
      onClick={() => wsSend?.({ command: "create-chat" })}
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

// -- Header -------------------------------------------------------------------

function Header() {
  // Mock data — will come from the runtime/store when backend is connected
  const mockChatId = "eG1S5JgkBShM";
  const mockSessionUuid = "23c558d0-ca83-480b-93ee-e2f9f6ab5ddb";
  const mockPercent = 15.9;
  const mockTokenCount = 159_000;
  const mockTokenLimit = 1_000_000;

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 bg-sidebar px-4 border-b border-sidebar-border">
      <SidebarTrigger className="size-9 text-muted-foreground hover:text-foreground transition-colors" />
      <ChatInfo chatId={mockChatId} sessionUuid={mockSessionUuid} />
      <div className="ml-auto">
        <ContextMeter
          percent={mockPercent}
          model="claude-opus-4-6"
          tokenCount={mockTokenCount}
          tokenLimit={mockTokenLimit}
        />
      </div>
    </header>
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
          <SidebarInset>
            <Header />
            <main className="flex-1 overflow-hidden">
              <Thread />
            </main>
          </SidebarInset>
        </SidebarProvider>
      </TooltipProvider>
    </RuntimeProvider>
  );
}
