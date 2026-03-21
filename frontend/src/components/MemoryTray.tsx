import { useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { MemoryCard } from "./MemoryCard";
import type { RecalledMemory } from "../store";

/**
 * Module-level flag: starts false, flips to true after a short delay.
 * Any MemoryTray that mounts before the flag flips is part of the initial
 * render (replay/page-load) and skips animation. Trays mounting after
 * the flag flips are "new" and get the full grow-beat-slide.
 */
let moduleReady = false;
setTimeout(() => {
  moduleReady = true;
}, 1500); // generous window for replay to finish

/**
 * Inset tray layout for recalled memories.
 *
 * Round-cornered container with a recessed feel (inner shadow, lighter bg).
 * Cards scroll horizontally inside it — no overlap, each fully readable.
 * The filing drawer, not the poker hand.
 *
 * Animation: grow → beat → slide (only on new trays, not replayed ones).
 * 1. Tray grows open (scaleY 0→1, 300ms ease-out, bottom-anchored)
 * 2. Brief pause (200ms) so you register the opening
 * 3. Cards slide in together from the right (400ms ease-out)
 *
 * Pass animate={false} to explicitly skip animation.
 */
export function MemoryTray({
  memories,
  animate,
}: {
  memories: RecalledMemory[];
  animate?: boolean;
}) {
  // Capture whether this tray was born during the initial render batch
  const shouldAnimate = useRef(animate ?? moduleReady).current;
  // Sort by score descending — best match first (leftmost)
  const sorted = useMemo(
    () => [...memories].sort((a, b) => b.score - a.score),
    [memories]
  );

  return (
    <motion.div
      initial={shouldAnimate ? { scaleY: 0, opacity: 0 } : false}
      animate={{ scaleY: 1, opacity: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      style={{ transformOrigin: "bottom" }}
      className="w-full rounded-xl bg-white/[0.03]
                 shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)]
                 overflow-hidden"
    >
      <div className="overflow-x-auto scrollbar-thin py-6">
        <motion.div
          initial={shouldAnimate ? { x: "100%" } : false}
          animate={{ x: 0 }}
          transition={{ duration: 0.4, ease: "easeOut", delay: shouldAnimate ? 0.5 : 0 }}
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
