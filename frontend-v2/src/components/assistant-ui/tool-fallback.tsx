/**
 * ToolFallback — Generic tool call UI.
 *
 * Same layout for all tools: icon left, content middle, dot right.
 * Progressive disclosure: streaming → executing → result (truncated).
 * Custom tool UIs registered via makeAssistantToolUI "graduate" out
 * of this component — everything else falls through to here.
 *
 * Ported from frontend/src/components/ToolFallback.tsx with theme
 * classes updated for the alpha.css / Tailwind v4 palette.
 */

import { useState } from "react";
import { Wrench } from "lucide-react";

/** Max output lines before truncating. */
const OUTPUT_TRUNCATE = 20;

interface ToolFallbackProps {
  toolName: string;
  toolCallId: string;
  args: unknown;
  argsText?: string;
  result?: unknown;
  status?: { type: string; reason?: string };
}

export const ToolFallback = ({
  toolName,
  argsText,
  args,
  result,
  status,
}: ToolFallbackProps) => {
  const [outputExpanded, setOutputExpanded] = useState(false);

  const safeName = toolName || "Unknown Tool";
  const displayName = safeName.charAt(0).toUpperCase() + safeName.slice(1);

  // Try to parse argsText first, fall back to args prop.
  let parsedArgs: Record<string, unknown> = {};
  let jsonComplete = false;
  try {
    if (argsText) {
      parsedArgs = JSON.parse(argsText);
      jsonComplete = true;
    } else if (args && typeof args === "object") {
      parsedArgs = args as Record<string, unknown>;
      jsonComplete = true;
    }
  } catch {
    // argsText is partial JSON — still streaming
  }

  const hasResult = result !== undefined && result !== null;
  const isStreaming = !jsonComplete && !hasResult;
  const isRunning = !hasResult && jsonComplete;
  const isError = status?.type === "incomplete" && status.reason === "error";

  // Arg summary for the title bar
  const argSummary = (() => {
    if (!jsonComplete) return "";
    const entries = Object.entries(parsedArgs);
    if (entries.length === 0) return "";

    if (parsedArgs.file_path) {
      const path = String(parsedArgs.file_path);
      const parts = path.split("/");
      return parts[parts.length - 1];
    }
    if (parsedArgs.query) {
      const q = String(parsedArgs.query);
      return q.length > 50 ? q.slice(0, 50) + "..." : q;
    }
    if (parsedArgs.pattern) return String(parsedArgs.pattern);
    if (parsedArgs.memory) {
      const m = String(parsedArgs.memory);
      return m.length > 50 ? m.slice(0, 50) + "..." : m;
    }
    if (parsedArgs.command) {
      const c = String(parsedArgs.command);
      return c.length > 60 ? c.slice(0, 60) + "..." : c;
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
  const outputTruncated =
    !outputExpanded && outputLines.length > OUTPUT_TRUNCATE;
  const displayOutput = outputTruncated
    ? outputLines.slice(0, OUTPUT_TRUNCATE).join("\n")
    : resultText;

  // Status dot color
  const dotColor =
    isStreaming || isRunning
      ? "var(--color-primary)"
      : isError
        ? "var(--color-destructive)"
        : "var(--color-success)"; // avocado green for success

  const iconColor =
    isStreaming || isRunning
      ? "var(--color-primary)"
      : isError
        ? "var(--color-destructive)"
        : undefined; // default muted for success

  return (
    <div
      data-testid="tool-call"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — tool name + arg summary, dot on right */}
      <div className="flex items-start gap-2 px-3 py-2.5 bg-muted/30">
        <Wrench
          size={14}
          className="mt-[2px] shrink-0 text-muted-foreground/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1">
          <div className="text-[12px] text-foreground mb-0.5">
            {displayName}
          </div>
          {argSummary && (
            <code className="text-[13px] text-muted-foreground font-mono leading-snug break-all">
              {argSummary}
            </code>
          )}
        </div>
        <span
          className={`w-2 h-2 mt-[5px] rounded-full shrink-0 ${
            isStreaming || isRunning ? "animate-pulse" : ""
          }`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Running indicator */}
      {(isStreaming || isRunning) && !hasResult && (
        <div className="px-3 py-2 border-t border-border bg-muted/15">
          <span className="text-muted-foreground/40 text-xs font-mono italic">
            {isStreaming ? "Generating..." : "Executing..."}
          </span>
        </div>
      )}

      {/* Output — only when result arrives */}
      {hasResult && (
        <div className="border-t border-border bg-muted/15">
          <pre
            className="m-0 px-3 py-2 text-xs font-mono text-muted-foreground leading-relaxed whitespace-pre"
            style={{
              maxHeight: outputExpanded ? "600px" : "120px",
              overflowX: "auto",
              overflowY: outputExpanded ? "auto" : "hidden",
              color: isError ? "var(--color-destructive)" : undefined,
            }}
          >
            {displayOutput}
          </pre>
          {outputLines.length > OUTPUT_TRUNCATE && (
            <button
              onClick={() => setOutputExpanded(!outputExpanded)}
              className="w-full px-3 py-1.5 text-[11px] text-muted-foreground hover:text-primary font-mono bg-transparent border-t border-border cursor-pointer text-center"
            >
              {outputExpanded
                ? `↑ Collapse (${outputLines.length} lines)`
                : `↓ Show full output (${outputLines.length} lines)`}
            </button>
          )}
        </div>
      )}
    </div>
  );
};
