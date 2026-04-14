/**
 * DeltaBuffer — adaptive streaming text drain.
 *
 * Smooths chunky streaming deltas into a steady character flow. The drain
 * rate is adaptive: proportional to buffer depth. Deep buffer = fast drain,
 * shallow buffer = slow drain. The result is text that breathes with the
 * source: fast when there's a lot to say, measured when there isn't.
 *
 * Formula: charsPerSec = baseRate + (bufferDepth * chaseFactor)
 *
 * Time-based, not frame-based — works correctly on 60Hz, 120Hz, and
 * variable refresh rate (ProMotion) displays. rAF determines update
 * frequency, not drain speed.
 *
 * Instantiate one per delta type (text, thinking, JSON). Each instance
 * is independent — no target-switching, no shared state.
 *
 * Debug vitals: set localStorage key 'alpha-debug-drain' to 'true'.
 */

export interface DeltaBufferOptions {
  /** Called each frame with the chars to drain. */
  onDrain: (text: string) => void;
  /** Called when the buffer empties after finish(). */
  onDrained?: () => void;
  /**
   * Base drain rate in chars/sec at zero buffer depth.
   * Default: 60.
   */
  baseRate?: number;
  /**
   * How aggressively the drain chases a deep buffer.
   * At 1000 chars deep with chaseFactor=0.5, adds 500 c/s.
   * Default: 0.5.
   */
  chaseFactor?: number;
}

export class DeltaBuffer {
  private queue = "";
  private frameId: number | null = null;
  private lastDrainTime = 0;
  private charRemainder = 0;
  private finishing = false;

  private readonly onDrain: (text: string) => void;
  private onDrained: (() => void) | null;
  private readonly baseRate: number;
  private readonly chaseFactor: number;
  constructor(options: DeltaBufferOptions) {
    this.onDrain = options.onDrain;
    this.onDrained = options.onDrained ?? null;
    this.baseRate = options.baseRate ?? 60;
    this.chaseFactor = options.chaseFactor ?? 0.5;
  }

  /** Add text to the buffer. Starts draining if idle. */
  push(text: string): void {
    this.queue += text;
    if (this.frameId === null) {
      this.lastDrainTime = performance.now();
      this.startLoop();
    }
  }

  /** How many chars are waiting. */
  get depth(): number {
    return this.queue.length;
  }

  /** True if the buffer has text or a running loop. */
  get active(): boolean {
    return this.queue.length > 0 || this.frameId !== null;
  }

  /**
   * Signal that no more text will arrive. The buffer drains naturally,
   * then fires onDrained. If already empty, fires immediately.
   */
  finish(): void {
    this.finishing = true;
    if (this.queue.length === 0 && this.frameId === null) {
      this.finishing = false;
      this.onDrained?.();
      return;
    }
    // If the loop is paused but queue has text, restart it.
    if (this.frameId === null && this.queue.length > 0) {
      this.lastDrainTime = performance.now();
      this.startLoop();
    }
  }

  /** Immediately flush all remaining text. For interrupts. */
  flush(): void {
    this.stopLoop();
    if (this.queue.length > 0) {
      this.onDrain(this.queue);
      this.queue = "";
    }
    this.charRemainder = 0;
    this.finishing = false;
    this.onDrained?.();
  }

  /** Set a new onDrained callback. Used to defer work until drain completes. */
  setOnDrained(fn: (() => void) | null): void {
    this.onDrained = fn;
  }

  /** Discard everything and stop. */
  reset(): void {
    this.stopLoop();
    this.queue = "";
    this.charRemainder = 0;
    this.finishing = false;
  }

  private stopLoop(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
  }

  private startLoop(): void {
    const drain = (timestamp: number) => {
      if (this.queue.length === 0) {
        this.frameId = null;
        this.charRemainder = 0;
        if (this.finishing) {
          this.finishing = false;
          this.onDrained?.();
        }
        // Empty mid-stream: pause. push() will restart.
        return;
      }

      // Time-based: works on any refresh rate.
      const elapsed = timestamp - this.lastDrainTime;
      this.lastDrainTime = timestamp;

      // Adaptive: deeper buffer = faster drain.
      const rate = this.baseRate + this.queue.length * this.chaseFactor;
      this.charRemainder += rate * (elapsed / 1000);

      const toDrain = Math.min(
        Math.floor(this.charRemainder),
        this.queue.length,
      );

      if (toDrain > 0) {
        const chunk = this.queue.slice(0, toDrain);
        this.queue = this.queue.slice(toDrain);
        this.charRemainder -= toDrain;
        this.onDrain(chunk);
      }

      this.frameId = requestAnimationFrame(drain);
    };

    this.frameId = requestAnimationFrame(drain);
  }
}
