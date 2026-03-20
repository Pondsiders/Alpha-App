/**
 * MemoryStore — Card UI for mcp__alpha__store tool calls.
 *
 * Feather icon, "Store" header, memory text with expand/collapse.
 * Framer Motion animates the container height. That is all.
 */

import { useState, useRef } from "react";
import { Feather, ChevronDown } from "lucide-react";
import { motion } from "framer-motion";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Collapsed height in pixels — approximately 2 lines of 13px text. */
const COLLAPSED_PX = 36;

export const MemoryStore: ToolCallMessagePartComponent = ({
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const clickStartRef = useRef<{ x: number; y: number } | null>(null);

  // Parse memory text
  let memoryText = "";
  let jsonComplete = false;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    memoryText = args.memory || "";
    jsonComplete = true;
  } catch {
    memoryText = argsText || "";
  }

  // State
  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError =
    (status?.type === "incomplete" && status.reason === "error") ||
    (hasResult && typeof result === "string" && /error|fail/i.test(result));

  // Get heights — collapsed is min(content, COLLAPSED_PX) so short
  // memories don't get padded to 2 lines
  const getFullHeight = () => contentRef.current?.scrollHeight ?? COLLAPSED_PX;
  const getCollapsedHeight = () => Math.min(getFullHeight(), COLLAPSED_PX);

  // Does the content overflow the collapsed height?
  const needsTruncation = contentRef.current
    ? contentRef.current.scrollHeight > COLLAPSED_PX + 4
    : memoryText.length > 100;

  // Result text
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

  // Click vs drag
  const handleMouseDown = (e: React.MouseEvent) => {
    clickStartRef.current = { x: e.clientX, y: e.clientY };
  };
  const handleMouseUp = (e: React.MouseEvent) => {
    if (!needsTruncation) return;
    const start = clickStartRef.current;
    if (!start) return;
    if (Math.abs(e.clientX - start.x) < 4 && Math.abs(e.clientY - start.y) < 4) {
      setExpanded(!expanded);
    }
    clickStartRef.current = null;
  };

  // Colors
  const dotColor = isStreaming || isRunning
    ? "var(--theme-primary)"
    : isError ? "var(--theme-error)" : "var(--theme-success)";
  const iconColor = isStreaming || isRunning
    ? "var(--theme-primary)"
    : isError ? "var(--theme-error)" : undefined;

  return (
    <div
      data-testid="memory-store"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      <div className="flex items-start gap-2 px-3 py-2.5 bg-surface">
        <Feather
          size={14}
          className="mt-[2px] shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-muted mb-0.5">Store</div>

          {memoryText && (
            <div className="relative">
              <motion.div
                className={`overflow-hidden ${needsTruncation ? "cursor-pointer" : ""}`}
                initial={false}
                animate={{ height: expanded ? getFullHeight() : getCollapsedHeight() }}
                transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
                onMouseDown={needsTruncation ? handleMouseDown : undefined}
                onMouseUp={needsTruncation ? handleMouseUp : undefined}
              >
                <div
                  ref={contentRef}
                  className="text-[13px] text-muted/70 leading-snug whitespace-pre-wrap break-words select-text"
                >
                  {memoryText}
                </div>
              </motion.div>

              {/* "More" indicator — only when collapsed and overflowing */}
              {!expanded && needsTruncation && (
                <div className="absolute bottom-0 right-0 flex items-center text-muted/30">
                  <ChevronDown size={14} />
                </div>
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

      {hasResult && (
        <div
          className={`px-3 py-2 border-t border-border bg-code-bg text-xs font-mono ${
            isError ? "text-error" : "text-muted/60"
          }`}
        >
          {resultText}
        </div>
      )}

      {isError && !hasResult && (
        <div className="px-3 py-2 border-t border-border bg-error/10 text-error text-xs font-mono font-bold">
          STORE FAILED
        </div>
      )}
    </div>
  );
};
