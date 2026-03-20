/**
 * MemoryStore — Card UI for mcp__alpha__store tool calls.
 *
 * Feather icon left, "Store" header, memory text truncated to 2 lines
 * with ellipsis. Click to expand/collapse with Framer Motion height
 * animation. Distinguishes click from text selection.
 */

import { useState, useRef, useEffect } from "react";
import { Feather } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

export const MemoryStore: ToolCallMessagePartComponent = ({
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const [collapsedHeight, setCollapsedHeight] = useState<number | null>(null);
  const [fullHeight, setFullHeight] = useState<number | null>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
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

  // Measure collapsed and full heights once on mount / text change.
  // A hidden measurer div (off-screen) gives us exact pixel values.
  useEffect(() => {
    const el = measureRef.current;
    if (!el || !memoryText) return;

    // Full height — no clamp
    el.style.cssText = "position:absolute;visibility:hidden;width:" + (textRef.current?.offsetWidth || 300) + "px;white-space:pre-wrap;word-break:break-word;font-size:13px;line-height:1.375;";
    el.textContent = memoryText;
    const full = el.offsetHeight;

    // Collapsed height — clamp to 2 lines
    el.style.cssText += "display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;";
    const collapsed = el.offsetHeight;

    setFullHeight(full);
    setCollapsedHeight(collapsed);
    setOverflows(full > collapsed + 2);

    el.style.cssText = "position:absolute;visibility:hidden;";
    el.textContent = "";
  }, [memoryText]);

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
            <>
              {/* Hidden measurer — off-screen, used to calculate heights */}
              <div ref={measureRef} style={{ position: "absolute", visibility: "hidden" }} />

              {/* Visible text — animates between collapsedHeight and fullHeight */}
              <div
                ref={textRef}
                className={`text-[13px] text-muted/70 leading-snug break-words select-text overflow-hidden ${
                  overflows ? "cursor-pointer" : ""
                }`}
                style={{
                  height: expanded
                    ? fullHeight != null ? `${fullHeight}px` : "auto"
                    : collapsedHeight != null ? `${collapsedHeight}px` : "auto",
                  transition: overflows ? "height 2500ms ease-in-out" : undefined,
                  whiteSpace: "pre-wrap",
                }}
                onMouseDown={overflows ? handleMouseDown : undefined}
                onMouseUp={overflows ? handleMouseUp : undefined}
              >
                {memoryText}
                {!expanded && overflows && (
                  <span className="text-muted/40">...</span>
                )}
              </div>
            </>
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
