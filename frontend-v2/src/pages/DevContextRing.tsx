/**
 * Dev preview: ContextRing at every percentage and threshold state.
 * Visit /dev/context-ring to see it side-by-side for pixelfucking.
 */

import { ContextRing, contextColorClass } from "@/components/ContextRing";

// A selection of percentages that cover the three threshold states plus
// the boundaries. Each row is labeled with the state it's testing.
const SAMPLES: { percent: number; label: string }[] = [
  { percent: 0, label: "empty" },
  { percent: 15.9, label: "current reality — morning" },
  { percent: 48.3, label: "current reality — noon" },
  { percent: 65, label: "old threshold (200K era)" },
  { percent: 79, label: "just below amber" },
  { percent: 80, label: "amber threshold" },
  { percent: 85, label: "mid-amber" },
  { percent: 89, label: "just below red" },
  { percent: 90, label: "red threshold" },
  { percent: 95, label: "auto-compaction point" },
  { percent: 100, label: "full" },
];

export default function DevContextRing() {
  return (
    <div className="min-h-screen bg-background text-foreground p-8">
      <h1 className="text-2xl font-semibold mb-2">ContextRing Preview</h1>
      <p className="text-muted-foreground mb-8 text-sm">
        Dev preview — progress ring at various percentages with
        threshold-based colors. Thresholds: &lt;80% muted, 80-90% amber,
        &gt;90% red.
      </p>

      {/* Section: different sizes */}
      <section className="mb-12">
        <h2 className="text-xs font-mono text-muted-foreground mb-4 uppercase tracking-wide">
          Sizes (at 42% to show the fill)
        </h2>
        <div className="flex items-end gap-6 text-muted-foreground">
          <div className="flex flex-col items-center gap-2">
            <ContextRing percent={42} className="size-4" />
            <span className="text-xs font-mono text-muted-foreground/60">size-4</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ContextRing percent={42} className="size-5" />
            <span className="text-xs font-mono text-muted-foreground/60">size-5</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ContextRing percent={42} className="size-6" />
            <span className="text-xs font-mono text-muted-foreground/60">size-6</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ContextRing percent={42} className="size-8" />
            <span className="text-xs font-mono text-muted-foreground/60">size-8</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <ContextRing percent={42} className="size-12" />
            <span className="text-xs font-mono text-muted-foreground/60">size-12</span>
          </div>
        </div>
      </section>

      {/* Section: percentages with thresholds applied */}
      <section className="mb-12">
        <h2 className="text-xs font-mono text-muted-foreground mb-4 uppercase tracking-wide">
          Percentages at real size (size-5) with threshold colors
        </h2>
        <div className="flex flex-wrap gap-6">
          {SAMPLES.map(({ percent, label }) => (
            <div
              key={label}
              className="flex flex-col items-center gap-2 min-w-24"
            >
              <span className={contextColorClass(percent)}>
                <ContextRing percent={percent} className="size-5" />
              </span>
              <span className="font-mono text-xs text-foreground">
                {percent}%
              </span>
              <span className="text-xs text-muted-foreground/60 text-center">
                {label}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* Section: large-size showcase of each color state */}
      <section className="mb-12">
        <h2 className="text-xs font-mono text-muted-foreground mb-4 uppercase tracking-wide">
          Threshold states (size-12 for detail)
        </h2>
        <div className="flex gap-8">
          <div className="flex flex-col items-center gap-2">
            <span className="text-muted-foreground">
              <ContextRing percent={50} className="size-12" />
            </span>
            <span className="text-xs font-mono text-foreground">muted</span>
            <span className="text-xs text-muted-foreground/60">&lt; 80%</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <span className="text-primary">
              <ContextRing percent={85} className="size-12" />
            </span>
            <span className="text-xs font-mono text-foreground">primary</span>
            <span className="text-xs text-muted-foreground/60">80-90%</span>
          </div>
          <div className="flex flex-col items-center gap-2">
            <span className="text-destructive">
              <ContextRing percent={95} className="size-12" />
            </span>
            <span className="text-xs font-mono text-foreground">destructive</span>
            <span className="text-xs text-muted-foreground/60">&gt; 90%</span>
          </div>
        </div>
      </section>

      {/* Section: in-context — how it'll look in the real UI */}
      <section>
        <h2 className="text-xs font-mono text-muted-foreground mb-4 uppercase tracking-wide">
          In context — floating in top-right corner
        </h2>
        <div className="relative h-40 rounded-lg border border-border bg-background overflow-hidden">
          <div className="absolute right-3 top-3">
            <span className={contextColorClass(48.3)}>
              <ContextRing percent={48.3} className="size-5" />
            </span>
          </div>
          <div className="p-6 text-sm text-muted-foreground/60">
            (imagine the chat content here)
          </div>
        </div>
      </section>
    </div>
  );
}
