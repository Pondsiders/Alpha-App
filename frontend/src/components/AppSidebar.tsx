/**
 * AppSidebar — Session list sidebar using shadcn/ui Sidebar.
 *
 * Shows a "New chat" button in the header, session list in the content,
 * and a rail for edge-hover toggling.
 */

import { useState, useEffect, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus, MessageSquare } from "lucide-react";

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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Session = {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return "yesterday";
  return `${diffDays}d ago`;
}

// ---------------------------------------------------------------------------
// AppSidebar
// ---------------------------------------------------------------------------

interface AppSidebarProps {
  onChatKey: () => void;
  refreshRef: React.MutableRefObject<(() => void) | null>;
}

export function AppSidebar({ onChatKey, refreshRef }: AppSidebarProps) {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const { setOpenMobile, isMobile } = useSidebar();

  const [sessions, setSessions] = useState<Session[]>([]);

  const refreshSessions = useCallback(() => {
    fetch("/api/sessions")
      .then((r) => {
        if (!r.ok) throw new Error("No backend");
        return r.json();
      })
      .then(setSessions)
      .catch(() => {
        // No backend — show empty list
        setSessions([]);
      });
  }, []);

  // Expose refresh function via ref so ChatPage can call it
  useEffect(() => {
    refreshRef.current = refreshSessions;
    return () => {
      refreshRef.current = null;
    };
  }, [refreshSessions, refreshRef]);

  // Fetch sessions on mount and when sessionId changes
  useEffect(() => {
    refreshSessions();
  }, [sessionId, refreshSessions]);

  const handleSessionClick = useCallback(
    (id: string) => {
      onChatKey();
      navigate(`/chat/${id}`);
      if (isMobile) setOpenMobile(false);
    },
    [navigate, isMobile, setOpenMobile, onChatKey]
  );

  const handleNewChat = useCallback(() => {
    onChatKey();
    navigate("/chat");
    if (isMobile) setOpenMobile(false);
  }, [navigate, isMobile, setOpenMobile, onChatKey]);

  return (
    <Sidebar side="left" variant="sidebar" collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={handleNewChat} tooltip="New chat">
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
              {sessions.map((s) => (
                <SidebarMenuItem key={s.session_id}>
                  <SidebarMenuButton
                    onClick={() => handleSessionClick(s.session_id)}
                    isActive={sessionId === s.session_id}
                    tooltip={s.title || "New chat"}
                  >
                    <MessageSquare size={14} className="shrink-0 opacity-50" />
                    <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
                      {s.title || "New chat"}
                    </span>
                    <span className="text-xs text-muted/50 shrink-0 group-data-[collapsible=icon]:hidden">
                      {formatRelative(s.updated_at)}
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
