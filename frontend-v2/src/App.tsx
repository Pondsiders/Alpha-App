/**
 * App — Main layout using shadcn Sidebar + assistant-ui Thread.
 *
 * Uses the real shadcn SidebarProvider for collapsible sidebar with
 * keyboard shortcut (Ctrl+B), mobile sheet, cookie persistence.
 * Thread fills the main content area.
 */

import { Thread } from "@/components/assistant-ui/thread";
import { ThreadListNew } from "@/components/assistant-ui/thread-list";
import { GroupedThreadList } from "@/components/grouped-thread-list";
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

// -- App Sidebar --------------------------------------------------------------

function AppSidebar() {
  return (
    <Sidebar>
      <SidebarHeader className="h-14 !flex-row !items-center justify-start px-4">
        <div className="flex items-center gap-2 font-medium text-sm">
          <span className="text-xl">🦆</span>
          <span className="text-foreground/90">Alpha</span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <GroupedThreadList />
      </SidebarContent>
      <SidebarFooter className="p-3">
        <ThreadListNew />
      </SidebarFooter>
    </Sidebar>
  );
}

// -- Header -------------------------------------------------------------------

function Header() {
  return (
    <header className="flex h-14 shrink-0 items-center gap-2 px-4">
      <SidebarTrigger className="size-9" />
      <div className="ml-auto text-muted-foreground text-xs">
        Alpha · Opus 4.6
      </div>
    </header>
  );
}

// -- App ----------------------------------------------------------------------

export default function App() {
  return (
    <RuntimeProvider>
      <TooltipProvider>
        <SidebarProvider>
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
