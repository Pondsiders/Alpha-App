/**
 * AgentResult — Progressively enhancing UI for Agent tool calls.
 *
 * Three phases:
 *   1. task_started → show prompt, spinner
 *   2. task_progress → update status line (description, tool count, duration)
 *   3. tool-result → show agent's output, stop spinner
 *
 * During streaming: reads transient agentProgress state from the store.
 * On reload from Postgres: falls back to args (prompt) and result (summary).
 * The child tool calls are ephemeral — visible live, gone on reload.
 *
 * Same visual language as BashResult: bands, status dot, click to expand.
 */

import { useState } from "react";
import { Bot } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { useWorkshopStore } from "@/store";

/** Max lines for collapsed prompt/result display. */
const COLLAPSED_LINES = 5;
const LINE_HEIGHT = "1.5rem";

export const AgentResult: ToolCallMessagePartComponent = ({
  toolCallId,
  argsText,
  result,
}) => {
  const [expanded, setExpanded] = useState(false);

  // Transient progress state (live streaming only — empty on reload)
  const progress = useWorkshopStore((s) => s.agentProgress[toolCallId]);

  // Parse args for the prompt and description
  let prompt = "";
  let description = "";
  let subagentType = "";
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    prompt = args.prompt || "";
    description = args.description || "";
    subagentType = args.subagent_type || "";
  } catch {
    // Partial JSON while streaming
  }

  // Resolve output text from result prop OR agent-done summary
  const resultText = (() => {
    // First try the tool result (populated by tool-result event)
    if (result !== undefined && result !== null) {
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
    }
    // Fall back to agent-done summary (from task_notification)
    if (progress?.summary) return progress.summary;
    return "";
  })();

  const hasResult = resultText.length > 0;
  const isDone = progress?.done || hasResult;
  const isRunning = !isDone;

  // Live status from progress events (or static from args)
  const statusDescription = progress?.description || description || "Agent";
  const lastToolName = progress?.lastToolName;
  const toolUses = progress?.toolUses || 0;
  const durationMs = progress?.durationMs || 0;
  const durationSec = (durationMs / 1000).toFixed(1);

  // Title: subagent type or "Agent"
  const title = subagentType
    ? subagentType.charAt(0).toUpperCase() + subagentType.slice(1)
    : "Agent";

  // Dot color
  const dotColor = isDone ? "var(--theme-success)" : "var(--theme-primary)";

  // Truncation helpers
  const truncate = (text: string, maxLines: number) => {
    const lines = text.split("\n");
    if (lines.length <= maxLines) return text;
    return lines.slice(0, maxLines).join("\n");
  };

  const promptTruncated = truncate(prompt, COLLAPSED_LINES);
  const promptOverflows = prompt.split("\n").length > COLLAPSED_LINES;
  const resultTruncated = truncate(resultText, COLLAPSED_LINES);
  const resultOverflows = resultText.split("\n").length > COLLAPSED_LINES;

  return (
    <div
      data-testid="agent-result"
      className="w-full rounded-lg border border-border overflow-hidden cursor-pointer select-none"
      onClick={() => setExpanded(!expanded)}
    >
      {/* ── Band 1: Title bar ── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-surface">
        <Bot
          size={14}
          className="shrink-0 text-muted/60"
          style={isRunning ? { color: "var(--theme-primary)" } : undefined}
        />
        <div className="min-w-0 flex-1 flex items-center gap-2">
          <span className="text-[13px] text-text font-medium">{title}</span>
          {description && (
            <span className="text-[12px] text-muted/60 truncate">{description}</span>
          )}
        </div>
        <span
          className={`w-2 h-2 rounded-full shrink-0 ${isRunning ? "animate-pulse-dot" : ""}`}
          style={{ backgroundColor: dotColor }}
        />
      </div>

      {/* ── Band 2: Prompt (click to expand) ── */}
      {prompt && (
        <div className="border-t border-border/50 bg-code-bg px-3 py-1.5 overflow-hidden">
          <pre
            className="m-0 text-[12px] text-muted leading-snug whitespace-pre-wrap break-words"
            style={!expanded && promptOverflows ? { maxHeight: `calc(${COLLAPSED_LINES} * ${LINE_HEIGHT})`, overflow: "hidden" } : undefined}
          >
            {expanded ? prompt : promptTruncated}
          </pre>
        </div>
      )}

      {/* ── Band 3: Live status (during streaming) ── */}
      {isRunning && (
        <div className="border-t border-border/50 bg-code-bg px-3 py-1.5">
          <div className="flex items-center gap-2 text-[12px] text-muted/60">
            {lastToolName && (
              <span className="font-mono">{lastToolName}</span>
            )}
            {toolUses > 0 && (
              <span>· {toolUses} tool{toolUses !== 1 ? "s" : ""}</span>
            )}
            {durationMs > 0 && (
              <span>· {durationSec}s</span>
            )}
          </div>
          <div className="text-[11px] text-muted/40 mt-0.5 truncate">
            {statusDescription}
          </div>
        </div>
      )}

      {/* ── Band 4: Result (after completion) ── */}
      {hasResult && (
        <div className="border-t border-border bg-code-bg px-3 py-2 overflow-hidden">
          {toolUses > 0 && (
            <div className="text-[11px] text-muted/40 mb-1">
              {toolUses} tool{toolUses !== 1 ? "s" : ""} · {durationSec}s
            </div>
          )}
          <pre
            className="m-0 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words"
            style={!expanded && resultOverflows ? { maxHeight: `calc(${COLLAPSED_LINES} * ${LINE_HEIGHT})`, overflow: "hidden" } : undefined}
          >
            {expanded ? resultText : resultTruncated}
          </pre>
        </div>
      )}
    </div>
  );
};
