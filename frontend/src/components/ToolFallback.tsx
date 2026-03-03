/**
 * ToolFallback — Collapsible UI for tool calls.
 *
 * Shows tool name, argument summary, and expandable input/output details.
 */

import { useState } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

export const ToolFallback: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);

  const safeName = toolName || "Unknown Tool";
  const displayName = safeName.charAt(0).toUpperCase() + safeName.slice(1);

  const isRunning = status?.type === "running";
  const isError =
    status?.type === "incomplete" && status.reason === "error";

  let args: Record<string, unknown> = {};
  try {
    args = argsText ? JSON.parse(argsText) : {};
  } catch {
    // argsText might not be valid JSON
  }

  const argSummary = (() => {
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

  const statusColor = isRunning
    ? "bg-primary"
    : isError
    ? "bg-error"
    : "bg-success";

  return (
    <div className="mb-3 rounded-lg border border-border bg-surface overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2.5 bg-transparent border-none cursor-pointer text-text font-mono text-[13px] text-left"
      >
        <span
          className={`w-2 h-2 rounded-full ${statusColor} ${isRunning ? "animate-pulse-dot" : ""}`}
        />
        <span className="text-primary font-semibold">
          {displayName}
        </span>
        {argSummary && (
          <span className="text-muted flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
            {argSummary}
          </span>
        )}
        <span className="text-muted text-[10px]">
          {expanded ? "\u25BC" : "\u25B6"}
        </span>
      </button>

      {expanded && (
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
