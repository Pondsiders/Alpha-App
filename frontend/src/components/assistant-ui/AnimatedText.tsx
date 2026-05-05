/**
 * AnimatedText — character-by-character text reveal with Streamdown.
 *
 * Reads live text from the streaming ref (per-message Map in streamingText.ts,
 * keyed by chatId:messageId — zero-cost, outside React, outside Zustand).
 * Maintains a local displayedLength via useState. A rAF loop advances
 * displayedLength at a depth-proportional rate with a floor.
 *
 *     rate = max(MIN_DRAIN_RATE, depth * DRAIN_K)
 *
 * Pure proportional, no integral, no derivative. Buffer self-regulates:
 * fills during bursts → rate scales up → drains. Empties between bursts →
 * rate decays toward the floor → buffer holds a small cushion. End of turn:
 * input stops, depth → 0, rate decelerates organically from depth*K down
 * to the floor as the last chars trickle out. Independent of Anthropic's
 * instantaneous streaming speed — the buffer depth IS the feedback signal.
 *
 * Calls onDone() when displayedLength catches up to the live text and
 * the message has been sealed (isStreaming === false).
 */

import { useState, useEffect, useRef, type FC, type CSSProperties } from "react";
import { Streamdown } from "streamdown";
import { createCodePlugin } from "@streamdown/code";
import { math } from "@streamdown/math";
import { mermaid } from "@streamdown/mermaid";
import "katex/dist/katex.min.css";
import a11yEmoji from "@fec/remark-a11y-emoji";
import { readStreamingText, clearStreamingEntry } from "@/lib/streamingText";

// ---------------------------------------------------------------------------
// Drain tuning — two knobs, change-and-Vite-reloads
// ---------------------------------------------------------------------------

/**
 * Floor: characters per second when the buffer is empty (or shallow enough
 * that the proportional term is below this floor). This is the comfortable
 * reading-pace the user sees when streaming is "idle." Sustained input
 * below this rate would stall — buffer empties faster than it fills — but
 * Anthropic's average streaming has been measured at ~116 c/s, well above
 * a 60 c/s floor. Tune up if end-of-turn deceleration feels too slow,
 * down if a slowly-streaming response feels rushed.
 */
const MIN_DRAIN_RATE = 60;

/**
 * Proportional factor: chars per second of drain rate per buffered char.
 * At K=2, equilibrium settles at depth ≈ input_rate / 2 — so a 200 c/s
 * burst settles at ~100 chars buffered, draining at 200 c/s. Larger K =
 * tighter buffer, faster catchup, sharper end-of-turn drain. Smaller K =
 * deeper buffer, longer trailing time after input stops.
 */
const DRAIN_K = 3;

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
// Debug widget — fixed top-right "⚡ N c/s | 📦 N chars"
// ---------------------------------------------------------------------------

function updateDebugWidget(rate: number, depth: number): void {
  if (typeof document === "undefined") return;
  let div = document.getElementById("drain-rate-debug") as HTMLDivElement | null;
  if (!div) {
    div = document.createElement("div");
    div.id = "drain-rate-debug";
    div.style.cssText =
      "position:fixed; top:8px; right:80px; background:#1a1a1a; color:#f0c040; " +
      "font-family:monospace; font-size:11px; padding:4px 8px; border-radius:4px; " +
      "z-index:9999; opacity:0.85; pointer-events:none; border:1px solid #333;";
    document.body.appendChild(div);
  }
  div.textContent = `⚡ ${Math.round(rate)} c/s | 📦 ${depth} chars`;
}

// ---------------------------------------------------------------------------
// AnimatedText
// ---------------------------------------------------------------------------

interface AnimatedTextProps {
  /** The full text from Zustand. Used as a fallback after the streaming
   *  ref is cleared (post-seal, drain complete). */
  text: string;
  /** Chat and message IDs for reading the streaming ref. */
  chatId: string;
  messageId: string;
  /** Whether this text part is still receiving deltas. */
  isStreaming: boolean;
  /** Called when the animation has caught up AND text has stopped growing. */
  onDone: () => void;
}

export const AnimatedText: FC<AnimatedTextProps> = ({
  text,
  chatId,
  messageId,
  isStreaming,
  onDone,
}) => {
  const [displayedLength, setDisplayedLength] = useState(0);
  const calledDone = useRef(false);
  const lastTime = useRef(0);
  const charRemainder = useRef(0);
  const frameId = useRef<number | null>(null);

  // Stable refs for the rAF callback
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;
  const messageIdRef = useRef(messageId);
  messageIdRef.current = messageId;
  const textRef = useRef(text);
  textRef.current = text;

  // Proportional drain loop — rate is recomputed from buffer depth on
  // every frame, the buffer self-regulates, end-of-turn snap is handled
  // by the seal effect below.
  useEffect(() => {
    if (calledDone.current) return;

    const animate = (timestamp: number) => {
      if (!lastTime.current) {
        lastTime.current = timestamp;
        frameId.current = requestAnimationFrame(animate);
        return;
      }

      const dt = (timestamp - lastTime.current) / 1000; // seconds
      lastTime.current = timestamp;

      // Read from the streaming ref — always the source during animation.
      const liveText = readStreamingText(chatIdRef.current, messageIdRef.current);
      const target = liveText.length || textRef.current.length;

      setDisplayedLength((prev) => {
        const remaining = target - prev;

        // Pure-proportional drain with a floor:
        //   rate = max(MIN_DRAIN_RATE, depth * DRAIN_K)
        // The buffer is the feedback signal — drain rate scales with how
        // much there is to drain, with a comfortable reading-pace floor.
        const rate = Math.max(MIN_DRAIN_RATE, remaining * DRAIN_K);

        // Accumulate fractional chars across frames so we hit the right
        // average even if dt jitters at 60Hz/120Hz boundaries.
        charRemainder.current += rate * dt;
        const budget = Math.floor(charRemainder.current);
        if (budget > 0) charRemainder.current -= budget;

        const next = budget > 0 ? Math.min(prev + budget, target) : prev;

        // Update the debug widget with the current rate and cushion depth.
        updateDebugWidget(rate, target - next);

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
  }, []);

  // Snap-to-end on seal: when isStreaming flips false (assistant-message
  // arrived and finalized this part), drop the buffer all at once. With
  // K=3 the cushion is rarely more than a line at seal time, so the
  // single-frame snap is barely perceptible. The benefit is snappier
  // turn-completion — no trailing deceleration eating perceived latency
  // before the next thing happens.
  useEffect(() => {
    if (calledDone.current) return;
    if (!isStreaming) {
      const refText = readStreamingText(chatId, messageId);
      const refLen = refText.length || text.length;
      if (refLen > 0) {
        setDisplayedLength(refLen);
      }
    }
  }, [isStreaming, chatId, messageId, text]);

  // Done condition: not streaming AND displayedLength has caught up. With
  // the snap-on-seal effect above, this fires the same frame as seal.
  useEffect(() => {
    if (calledDone.current) return;
    const refText = readStreamingText(chatId, messageId);
    const refLen = refText.length || text.length;
    if (
      !isStreaming &&
      displayedLength >= refLen &&
      refLen > 0
    ) {
      calledDone.current = true;
      clearStreamingEntry(chatId, messageId);
      // Idle: no rate, empty buffer.
      updateDebugWidget(MIN_DRAIN_RATE, 0);
      if (frameId.current !== null) {
        cancelAnimationFrame(frameId.current);
        frameId.current = null;
      }
      onDone();
    }
  }, [displayedLength, text.length, isStreaming, chatId, messageId, onDone]);

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
