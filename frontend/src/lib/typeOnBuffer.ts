/**
 * Type-on buffer — smooths out chunky streaming deltas into steady character flow.
 *
 * Instead of appending text deltas directly to the store (which causes
 * burst-render-pause-burst choppiness), deltas go into a buffer.
 * A requestAnimationFrame loop drains the buffer at a steady rate,
 * producing smooth character-by-character rendering.
 *
 * The buffer does NOT flush on stream end — it drains naturally so the
 * type-on effect continues past the last delta. Flush is reserved for
 * interrupts: user sends a message, tool call arrives, or the turn is
 * explicitly interrupted.
 *
 * Usage:
 *   const buffer = new TypeOnBuffer(
 *     (text) => appendToAssistant(id, text),
 *     () => setDrainComplete(true),
 *   );
 *   buffer.push(deltaText);    // on each text-delta event
 *   // DON'T flush on done — let it drain naturally
 *   buffer.flush();             // only on interrupt/tool-call/user-send
 */

export class TypeOnBuffer {
  private queue = "";
  private frameId: number | null = null;
  private callback: (text: string) => void;
  private onDrained: (() => void) | null;

  /**
   * Characters to drain per animation frame (~16ms at 60fps).
   * At 4 chars/frame: ~240 chars/sec at 60fps. Deliberately fast so
   * we can tell if it's working — dial back to 2 once confirmed.
   */
  charsPerFrame = 4;

  constructor(callback: (text: string) => void, onDrained?: () => void) {
    this.callback = callback;
    this.onDrained = onDrained ?? null;
  }

  /** Add text to the buffer. Starts draining if not already running. */
  push(text: string): void {
    this.queue += text;
    if (this.frameId === null) {
      this.startDraining();
    }
  }

  /** True if the buffer has text remaining to drain. */
  get isDraining(): boolean {
    return this.queue.length > 0 || this.frameId !== null;
  }

  /** Immediately flush all remaining text. Use for interrupts only. */
  flush(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    if (this.queue.length > 0) {
      this.callback(this.queue);
      this.queue = "";
    }
    this.onDrained?.();
  }

  /** Stop the drain loop and discard any remaining text. */
  reset(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    this.queue = "";
  }

  private startDraining(): void {
    const drain = () => {
      if (this.queue.length === 0) {
        this.frameId = null;
        this.onDrained?.();
        return;
      }

      // Drain up to charsPerFrame characters
      const chunk = this.queue.slice(0, this.charsPerFrame);
      this.queue = this.queue.slice(this.charsPerFrame);

      this.callback(chunk);

      // Schedule next frame
      this.frameId = requestAnimationFrame(drain);
    };

    this.frameId = requestAnimationFrame(drain);
  }
}
