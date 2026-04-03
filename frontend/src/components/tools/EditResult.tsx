/**
 * EditResult — Inline diff for Edit tool calls.
 *
 * Shows the file path, then old→new text as a mini diff.
 * Removed text in corrupted red (#C4504A), added text in avocado green (#7A8C42).
 * The result (success/error) shows as the status dot.
 */

import { useState } from "react";
import { Pencil } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";

/** Extract just the filename from a full path. */
function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

/** Max lines of old/new to show before truncating. */
const TRUNCATE_LINES = 8;

export const EditResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);

  // Parse args
  let filePath = "";
  let oldString = "";
  let newString = "";
  let replaceAll = false;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    filePath = args.file_path || "";
    oldString = args.old_string || "";
    newString = args.new_string || "";
    replaceAll = args.replace_all || false;
  } catch {
    filePath = argsText || "";
  }

  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

  // Detect error from result text
  const resultText = typeof result === "string" ? result : "";
  const isError = hasResult && (
    /error/i.test(resultText) ||
    /not found/i.test(resultText) ||
    /failed/i.test(resultText) ||
    /not unique/i.test(resultText)
  );

  const dotColor = isRunning
    ? "var(--primary)"
    : isError
    ? "var(--destructive)"
    : "var(--success)";

  const iconColor = isRunning
    ? "var(--primary)"
    : isError
    ? "var(--destructive)"
    : undefined;

  // Truncation logic for old/new strings
  const oldLines = oldString.split("\n");
  const newLines = newString.split("\n");
  const maxLines = Math.max(oldLines.length, newLines.length);
  const needsTruncation = maxLines > TRUNCATE_LINES;

  const displayOld = !expanded && needsTruncation
    ? oldLines.slice(0, TRUNCATE_LINES).join("\n")
    : oldString;
  const displayNew = !expanded && needsTruncation
    ? newLines.slice(0, TRUNCATE_LINES).join("\n")
    : newString;

  return (
    <div
      data-testid="edit-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — file path + replace_all badge */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-surface">
        <Pencil
          size={14}
          className="shrink-0 text-muted/60"
          style={iconColor ? { color: iconColor } : undefined}
        />
        <div className="min-w-0 flex-1 flex items-baseline gap-2">
          <TooltipProvider delayDuration={0}>
            <Tooltip>
              <TooltipTrigger asChild>
                <code className="text-[13px] text-text font-semibold truncate cursor-default">
                  {basename(filePath)}
                </code>
              </TooltipTrigger>
              <TooltipContent side="right" className="max-w-[400px] font-mono" style={{ wordBreak: "keep-all" }}>
                {filePath.split("/").reduce<React.ReactNode[]>((acc, seg, i) => {
                  if (i > 0) acc.push("/", <wbr key={i} />);
                  acc.push(seg);
                  return acc;
                }, [])}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          {replaceAll && (
            <span className="text-[11px] text-muted shrink-0">replace all</span>
          )}
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Diff view */}
      {isRunning && !oldString && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            Editing...
          </span>
        </div>
      )}

      {(oldString || newString) && (
        <div className="border-t border-border bg-code-bg">
          {/* Removed text */}
          {displayOld && (
            <pre
              className="m-0 px-3 py-1.5 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words"
              style={{
                color: "var(--destructive)",
                backgroundColor: "rgba(196, 80, 74, 0.08)",
              }}
            >
              {displayOld.split("\n").map((line, i) => (
                <span key={`old-${i}`}>
                  {i > 0 && "\n"}
                  <span className="select-none opacity-50">− </span>
                  {line}
                </span>
              ))}
            </pre>
          )}

          {/* Added text */}
          {displayNew && (
            <pre
              className="m-0 px-3 py-1.5 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words"
              style={{
                color: "var(--success)",
                backgroundColor: "rgba(122, 140, 66, 0.08)",
              }}
            >
              {displayNew.split("\n").map((line, i) => (
                <span key={`new-${i}`}>
                  {i > 0 && "\n"}
                  <span className="select-none opacity-50">+ </span>
                  {line}
                </span>
              ))}
            </pre>
          )}

          {/* Expand / collapse */}
          {needsTruncation && !expanded && (
            <button
              onClick={() => setExpanded(true)}
              className="w-full px-3 py-1.5 text-xs text-primary bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↓ Show full diff ({maxLines} lines)
            </button>
          )}
          {needsTruncation && expanded && (
            <button
              onClick={() => setExpanded(false)}
              className="w-full px-3 py-1.5 text-xs text-muted bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↑ Collapse
            </button>
          )}

          {/* Error message from the tool result */}
          {isError && resultText && (
            <div
              className="px-3 py-1.5 text-xs border-t border-border"
              style={{ color: "var(--destructive)" }}
            >
              {resultText}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
