/**
 * Type-on buffer — smooths out chunky streaming deltas into steady character flow.
 *
 * Instead of appending text deltas directly to the store (which causes
 * burst-render-pause-burst choppiness), deltas go into a buffer.
 * A requestAnimationFrame loop drains the buffer at a steady rate,
 * producing smooth character-by-character rendering.
 *
 * Usage:
 *   const buffer = new TypeOnBuffer((text) => appendToAssistant(id, text));
 *   // On each text-delta event:
 *   buffer.push(deltaText);
 *   // When streaming ends:
 *   buffer.flush();
 */

export class TypeOnBuffer {
  private queue = "";
  private frameId: number | null = null;
  private callback: (text: string) => void;

  /**
   * Characters to drain per animation frame (~16ms at 60fps).
   * Higher = faster reveal. Lower = smoother but potentially laggy.
   *
   * At 5 chars/frame: ~300 chars/sec = readable typewriter speed
   * At 10 chars/frame: ~600 chars/sec = fast but smooth
   * At 20 chars/frame: ~1200 chars/sec = very fast, still smoothed
   *
   * Claude produces ~60 chars/sec (15 tok/s × 4 chars/tok), so even
   * 5 chars/frame drains faster than it arrives. The smoothing comes
   * from spreading bursts across frames instead of dumping them all
   * in one render.
   */
  charsPerFrame = 2;

  constructor(callback: (text: string) => void) {
    this.callback = callback;
  }

  /** Add text to the buffer. Starts draining if not already running. */
  push(text: string): void {
    this.queue += text;
    if (this.frameId === null) {
      this.startDraining();
    }
  }

  /** Immediately flush all remaining text. Call when streaming ends. */
  flush(): void {
    if (this.frameId !== null) {
      cancelAnimationFrame(this.frameId);
      this.frameId = null;
    }
    if (this.queue.length > 0) {
      this.callback(this.queue);
      this.queue = "";
    }
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
