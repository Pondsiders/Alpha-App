/**
 * MemoryStore — Card UI for mcp__alpha__store tool calls.
 *
 * Layout matches BashResult/EditResult: feather icon left, content middle,
 * dot right. Memory text in muted sans-serif, truncated to ~2 lines with
 * ellipsis, click to expand/collapse. Distinguishes click from text selection
 * so expanding to copy text works naturally.
 *
 * Route: tools.by_name["mcp__alpha__store"]
 */

import { useState, useRef } from "react";
import { Feather } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Approximate characters before truncation (2 lines at ~60 chars/line). */
const TRUNCATE_CHARS = 120;

export const MemoryStore: ToolCallMessagePartComponent = ({
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const clickStartRef = useRef<{ x: number; y: number } | null>(null);

  // Parse memory text from args
  let memoryText = "";
  let jsonComplete = false;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    memoryText = args.memory || "";
    jsonComplete = true;
  } catch {
    // Partial JSON while streaming
    memoryText = argsText || "";
  }

  // State detection
  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError =
    (status?.type === "incomplete" && status.reason === "error") ||
    (hasResult && typeof result === "string" && /error|fail/i.test(result));

  // Truncation
  const needsTruncation = memoryText.length > TRUNCATE_CHARS;
  const displayText =
    !expanded && needsTruncation
      ? memoryText.slice(0, TRUNCATE_CHARS).trimEnd()
      : memoryText;

  // Parse result
  const resultText = hasResult
    ? typeof result === "string"
      ? result
      : typeof result === "object" && result !== null && "content" in result
      ? (result as { content: Array<{ text?: string }> }).content
          ?.filter((c) => c.text)
          .map((c) => c.text)
          .join("\n") || JSON.stringify(result)
      : JSON.stringify(result)
    : "";

  // Click vs drag detection — only toggle on clean clicks, not text selection
  const handleMouseDown = (e: React.MouseEvent) => {
    clickStartRef.current = { x: e.clientX, y: e.clientY };
  };
  const handleMouseUp = (e: React.MouseEvent) => {
    if (!needsTruncation) return;
    const start = clickStartRef.current;
    if (!start) return;
    const dx = Math.abs(e.clientX - start.x);
    const dy = Math.abs(e.clientY - start.y);
    // If the mouse moved more than 4px, it's a drag/selection, not a click
    if (dx < 4 && dy < 4) {
      setExpanded(!expanded);
    }
    clickStartRef.current = null;
  };

  // Theme colors
  const dotColor = isStreaming || isRunning
    ? "var(--theme-primary)"
    : isError
    ? "var(--theme-error)"
    : "var(--theme-success)";

  const iconColor = isStreaming || isRunning
    ? "var(--theme-primary)"
    : isError
    ? "var(--theme-error)"
    : undefined;

  return (
    <div
      data-testid="memory-store"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-start gap-2 px-3 py-2.5 bg-surface">
        <Feather
          size={14}
          className="mt-[2px] shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-muted mb-0.5">Store</div>

          {memoryText && (
            <div
              className={`text-[13px] text-muted/70 leading-snug whitespace-pre-wrap break-words select-text ${
                needsTruncation ? "cursor-pointer" : ""
              }`}
              onMouseDown={needsTruncation ? handleMouseDown : undefined}
              onMouseUp={needsTruncation ? handleMouseUp : undefined}
            >
              {displayText}
              {!expanded && needsTruncation && (
                <span className="text-muted/40">...</span>
              )}
            </div>
          )}
        </div>
        <span
          className={`w-2 h-2 mt-[5px] rounded-full shrink-0 ${
            isStreaming || isRunning ? "animate-pulse-dot" : ""
          }`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Result */}
      {hasResult && (
        <div
          className={`px-3 py-2 border-t border-border bg-code-bg text-xs font-mono ${
            isError ? "text-error" : "text-muted/60"
          }`}
        >
          {resultText}
        </div>
      )}

      {/* Error without result */}
      {isError && !hasResult && (
        <div className="px-3 py-2 border-t border-border bg-error/10 text-error text-xs font-mono font-bold">
          STORE FAILED
        </div>
      )}
    </div>
  );
};
