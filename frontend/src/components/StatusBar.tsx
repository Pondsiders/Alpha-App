/**
 * StatusBar — Slim bar at the top of the chat area.
 *
 * Left:  Connection indicator (dot, hover for health popover) + session ID (click to copy)
 * Right: ContextMeter
 *
 * Connection dot states:
 *   Gray     no session (disconnected)
 *   Amber    session active, idle
 *   Green    API request in flight (pulses)
 */

import { useState, useCallback } from "react";
import { PanelLeft } from "lucide-react";
import { ContextMeter } from "@/components/ContextMeter";
import { ConnectionInfo } from "@/components/ConnectionInfo";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useSidebar } from "@/components/ui/sidebar";
import { useWorkshopStore } from "@/store";

function getConnectionColor(
  sessionId: string | null,
  isRunning: boolean
): string {
  if (isRunning) return "var(--theme-success)";   // streaming → green, always
  if (!sessionId) return "var(--theme-muted)";     // no session, idle → gray
  return "var(--theme-primary)";                   // session active, idle → amber
}

export function StatusBar() {
  const { toggleSidebar } = useSidebar();

  const sessionId = useWorkshopStore((s) => s.sessionId);
  const isRunning = useWorkshopStore((s) => s.isRunning);
  const contextPercent = useWorkshopStore((s) => s.contextPercent);
  const model = useWorkshopStore((s) => s.model);
  const tokenCount = useWorkshopStore((s) => s.tokenCount);
  const tokenLimit = useWorkshopStore((s) => s.tokenLimit);

  const [copied, setCopied] = useState(false);

  const dotColor = getConnectionColor(sessionId, isRunning);
  const shortId = sessionId ? sessionId.slice(0, 8) : null;

  const handleCopySession = useCallback(async () => {
    if (!sessionId) return;
    await navigator.clipboard.writeText(sessionId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [sessionId]);

  return (
    <div className="flex items-center justify-between px-4 h-12 bg-surface/50 border-b border-border shrink-0">
      {/* Left: sidebar toggle + connection dot (hover → health popover) + session ID (click → copy) */}
      <div className="flex items-center gap-2.5">
        {/* Sidebar toggle */}
        <button
          onClick={toggleSidebar}
          className="inline-flex items-center justify-center w-5 h-5 rounded cursor-pointer bg-transparent border-none text-muted hover:text-text hover:bg-background/50 transition-colors"
          aria-label="Toggle sidebar"
        >
          <PanelLeft size={14} />
        </button>

        {/* Connection dot — hover opens health popover */}
        <ConnectionInfo
          sessionId={sessionId}
          isRunning={isRunning}
          model={model}
          tokenCount={tokenCount}
          tokenLimit={tokenLimit}
        >
          <button
            className="inline-flex items-center justify-center w-5 h-5 rounded cursor-pointer bg-transparent border-none hover:bg-background/50 transition-colors"
            aria-label="Connection info"
          >
            <span
              className={`inline-block w-[7px] h-[7px] rounded-full transition-colors duration-300 ${
                isRunning ? "animate-pulse-dot" : ""
              }`}
              style={{ backgroundColor: dotColor }}
            />
          </button>
        </ConnectionInfo>

        {/* Session ID — click to copy full UUID */}
        {shortId && (
          <TooltipProvider>
            <Tooltip open={copied}>
              <TooltipTrigger asChild>
                <span
                  className="font-mono text-[11px] text-muted cursor-pointer select-none hover:text-text transition-colors"
                  onClick={handleCopySession}
                >
                  {shortId}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">Copied!</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </div>

      {/* Right: context meter */}
      <ContextMeter percent={contextPercent} />
    </div>
  );
}
