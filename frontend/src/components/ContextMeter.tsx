/**
 * ContextMeter — Shows context window usage as a thin bar + percentage.
 *
 * Color thresholds:
 *   < 65%  muted  (comfortable)
 *   65–75% amber  (warming up)
 *   > 75%  red    (hot, compaction approaching at ~85%)
 *
 * Click to copy the percentage string to clipboard.
 * Shows a tooltip "Copied!" on click instead of swapping the text.
 */

import { useState, useCallback } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface ContextMeterProps {
  /** Context usage as a percentage (0–100). */
  percent: number;
}

function getMeterColor(percent: number): string {
  if (percent >= 75) return "var(--theme-error)";
  if (percent >= 65) return "var(--theme-primary)";
  return "var(--theme-muted)";
}

export function ContextMeter({ percent }: ContextMeterProps) {
  const [copied, setCopied] = useState(false);
  const color = getMeterColor(percent);
  const display = percent.toFixed(1) + "%";

  const handleClick = useCallback(async () => {
    await navigator.clipboard.writeText(display);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [display]);

  return (
    <TooltipProvider>
      <Tooltip open={copied}>
        <TooltipTrigger asChild>
          <div
            className="inline-flex items-center gap-2 cursor-pointer select-none"
            onClick={handleClick}
          >
            {/* Progress bar track */}
            <div
              className="w-20 h-1 rounded-full overflow-hidden"
              style={{ backgroundColor: "var(--theme-border)" }}
            >
              <div
                className="h-full rounded-full transition-all duration-500 ease-out"
                style={{
                  width: `${Math.min(percent, 100)}%`,
                  backgroundColor: color,
                }}
              />
            </div>

            {/* Percentage readout — always shows the number, never swaps text */}
            <span
              className="font-mono text-[11px] tabular-nums transition-colors duration-500"
              style={{ color }}
            >
              {display}
            </span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="top">Copied!</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
