/**
 * TurnBuffer — event-ordered adaptive streaming drain.
 *
 * Queues all events from a single assistant turn and drains them in
 * order. Text events drain character-by-character at an adaptive rate
 * proportional to the total remaining text depth. Non-text events
 * (tool calls, thinking, message completion) fire immediately when
 * they reach the front of the queue — but they never jump ahead of
 * undrained text.
 *
 * The drain rate formula: charsPerSec = BASE_RATE + textDepth * CHASE_FACTOR
 *
 * Time-based (not frame-based) — works on any refresh rate.
 *
 * The deceleration at the end of a response is free: as textDepth drops,
 * the rate drops, and the last characters trickle out naturally.
 */

// ---------------------------------------------------------------------------
// Event types — the things that go in the queue
// ---------------------------------------------------------------------------

export type TurnEvent =
  | { kind: "text"; delta: string }
  | { kind: "thinking"; delta: string }
  | { kind: "immediate"; fire: () => void };

// "immediate" is the catch-all for non-text events: tool-call-start,
// tool-call-result, assistant-message, etc. The caller wraps them in
// a closure. The buffer just calls fire() when they reach the front.

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

export interface TurnBufferOptions {
  /** Called to drain text characters into the store. */
  onText: (text: string) => void;
  /** Called to drain thinking characters into the store. */
  onThinking: (text: string) => void;
  /**
   * Base drain rate in chars/sec at zero text depth.
   * Default: 60.
   */
  baseRate?: number;
  /**
   * How aggressively the drain chases buffered text.
   * Default: 0.5.
   */
  chaseFactor?: number;
  /**
   * Drain rate multiplier for thinking text.
   * Thinking drains faster because you skim it.
   * Default: 2 (2x the text rate).
   */
  thinkingMultiplier?: number;
}

// ---------------------------------------------------------------------------
// TurnBuffer
// ---------------------------------------------------------------------------

export class TurnBuffer {
  private queue: TurnEvent[] = [];
  private frameId: number | null = null;
  private lastDrainTime = 0;
  private charRemainder = 0;

  // Current text event being partially drained. When we drain some
  // characters from a text/thinking event, the remainder stays here
  // until fully consumed.
  private currentOffset = 0;

  // Debug div — raw innerHTML bypass, no React, no Zustand
  private debugDiv: HTMLDivElement | null = null;
  private debugText = "";
  private debugFrameCount = 0;

  private readonly onText: (text: string) => void;
  private readonly onThinking: (text: string) => void;
  private readonly baseRate: number;
  private readonly chaseFactor: number;
  private readonly thinkingMultiplier: number;

  constructor(options: TurnBufferOptions) {
    this.onText = options.onText;
    this.onThinking = options.onThinking;
    this.baseRate = options.baseRate ?? 60;
    this.chaseFactor = options.chaseFactor ?? 0.5;
    this.thinkingMultiplier = options.thinkingMultiplier ?? 2;

    // Create debug div — fixed position, always visible
    if (typeof document !== "undefined") {
      let existing = document.getElementById("turn-buffer-debug") as HTMLDivElement;
      if (!existing) {
        existing = document.createElement("div");
        existing.id = "turn-buffer-debug";
        existing.style.cssText =
          "position:fixed; top:50%; left:16px; right:16px; max-height:200px; transform:translateY(-50%); " +
          "overflow-y:auto; background:#1a1a1a; color:#e0e0e0; font-family:monospace; " +
          "font-size:13px; padding:12px; border-radius:8px; z-index:9999; " +
          "white-space:pre-wrap; border:1px solid #333; opacity:0.9;";
        document.body.appendChild(existing);
      }
      this.debugDiv = existing;
      this.debugText = "";
      this.debugDiv.innerHTML = "<em style='color:#666'>waiting for text...</em>";
    }
  }

  private sealed = false;

  // Batched text — accumulate chars across frames, flush to React periodically
  private pendingText = "";
  private pendingThinking = "";
  private framesSinceFlush = 0;
  private readonly flushEveryN = 2; // flush to React every 2 rAF frames

  /** Push an event onto the queue. Starts the drain loop if idle. */
  push(event: TurnEvent): void {
    this.queue.push(event);
    if (this.frameId === null) {
      this.lastDrainTime = performance.now();
      this.sealed = false;
      this.startLoop();
    }
  }

  /**
   * Signal that no more events will be pushed. The loop will stop
   * once the queue drains empty. Without this, the loop spins
   * indefinitely waiting for more pushes.
   */
  seal(): void {
    this.sealed = true;
  }

  /** Immediately flush everything remaining. For interrupts. */
  flush(): void {
    this.stopLoop();
    // Flush any batched text first
    if (this.pendingText) { this.onText(this.pendingText); this.pendingText = ""; }
    if (this.pendingThinking) { this.onThinking(this.pendingThinking); this.pendingThinking = ""; }
    // Drain all remaining events instantly
    while (this.queue.length > 0) {
      const event = this.queue[0];
      if (event.kind === "text") {
        const remaining = event.delta.slice(this.currentOffset);
        if (remaining) this.onText(remaining);
      } else if (event.kind === "thinking") {
        const remaining = event.delta.slice(this.currentOffset);
        if (remaining) this.onThinking(remaining);
      } else {
        event.fire();
      }
      this.queue.shift();
      this.currentOffset = 0;
    }
    this.charRemainder = 0;
  }

  /** Discard everything and stop. */
  reset(): void {
    this.stopLoop();
    this.queue = [];
    this.currentOffset = 0;
    this.charRemainder = 0;
  }

  /** True if the buffer has events or a running loop. */
  get active(): boolean {
    return this.queue.length > 0 || this.frameId !== null;
  }

  /** Total text characters remaining (for external inspection). */
  get textDepth(): number {
    return this.computeTextDepth();
  }

  // -----------------------------------------------------------------------
  // Internals
  // -----------------------------------------------------------------------

  private stopLoop(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
  }

  private startLoop(): void {
    const drain = (timestamp: number) => {
      this.debugFrameCount++;
      if (this.queue.length === 0) {
        if (this.sealed) {
          // Stream is done and queue is empty — stop for real.
          this.frameId = null;
          this.charRemainder = 0;
          this.currentOffset = 0;
          return;
        }
        // Queue is empty but stream is still going. Keep spinning
        // so we don't pay a one-frame restart penalty when push()
        // adds more text.
        this.lastDrainTime = timestamp;
        this.frameId = requestAnimationFrame(drain);
        return;
      }

      const elapsed = timestamp - this.lastDrainTime;
      this.lastDrainTime = timestamp;

      // Process events from the front of the queue
      let budget = this.computeCharBudget(elapsed);

      while (this.queue.length > 0 && budget > 0) {
        const event = this.queue[0];

        if (event.kind === "immediate") {
          // Non-text events fire instantly and are consumed
          event.fire();
          this.queue.shift();
          this.currentOffset = 0;
          continue;
        }

        // Text or thinking event — drain characters
        const remaining = event.delta.length - this.currentOffset;
        const isThinking = event.kind === "thinking";
        const effectiveBudget = isThinking
          ? Math.ceil(budget * this.thinkingMultiplier)
          : budget;
        const toDrain = Math.min(effectiveBudget, remaining);

        // Always drain at least 1 character per frame to avoid
        // the "0, 1, 0, 1" stutter at low rates.
        const actualDrain = Math.max(toDrain, 1);
        const clamped = Math.min(actualDrain, remaining);

        if (clamped > 0) {
          const chunk = event.delta.slice(
            this.currentOffset,
            this.currentOffset + clamped,
          );
          this.currentOffset += clamped;

          if (isThinking) {
            this.pendingThinking += chunk;
          } else {
            this.pendingText += chunk;
          }

          // Debug: write directly to innerHTML — bypasses React/Zustand entirely
          if (this.debugDiv && !isThinking) {
            this.debugText += chunk;
            this.debugDiv.innerHTML =
              `<span style="color:#f0c040;font-size:11px">[f${this.debugFrameCount} +${clamped}c depth=${this.computeTextDepth()}]</span> ` +
              this.debugText;
          }

          // Deduct from budget (thinking uses the base budget, not multiplied)
          budget -= isThinking ? Math.ceil(clamped / this.thinkingMultiplier) : clamped;
        }

        if (this.currentOffset >= event.delta.length) {
          // Fully consumed — pop and move to next
          this.queue.shift();
          this.currentOffset = 0;
        } else {
          // Partially consumed — stop here, continue next frame
          break;
        }
      }

      // Flush accumulated text to React every N frames. This batches
      // Zustand updates so React reconciliation doesn't eat every frame.
      this.framesSinceFlush++;
      if (this.framesSinceFlush >= this.flushEveryN || this.queue.length === 0) {
        if (this.pendingText) {
          this.onText(this.pendingText);
          this.pendingText = "";
        }
        if (this.pendingThinking) {
          this.onThinking(this.pendingThinking);
          this.pendingThinking = "";
        }
        this.framesSinceFlush = 0;
      }

      this.frameId = requestAnimationFrame(drain);
    };

    this.frameId = requestAnimationFrame(drain);
  }

  /**
   * Compute how many characters to drain this frame based on elapsed
   * time and total remaining text depth.
   */
  private computeCharBudget(elapsedMs: number): number {
    const depth = this.computeTextDepth();
    const rate = this.baseRate + depth * this.chaseFactor;
    this.charRemainder += rate * (elapsedMs / 1000);
    const budget = Math.floor(this.charRemainder);
    this.charRemainder -= budget;
    return Math.max(budget, 0);
  }

  /**
   * Count total remaining text + thinking characters in the queue.
   * This drives the adaptive rate.
   */
  private computeTextDepth(): number {
    let depth = 0;
    for (let i = 0; i < this.queue.length; i++) {
      const event = this.queue[i];
      if (event.kind === "text" || event.kind === "thinking") {
        const offset = i === 0 ? this.currentOffset : 0;
        depth += event.delta.length - offset;
      }
    }
    return depth;
  }
}
