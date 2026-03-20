/**
 * MemoryStore — Card UI for mcp__alpha__store tool calls.
 *
 * Layout matches BashResult/EditResult: icon left, content middle, dot right.
 * Memory text displayed in muted sans-serif, two-line truncation with
 * click-to-expand. Output shows "Memory stored (id: N)" on success,
 * screams on failure.
 *
 * Route: tools.by_name["mcp__alpha__store"]
 */

import { useState, useRef, useEffect } from "react";
import { Feather } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max height for collapsed memory text: ~2 lines at 13px. */
const COLLAPSED_MAX = "2.6em";

export const MemoryStore: ToolCallMessagePartComponent = ({
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);

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

  // Detect overflow after text renders
  useEffect(() => {
    const el = textRef.current;
    if (!el || expanded) return;
    const check = () => setOverflows(el.scrollHeight > el.clientHeight + 2);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [memoryText, expanded]);

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
      {/* Header — "Store" + dot */}
      <div className="flex items-start gap-2 px-3 py-2.5 bg-surface">
        <Feather
          size={14}
          className="mt-[2px] shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-muted mb-0.5">Store</div>

          {/* Memory text — muted, sans-serif, truncatable */}
          {memoryText && (
            <div
              className={`relative ${overflows && !expanded ? "cursor-pointer" : ""}`}
              onClick={() => overflows && setExpanded(!expanded)}
            >
              <div
                ref={textRef}
                className="text-[13px] text-muted/70 leading-snug whitespace-pre-wrap break-words transition-[max-height] duration-300 ease-in-out"
                style={{ maxHeight: expanded ? "2000px" : COLLAPSED_MAX }}
              >
                {memoryText}
              </div>

              {/* Gradient fade when truncated */}
              {!expanded && overflows && (
                <div
                  className="absolute bottom-0 left-0 right-0 h-5 pointer-events-none"
                  style={{
                    background:
                      "linear-gradient(to top, var(--theme-surface), transparent)",
                  }}
                />
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

      {/* Result — success or error */}
      {hasResult && (
        <div
          className={`px-3 py-2 border-t border-border bg-code-bg text-xs font-mono ${
            isError ? "text-error" : "text-muted/60"
          }`}
        >
          {resultText}
        </div>
      )}

      {/* Error state — loud */}
      {isError && !hasResult && (
        <div className="px-3 py-2 border-t border-border bg-error/10 text-error text-xs font-mono font-bold">
          STORE FAILED
        </div>
      )}
    </div>
  );
};
