/**
 * TypeOnBuffer — adaptive streaming text drain with target-switching.
 *
 * Smooths chunky streaming deltas into a steady character flow. The drain
 * rate is ADAPTIVE — proportional to buffer depth. Deep buffer = fast drain,
 * shallow buffer = slow drain. The result is text that breathes with the
 * source: fast when there's a lot to say, measured when there isn't. No
 * stutters, no lag, no catching-up tail after the stream ends.
 *
 * Formula: charsPerSec = BASE_RATE + (bufferDepth * CHASE_FACTOR)
 *
 * Supports two drain targets ("text" and "thinking") with automatic flush
 * on target switch.
 *
 * Debug vitals: when localStorage 'alpha-debug-typeon' is set, exposes
 * real-time stats (effective rate, buffer depth, sparkline) via a callback.
 */

export type DrainTarget = "text" | "thinking";

export interface TypeOnVitals {
  effectiveRate: number;    // chars/sec over last ~500ms
  bufferDepth: number;      // chars waiting in queue
  totalChars: number;       // total chars drained
  totalFrames: number;      // total rAF frames
  charsThisFrame: number;   // chars drained on the most recent frame
  sparkline: string;        // unicode bar chart of recent buffer depths
  target: DrainTarget;      // current drain target
  currentRate: number;      // instantaneous adaptive rate (c/s)
}

export interface TypeOnBufferOptions {
  /** Called each frame with the chars to render and the target. */
  onDrain: (text: string, target: DrainTarget) => void;
  /** Called when the buffer empties naturally (stream tail finished). */
  onDrained?: () => void;
  /** Called ~10/sec with debug vitals when debug mode is enabled. */
  onVitals?: (vitals: TypeOnVitals) => void;
  /**
   * Base drain rate in chars/sec. The adaptive formula adds to this
   * based on buffer depth: effective = baseRate + depth * chaseFactor.
   * Default: 60.
   */
  baseRate?: number;
  /**
   * How aggressively the drain chases a deep buffer.
   * Higher = faster ramp-up. Default: 0.5 (at 1000 chars deep,
   * adds 500 c/s to the base rate).
   */
  chaseFactor?: number;
}

// Sparkline characters for buffer depth visualization
const SPARK_CHARS = "▁▂▃▄▅▆▇█";

export class TypeOnBuffer {
  private queue = "";
  private target: DrainTarget = "text";
  private frameId: number | null = null;
  private lastDrainTime = 0;
  private charRemainder = 0;
  private _finishing = false; // true after finish() — onDrained fires when queue empties

  // Stats
  private totalChars = 0;
  private totalFrames = 0;
  private recentDrains: Array<{ chars: number; time: number }> = [];
  private recentDepths: number[] = [];
  private lastVitalsTime = 0;

  // Options
  private onDrain: (text: string, target: DrainTarget) => void;
  private onDrained: (() => void) | null;
  private onVitals: ((vitals: TypeOnVitals) => void) | null;
  private baseRate: number;
  private chaseFactor: number;

  // Debug mode — check localStorage once on construction
  private debugEnabled: boolean;

  constructor(options: TypeOnBufferOptions) {
    this.onDrain = options.onDrain;
    this.onDrained = options.onDrained ?? null;
    this.onVitals = options.onVitals ?? null;
    this.baseRate = options.baseRate ?? 60;
    this.chaseFactor = options.chaseFactor ?? 0.5;
    this.debugEnabled = typeof localStorage !== "undefined"
      && localStorage.getItem("alpha-debug-typeon") === "true";
  }

  /** Add text to the buffer. Starts draining if not already running. */
  push(text: string, target: DrainTarget = "text"): void {
    // Target switch: flush old target immediately, then switch
    if (target !== this.target && this.queue.length > 0) {
      this.onDrain(this.queue, this.target);
      this.totalChars += this.queue.length;
      this.queue = "";
      this.charRemainder = 0;
    }
    this.target = target;
    this.queue += text;
    if (this.frameId === null) {
      this.lastDrainTime = performance.now();
      this.startDraining();
    }
  }

  /** True if the buffer has text remaining to drain. */
  get isDraining(): boolean {
    return this.queue.length > 0 || this.frameId !== null;
  }

  /** Current drain target. */
  get currentTarget(): DrainTarget {
    return this.target;
  }

  /**
   * Signal that no more text will arrive. If the queue still has text,
   * let it drain naturally and fire onDrained when it empties. If the
   * queue is already empty, fire onDrained immediately.
   */
  finish(): void {
    if (this.queue.length === 0 && this.frameId === null) {
      // Already drained — finalize now
      this.onDrained?.();
      return;
    }
    // Still draining — install onDrained as the empty-queue action
    // by re-enabling it in the drain loop. We do this by setting a flag.
    this._finishing = true;
    // If the rAF loop is paused (queue was empty), it'll finalize here.
    // If the loop is running, it'll finalize when the queue empties.
    if (this.frameId === null && this.queue.length > 0) {
      // Queue has text but loop is paused — restart it
      this.lastDrainTime = performance.now();
      this.startDraining();
    }
  }

  /** Immediately flush all remaining text. Use for interrupts only. */
  flush(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    if (this.queue.length > 0) {
      this.onDrain(this.queue, this.target);
      this.totalChars += this.queue.length;
      this.queue = "";
    }
    this.charRemainder = 0;
    this.onDrained?.();
  }

  /** Stop the drain loop and discard any remaining text. */
  reset(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    this.queue = "";
    this.charRemainder = 0;
    this.totalChars = 0;
    this.totalFrames = 0;
    this.recentDrains = [];
    this.recentDepths = [];
  }

  private startDraining(): void {
    const drain = (timestamp: number) => {
      this.totalFrames++;

      if (this.queue.length === 0) {
        this.frameId = null;
        this.charRemainder = 0;
        if (this._finishing) {
          // finish() was called and the queue has drained — finalize.
          this._finishing = false;
          this.onDrained?.();
        }
        // Otherwise: queue empty mid-stream. Just pause. push() will
        // restart the loop when more text arrives.
        return;
      }

      // Adaptive rate: base + depth * chase. Deeper buffer = faster drain.
      const adaptiveRate = this.baseRate + this.queue.length * this.chaseFactor;
      // Time-based accumulation with adaptive rate
      const elapsed = timestamp - this.lastDrainTime;
      this.lastDrainTime = timestamp;
      this.charRemainder += adaptiveRate * (elapsed / 1000);

      const toDrain = Math.min(Math.floor(this.charRemainder), this.queue.length);
      if (toDrain > 0) {
        const chunk = this.queue.slice(0, toDrain);
        this.queue = this.queue.slice(toDrain);
        this.charRemainder -= toDrain;
        this.totalChars += toDrain;
        this.onDrain(chunk, this.target);
      }

      // Debug vitals — accumulate always (cheap), fire callback ~10/sec
      if (this.debugEnabled) {
        this.recentDrains.push({ chars: toDrain, time: timestamp });
        this.recentDepths.push(this.queue.length);
        const cutoff = timestamp - 500;
        while (this.recentDrains.length > 0 && this.recentDrains[0].time < cutoff) {
          this.recentDrains.shift();
        }
        if (this.recentDepths.length > 60) this.recentDepths.shift();

        if (this.onVitals && timestamp - this.lastVitalsTime >= 100) {
          this.lastVitalsTime = timestamp;
          this.onVitals({
            effectiveRate: this.computeEffectiveRate(timestamp),
            bufferDepth: this.queue.length,
            totalChars: this.totalChars,
            totalFrames: this.totalFrames,
            charsThisFrame: toDrain,
            sparkline: this.computeSparkline(),
            target: this.target,
            currentRate: Math.round(adaptiveRate),
          });
        }
      }

      this.frameId = requestAnimationFrame(drain);
    };

    this.frameId = requestAnimationFrame(drain);
  }

  private computeEffectiveRate(now: number): number {
    if (this.recentDrains.length < 2) return 0;
    const first = this.recentDrains[0].time;
    const windowMs = now - first;
    if (windowMs <= 0) return 0;
    const totalCharsInWindow = this.recentDrains.reduce((sum, d) => sum + d.chars, 0);
    return Math.round((totalCharsInWindow / windowMs) * 1000);
  }

  private computeSparkline(): string {
    if (this.recentDepths.length === 0) return "";
    const max = Math.max(...this.recentDepths, 1);
    return this.recentDepths
      .map(d => SPARK_CHARS[Math.min(Math.floor((d / max) * (SPARK_CHARS.length - 1)), SPARK_CHARS.length - 1)])
      .join("");
  }
}
