/**
 * SystemMessage — Renders system events in the message stream.
 *
 * Matches BashResult's visual language: title bar + content band,
 * wrapped in a ToolGroup for animated entry. Two bands only (no output).
 *
 * Used as the SystemMessage component in ThreadPrimitive.Messages.
 */

import { useRef, useState, useCallback } from "react";
import { CheckCircle, XCircle, Bell } from "lucide-react";
import { useMessage, MessagePrimitive } from "@assistant-ui/react";
import { motion } from "framer-motion";

const OPEN_DURATION = 0.7;
const EASE = [0.4, 0, 0.2, 1] as const;

let moduleReady = false;
setTimeout(() => {
  moduleReady = true;
}, 1500);

export const SystemMessage = () => {
  const message = useMessage();
  const text =
    message.content
      ?.filter((p) => p.type === "text")
      .map((p) => ("text" in p ? p.text : ""))
      .join("") || "System event";

  // Infer status from text content
  const isError = /failed|error/i.test(text) && !/exit code 0/i.test(text);
  const isCompleted = /completed|exit code 0/i.test(text);
  const Icon = isError ? XCircle : isCompleted ? CheckCircle : Bell;
  const dotColor = isError ? "var(--theme-error)" : "var(--theme-success)";
  const iconColor = isError ? "var(--theme-error)" : undefined;

  // Animated height — same pattern as ToolGroup
  const outerRef = useRef<HTMLDivElement>(null);
  const shouldAnimate = useRef(moduleReady).current;
  const [height, setHeight] = useState(0);

  const contentRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.borderBoxSize?.[0];
      const h = box ? box.blockSize : (entries[0]?.contentRect.height ?? 0);
      if (h > 0) setHeight(h);
    });
    observer.observe(node);
  }, []);

  return (
    <MessagePrimitive.Root>
      <motion.div
        ref={outerRef}
        initial={shouldAnimate ? { height: 0, opacity: 0 } : false}
        animate={{ height, opacity: 1 }}
        transition={{
          height: { duration: OPEN_DURATION, ease: EASE },
          opacity: { duration: 0.2 },
        }}
        style={{ overflow: "hidden", transformOrigin: "bottom" }}
      >
        <div
          ref={contentRef}
          className="w-full rounded-xl shadow-[inset_0_0.125rem_0.625rem_rgba(0,0,0,0.4)] p-6"
          style={{ backgroundColor: "var(--theme-surface)" }}
        >
          <div className="w-full rounded-lg border border-border overflow-hidden">
            {/* ── Band 1: Title bar ── */}
            <div className="flex items-center gap-2 px-3 py-2 bg-surface">
              <Icon
                size={14}
                className="shrink-0 text-muted/60"
                style={iconColor ? { color: iconColor } : undefined}
              />
              <div className="min-w-0 flex-1 truncate">
                <span className="text-[13px] text-text">Task notification</span>
              </div>
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ backgroundColor: dotColor }}
              />
            </div>

            {/* ── Band 2: Notification text ── */}
            <div className="border-t border-border/50 bg-code-bg px-3 py-1.5">
              <code className="text-[12px] text-muted leading-snug block break-all whitespace-pre-wrap">
                {text}
              </code>
            </div>
          </div>
        </div>
      </motion.div>
    </MessagePrimitive.Root>
  );
};
