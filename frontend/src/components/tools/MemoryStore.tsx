/**
 * MemoryStore — Card UI for mcp__alpha__store tool calls.
 *
 * Feather icon left, "Store" header, memory text truncated to 2 lines
 * with ellipsis. Click to expand/collapse with Framer Motion height
 * animation. Distinguishes click from text selection.
 */

import { useState, useRef, useEffect } from "react";
import { Feather } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

export const MemoryStore: ToolCallMessagePartComponent = ({
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);
  const clickStartRef = useRef<{ x: number; y: number } | null>(null);

  // Parse memory text from args
  let memoryText = "";
  let jsonComplete = false;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    memoryText = args.memory || "";
    jsonComplete = true;
  } catch {
    memoryText = argsText || "";
  }

  // State detection
  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError =
    (status?.type === "incomplete" && status.reason === "error") ||
    (hasResult && typeof result === "string" && /error|fail/i.test(result));

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

  // Detect overflow — only when collapsed
  useEffect(() => {
    if (expanded) return;
    const el = textRef.current;
    if (!el) return;
    const check = () => setOverflows(el.scrollHeight > el.clientHeight + 2);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [memoryText, expanded]);

  // Click vs drag detection
  const handleMouseDown = (e: React.MouseEvent) => {
    clickStartRef.current = { x: e.clientX, y: e.clientY };
  };
  const handleMouseUp = (e: React.MouseEvent) => {
    if (!overflows) return;
    const start = clickStartRef.current;
    if (!start) return;
    const dx = Math.abs(e.clientX - start.x);
    const dy = Math.abs(e.clientY - start.y);
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
              className={overflows ? "cursor-pointer" : ""}
              onMouseDown={overflows ? handleMouseDown : undefined}
              onMouseUp={overflows ? handleMouseUp : undefined}
            >
              <AnimatePresence initial={false} mode="wait">
                {!expanded ? (
                  <motion.div
                    key="collapsed"
                    ref={textRef}
                    className="text-[13px] text-muted/70 leading-snug break-words select-text"
                    style={{
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical" as const,
                      overflow: "hidden",
                    }}
                    initial={false}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15 }}
                  >
                    {memoryText}
                  </motion.div>
                ) : (
                  <motion.div
                    key="expanded"
                    className="text-[13px] text-muted/70 leading-snug whitespace-pre-wrap break-words select-text"
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{
                      height: { duration: 0.4, ease: [0.25, 0.1, 0.25, 1] },
                      opacity: { duration: 0.2 },
                    }}
                    style={{ overflow: "hidden" }}
                  >
                    {memoryText}
                  </motion.div>
                )}
              </AnimatePresence>
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
