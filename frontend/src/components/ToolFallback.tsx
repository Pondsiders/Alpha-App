/**
 * ToolFallback — Generic tool call UI matching named component style.
 *
 * Same layout as BashResult/EditResult: icon left, content middle, dot right.
 * Progressive disclosure: void → raw JSON → pretty JSON → pretty JSON + output.
 */

import { useState } from "react";
import { Wrench } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max output lines before truncating. */
const OUTPUT_TRUNCATE = 20;

export const ToolFallback: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
}) => {
  const [outputExpanded, setOutputExpanded] = useState(false);

  const safeName = toolName || "Unknown Tool";
  const displayName = safeName.charAt(0).toUpperCase() + safeName.slice(1);

  // Try to parse — if it fails, JSON is still streaming in.
  let args: Record<string, unknown> = {};
  let jsonComplete = false;
  try {
    args = argsText ? JSON.parse(argsText) : {};
    jsonComplete = true;
  } catch {
    // argsText is partial JSON — still streaming
  }

  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError =
    status?.type === "incomplete" && status.reason === "error";

  // Arg summary for the title bar (only when JSON is complete)
  const argSummary = (() => {
    if (!jsonComplete) return "";
    const entries = Object.entries(args);
    if (entries.length === 0) return "";

    if (args.file_path) {
      const path = String(args.file_path);
      const parts = path.split("/");
      return parts[parts.length - 1];
    }
    if (args.query) {
      const q = String(args.query);
      return q.length > 50 ? q.slice(0, 50) + "..." : q;
    }
    if (args.pattern) {
      return String(args.pattern);
    }
    if (args.memory) {
      const m = String(args.memory);
      return m.length > 50 ? m.slice(0, 50) + "..." : m;
    }

    const firstString = entries.find(([, v]) => typeof v === "string");
    if (firstString) {
      const val = String(firstString[1]);
      return val.length > 50 ? val.slice(0, 50) + "..." : val;
    }

    return `${entries.length} args`;
  })();

  // Format result for display
  const resultText = hasResult
    ? typeof result === "string"
      ? result
      : JSON.stringify(result, null, 2)
    : "";

  const outputLines = resultText.split("\n");
  const outputTruncated = !outputExpanded && outputLines.length > OUTPUT_TRUNCATE;
  const displayOutput = outputTruncated
    ? outputLines.slice(0, OUTPUT_TRUNCATE).join("\n")
    : resultText;

  // Theme colors — match named components
  const dotColor = isStreaming || isRunning
    ? "var(--primary)"
    : isError
    ? "var(--destructive)"
    : "var(--success)";

  const iconColor = isStreaming || isRunning
    ? "var(--primary)"
    : isError
    ? "var(--destructive)"
    : undefined; // default muted for success

  return (
    <div data-testid="tool-call" className="w-full rounded-lg border border-border overflow-hidden">
      {/* Header — tool name + arg summary, dot on right */}
      <div className="flex items-start gap-2 px-3 py-2.5 bg-surface">
        <Wrench
          size={14}
          className="mt-[2px] shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-muted mb-0.5">{displayName}</div>
          {argSummary && (
            <code className="text-[13px] text-text leading-snug break-all">
              {argSummary}
            </code>
          )}
        </div>
        <span
          className={`w-2 h-2 mt-[5px] rounded-full shrink-0 ${
            isStreaming || isRunning ? "animate-pulse-dot" : ""
          }`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Running indicator */}
      {(isStreaming || isRunning) && !hasResult && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            {isStreaming ? "Generating..." : "Executing..."}
          </span>
        </div>
      )}

      {/* Output — only when result arrives */}
      {hasResult && (
        <div className="border-t border-border bg-code-bg">
          <pre
            className="m-0 px-3 py-2 text-xs font-mono overflow-auto leading-relaxed whitespace-pre-wrap break-words"
            style={{
              maxHeight: outputExpanded ? "600px" : "320px",
              color: isError ? "var(--destructive)" : undefined,
            }}
          >
            {displayOutput}
          </pre>
          {outputTruncated && (
            <button
              onClick={() => setOutputExpanded(true)}
              className="w-full px-3 py-1.5 text-[11px] text-muted hover:text-primary font-mono bg-transparent border-t border-border cursor-pointer text-center"
            >
              ↓ Show full output ({outputLines.length} lines)
            </button>
          )}
        </div>
      )}
    </div>
  );
};
