/**
 * StatusBar — Slim bar at the top of the chat area.
 *
 * Left:  Sidebar toggle + chat nanoid (click for ID popover)
 * Right: ContextMeter
 *
 * The nanoid is clickable and opens a popover showing:
 *   - Chat ID (nanoid) with copy button
 *   - Session UUID (claude's --resume key) with copy button
 */

import { useState, useCallback } from "react";
import { PanelLeft, Copy, Check, Feather } from "lucide-react";
import { ContextMeter } from "@/components/ContextMeter";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import { useSidebar } from "@/components/ui/sidebar";
import { useWorkshopStore } from "@/store";

/** Tiny copy button — shows a check mark for 1.5s after copying. */
function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [value]);

  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center justify-center w-5 h-5 rounded cursor-pointer bg-transparent border-none text-muted hover:text-text transition-colors shrink-0 outline-none focus:outline-none"
      aria-label="Copy to clipboard"
      tabIndex={-1}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  );
}

export function StatusBar() {
  const { toggleSidebar } = useSidebar();

  const activeChatId = useWorkshopStore((s) => s.activeChatId);
  const activeChat = useWorkshopStore((s) =>
    s.activeChatId ? s.chats[s.activeChatId] : null
  );
  const contextPercent = useWorkshopStore((s) => s.contextPercent);
  const model = useWorkshopStore((s) => s.model);
  const tokenCount = useWorkshopStore((s) => s.tokenCount);
  const tokenLimit = useWorkshopStore((s) => s.tokenLimit);

  const sessionUuid = activeChat?.sessionUuid;
  const isWorking = activeChat?.state === "busy" || activeChat?.state === "starting";

  return (
    <div className="flex items-center justify-between px-4 h-12 bg-surface/50 border-b border-border shrink-0">
      {/* Left: sidebar toggle + status lights + chat ID */}
      <div className="flex items-center gap-2.5">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={toggleSidebar}
              className="inline-flex items-center justify-center w-5 h-5 rounded cursor-pointer bg-transparent border-none text-muted hover:text-text hover:bg-background/50 transition-colors"
              aria-label="Toggle sidebar"
            >
              <PanelLeft size={14} />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Toggle sidebar</TooltipContent>
        </Tooltip>

        {/* Status feather — animated when working, still when idle */}
        {activeChatId && (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center justify-center w-5 h-5">
                <Feather
                  size={14}
                  className={
                    isWorking
                      ? "text-[#7A8C42] animate-[quill-write_2s_ease-in-out_infinite]"
                      : "text-muted/30 transition-colors duration-500"
                  }
                />
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {isWorking ? "Alpha is working" : "Idle"}
            </TooltipContent>
          </Tooltip>
        )}

        {activeChatId && (
          <Popover>
            <PopoverTrigger asChild>
              <span
                className="font-mono text-[11px] text-muted cursor-pointer select-none hover:text-text transition-colors"
              >
                {activeChatId}
              </span>
            </PopoverTrigger>
            <PopoverContent side="bottom" align="start" className="w-auto min-w-[200px]">
              <div className="space-y-3">
                {/* Chat ID */}
                <div>
                  <div className="text-[11px] text-muted mb-1">Chat ID</div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-mono break-all">{activeChatId}</span>
                    <CopyButton value={activeChatId} />
                  </div>
                </div>

                {/* Session UUID */}
                <div>
                  <div className="text-[11px] text-muted mb-1">Session</div>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[11px] font-mono break-all">
                      {sessionUuid || "\u2014"}
                    </span>
                    {sessionUuid ? <CopyButton value={sessionUuid} /> : <span className="w-5 shrink-0" />}
                  </div>
                </div>
              </div>
            </PopoverContent>
          </Popover>
        )}
      </div>

      {/* Right: context meter (hover for token details) */}
      <ContextMeter
        percent={contextPercent}
        model={model}
        tokenCount={tokenCount}
        tokenLimit={tokenLimit}
      />
    </div>
  );
}
