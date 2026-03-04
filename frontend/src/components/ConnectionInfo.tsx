/**
 * ConnectionInfo — Health card that appears on hover over the connection dot.
 *
 * Shows at a glance:
 *   - Connection status (human-readable)
 *   - Model name
 *   - Chat ID
 *   - Token usage (raw numbers behind the ContextMeter percentage)
 *
 * Uses Radix HoverCard for hover-triggered display with nice open/close delays.
 */

import { type ReactNode } from "react";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";

export interface ConnectionInfoProps {
  /** The connection dot element */
  children: ReactNode;
  /** Whether WebSocket is connected */
  connected: boolean;
  /** Whether the active chat is busy */
  isRunning: boolean;
  /** Active chat ID, or null */
  chatId: string | null;
  /** Current model name, e.g. "claude-opus-4-6" */
  model: string | null;
  /** Raw token count for current context */
  tokenCount: number;
  /** Max token limit for the model */
  tokenLimit: number;
}

function getStatusLabel(
  connected: boolean,
  isRunning: boolean
): { text: string; color: string } {
  if (!connected) return { text: "Disconnected", color: "var(--theme-muted)" };
  if (isRunning) return { text: "Connected \u00b7 Streaming", color: "var(--theme-success)" };
  return { text: "Connected \u00b7 Idle", color: "var(--theme-primary)" };
}

function formatTokens(count: number): string {
  if (count === 0) return "0";
  if (count >= 1_000_000) return (count / 1_000_000).toFixed(1) + "M";
  if (count >= 1_000) return (count / 1_000).toFixed(1) + "k";
  return count.toLocaleString();
}

/** Row in the info card — label on left, value on right, value wraps at hyphens. */
function InfoRow({
  label,
  value,
  color,
  mono,
}: {
  label: string;
  value: string;
  color?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-muted shrink-0">{label}</span>
      <span
        className={`text-[11px] text-right ${mono ? "font-mono" : ""}`}
        style={{
          ...(color ? { color } : {}),
          overflowWrap: "anywhere",
        }}
      >
        {value}
      </span>
    </div>
  );
}

export function ConnectionInfo({
  children,
  connected,
  isRunning,
  chatId,
  model,
  tokenCount,
  tokenLimit,
}: ConnectionInfoProps) {
  const status = getStatusLabel(connected, isRunning);
  const tokenDisplay =
    tokenLimit > 0
      ? `${formatTokens(tokenCount)} / ${formatTokens(tokenLimit)}`
      : "\u2014";

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>{children}</HoverCardTrigger>
      <HoverCardContent side="bottom" align="start">
        <div className="space-y-2">
          {/* Status */}
          <InfoRow label="Status" value={status.text} color={status.color} />

          {/* Model */}
          <InfoRow
            label="Model"
            value={model ?? "\u2014"}
            mono
          />

          {/* Chat ID */}
          <InfoRow
            label="Chat"
            value={chatId ?? "\u2014"}
            mono
          />

          {/* Token usage */}
          <InfoRow
            label="Tokens"
            value={tokenDisplay}
            mono
          />
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
