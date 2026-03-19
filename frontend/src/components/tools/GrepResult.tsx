/**
 * GrepResult — Search results for Grep tool calls.
 *
 * Shows the search pattern prominently, then results as a compact list
 * of file paths (files_with_matches mode) or content lines with context.
 * Also handles Glob tool calls (same shape: pattern + file list).
 */

import { useState } from "react";
import { Search } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max result lines to show before truncating. */
const TRUNCATE_AFTER = 15;

export const GrepResult: ToolCallMessagePartComponent = ({
  toolName,
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);

  // Parse args — Grep sends pattern, path, output_mode, glob, type, etc.
  // Glob sends pattern and path.
  let pattern = "";
  let searchPath = "";
  let globFilter = "";
  const isGlob = toolName === "Glob";
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    pattern = args.pattern || "";
    searchPath = args.path || "";
    globFilter = args.glob || "";
  } catch {
    pattern = argsText || "";
  }

  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

  // Resolve output
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

  const isError = hasResult && /^(No matches|Error|No such)/i.test(outputText);
  const isEmpty = hasResult && outputText.trim() === "";

  // Filter out summary lines like "Found 6 files" — they're metadata, not results
  const outputLines = outputText.split("\n").filter((l) => l.trim() && !/^Found \d+ /i.test(l.trim()));
  const isTruncated = !expanded && outputLines.length > TRUNCATE_AFTER;
  const canCollapse = expanded && outputLines.length > TRUNCATE_AFTER;
  const displayLines = isTruncated
    ? outputLines.slice(0, TRUNCATE_AFTER)
    : outputLines;

  const dotColor = isRunning
    ? "var(--theme-primary)"
    : isError || isEmpty
    ? "var(--theme-error)"
    : "var(--theme-success)";

  const runningLabel = "Searching...";

  return (
    <div
      data-testid="grep-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — pattern + metadata */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-surface">
        <Search
          size={14}
          className="shrink-0 text-muted/60"
          style={isRunning ? { color: "var(--theme-primary)" } : undefined}
        />
        <div className="min-w-0 flex-1 flex items-baseline gap-2">
          <code className="text-[13px] text-text font-semibold">
            {pattern}
          </code>
          {globFilter && (
            <span className="text-[11px] text-muted shrink-0">{globFilter}</span>
          )}
          {searchPath && !isGlob && (
            <span className="text-[11px] text-muted shrink-0 truncate" title={searchPath}>
              in {searchPath.split("/").pop() || searchPath}
            </span>
          )}
          {hasResult && !isError && !isEmpty && (
            <span className="text-[11px] text-muted shrink-0">
              {outputLines.length} {outputLines.length === 1 ? "match" : "matches"}
            </span>
          )}
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Results */}
      {isRunning && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            {runningLabel}
          </span>
        </div>
      )}

      {isEmpty && hasResult && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            No matches found
          </span>
        </div>
      )}

      {displayLines.length > 0 && (
        <div className="border-t border-border bg-code-bg">
          <div className="px-3 py-1.5">
            {displayLines.map((line, i) => (
              <div
                key={i}
                className="text-xs font-mono text-text/70 py-0.5 truncate"
                title={line}
              >
                {line}
              </div>
            ))}
          </div>

          {isTruncated && (
            <button
              onClick={() => setExpanded(true)}
              className="w-full px-3 py-1.5 text-xs text-primary bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↓ {outputLines.length - TRUNCATE_AFTER} more
            </button>
          )}
          {canCollapse && (
            <button
              onClick={() => setExpanded(false)}
              className="w-full px-3 py-1.5 text-xs text-muted bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↑ Collapse
            </button>
          )}
        </div>
      )}

      {isError && outputText && (
        <div
          className="px-3 py-1.5 text-xs border-t border-border"
          style={{ color: "var(--theme-error)" }}
        >
          {outputText}
        </div>
      )}
    </div>
  );
};
