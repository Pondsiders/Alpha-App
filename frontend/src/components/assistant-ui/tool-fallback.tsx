/**
 * ToolFallback — Generic tool call UI.
 *
 * The catch-all consumer of ToolShell. Renders args as code and
 * results as monospace preformatted text. Custom tool UIs
 * (BashTool, StoreTool, etc.) graduate out of this component
 * by providing their own ToolShell consumers.
 */

import { Wrench } from "lucide-react";
import { ToolShell } from "./tool-shell";

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
  const safeName = toolName || "Unknown Tool";
  const displayName = safeName.charAt(0).toUpperCase() + safeName.slice(1);

  // Parse args
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
  const isError = status?.type === "incomplete" && status.reason === "error";

  // Derive status for ToolShell
  const shellStatus = (() => {
    if (!jsonComplete && !hasResult) return "streaming" as const;
    if (!hasResult && jsonComplete) return "running" as const;
    if (isError) return "error" as const;
    return "success" as const;
  })();

  // Arg summary (one-line)
  const argSummary = (() => {
    if (!jsonComplete) return argsText || "";
    const entries = Object.entries(parsedArgs);
    if (entries.length === 0) return "";

    if (parsedArgs.file_path) {
      const path = String(parsedArgs.file_path);
      const parts = path.split("/");
      return parts[parts.length - 1];
    }
    if (parsedArgs.query) {
      const q = String(parsedArgs.query);
      return q.length > 80 ? q.slice(0, 80) + "…" : q;
    }
    if (parsedArgs.pattern) return String(parsedArgs.pattern);
    if (parsedArgs.memory) {
      const m = String(parsedArgs.memory);
      return m.length > 80 ? m.slice(0, 80) + "…" : m;
    }
    if (parsedArgs.command) {
      const c = String(parsedArgs.command);
      return c.length > 80 ? c.slice(0, 80) + "…" : c;
    }

    const firstString = entries.find(([, v]) => typeof v === "string");
    if (firstString) {
      const val = String(firstString[1]);
      return val.length > 80 ? val.slice(0, 80) + "…" : val;
    }

    return `${entries.length} args`;
  })();

  // Full args
  const fullArgsText = jsonComplete
    ? JSON.stringify(parsedArgs, null, 2)
    : argsText || "";

  // Result text
  const resultText = hasResult
    ? typeof result === "string"
      ? result
      : JSON.stringify(result, null, 2)
    : "";
  const resultFirstLine = resultText.split("\n")[0] || "";

  // Has anything to show in input/output?
  const hasArgs = jsonComplete ? Object.keys(parsedArgs).length > 0 : !!argsText;
  const multiLineArgs = fullArgsText.split("\n").length > 1;
  const multiLineResult = resultText.split("\n").length > 1;

  return (
    <ToolShell
      icon={Wrench}
      name={displayName}
      status={shellStatus}

      input={hasArgs ? (
        <code className="text-xs font-mono text-foreground leading-relaxed truncate block">
          {argSummary}
        </code>
      ) : undefined}

      expandedInput={hasArgs && multiLineArgs ? (
        <pre className="m-0 text-xs font-mono text-foreground leading-relaxed whitespace-pre max-h-[300px] overflow-auto">
          {fullArgsText}
        </pre>
      ) : undefined}

      output={
        shellStatus === "running" ? (
          <span className="text-muted-foreground/50 text-xs font-mono italic">
            Executing…
          </span>
        ) : hasResult ? (
          <code
            className="text-xs font-mono leading-relaxed truncate block"
            style={{ color: isError ? "var(--color-destructive)" : "var(--color-foreground)" }}
          >
            {resultFirstLine}
          </code>
        ) : undefined
      }

      expandedOutput={hasResult && multiLineResult ? (
        <pre
          className="m-0 text-xs font-mono leading-relaxed whitespace-pre max-h-[600px] overflow-auto"
          style={{ color: isError ? "var(--color-destructive)" : "var(--color-foreground)" }}
        >
          {resultText}
        </pre>
      ) : undefined}
    />
  );
};
