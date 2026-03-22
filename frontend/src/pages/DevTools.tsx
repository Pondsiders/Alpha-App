/**
 * Dev preview page for tool components.
 *
 * Shows every tool at every interesting state — compact and expanded,
 * running and complete, success and error. One giant page for pixelfucking.
 *
 * Route: /dev/tools
 */

import { BashResult } from "../components/tools/BashResult";
import { GrepResult } from "../components/tools/GrepResult";
import { ToolFallback } from "../components/ToolFallback";
import { ToolGroup } from "../components/ToolGroup";

/** Wrapper that mocks the ToolCallMessagePartComponent props */
function MockTool({
  label,
  Component,
  toolName,
  argsText,
  result,
  isError,
}: {
  label: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  Component: any;
  toolName: string;
  argsText: string;
  result?: string | { content: Array<{ type: string; text: string }> };
  isError?: boolean;
}) {
  const status = isError
    ? { type: "incomplete" as const, reason: "error" as const }
    : result !== undefined
      ? { type: "complete" as const }
      : { type: "running" as const };

  return (
    <div className="mb-6">
      <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
        {label}
      </div>
      <div className="max-w-2xl">
        <ToolGroup startIndex={0} endIndex={0}>
          <Component
            toolName={toolName}
            toolCallId={`mock-${label.replace(/\s+/g, "-")}`}
            argsText={argsText}
            result={result}
            status={status}
            addResult={() => {}}
            resume={() => {}}
          />
        </ToolGroup>
      </div>
    </div>
  );
}

/** Section header */
function Section({ title }: { title: string }) {
  return (
    <h2 className="text-lg text-primary mb-4 mt-12 first:mt-0">{title}</h2>
  );
}

export default function DevTools() {
  return (
    <div className="min-h-screen bg-background text-text p-8">
      <h1 className="text-2xl mb-1">Tool Components</h1>
      <p className="text-muted mb-8 text-sm">
        Every tool, every state, one page. Pixelfuck at will.
      </p>

      <div className="max-w-3xl mx-auto space-y-2">
        {/* ═══════════════ BASH ═══════════════ */}
        <Section title="BashResult — Compact States" />

        <MockTool
          label="Void — tool-use-start, no JSON yet"
          Component={BashResult}
          toolName="Bash"
          argsText=""
        />

        <MockTool
          label="Streaming — partial JSON"
          Component={BashResult}
          toolName="Bash"
          argsText='{"comm'
        />

        <MockTool
          label="Running — JSON complete, no result"
          Component={BashResult}
          toolName="Bash"
          argsText={JSON.stringify({
            command: "sleep 5 && echo 'Hello world'",
            description: "Five second delay then echo",
          })}
        />

        <MockTool
          label="Complete — short output"
          Component={BashResult}
          toolName="Bash"
          argsText={JSON.stringify({
            command: "echo 'Hello world'",
            description: "Say hello",
          })}
          result="Hello world"
        />

        <MockTool
          label="Complete — long output (should truncate in preview)"
          Component={BashResult}
          toolName="Bash"
          argsText={JSON.stringify({
            command: "git log --oneline -20",
            description: "Show recent commits",
          })}
          result={`e76205b Replace TypeOnBuffer with assistant-ui native smooth streaming
52ddedf feat: adaptive type-on engine with zero duplication
aa08bf2 feat: zero-flash handoff via dangerouslySetInnerHTML
78054ad Revert "feat: single-div handoff"
2008972 fix: hide empty debug vitals div
133cbd0 feat: single-div handoff — hijack React's own MarkdownText
794f95d feat: block queue — natural text drain before tool cards
7038364 feat: adaptive streaming type-on with React bypass
1f3140f chore: revert tool animation, bump type-on to 4cpf
09f9e1b fix: skip tray animation on replay
fac2842 feat: MemoryStore grow-beat-slide animation
942c3e1 polish: memory card styling
6039e5e polish: ToolGroup color hierarchy
dfdea0d feat: ToolGroup container with spring-animated growth
b3c1f9a fix: scroll to bottom on send
a8e7d24 chore: remove dead streaming code
3f2f8c7 feat: MemoryTray inset layout
f1b2c3d feat: smooth scroll animation 400ms
e9a8b7c fix: turn anchor with ViewportSlack
d5c4a3b feat: BashResult terminal UI`}
        />

        <MockTool
          label="Error — exit code 1"
          Component={BashResult}
          toolName="Bash"
          argsText={JSON.stringify({
            command: "cat /nonexistent/file",
            description: "Read a file that doesn't exist",
          })}
          result={`Exit code 1\ncat: /nonexistent/file: No such file or directory`}
          isError
        />

        <MockTool
          label="Long command — truncated in compact"
          Component={BashResult}
          toolName="Bash"
          argsText={JSON.stringify({
            command:
              "sleep 5 && echo 'The quick brown duck jumped over the lazy cat who was sleeping on a pile of stolen bread crusts in the kitchen while the green banker\\'s lamp cast amber light'",
            description:
              "Five second delay with a very long command string to test truncation",
          })}
          result="The quick brown duck jumped over the lazy cat who was sleeping on a pile of stolen bread crusts in the kitchen while the green banker's lamp cast amber light"
        />

        {/* ═══════════════ GREP / GLOB ═══════════════ */}
        <Section title="GrepResult — States" />

        <MockTool
          label="Grep — running"
          Component={GrepResult}
          toolName="Grep"
          argsText={JSON.stringify({
            pattern: "ToolGroup",
            path: "/Pondside/Workshop/Projects/Alpha-App/frontend/src",
          })}
        />

        <MockTool
          label="Grep — few matches"
          Component={GrepResult}
          toolName="Grep"
          argsText={JSON.stringify({
            pattern: "ToolGroup",
            path: "/Pondside/Workshop/Projects/Alpha-App/frontend/src",
          })}
          result={`Found 2 files\nsrc/components/ToolGroup.tsx\nsrc/pages/ChatPage.tsx`}
        />

        <MockTool
          label="Grep — many matches (truncated)"
          Component={GrepResult}
          toolName="Grep"
          argsText={JSON.stringify({
            pattern: "className",
            path: "/Pondside/Workshop/Projects/Alpha-App/frontend/src",
          })}
          result={Array.from(
            { length: 30 },
            (_, i) => `src/components/File${i + 1}.tsx`
          ).join("\n")}
        />

        <MockTool
          label="Grep — no matches"
          Component={GrepResult}
          toolName="Grep"
          argsText={JSON.stringify({ pattern: "xyzzy_nonexistent" })}
          result="No matches found"
        />

        <MockTool
          label="Glob — running"
          Component={GrepResult}
          toolName="Glob"
          argsText={JSON.stringify({ pattern: "**/*.tsx" })}
        />

        <MockTool
          label="Glob — matches"
          Component={GrepResult}
          toolName="Glob"
          argsText={JSON.stringify({
            pattern: "**/*.tsx",
            path: "/Pondside/Workshop/Projects/Alpha-App/frontend/src/components/tools",
          })}
          result={`src/components/tools/BashResult.tsx\nsrc/components/tools/GrepResult.tsx\nsrc/components/tools/MemoryStore.tsx\nsrc/components/tools/ReadResult.tsx\nsrc/components/tools/EditResult.tsx`}
        />

        {/* ═══════════════ TOOL FALLBACK ═══════════════ */}
        <Section title="ToolFallback — Generic Tools" />

        <MockTool
          label="Agent tool — running"
          Component={ToolFallback}
          toolName="Agent"
          argsText={JSON.stringify({
            description: "Research animated div resizing",
            prompt: "I need to find the best way to...",
          })}
        />

        <MockTool
          label="Agent tool — complete"
          Component={ToolFallback}
          toolName="Agent"
          argsText={JSON.stringify({
            description: "Research animated div resizing",
            prompt: "I need to find the best way to...",
          })}
          result="The researcher confirmed: ResizeObserver + animate is the answer."
        />

        <MockTool
          label="WebFetch — running"
          Component={ToolFallback}
          toolName="WebFetch"
          argsText={JSON.stringify({
            url: "https://www.nytimes.com",
            prompt: "Summarize the front page",
          })}
        />

        <MockTool
          label="MCP store — complete"
          Component={ToolFallback}
          toolName="mcp__alpha__search"
          argsText={JSON.stringify({ query: "ToolGroup animation" })}
          result="Found 5 matching memories"
        />

        {/* ═══════════════ TOOL GROUP ═══════════════ */}
        <Section title="ToolGroup — Grouped Tools" />

        <div className="mb-6">
          <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
            Two tools in a group
          </div>
          <div className="max-w-2xl">
            <ToolGroup startIndex={0} endIndex={1}>
              <BashResult
                toolName="Bash"
                toolCallId="group-bash-1"
                argsText={JSON.stringify({
                  command: "echo 'first'",
                  description: "First command",
                })}
                result="first"
                status={{ type: "complete" }}
                addResult={() => {}}
                resume={() => {}}
              />
              <GrepResult
                toolName="Grep"
                toolCallId="group-grep-1"
                argsText={JSON.stringify({ pattern: "ToolGroup" })}
                result={`Found 2 files\nsrc/ToolGroup.tsx\nsrc/ChatPage.tsx`}
                status={{ type: "complete" }}
                addResult={() => {}}
                resume={() => {}}
              />
            </ToolGroup>
          </div>
        </div>

        <div className="mb-6">
          <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
            Three tools in a group (mixed running/complete)
          </div>
          <div className="max-w-2xl">
            <ToolGroup startIndex={0} endIndex={2}>
              <BashResult
                toolName="Bash"
                toolCallId="group-bash-2"
                argsText={JSON.stringify({
                  command: "echo 'done'",
                  description: "Already finished",
                })}
                result="done"
                status={{ type: "complete" }}
                addResult={() => {}}
                resume={() => {}}
              />
              <GrepResult
                toolName="Grep"
                toolCallId="group-grep-2"
                argsText={JSON.stringify({ pattern: "ResizeObserver" })}
                status={{ type: "running" }}
                addResult={() => {}}
                resume={() => {}}
              />
              <BashResult
                toolName="Bash"
                toolCallId="group-bash-3"
                argsText={JSON.stringify({
                  command: "sleep 10",
                  description: "Still running",
                })}
                status={{ type: "running" }}
                addResult={() => {}}
                resume={() => {}}
              />
            </ToolGroup>
          </div>
        </div>
      </div>
    </div>
  );
}
