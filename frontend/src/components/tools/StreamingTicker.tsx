/**
 * StreamingTicker — Matrix-rain effect for streaming tool-use JSON deltas.
 *
 * Shows raw JSON fragments scrolling across a monospace display while the
 * tool call's input is still being generated. Once the JSON is complete
 * (streaming prop goes false), the ticker fades out and the real tool
 * component takes over.
 *
 * The ticker displays at most VISIBLE_CHARS characters at a time, pushing
 * new characters in from the right and shifting the viewport. Characters
 * render at reduced opacity, giving the "falling code" feel without
 * literally copying The Matrix. The effect is fast, subtle, ambient.
 */

import { useRef, useEffect, useState, useCallback } from "react";

/** Max characters visible in the ticker window at once. */
const VISIBLE_CHARS = 64;

/** Chars pushed per animation frame. */
const CHARS_PER_FRAME = 4;

/** Frame interval in ms (~30fps). */
const FRAME_MS = 33;

interface StreamingTickerProps {
  /** Accumulated partial JSON string so far. */
  text: string;
  /** True while deltas are still arriving. */
  active: boolean;
}

export function StreamingTicker({ text, active }: StreamingTickerProps) {
  const [displayText, setDisplayText] = useState("");
  const cursorRef = useRef(0);
  const rafRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const tick = useCallback(() => {
    cursorRef.current = Math.min(cursorRef.current + CHARS_PER_FRAME, text.length);
    const end = cursorRef.current;
    const start = Math.max(0, end - VISIBLE_CHARS);
    setDisplayText(text.slice(start, end));
  }, [text]);

  useEffect(() => {
    if (!active && cursorRef.current >= text.length) return;

    rafRef.current = setInterval(tick, FRAME_MS);
    return () => {
      if (rafRef.current !== null) clearInterval(rafRef.current);
    };
  }, [tick, active, text]);

  // When text grows (new delta), don't reset cursor — it'll catch up naturally
  // via the interval. This creates the "streaming in" feel.

  return (
    <div className="px-3 py-2 border-t border-border bg-code-bg overflow-hidden">
      <code
        className="text-[11px] font-mono whitespace-nowrap transition-opacity duration-300"
        style={{
          color: "var(--theme-primary)",
          opacity: active ? 0.5 : 0,
        }}
      >
        {displayText || "\u00A0"}
        {active && <span className="animate-pulse-dot">_</span>}
      </code>
    </div>
  );
}
