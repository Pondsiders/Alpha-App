/**
 * ChatHoverCard — Hover-activated details card for a chat item.
 *
 * Wraps a trigger element (usually a sidebar menu button) and reveals
 * a card with:
 *   - PSO-8601 creation timestamp
 *   - Chat ID with copy button
 *   - Session UUID with copy button (if present)
 *
 * Replaces the old plain-text PSO-8601 Tooltip in the sidebar. Each
 * chat item's passport is now one hover away — disaster-recovery
 * access for the session UUID, and eventually room for a title or
 * summary when we add one.
 *
 * Also exports CopyButton as a reusable component.
 */

import { useState, type ReactNode } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import type { Chat } from "@/store";

// ---------------------------------------------------------------------------
// CopyButton — small 5x5 icon button that copies text and briefly shows a check
// ---------------------------------------------------------------------------

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API requires HTTPS — ensure Vite serves over TLS
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex size-5 items-center justify-center rounded text-muted-foreground transition-colors hover:text-foreground"
      aria-label="Copy"
    >
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
    </button>
  );
}

// ---------------------------------------------------------------------------
// PSO-8601 helper (local to this file — matches grouped-thread-list's format)
// ---------------------------------------------------------------------------

const LA = "America/Los_Angeles";

function toPSO8601(iso: string): string {
  const d = new Date(iso);
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

// ---------------------------------------------------------------------------
// ChatHoverCard — the main export
// ---------------------------------------------------------------------------

interface ChatHoverCardProps {
  chat: Chat;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
}

export function ChatHoverCard({
  chat,
  children,
  side = "right",
  align = "start",
}: ChatHoverCardProps) {
  return (
    <HoverCard openDelay={0} closeDelay={300}>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent
        side={side}
        align={align}
        className="w-auto min-w-[260px] p-2"
      >
        <div className="space-y-2">
          <div className="space-y-0.5">
            <div className="text-[10px] leading-none text-muted-foreground tracking-wide">
              Created
            </div>
            <div className="text-xs tabular-nums text-foreground">
              {toPSO8601(chat.createdAt)}
            </div>
          </div>

          <div className="space-y-0.5">
            <div className="text-[10px] leading-none text-muted-foreground tracking-wide">
              Chat Id
            </div>
            <div className="flex items-center gap-1.5">
              <code className="break-all text-xs text-foreground">
                {chat.chatId}
              </code>
              <CopyButton text={chat.chatId} />
            </div>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
