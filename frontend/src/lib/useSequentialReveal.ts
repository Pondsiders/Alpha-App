/**
 * useSequentialReveal — reveal queue for sequential part rendering.
 *
 * Maintains a counter of how many parts are allowed to render. Each part
 * calls markDone(index) when its animation completes. The counter only
 * advances when the frontier part (the one currently animating) finishes.
 *
 * For completed messages (not streaming), skip this entirely and render
 * all parts.
 */

import { useState, useCallback, useRef, useEffect } from "react";

export function useSequentialReveal(totalParts: number) {
  // How many parts are allowed to render (1-indexed ceiling).
  // Start at 1 so the first part renders immediately.
  const [revealedCount, setRevealedCount] = useState(1);

  // Track which parts have reported "done"
  const doneSet = useRef(new Set<number>());

  // Use a ref for totalParts inside callbacks to avoid dependency churn
  const totalRef = useRef(totalParts);
  totalRef.current = totalParts;

  const markDone = useCallback((partIndex: number) => {
    doneSet.current.add(partIndex);
    setRevealedCount((prev) => {
      // The frontier is prev - 1 (0-indexed)
      if (doneSet.current.has(prev - 1)) {
        return Math.min(prev + 1, totalRef.current);
      }
      return prev;
    });
  }, []);

  // When totalParts grows (new parts arrive from the store), check if
  // the frontier is already done — if so, advance immediately.
  useEffect(() => {
    setRevealedCount((prev) => {
      if (doneSet.current.has(prev - 1)) {
        return Math.min(prev + 1, totalParts);
      }
      return prev;
    });
  }, [totalParts]);

  return { revealedCount, markDone };
}
