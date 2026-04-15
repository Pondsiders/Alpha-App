/**
 * AnimatedText — smooth character-by-character text reveal with Streamdown.
 *
 * Reads the full text from props (already in Zustand via the store).
 * Maintains a local displayedLength via useState. Uses rAF to advance
 * displayedLength at the rate provided by DrainRateContext.
 *
 * Renders the visible prefix through Streamdown for live markdown rendering.
 * Only this component re-renders on each animation frame — not the store.
 *
 * Calls onDone() when displayedLength catches up to text.length and the
 * text has stopped growing (message is complete or a non-text part follows).
 */

import { useState, useEffect, useRef, useMemo, type FC, type CSSProperties } from "react";
import { Streamdown } from "streamdown";
import { createCodePlugin } from "@streamdown/code";
import { math } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import "katex/dist/katex.min.css";
import a11yEmoji from "@fec/remark-a11y-emoji";
import { useDrainRate } from "@/lib/DrainRateContext";
import { readStreamingText } from "@/lib/streamingText";

// ---------------------------------------------------------------------------
// Shiki code plugin (shared instance)
// ---------------------------------------------------------------------------

const codePlugin = createCodePlugin({
  themes: ["vitesse-dark", "vitesse-light"],
});

const plugins = { code: codePlugin, math, mermaid };

// Stable references — Streamdown memoizes based on object identity
const remarkPlugins = [a11yEmoji];
const emojiAllowedTags = { span: ["role", "aria-label"] };

// ---------------------------------------------------------------------------
// Prose styling (matches markdown-text.tsx)
// ---------------------------------------------------------------------------

const PROSE_VARS = {
  "--tw-prose-body": "var(--color-foreground)",
  "--tw-prose-bullets": "var(--color-primary)",
  "--tw-prose-counters": "var(--color-primary)",
  "--tw-prose-th-borders":
    "color-mix(in oklch, var(--color-primary) 40%, transparent)",
  "--tw-prose-td-borders":
    "color-mix(in oklch, var(--color-primary) 25%, transparent)",
} as CSSProperties;

const PROSE_CLASSES = [
  "prose prose-invert text-foreground font-light",
  "prose-headings:text-foreground",
  "prose-h1:font-[500] prose-h2:font-[600]",
  "prose-strong:text-foreground",
  "prose-a:text-primary",
  "prose-blockquote:text-muted-foreground prose-blockquote:border-primary/40",
  "prose-code:text-foreground",
  "prose-pre:my-0 prose-pre:p-0",
  "prose-li:marker:text-primary",
  "prose-hr:border-primary/30",
  "prose-th:border-primary/30 prose-td:border-primary/20",
  "prose-base",
  "prose-p:my-2 prose-headings:mt-4 prose-headings:mb-2",
].join(" ");

// ---------------------------------------------------------------------------
// AnimatedText
// ---------------------------------------------------------------------------

interface AnimatedTextProps {
  /** The full text from Zustand. May lag behind the streaming ref. */
  text: string;
  /** Chat and message IDs for reading the streaming ref. */
  chatId: string;
  messageId: string;
  /** Whether this text part is still receiving deltas. */
  isStreaming: boolean;
  /** Index of this part in the parts array (for DrainRateContext reporting). */
  partIndex: number;
  /** Called when the animation has caught up AND text has stopped growing. */
  onDone: () => void;
}

export const AnimatedText: FC<AnimatedTextProps> = ({
  text,
  chatId,
  messageId,
  isStreaming,
  partIndex,
  onDone,
}) => {
  const [displayedLength, setDisplayedLength] = useState(0);
  const { rate, reportRemaining } = useDrainRate();
  const calledDone = useRef(false);
  const lastTime = useRef(0);
  const charRemainder = useRef(0);
  const frameId = useRef<number | null>(null);

  // Stable refs for the rAF callback
  const rateRef = useRef(rate);
  rateRef.current = rate;
  const reportRemainingRef = useRef(reportRemaining);
  reportRemainingRef.current = reportRemaining;
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;
  const messageIdRef = useRef(messageId);
  messageIdRef.current = messageId;
  // Fall back to prop text when not streaming (completed messages)
  const textRef = useRef(text);
  textRef.current = text;

  // Start/maintain the animation loop
  useEffect(() => {
    if (calledDone.current) return;

    const animate = (timestamp: number) => {
      if (!lastTime.current) {
        lastTime.current = timestamp;
        frameId.current = requestAnimationFrame(animate);
        return;
      }

      const elapsed = timestamp - lastTime.current;
      lastTime.current = timestamp;

      const currentRate = rateRef.current;
      charRemainder.current += currentRate * (elapsed / 1000);

      const budget = Math.max(1, Math.floor(charRemainder.current));
      charRemainder.current -= budget;

      setDisplayedLength((prev) => {
        // Read from the streaming ref (zero cost) — this has the
        // latest text without waiting for Zustand/React.
        const liveText = readStreamingText(chatIdRef.current, messageIdRef.current);
        const target = liveText.length || textRef.current.length;
        const next = Math.min(prev + budget, target);

        // Report remaining to the DrainRateProvider
        reportRemaining(partIndex, target - next);

        return next;
      });

      frameId.current = requestAnimationFrame(animate);
    };

    frameId.current = requestAnimationFrame(animate);

    return () => {
      if (frameId.current !== null) {
        cancelAnimationFrame(frameId.current);
        frameId.current = null;
      }
    };
  }, [partIndex, reportRemaining]);

  // Check if we're done: caught up AND text has stopped growing
  useEffect(() => {
    if (
      !calledDone.current &&
      displayedLength >= text.length &&
      text.length > 0 &&
      !isStreaming
    ) {
      calledDone.current = true;
      reportRemaining(partIndex, 0);
      if (frameId.current !== null) {
        cancelAnimationFrame(frameId.current);
        frameId.current = null;
      }
      onDone();
    }
  }, [displayedLength, text.length, isStreaming, onDone, partIndex, reportRemaining]);

  // Read from streaming ref for live text, fall back to prop for history
  const liveText = readStreamingText(chatId, messageId) || text;
  const visibleText = liveText.slice(0, displayedLength);

  return (
    <div className={PROSE_CLASSES} style={PROSE_VARS}>
      <Streamdown
        mode="streaming"
        isAnimating={displayedLength < liveText.length}
        plugins={plugins}
        remarkPlugins={remarkPlugins}
        allowedTags={emojiAllowedTags}
        caret="block"
      >
        {visibleText}
      </Streamdown>
    </div>
  );
};
