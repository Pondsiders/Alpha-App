import { useMemo } from "react";
import { MemoryCard } from "./MemoryCard";
import type { RecalledMemory } from "../store";

/**
 * Inset tray layout for recalled memories.
 *
 * Round-cornered container with a recessed feel (inner shadow, lighter bg).
 * Cards scroll horizontally inside it — no overlap, each fully readable.
 * The filing drawer, not the poker hand.
 */
export function MemoryTray({ memories }: { memories: RecalledMemory[] }) {
  // Sort by score descending — best match first (leftmost)
  const sorted = useMemo(
    () => [...memories].sort((a, b) => b.score - a.score),
    [memories]
  );

  return (
    <div
      className="w-full rounded-xl bg-white/[0.03]
                 shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)]
                 overflow-x-auto scrollbar-thin py-6"
    >
      <div className="flex gap-6 px-6">
        {sorted.map((m) => (
          <MemoryCard key={m.id} memory={m} flat />
        ))}
        {/* Spacer to preserve right padding inside scrollable container */}
        <div className="shrink-0 w-px" aria-hidden="true" />
      </div>
    </div>
  );
}
