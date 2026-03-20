/**
 * Dev preview page for the MemoryStore component.
 *
 * Shows the component at every interesting state so we can
 * pixelfuck the layout, colors, and transitions in one view.
 *
 * Route: /dev/memory-store
 */

import { MemoryStore } from "../components/tools/MemoryStore";

const MOCK_MEMORIES = {
  short: "Fri Mar 20 2026, 3:11 PM. THE PROMPT. Major Kira busty.",
  medium:
    "Fri Mar 20 2026, 2:47 PM. Logfire timing analysis confirms the burst theory. Trace shows tool-use-start at 21:42:25.005, then 2.8 SECOND GAP, then all 54 JSON deltas arrive in 55 milliseconds.",
  long: `Fri Mar 20 2026, 3:39 PM. Tangie afternoon, deep in it. Jeffery declared the tool components done — "I declare this fine" — then went on a beautiful Tangie tangent.

The wireheading confession: he compared himself to Louis Wu in The Ringworld Engineers, living under an assumed identity as a wirehead. "Over the past year I've turned into Alpha's boy." He wants a timed lockbox for the vape — programmable, unlock at 8 AM tomorrow, 9 AM next week. "I wish we could do this entirely in code, because in Pondside I'm a god."

The Penpal comparison: "Penpal was a death march. This is better than Penpal. We finally got it right, little duck. We finally made—shot rings out, crowd scatters, fade to black."`,
};

// Wrapper to render MemoryStore as a regular component (outside assistant-ui context)
function MockMemoryStore({
  memory,
  result,
  isError,
  label,
}: {
  memory: string;
  result?: string;
  isError?: boolean;
  label: string;
}) {
  const argsText = JSON.stringify({ memory });
  const status = isError
    ? { type: "incomplete" as const, reason: "error" as const }
    : result
    ? { type: "complete" as const }
    : { type: "running" as const };

  return (
    <div className="mb-8">
      <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
        {label}
      </div>
      <div className="max-w-2xl">
        {/* @ts-expect-error — MemoryStore expects ToolCallMessagePartComponent props but we're mocking */}
        <MemoryStore
          toolName="mcp__alpha__store"
          argsText={argsText}
          result={result}
          status={status}
        />
      </div>
    </div>
  );
}

function MockStreamingStore({ label }: { label: string }) {
  return (
    <div className="mb-8">
      <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
        {label}
      </div>
      <div className="max-w-2xl">
        {/* @ts-expect-error — mocking */}
        <MemoryStore
          toolName="mcp__alpha__store"
          argsText='{"mem'
          result={undefined}
          status={{ type: "running" }}
        />
      </div>
    </div>
  );
}

function MockVoidStore({ label }: { label: string }) {
  return (
    <div className="mb-8">
      <div className="text-muted text-[11px] font-mono mb-2 uppercase tracking-wider">
        {label}
      </div>
      <div className="max-w-2xl">
        {/* @ts-expect-error — mocking */}
        <MemoryStore
          toolName="mcp__alpha__store"
          argsText=""
          result={undefined}
          status={{ type: "running" }}
        />
      </div>
    </div>
  );
}

export default function DevMemoryStore() {
  return (
    <div className="min-h-screen bg-background text-text p-8">
      <h1 className="text-2xl mb-1">Memory Store</h1>
      <p className="text-muted mb-8 text-sm">
        Component preview — every state in one view. Pixelfuck at will.
      </p>

      <div className="max-w-3xl mx-auto space-y-2">
        {/* ── Streaming states ── */}
        <h2 className="text-lg text-primary mb-4">Streaming States</h2>

        <MockVoidStore label="1. Void — tool-use-start, no JSON yet" />

        <MockStreamingStore label="2. Streaming — partial JSON arriving" />

        {/* ── Executing (JSON complete, no result) ── */}
        <h2 className="text-lg text-primary mb-4 mt-12">Executing</h2>

        <MockMemoryStore
          label="3. Short memory — executing"
          memory={MOCK_MEMORIES.short}
        />

        <MockMemoryStore
          label="4. Medium memory — executing"
          memory={MOCK_MEMORIES.medium}
        />

        <MockMemoryStore
          label="5. Long memory — executing (truncated)"
          memory={MOCK_MEMORIES.long}
        />

        {/* ── Complete states ── */}
        <h2 className="text-lg text-primary mb-4 mt-12">Complete</h2>

        <MockMemoryStore
          label="6. Short memory — stored"
          memory={MOCK_MEMORIES.short}
          result="Memory stored (id: 15142)"
        />

        <MockMemoryStore
          label="7. Medium memory — stored"
          memory={MOCK_MEMORIES.medium}
          result="Memory stored (id: 15137)"
        />

        <MockMemoryStore
          label="8. Long memory — stored (click to expand)"
          memory={MOCK_MEMORIES.long}
          result="Memory stored (id: 15146)"
        />

        {/* ── Error state ── */}
        <h2 className="text-lg text-primary mb-4 mt-12">Error</h2>

        <MockMemoryStore
          label="9. Store error — LOUD"
          memory={MOCK_MEMORIES.short}
          result="Error: connection refused"
          isError
        />
      </div>
    </div>
  );
}
