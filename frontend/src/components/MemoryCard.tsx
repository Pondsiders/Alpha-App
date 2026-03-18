/**
 * MemoryCard — A compact card showing a recalled memory.
 *
 * Renders as a small pill-shaped card with memory ID and score.
 * First line of memory content as preview. Click to expand (TBD).
 */

import type { RecalledMemory } from "../store";

interface MemoryCardProps {
  memory: RecalledMemory;
}

export function MemoryCard({ memory }: MemoryCardProps) {
  // First line of content as preview (truncated)
  const preview = memory.content
    ? memory.content.split("\n")[0].slice(0, 80)
    : "";

  return (
    <div
      className="flex-shrink-0 px-3 py-2 rounded-lg border border-border bg-surface/50 max-w-[220px] min-w-[160px] cursor-default select-none"
      title={memory.content}
    >
      <div className="flex items-center justify-between text-[11px] text-muted mb-0.5">
        <span className="font-mono">#{memory.id}</span>
        <span className="font-mono">{memory.score.toFixed(2)}</span>
      </div>
      {preview && (
        <div className="text-[12px] text-text/70 leading-tight line-clamp-2">
          {preview}
        </div>
      )}
    </div>
  );
}
