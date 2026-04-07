/**
 * ChatInfo — Chat ID display with hover card showing copyable IDs.
 *
 * Shows a truncated chat ID in the header. Hover opens a card with
 * the full chat ID and session UUID, each with a copy button.
 */

import { useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";

interface ChatInfoProps {
  chatId?: string;
  sessionUuid?: string;
}

function CopyButton({ text }: { text: string }) {
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
      onClick={handleCopy}
      className="inline-flex items-center justify-center size-5 rounded text-muted-foreground hover:text-foreground transition-colors"
      aria-label="Copy"
    >
      {copied ? <CheckIcon className="size-3" /> : <CopyIcon className="size-3" />}
    </button>
  );
}

export function ChatInfo({ chatId, sessionUuid }: ChatInfoProps) {
  if (!chatId) return null;

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <span className="cursor-default select-none font-mono text-xs text-muted-foreground">
          {chatId}
        </span>
      </HoverCardTrigger>
      <HoverCardContent side="bottom" align="start" className="w-auto min-w-[200px]">
        <div className="space-y-2">
          <div className="space-y-0.5">
            <span className="text-[10px] text-secondary tracking-wide">
              Chat Id
            </span>
            <div className="flex items-center gap-1.5">
              <code className="text-xs font-mono break-all">{chatId}</code>
              <CopyButton text={chatId} />
            </div>
          </div>

          {sessionUuid && (
            <div className="space-y-0.5">
              <span className="text-[10px] text-secondary tracking-wide">
                Session
              </span>
              <div className="flex items-center gap-1.5">
                <code className="text-xs font-mono break-all">{sessionUuid}</code>
                <CopyButton text={sessionUuid} />
              </div>
            </div>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
