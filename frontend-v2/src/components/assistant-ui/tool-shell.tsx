/**
 * ToolShell — Shared layout for all tool components.
 *
 * Three-band layout:
 *   Header:  [icon  Tool Name] ··· [chevron] [●]    bg-muted/60
 *   Input:   compact or expanded ReactNode           bg-card
 *   Output:  compact or expanded ReactNode           bg-card
 *
 * Compact by default — fixed height, zero layout reflow.
 * Click header to expand. Each tool passes its own content
 * into the four slots (input, expandedInput, output, expandedOutput).
 *
 * Composition over inheritance: the shell handles chrome,
 * the consumer handles content.
 */

import { useState, type FC, type ReactNode } from "react";
import { ChevronDown, ChevronUp, type LucideIcon } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export interface ToolShellProps {
  /** Lucide icon component (Terminal, FileText, Wrench, etc.) */
  icon: LucideIcon;
  /** Display name shown in header and tooltip */
  name: string;
  /** Tool status */
  status: "streaming" | "running" | "success" | "error";

  /** Compact input (1 line) */
  input?: ReactNode;
  /** Expanded input (full content) */
  expandedInput?: ReactNode;
  /** Compact output (1 line) */
  output?: ReactNode;
  /** Expanded output (full content) */
  expandedOutput?: ReactNode;

  /** Start expanded? Default false. */
  defaultExpanded?: boolean;
}

const STATUS_COLORS = {
  streaming: "var(--color-primary)",
  running: "var(--color-primary)",
  success: "var(--color-success)",
  error: "var(--color-destructive)",
} as const;

export const ToolShell: FC<ToolShellProps> = ({
  icon: Icon,
  name,
  status,
  input,
  expandedInput,
  output,
  expandedOutput,
  defaultExpanded = false,
}) => {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const dotColor = STATUS_COLORS[status];
  const iconColor = dotColor; // Icon always matches the status dot
  const isActive = status === "streaming" || status === "running";

  // Can expand if there's expanded content that differs from compact
  const canExpand = !!(expandedInput || expandedOutput);

  return (
    <div
      data-testid="tool-call"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* ── Band 1: Header ── */}
      <div
        className={`flex items-center gap-2 px-3 py-1.5 bg-muted/60 select-none ${
          canExpand ? "cursor-pointer" : ""
        }`}
        onClick={() => canExpand && setExpanded(!expanded)}
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="shrink-0 flex items-center">
              <Icon
                size={14}
                className="text-foreground"
                style={iconColor ? { color: iconColor } : undefined}
              />
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            {name}
          </TooltipContent>
        </Tooltip>
        <span className="text-[12px] text-foreground truncate">
          {name}
        </span>
        <div className="flex-1" />
        {canExpand && (
          expanded
            ? <ChevronUp size={12} className="text-muted-foreground shrink-0" />
            : <ChevronDown size={12} className="text-muted-foreground shrink-0" />
        )}
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isActive ? "animate-pulse" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* ── Band 2: Input ── */}
      {(input || expandedInput) && (
        <div className="border-t border-border bg-card px-3 py-1.5">
          {expanded && expandedInput ? expandedInput : input}
        </div>
      )}

      {/* ── Band 3: Output ── */}
      {(output || expandedOutput) && (
        <div className="border-t border-border bg-card px-3 py-1.5">
          {expanded && expandedOutput ? expandedOutput : output}
        </div>
      )}
    </div>
  );
};
