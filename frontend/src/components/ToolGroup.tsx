/**
 * ToolGroup — recessed container for consecutive tool-call parts.
 *
 * Same visual language as MemoryTray: roundrect with inner shadow, lighter
 * background, giving the illusion of a cutout into a layer below.
 *
 * Animation: ResizeObserver measures the inner content's height.
 * Framer Motion animates to that height — both for the initial open
 * AND for growth when new tools are added. One source of truth.
 */

import { useRef, useState, useCallback, type PropsWithChildren } from "react";
import { motion } from "framer-motion";

let moduleReady = false;
setTimeout(() => {
  moduleReady = true;
}, 1500);

const OPEN_DURATION = 0.7;
const GROW_DURATION = 0.35;
const EASE = [0.4, 0, 0.2, 1] as const;

function findScrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement;
  while (node) {
    const style = getComputedStyle(node);
    if (style.overflowY === "scroll" || style.overflowY === "auto") return node;
    node = node.parentElement;
  }
  return null;
}

/** Only scroll if we're within ~150px of the bottom (user hasn't scrolled up). */
function scrollToBottomIfNear(el: HTMLElement | null) {
  if (!el) return;
  const distFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
  if (distFromBottom < 150) {
    el.scrollTop = el.scrollHeight - el.clientHeight;
  }
}

export function ToolGroup({
  children,
}: PropsWithChildren<{ startIndex: number; endIndex: number }>) {
  const outerRef = useRef<HTMLDivElement>(null);
  const shouldAnimate = useRef(moduleReady).current;
  const [height, setHeight] = useState(0);
  const isFirstMeasure = useRef(true);

  // Callback ref on the inner content div. When it mounts, we start
  // observing its height. Every change updates our animated target.
  const contentRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return;

    const observer = new ResizeObserver((entries) => {
      // Use borderBoxSize for the full height including padding.
      // contentRect.height misses the padding, cutting off corners.
      const box = entries[0]?.borderBoxSize?.[0];
      const h = box ? box.blockSize : (entries[0]?.contentRect.height ?? 0);
      if (h > 0) {
        setHeight(h);
        isFirstMeasure.current = false;

        // Chase scroll on height change — only if near bottom
        scrollToBottomIfNear(findScrollParent(outerRef.current));
      }
    });

    observer.observe(node);
    // No cleanup — the observer dies when the component unmounts
    // and the node is garbage collected.
  }, []);

  const isOpening = isFirstMeasure.current;

  // Chase scroll on EVERY FRAME of the height animation.
  // This makes the growth appear to go upward — the bottom stays fixed
  // in the viewport, the top edge rises. Without this, the container
  // grows downward and then snaps up when the scroll catches up.
  const handleAnimationFrame = useCallback(() => {
    scrollToBottomIfNear(findScrollParent(outerRef.current));
  }, []);

  return (
    <motion.div
      ref={outerRef}
      initial={shouldAnimate ? { height: 0, opacity: 0 } : false}
      animate={{ height, opacity: 1 }}
      transition={{
        height: {
          duration: isOpening ? OPEN_DURATION : GROW_DURATION,
          ease: EASE,
        },
        opacity: { duration: 0.2 },
      }}
      onUpdate={handleAnimationFrame}
      style={{ overflow: "hidden", transformOrigin: "bottom" }}
    >
      <div
        ref={contentRef}
        className="tool-group-inner w-full rounded-xl
                   shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)]
                   flex flex-col gap-3 p-5"
        style={{ backgroundColor: "var(--surface)" }}
      >
        {children}
      </div>
    </motion.div>
  );
}
