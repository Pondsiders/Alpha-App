/**
 * MemoryNote — Marginalia for cortex_store tool calls.
 *
 * Renders as a quiet note in the chat timeline: feather icon, italic text,
 * gradient fade on overflow, click to expand. Not a bubble — a thought
 * jotted in the margin.
 */

import { useState, useRef, useEffect } from "react";
import { Feather } from "lucide-react";
import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

/** Collapsed height: roughly 2 lines of 13px text at snug line-height. */
const COLLAPSED_MAX = "2.6em";

export const MemoryNote: ToolCallMessagePartComponent = ({
  argsText,
  status,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const textRef = useRef<HTMLDivElement>(null);

  // Parse memory text from args
  let memoryText = "";
  try {
    const args = argsText ? JSON.parse(argsText) : {};
    memoryText = args.memory || "";
  } catch {
    // argsText might be partial JSON while streaming — show raw
    memoryText = argsText || "";
  }

  const isRunning = status?.type === "running";

  // Detect overflow after text renders
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;

    // Compare actual content height vs collapsed container
    const check = () => setOverflows(el.scrollHeight > el.clientHeight + 2);
    check();

    // Re-check if content changes (streaming args)
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [memoryText, expanded]);

  if (!memoryText) return null;

  return (
    <div
      className={`my-[1.25lh] flex items-start gap-2 ${overflows && !expanded ? "cursor-pointer" : ""}`}
      onClick={() => overflows && setExpanded(!expanded)}
      role={overflows ? "button" : undefined}
      tabIndex={overflows ? 0 : undefined}
      onKeyDown={(e) => {
        if (overflows && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          setExpanded(!expanded);
        }
      }}
    >
      <Feather
        size={14}
        className={`mt-[3px] shrink-0 text-muted/50 ${isRunning ? "animate-pulse-dot" : ""}`}
      />
      <div className="relative overflow-hidden min-w-0 flex-1">
        <div
          ref={textRef}
          className="text-[13px] italic text-muted/60 leading-snug whitespace-pre-wrap break-words transition-[max-height] duration-300 ease-in-out"
          style={{ maxHeight: expanded ? "2000px" : COLLAPSED_MAX }}
        >
          {memoryText}
        </div>

        {/* Gradient fade when collapsed and overflowing */}
        {!expanded && overflows && (
          <div
            className="absolute bottom-0 left-0 right-0 h-5 pointer-events-none"
            style={{
              background:
                "linear-gradient(to top, var(--theme-background), transparent)",
            }}
          />
        )}
      </div>
    </div>
  );
};
