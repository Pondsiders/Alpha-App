/**
 * BashResult — Terminal-style UI for Bash tool calls.
 *
 * Three bands: title bar, command, output. Always the same structure.
 * Collapsed = truncated. Expanded = not truncated. That's the whole thing.
 * Click anywhere to toggle. ToolGroup handles container animation.
 *
 * Colors: avocado (#7A8C42), amber (primary), corrupted red (#C4504A).
 */

import { useState, useRef, useEffect } from "react";
import { Terminal } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max height of the output container in collapsed mode.
 *  Set on the outer div, not the pre — so padding is included.
 *  2.5 line-heights of the pre's font-size × leading-relaxed. */
const COLLAPSED_OUTPUT_MAX = "2.4lh";

/** Max output height in expanded mode before scrolling. */
const MAX_EXPANDED_HEIGHT = 600;

export const BashResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);
  const outputRef = useRef<HTMLPreElement>(null);

  // Parse args
  let command = "";
  let description = "";
  let jsonComplete = false;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    command = args.command || "";
    description = args.description || "";
    jsonComplete = true;
  } catch {
    // Partial JSON while streaming
  }

  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;

  // Resolve output text
  const outputText = (() => {
    if (!hasResult) return "";
    if (typeof result === "string") return result;
    if (typeof result === "object" && "content" in result) {
      const content = (result as { content: Array<{ text?: string }> }).content;
      if (Array.isArray(content)) {
        return content
          .filter((c) => c.text)
          .map((c) => c.text)
          .join("\n");
      }
    }
    return JSON.stringify(result, null, 2);
  })();

  const isError = hasResult && /^Exit code [1-9]/.test(outputText);

  // Theme colors
  const dotColor =
    isRunning || isStreaming
      ? "var(--theme-primary)"
      : isError
        ? "var(--theme-error)"
        : "var(--theme-success)";

  const iconColor =
    isRunning || isStreaming
      ? "var(--theme-primary)"
      : isError
        ? "var(--theme-error)"
        : undefined;

  // Title: description if available, else "Bash"
  const title = description || "Bash";

  // Auto-scroll to bottom of expanded output
  useEffect(() => {
    if (expanded && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [expanded, outputText]);

  return (
    <div
      data-testid="bash-result"
      className="w-full rounded-lg border border-border overflow-hidden cursor-pointer select-none"
      onClick={() => setExpanded(!expanded)}
    >
      {/* ── Band 1: Title bar ── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-surface">
        <Terminal
          size={14}
          className="shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1 truncate">
          <span className="text-[13px] text-text">{title}</span>
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning || isStreaming ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* ── Band 2: Command ── */}
      {command && (
        <div className="border-t border-border/50 bg-code-bg px-3 py-1.5 overflow-hidden">
          <code
            className={`text-[12px] text-muted leading-snug block ${expanded ? "break-all whitespace-pre-wrap" : "truncate"}`}
          >
            {command}
          </code>
        </div>
      )}

      {/* ── Band 3: Output ── */}
      <div
        className="relative border-t border-border bg-code-bg"
        style={!expanded ? { maxHeight: COLLAPSED_OUTPUT_MAX, overflow: "hidden" } : undefined}
      >
        {(isRunning || isStreaming) && !outputText ? (
          <div className="px-3 py-2">
            <span className="text-muted/40 text-xs font-mono italic">
              Running...
            </span>
          </div>
        ) : outputText ? (
          <pre
            ref={expanded ? outputRef : undefined}
            className={`m-0 px-3 py-2 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words ${
              expanded ? "overflow-auto" : ""
            }`}
            style={{
              ...(expanded ? { maxHeight: `${MAX_EXPANDED_HEIGHT}px` } : {}),
              color: isError ? "var(--theme-error)" : undefined,
            }}
          >
            {outputText}
          </pre>
        ) : (
          <div className="px-3 py-2">
            <span className="text-muted/30 text-xs font-mono italic">
              &nbsp;
            </span>
          </div>
        )}
      </div>
    </div>
  );
};
