/**
 * WriteResult — File creation viewer for Write tool calls.
 *
 * Shows the file path and the content being written.
 * Essentially ReadResult with a different icon and "Creating..." state.
 */

import { useState, useRef, useEffect } from "react";
import { FilePlus } from "lucide-react";
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

export const WriteResult: ToolCallMessagePartComponent = ({
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);
  const contentRef = useRef<HTMLPreElement>(null);

  // Parse args — Write sends file_path and content
  let filePath = "";
  let fileContent = "";
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    filePath = args.file_path || "";
    fileContent = args.content || "";
  } catch {
    filePath = argsText || "";
  }

  const hasResult = result !== undefined && result !== null;
  const isRunning = !hasResult;

  const resultText = typeof result === "string" ? result : "";
  const isError = hasResult && /error/i.test(resultText);

  // Show file content from args (what's being written), not from result
  const contentLines = fileContent.split("\n");
  const isTruncated = !expanded && contentLines.length > TRUNCATE_AFTER;
  const canCollapse = expanded && contentLines.length > TRUNCATE_AFTER;
  const displayText = isTruncated
    ? contentLines.slice(0, TRUNCATE_AFTER).join("\n")
    : fileContent;

  useEffect(() => {
    if (contentRef.current && !isTruncated) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [fileContent, isTruncated]);

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
      data-testid="write-result"
      className="w-full rounded-lg border border-border overflow-hidden"
    >
      {/* Header — file path */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-surface">
        <FilePlus
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
          <span className="text-[11px] text-muted shrink-0">
            {contentLines.length} lines
          </span>
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* Content being written */}
      {isRunning && !fileContent && (
        <div className="px-3 py-2 border-t border-border bg-code-bg">
          <span className="text-muted/40 text-xs font-mono italic">
            Creating...
          </span>
        </div>
      )}

      {fileContent && (
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
