/**
 * ReadResult — File viewer for Read tool calls.
 *
 * Shows the file path prominently, with content in a code block.
 * Long files are truncated with expand/collapse.
 */

import { useState, useRef, useEffect } from "react";
import { FileText } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";

/** Max lines to show before truncating. */
const TRUNCATE_AFTER = 20;

/** Extract just the filename from a full path. */
function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

export const ReadResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);
  const contentRef = useRef<HTMLPreElement>(null);

  // Parse args
  let filePath = "";
  let offset: number | undefined;
  let limit: number | undefined;
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    filePath = args.file_path || "";
    offset = args.offset;
    limit = args.limit;
  } catch {
    filePath = argsText || "";
  }

  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

  // Resolve content text
  const contentText = (() => {
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

  const isError = hasResult && /^(Error|No such file|Permission denied|File not found)/i.test(contentText);

  const contentLines = contentText.split("\n");
  const isTruncated = !expanded && contentLines.length > TRUNCATE_AFTER;
  const canCollapse = expanded && contentLines.length > TRUNCATE_AFTER;
  const displayText = isTruncated
    ? contentLines.slice(0, TRUNCATE_AFTER).join("\n")
    : contentText;

  useEffect(() => {
    if (contentRef.current && !isTruncated) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [contentText, isTruncated]);

  // Range annotation (e.g., "lines 50-80")
  const rangeLabel = offset
    ? limit
      ? `lines ${offset}–${offset + limit - 1}`
      : `from line ${offset}`
    : limit
    ? `first ${limit} lines`
    : null;

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

  return (
    <div
      data-testid="read-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — file path */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-surface">
        <FileText
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
                {/* Insert zero-width spaces after slashes so paths break at directory boundaries */}
                {filePath.split("/").reduce<React.ReactNode[]>((acc, seg, i) => {
                  if (i > 0) acc.push("/", <wbr key={i} />);
                  acc.push(seg);
                  return acc;
                }, [])}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
          {rangeLabel && (
            <span className="text-[11px] text-muted shrink-0">{rangeLabel}</span>
          )}
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Content */}
      {isRunning && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            Reading...
          </span>
        </div>
      )}

      {contentText && (
        <div className="border-t border-border bg-code-bg">
          <pre
            ref={contentRef}
            className="m-0 px-3 py-2 text-xs font-mono overflow-auto leading-relaxed whitespace-pre-wrap break-words"
            style={{
              maxHeight: expanded ? "600px" : "320px",
              color: isError ? "var(--destructive)" : undefined,
            }}
          >
            {displayText}
          </pre>

          {isTruncated && (
            <button
              onClick={() => setExpanded(true)}
              className="w-full px-3 py-1.5 text-xs text-primary bg-transparent border-none border-t border-border cursor-pointer hover:bg-surface/50 transition-colors font-mono"
            >
              ↓ {contentLines.length - TRUNCATE_AFTER} more lines
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
