/**
 * ContextMeter — Shows context window usage as a thin bar + percentage.
 *
 * Color thresholds:
 *   < 65%  muted  (comfortable)
 *   65–75% amber  (warming up)
 *   > 75%  red    (hot, compaction approaching at ~85%)
 *
 * Hover opens a card with model name and raw token counts.
 */

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";

interface ContextMeterProps {
  /** Context usage as a percentage (0–100). */
  percent: number;
  /** Current model name, e.g. "claude-opus-4-6" */
  model?: string | null;
  /** Raw token count for current context */
  tokenCount?: number;
  /** Max token limit for the model */
  tokenLimit?: number;
}

function getMeterColor(percent: number): string {
  if (percent >= 75) return "var(--destructive)";
  if (percent >= 65) return "var(--primary)";
  return "var(--muted-foreground)";
}

function formatTokens(count: number): string {
  return count.toLocaleString();
}

export function ContextMeter({ percent, model, tokenCount, tokenLimit }: ContextMeterProps) {
  const color = getMeterColor(percent);
  const display = percent.toFixed(1) + "%";

  const tokenDisplay =
    tokenLimit && tokenLimit > 0
      ? `${formatTokens(tokenCount ?? 0)} / ${formatTokens(tokenLimit)}`
      : null;

  return (
    <HoverCard openDelay={200} closeDelay={100}>
      <HoverCardTrigger asChild>
        <div className="inline-flex items-center gap-2 cursor-default select-none">
          {/* Progress bar track */}
          <div
            className="w-20 h-1 rounded-full overflow-hidden"
            style={{ backgroundColor: "var(--border)" }}
          >
            <div
              className="h-full rounded-full transition-all duration-500 ease-out"
              style={{
                width: `${Math.min(percent, 100)}%`,
                backgroundColor: color,
              }}
            />
          </div>

          {/* Percentage readout */}
          <span
            className="font-mono text-[11px] tabular-nums transition-colors duration-500"
            style={{ color }}
          >
            {display}
          </span>
        </div>
      </HoverCardTrigger>
      <HoverCardContent side="top" align="end" className="w-auto min-w-[160px]">
        <div className="space-y-1.5">
          {/* Context percentage */}
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted-foreground shrink-0">Context</span>
            <span className="text-[11px] font-mono" style={{ color }}>
              {display}
            </span>
          </div>

          {/* Token usage */}
          {tokenDisplay && (
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[11px] text-muted-foreground shrink-0">Tokens</span>
              <span className="text-[11px] font-mono text-muted-foreground">
                {tokenDisplay}
              </span>
            </div>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
