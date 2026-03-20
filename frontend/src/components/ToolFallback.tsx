/**
 * ToolFallback — Collapsible UI for tool calls.
 *
 * Shows tool name, argument summary, and expandable input/output details.
 * While JSON is streaming (argsText is non-empty but unparseable),
 * shows a StreamingTicker with the raw fragments scrolling across.
 */

import { useState } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { StreamingTicker } from "./tools/StreamingTicker";

export const ToolFallback: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);

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
  // Streaming: JSON not yet complete AND no result yet (includes empty argsText on tool-use-start)
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError =
    status?.type === "incomplete" && status.reason === "error";

  const argSummary = (() => {
    if (!jsonComplete) return "";
    const entries = Object.entries(args);
    if (entries.length === 0) return "";

    if (safeName.toLowerCase() === "bash" && args.command) {
      const cmd = String(args.command);
      return cmd.length > 50 ? cmd.slice(0, 50) + "..." : cmd;
    }
    if (args.file_path) {
      const path = String(args.file_path);
      const parts = path.split("/");
      return parts[parts.length - 1];
    }
    if (args.pattern) {
      return String(args.pattern);
    }

    const firstString = entries.find(([, v]) => typeof v === "string");
    if (firstString) {
      const val = String(firstString[1]);
      return val.length > 40 ? val.slice(0, 40) + "..." : val;
    }

    return `${entries.length} args`;
  })();

  const statusColor = isStreaming
    ? "bg-primary"
    : isRunning && !hasResult
    ? "bg-primary"
    : isError
    ? "bg-error"
    : "bg-success";

  return (
    <div data-testid="tool-call" className="rounded-lg border border-border bg-surface overflow-hidden">
      <button
        onClick={() => !isStreaming && setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-3 py-2.5 bg-transparent border-none text-text font-mono text-[13px] text-left ${
          isStreaming ? "cursor-default" : "cursor-pointer"
        }`}
      >
        <span
          className={`w-2 h-2 rounded-full ${statusColor} ${
            isStreaming || (isRunning && !hasResult) ? "animate-pulse-dot" : ""
          }`}
        />
        <span className="text-primary font-semibold">
          {displayName}
        </span>
        {argSummary && (
          <span className="text-muted flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
            {argSummary}
          </span>
        )}
        {!isStreaming && (
          <span className="text-muted text-[10px]">
            {expanded ? "\u25BC" : "\u25B6"}
          </span>
        )}
      </button>

      {/* Streaming ticker — visible while JSON is still arriving */}
      {isStreaming && (
        <StreamingTicker text={argsText || ""} active={true} />
      )}

      {expanded && jsonComplete && (
        <div className="border-t border-border p-3">
          <div className={result !== undefined ? "mb-3" : ""}>
            <div className="text-muted text-[11px] mb-1 font-mono">
              INPUT
            </div>
            <pre className="m-0 p-2 bg-code-bg rounded text-xs font-mono text-text overflow-auto max-h-[200px]">
              {argsText || "{}"}
            </pre>
          </div>

          {result !== undefined && (
            <div>
              <div className="text-muted text-[11px] mb-1 font-mono">
                OUTPUT
              </div>
              <pre className="m-0 p-2 bg-code-bg rounded text-xs font-mono text-text overflow-auto max-h-[300px]">
                {typeof result === "string"
                  ? result
                  : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
