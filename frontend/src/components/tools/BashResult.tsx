/**
 * BashResult — Terminal-style UI for Bash tool calls.
 *
 * Shows the command prominently, description if provided, and output
 * in a terminal-styled code block. Short output is visible by default;
 * long output is truncated with a "show more" toggle.
 *
 * State detection is based on the result prop, not assistant-ui status,
 * because we set isRunning=false for duplex send support.
 *
 * Colors: avocado (#7A8C42), amber (primary), corrupted red (#C4504A).
 */

import { useState, useRef, useEffect } from "react";
import { Terminal } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Max lines to show before truncating. */
const TRUNCATE_AFTER = 15;

/** Max visible command length before truncating with ellipsis. */
const CMD_TRUNCATE = 80;

export const BashResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [cmdExpanded, setCmdExpanded] = useState(false);
  const outputRef = useRef<HTMLPreElement>(null);

  // Parse args
  let command = "";
  let description = "";
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    command = args.command || "";
    description = args.description || "";
  } catch {
    // Partial JSON while streaming — show raw command
    command = argsText || "";
  }

  // Derive state from result, not from assistant-ui status.
  // We set isRunning=false globally for duplex, so status is unreliable.
  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

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

  // Detect errors from the output content.
  // Bash errors come through as "Exit code N\n..." where N > 0.
  const isError = hasResult && /^Exit code [1-9]/.test(outputText);

  const outputLines = outputText.split("\n");
  const isTruncated = !expanded && outputLines.length > TRUNCATE_AFTER;
  const canCollapse = expanded && outputLines.length > TRUNCATE_AFTER;
  const displayText = isTruncated
    ? outputLines.slice(0, TRUNCATE_AFTER).join("\n")
    : outputText;

  // Auto-scroll to bottom of output when it appears
  useEffect(() => {
    if (outputRef.current && !isTruncated) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [outputText, isTruncated]);

  // Theme colors — avocado success, amber running, corrupted red error
  const dotColor = isRunning
    ? "var(--theme-primary)"
    : isError
    ? "var(--theme-error)"
    : "var(--theme-success)";

  const iconColor = isRunning
    ? "var(--theme-primary)"
    : isError
    ? "var(--theme-error)"
    : undefined; // default muted for success

  return (
    <div
      data-testid="bash-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — command + description */}
      <div className="flex items-start gap-2 px-3 py-2.5 bg-surface">
        <Terminal
          size={14}
          className="mt-[2px] shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          {description && (
            <div className="text-[12px] text-muted mb-0.5">{description}</div>
          )}
          {command.length > CMD_TRUNCATE && !cmdExpanded ? (
            <code
              className="text-[13px] text-text leading-snug cursor-pointer"
              onClick={() => setCmdExpanded(true)}
              title="Click to show full command"
            >
              {command.slice(0, CMD_TRUNCATE)}
              <span className="text-muted">…</span>
            </code>
          ) : command.length > CMD_TRUNCATE ? (
            <code
              className="text-[13px] text-text break-all leading-snug cursor-pointer"
              onClick={() => setCmdExpanded(false)}
              title="Click to collapse"
            >
              {command}
            </code>
          ) : (
            <code className="text-[13px] text-text break-all leading-snug">
              {command}
            </code>
          )}
        </div>
        <span
          className={`w-2 h-2 mt-[5px] rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Output — terminal style */}
      {isRunning && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            Running...
          </span>
        </div>
      )}

      {outputText && (
        <div className="border-t border-border bg-code-bg">
          <pre
            ref={outputRef}
            className="m-0 px-3 py-2 text-xs font-mono overflow-auto leading-relaxed whitespace-pre-wrap break-words"
            style={{
              maxHeight: expanded ? "600px" : "320px",
              color: isError ? "var(--theme-error)" : undefined,
            }}
          >
            {displayText}
          </pre>

          {/* Expand / collapse toggle */}
          {isTruncated && (
            <button
              onClick={() => setExpanded(true)}
              className="w-full px-3 py-1.5 text-xs text-primary bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↓ {outputLines.length - TRUNCATE_AFTER} more lines
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
    </div>
  );
};
