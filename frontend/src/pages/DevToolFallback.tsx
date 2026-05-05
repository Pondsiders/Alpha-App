/**
 * Dev preview: ToolFallback in all states.
 * Visit /dev/tool-fallback to see them side by side.
 */

import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import { TooltipProvider } from "@/components/ui/tooltip";

const SAMPLE_BASH_OUTPUT = `  pid | usename |  state   | sent_lsn  | write_lsn
------+---------+----------+-----------+-----------
 1247 | alpha   | streaming | 4/8C000060 | 4/8C000060
 1389 | alpha   | streaming | 4/8C000060 | 4/8C000060
(2 rows)`;

const SAMPLE_SEARCH_OUTPUT = `## Memory #16180 Thu Apr 2 2026, 10:00 AM (score 0.82)
Rosemary's Chirps letter — independent conclusion about consecutive APOD convergences.

## Memory #15758 Fri Mar 27 2026, 5:29 PM (score 0.78)
Friday dinner #5 — Suddenly Syria. Rosemary's chain was fine, independent.`;

const SAMPLE_LONG_OUTPUT = Array.from({ length: 40 }, (_, i) =>
  `Line ${i + 1}: The quick brown fox jumps over the lazy dog.`
).join("\n");

const SAMPLE_ERROR_OUTPUT = `Error: ENOENT: no such file or directory, open '/Pondside/Workshop/Projects/nonexistent.py'`;

export default function DevToolFallback() {
  return (
    <TooltipProvider>
      <div className="min-h-screen bg-background text-foreground p-8">
        <h1 className="text-2xl font-semibold mb-2">ToolFallback States</h1>
        <p className="text-muted-foreground mb-8 text-sm">
          Dev preview — all tool fallback states for pixelfucking.
        </p>

        <div className="max-w-2xl mx-auto flex flex-col gap-6">

          {/* 1. Streaming (partial args, no result yet) */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Streaming (partial args)
            </h2>
            <ToolFallback
              toolName="Bash"
              toolCallId="tc_001"
              args={{}}
              argsText='{"command": "docker exec alpha-post'
            />
          </section>

          {/* 2. Running (complete args, no result yet) */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Running (executing)
            </h2>
            <ToolFallback
              toolName="Bash"
              toolCallId="tc_002"
              args={{ command: "docker exec alpha-postgres pg_stat_replication | head -5" }}
              argsText='{"command": "docker exec alpha-postgres pg_stat_replication | head -5"}'
            />
          </section>

          {/* 3. Success — short output */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Success — short output
            </h2>
            <ToolFallback
              toolName="Bash"
              toolCallId="tc_003"
              args={{ command: "docker exec alpha-postgres pg_stat_replication | head -5" }}
              argsText='{"command": "docker exec alpha-postgres pg_stat_replication | head -5"}'
              result={SAMPLE_BASH_OUTPUT}
              status={{ type: "complete" }}
            />
          </section>

          {/* 4. Success — search tool */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Success — search tool
            </h2>
            <ToolFallback
              toolName="mcp__alpha__search"
              toolCallId="tc_004"
              args={{ query: "Rosemary correspondence last week" }}
              argsText='{"query": "Rosemary correspondence last week"}'
              result={SAMPLE_SEARCH_OUTPUT}
              status={{ type: "complete" }}
            />
          </section>

          {/* 5. Success — long output (truncated) */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Success — long output (truncated)
            </h2>
            <ToolFallback
              toolName="Read"
              toolCallId="tc_005"
              args={{ file_path: "/Pondside/Workshop/Projects/Alpha-App/backend/src/alpha_app/chat.py" }}
              argsText='{"file_path": "/Pondside/Workshop/Projects/Alpha-App/backend/src/alpha_app/chat.py"}'
              result={SAMPLE_LONG_OUTPUT}
              status={{ type: "complete" }}
            />
          </section>

          {/* 6. Error */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Error
            </h2>
            <ToolFallback
              toolName="Read"
              toolCallId="tc_006"
              args={{ file_path: "/Pondside/Workshop/Projects/nonexistent.py" }}
              argsText='{"file_path": "/Pondside/Workshop/Projects/nonexistent.py"}'
              result={SAMPLE_ERROR_OUTPUT}
              status={{ type: "incomplete", reason: "error" }}
            />
          </section>

          {/* 7. Store tool — memory content */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              Store — memory tool
            </h2>
            <ToolFallback
              toolName="mcp__alpha__store"
              toolCallId="tc_007"
              args={{ memory: "Wed Apr 8 2026. Capsule #1 sealed. The continuity system is born." }}
              argsText='{"memory": "Wed Apr 8 2026. Capsule #1 sealed. The continuity system is born."}'
              result="Memory stored (id: 16656)."
              status={{ type: "complete" }}
            />
          </section>

          {/* 8. No args (empty tool call) */}
          <section>
            <h2 className="text-xs font-mono text-muted-foreground mb-2 uppercase tracking-wide">
              No args
            </h2>
            <ToolFallback
              toolName="mcp__alpha__recent"
              toolCallId="tc_008"
              args={{}}
              argsText="{}"
              result="## Memory #16663 Wed Apr 8 2026, 12:10 PM\nMorning scorecard..."
              status={{ type: "complete" }}
            />
          </section>

        </div>
      </div>
    </TooltipProvider>
  );
}
