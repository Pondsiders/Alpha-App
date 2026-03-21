import { useRef, useState, useEffect, useMemo } from "react";
import { MemoryCard } from "./MemoryCard";
import type { RecalledMemory } from "../store";

/** Card width range — must match MemoryCard's min-w / max-w */
const CARD_MIN_W = 160;

/**
 * Fanned/overlapping card layout, like playing cards spread in a hand.
 *
 * Cards are sorted by score (highest first / leftmost) and stacked so
 * the leftmost card is on top (highest z-index). Overlap is calculated
 * dynamically so cards compress to fit the container width.
 */
export function FannedCards({ memories }: { memories: RecalledMemory[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [overlap, setOverlap] = useState(70);

  // Sort by score descending — best match on the left, in front
  const sorted = useMemo(
    () => [...memories].sort((a, b) => b.score - a.score),
    [memories]
  );

  useEffect(() => {
    if (!containerRef.current || sorted.length <= 1) return;

    const containerW = containerRef.current.offsetWidth;
    const n = sorted.length;

    // Total width if no overlap: n * CARD_MIN_W
    // We need: CARD_MIN_W + (n-1) * (CARD_MIN_W - overlap) <= containerW
    // Solving: overlap >= CARD_MIN_W - (containerW - CARD_MIN_W) / (n - 1)
    const neededOverlap = CARD_MIN_W - (containerW - CARD_MIN_W) / (n - 1);
    // Clamp: at least 40px visible per card, at most 70% overlap
    const maxOverlap = CARD_MIN_W * 0.7; // 112px — show at least 48px per card
    const minOverlap = 40; // gentle overlap for few cards
    setOverlap(Math.max(minOverlap, Math.min(maxOverlap, neededOverlap)));
  }, [sorted.length]);

  const n = sorted.length;

  return (
    <div
      ref={containerRef}
      className="flex justify-end items-end w-full overflow-visible pt-1"
    >
      {sorted.map((m, i) => (
        <div
          key={m.id}
          style={{
            marginLeft: i > 0 ? `-${overlap}px` : undefined,
            zIndex: (n - i) * 10, // leftmost on top, rightmost behind
          }}
        >
          <MemoryCard memory={m} />
        </div>
      ))}
    </div>
  );
}
