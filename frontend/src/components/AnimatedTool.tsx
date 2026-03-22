/**
 * AnimatedTool — wraps any tool component with a two-phase entrance.
 *
 * Phase 1: Space opens — outer wrapper animates height from 0 to auto,
 *          overflow hidden. Simultaneously scrolls the thread viewport to
 *          bottom on every frame so the cursor stays fixed and the chat
 *          above appears to slide up.
 * Phase 2: Card slides in from off-screen left. Pure slide, no fade.
 *
 * Dedup: tracks toolCallId in a module-level Set. Each unique tool call
 * animates exactly once; subsequent re-renders skip animation.
 *
 * Skips animation during page load / replay (moduleReady pattern).
 */

import { useState, useRef, useCallback, type ComponentType } from "react";
import { motion } from "framer-motion";

let moduleReady = false;
setTimeout(() => {
  moduleReady = true;
}, 1500);

/** Tool call IDs that have already animated — survives component lifecycle. */
const animatedIds = new Set<string>();

/** Timing constants (seconds). */
const SPACE_DURATION = 0.35;    // height 0 → auto
const SPACE_EASE = [0.4, 0, 0.2, 1] as const; // Material ease-out
const SLIDE_DURATION = 0.4;     // slide from left
const SLIDE_DELAY = 0.15;       // slight overlap with space opening
const SLIDE_EASE = "easeOut" as const;

/**
 * Find the nearest scrollable ancestor (the thread viewport).
 * Walks up the DOM looking for an element with overflow-y: scroll or auto.
 */
function findScrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement;
  while (node) {
    const style = getComputedStyle(node);
    if (style.overflowY === "scroll" || style.overflowY === "auto") return node;
    node = node.parentElement;
  }
  return null;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function animated<P extends Record<string, any>>(
  Component: ComponentType<P>
): ComponentType<P> {
  const Wrapped = (props: P) => {
    const toolCallId = (props as { toolCallId?: string }).toolCallId;
    const outerRef = useRef<HTMLDivElement>(null);

    // useState initializer runs once per mount — frozen decision
    const [shouldAnimate] = useState(() => {
      if (!moduleReady) return false;
      if (!toolCallId) return false;
      if (animatedIds.has(toolCallId)) return false;
      animatedIds.add(toolCallId);
      return true;
    });

    // On every frame of the height animation, scroll the viewport to bottom.
    // This keeps the cursor fixed and makes the chat appear to slide up.
    const handleUpdate = useCallback(() => {
      const viewport = findScrollParent(outerRef.current);
      if (viewport) {
        viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
      }
    }, []);

    return (
      /* Phase 1: Space opens. Height 0 → auto. Scrolls viewport to bottom
         on each frame so the cursor stays put and chat slides up. */
      <motion.div
        ref={outerRef}
        initial={shouldAnimate ? { height: 0 } : false}
        animate={{ height: "auto" }}
        transition={{
          height: { duration: SPACE_DURATION, ease: SPACE_EASE },
        }}
        onUpdate={shouldAnimate ? handleUpdate : undefined}
        style={{ overflow: "hidden" }}
      >
        {/* Phase 2: Card slides in from off-screen left. No fade — pure slide. */}
        <motion.div
          initial={shouldAnimate ? { x: "-100%" } : false}
          animate={{ x: 0 }}
          transition={{
            duration: SLIDE_DURATION,
            ease: SLIDE_EASE,
            delay: shouldAnimate ? SLIDE_DELAY : 0,
          }}
        >
          <Component {...props} />
        </motion.div>
      </motion.div>
    );
  };

  Wrapped.displayName = `Animated(${
    Component.displayName || Component.name || "Component"
  })`;

  return Wrapped;
}
