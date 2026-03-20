/**
 * ToolFallback — Progressive disclosure UI for tool calls.
 *
 * Four-state lifecycle:
 *   1. Void     — tool-use-start fired, no JSON yet.
 *                  Title bar: tool name + pulsing amber dot. Content: empty dark area.
 *   2. Streaming — partial JSON arriving via tool-use-delta.
 *                  Title bar: tool name + pulsing dot. Content: raw JSON as it accumulates.
 *   3. Complete — JSON parseable, tool executing, no result yet.
 *                  Title bar: tool name + arg summary + pulsing dot. Content: pretty-printed input.
 *   4. Done     — result arrived.
 *                  Title bar: tool name + arg summary + green dot. Content: input + output.
 */

import { useState } from "react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max lines of JSON to show before truncating. */
const TRUNCATE_LINES = 12;

export const ToolFallback: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
  status,
}) => {
  const [inputExpanded, setInputExpanded] = useState(false);

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

  // Pretty-print input when parseable
  const prettyInput = jsonComplete
    ? JSON.stringify(args, null, 2)
    : argsText || "";

  const inputLines = prettyInput.split("\n");
  const inputTruncated = !inputExpanded && inputLines.length > TRUNCATE_LINES;
  const displayInput = inputTruncated
    ? inputLines.slice(0, TRUNCATE_LINES).join("\n")
    : prettyInput;

  // Arg summary for the title bar (only when JSON is complete)
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

  // Format result for display
  const resultText = hasResult
    ? typeof result === "string"
      ? result
      : JSON.stringify(result, null, 2)
    : "";

  // Status dot color
  const dotColor = isStreaming || isRunning
    ? "bg-primary"
    : isError
    ? "bg-error"
    : "bg-success";

  const showContent = isStreaming || isRunning || hasResult;

  return (
    <div data-testid="tool-call" className="rounded-lg border border-border bg-surface overflow-hidden">
      {/* Title bar */}
      <div className="flex items-center gap-2 px-3 py-2.5 font-mono text-[13px]">
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${dotColor} ${
            isStreaming || isRunning ? "animate-pulse-dot" : ""
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
      </div>

      {/* Content area — progressive disclosure */}
      {showContent && (
        <div className="border-t border-border">
          {/* Input JSON */}
          <div className="p-3">
            <div className="text-muted text-[10px] mb-1 font-mono uppercase tracking-wider">
              Input
            </div>
            <pre className="m-0 p-2 bg-code-bg rounded text-xs font-mono text-text overflow-auto max-h-[200px]">
              {displayInput || "\u00A0"}
            </pre>
            {inputTruncated && (
              <button
                onClick={() => setInputExpanded(true)}
                className="mt-1 text-[11px] text-muted hover:text-primary font-mono bg-transparent border-none cursor-pointer p-0"
              >
                ↓ Show all ({inputLines.length} lines)
              </button>
            )}
          </div>

          {/* Output — only when result arrives */}
          {hasResult && (
            <div className="border-t border-border p-3">
              <div className="text-muted text-[10px] mb-1 font-mono uppercase tracking-wider">
                Output
              </div>
              <pre className="m-0 p-2 bg-code-bg rounded text-xs font-mono text-text overflow-auto max-h-[300px]">
                {resultText}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
