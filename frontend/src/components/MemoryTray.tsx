import { useMemo } from "react";
import { motion } from "framer-motion";
import { MemoryCard } from "./MemoryCard";
import type { RecalledMemory } from "../store";

/**
 * Inset tray layout for recalled memories.
 *
 * Round-cornered container with a recessed feel (inner shadow, lighter bg).
 * Cards scroll horizontally inside it — no overlap, each fully readable.
 * The filing drawer, not the poker hand.
 *
 * Animation: grow → beat → slide.
 * 1. Tray grows open (height from 0, 300ms ease-out)
 * 2. Brief pause (200ms) so you register the opening
 * 3. Cards slide in together from the right (400ms ease-out)
 */
export function MemoryTray({ memories }: { memories: RecalledMemory[] }) {
  // Sort by score descending — best match first (leftmost)
  const sorted = useMemo(
    () => [...memories].sort((a, b) => b.score - a.score),
    [memories]
  );

  return (
    <motion.div
      initial={{ scaleY: 0, opacity: 0 }}
      animate={{ scaleY: 1, opacity: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      style={{ transformOrigin: "bottom" }}
      className="w-full rounded-xl bg-white/[0.03]
                 shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)]
                 overflow-hidden"
    >
      <div className="overflow-x-auto scrollbar-thin py-6">
        <motion.div
          initial={{ x: "100%" }}
          animate={{ x: 0 }}
          transition={{ duration: 0.4, ease: "easeOut", delay: 0.5 }}
          className="flex gap-6 px-6"
        >
          {sorted.map((m) => (
            <MemoryCard key={m.id} memory={m} flat />
          ))}
          {/* Spacer to preserve right padding inside scrollable container */}
          <div className="shrink-0 w-px" aria-hidden="true" />
        </motion.div>
      </div>
    </motion.div>
  );
}
