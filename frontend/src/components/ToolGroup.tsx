/**
 * ToolGroup — recessed container for consecutive tool-call parts.
 *
 * Same visual language as MemoryTray: roundrect with inner shadow, lighter
 * background, giving the illusion of a cutout into a layer below. "The chat
 * is the surface, stuff's going on under the surface."
 *
 * Assistant-ui automatically wraps consecutive tool-call parts in this
 * component via the ToolGroup slot in the components map. We just provide
 * the visual wrapper and entrance animation.
 *
 * Animation phases:
 * 1. Initial mount: height 0 → measured height (space opens, chat slides up)
 * 2. Growth: when new tools are added, we detect the height change via
 *    ResizeObserver on the inner container and animate to the new height.
 *    Once the animation settles, we switch back to height: "auto" so the
 *    container can flex naturally (e.g. tool output expanding).
 */

import { useRef, useEffect, useState, useCallback, type PropsWithChildren } from "react";
import { motion, useMotionValue, useSpring } from "framer-motion";

/**
 * Module-level flag: starts false, flips to true after initial render.
 * ToolGroups that mount before the flag flips are part of replay and
 * skip animation. Groups mounting after get the full entrance.
 */
let moduleReady = false;
setTimeout(() => {
  moduleReady = true;
}, 1500);

/** Timing constants (seconds). */
const OPEN_DURATION = 0.7;
const OPEN_EASE = [0.4, 0, 0.2, 1] as const;
const SPRING_CONFIG = { stiffness: 300, damping: 30, mass: 0.8 };

/**
 * Find the nearest scrollable ancestor (the thread viewport).
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

export function ToolGroup({
  children,
}: PropsWithChildren<{ startIndex: number; endIndex: number }>) {
  const outerRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const shouldAnimate = useRef(moduleReady).current;
  const [phase, setPhase] = useState<"opening" | "tracking" | "settled">(
    shouldAnimate ? "opening" : "settled"
  );

  // Spring-driven height for smooth growth animations.
  const heightMotion = useMotionValue(shouldAnimate ? 0 : "auto" as unknown as number);
  const heightSpring = useSpring(heightMotion, SPRING_CONFIG);

  // After initial open animation completes, switch to tracking mode.
  useEffect(() => {
    if (!shouldAnimate) return;
    const timer = setTimeout(() => {
      setPhase("tracking");
    }, OPEN_DURATION * 1000 + 50);
    return () => clearTimeout(timer);
  }, [shouldAnimate]);

  // Observe the inner container's height. When it changes (new tools added,
  // tool output arrives), animate the outer container to match.
  useEffect(() => {
    if (phase === "opening") return;
    const inner = innerRef.current;
    const outer = outerRef.current;
    if (!inner || !outer) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const newHeight = entry.contentBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
        // Add padding back (p-6 = 24px × 2 = 48px) + border compensation
        const totalHeight = newHeight + 48;

        if (phase === "tracking") {
          // Animate to new height
          heightMotion.set(totalHeight);

          // Scroll viewport to follow
          const viewport = findScrollParent(outer);
          if (viewport) {
            viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
          }
        } else {
          // Already settled — just let it be auto
          outer.style.height = "auto";
        }
      }
    });

    observer.observe(inner);
    return () => observer.disconnect();
  }, [phase, heightMotion]);

  // Scroll viewport to bottom during the initial open animation.
  const handleUpdate = useCallback(() => {
    if (phase !== "opening") return;
    const viewport = findScrollParent(outerRef.current);
    if (viewport) {
      viewport.scrollTop = viewport.scrollHeight - viewport.clientHeight;
    }
  }, [phase]);

  // For the opening phase, use Framer Motion's height animation.
  // For tracking phase, use the spring-driven height.
  // For settled phase, just auto.
  const style = (() => {
    if (phase === "opening") {
      return { overflow: "hidden" as const, transformOrigin: "bottom" as const };
    }
    if (phase === "tracking") {
      return {
        overflow: "hidden" as const,
        transformOrigin: "bottom" as const,
        height: heightSpring,
      };
    }
    return { overflow: "hidden" as const };
  })();

  return (
    <motion.div
      ref={outerRef}
      initial={shouldAnimate ? { height: 0, opacity: 0 } : false}
      animate={
        phase === "opening"
          ? { height: "auto", opacity: 1 }
          : phase === "settled"
            ? { opacity: 1 }
            : { opacity: 1 }
      }
      transition={{
        height: { duration: OPEN_DURATION, ease: OPEN_EASE },
        opacity: { duration: 0.2 },
      }}
      onUpdate={handleUpdate}
      style={style}
    >
      {/* Recessed container — same visual as MemoryTray but lighter */}
      <div
        ref={innerRef}
        className="w-full rounded-xl bg-white/[0.07]
                   shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)]
                   flex flex-col gap-6 p-6"
      >
        {children}
      </div>
    </motion.div>
  );
}
