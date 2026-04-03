/**
 * Dev preview page for the ContextMeter component.
 *
 * Shows the meter at every interesting threshold so we can
 * pixelfuck the colors, sizing, and transitions in one view.
 *
 * Route: /dev/context-meter
 */

import { ContextMeter } from "../components/ContextMeter";

const DEMO_VALUES = [0, 15, 30, 45, 55, 62, 65, 70, 75, 80, 85, 90, 100];

const label = (pct: number): string => {
  if (pct < 65) return "comfortable";
  if (pct < 75) return "warming up";
  if (pct < 85) return "hot";
  return "compaction zone";
};

export default function DevContextMeter() {
  return (
    <div className="min-h-screen bg-background text-text p-8">
      <h1 className="text-2xl mb-1">Context Meter</h1>
      <p className="text-muted mb-8 text-sm">
        Component preview. Click any meter to copy its percentage.
      </p>

      {/* ── Lineup ── */}
      <div className="max-w-xl mx-auto space-y-5">
        {DEMO_VALUES.map((pct) => (
          <div key={pct} className="flex items-center gap-6">
            <span className="text-muted text-sm font-mono w-12 text-right">
              {pct}%
            </span>
            <ContextMeter percent={pct} />
            <span className="text-muted/40 text-xs">{label(pct)}</span>
          </div>
        ))}
      </div>

      {/* ── Mock status bar ── */}
      <div className="max-w-xl mx-auto mt-16">
        <h2 className="text-lg mb-4 text-muted">In context — mock status bar</h2>

        <div className="rounded-lg border border-border overflow-hidden">
          {/* Status bar */}
          <div className="flex items-center justify-between px-4 h-8 bg-surface border-b border-border">
            <div className="flex items-center gap-2.5">
              {/* Connection dot */}
              <span
                className="inline-block w-[7px] h-[7px] rounded-full"
                style={{ backgroundColor: "var(--primary)" }}
              />
              {/* Session ID */}
              <span className="font-mono text-[11px] text-muted">a1b2c3d4</span>
            </div>
            <ContextMeter percent={62.3} />
          </div>

          {/* Fake chat area */}
          <div className="h-48 bg-background flex items-center justify-center">
            <span className="text-muted/20 text-sm italic">messages</span>
          </div>
        </div>

        {/* Second mock — amber zone */}
        <div className="rounded-lg border border-border overflow-hidden mt-6">
          <div className="flex items-center justify-between px-4 h-8 bg-surface border-b border-border">
            <div className="flex items-center gap-2.5">
              <span
                className="inline-block w-[7px] h-[7px] rounded-full animate-pulse-dot"
                style={{ backgroundColor: "var(--success)" }}
              />
              <span className="font-mono text-[11px] text-muted">f9e8d7c6</span>
            </div>
            <ContextMeter percent={68.4} />
          </div>
          <div className="h-48 bg-background flex items-center justify-center">
            <span className="text-muted/20 text-sm italic">messages (API in flight)</span>
          </div>
        </div>

        {/* Third mock — red zone */}
        <div className="rounded-lg border border-border overflow-hidden mt-6">
          <div className="flex items-center justify-between px-4 h-8 bg-surface border-b border-border">
            <div className="flex items-center gap-2.5">
              <span
                className="inline-block w-[7px] h-[7px] rounded-full"
                style={{ backgroundColor: "var(--primary)" }}
              />
              <span className="font-mono text-[11px] text-muted">deadbeef</span>
            </div>
            <ContextMeter percent={81.2} />
          </div>
          <div className="h-48 bg-background flex items-center justify-center">
            <span className="text-muted/20 text-sm italic">messages (compaction approaching)</span>
          </div>
        </div>
      </div>
    </div>
  );
}
