/**
 * ContextRing — a Lucide-styled progress ring for context window usage.
 *
 * Visually indistinguishable from a Lucide icon at a glance: same stroke
 * weight (1.5), same 24px viewBox, stroke="currentColor" so color is
 * inherited from the parent. Semantically a capacity indicator, not a
 * clock. A clock is time; a ring is fullness. This is the right metaphor.
 *
 * The ring is pure visual — it takes a percent (0-100) and draws. Color
 * and threshold logic live in the consumer. Wrap in a parent with
 * `text-muted-foreground`, `text-primary`, or `text-destructive` and the
 * ring picks it up via currentColor.
 *
 * Usage:
 *   <span className={percent > 90 ? "text-destructive" : "text-muted-foreground"}>
 *     <ContextRing percent={percent} />
 *   </span>
 */

import type { FC, SVGAttributes } from "react";

export interface ContextRingProps extends Omit<SVGAttributes<SVGSVGElement>, "viewBox"> {
  /** Fill percentage, 0-100. Clamped internally. */
  percent: number;
}

// Lucide uses a 24×24 viewBox with 1.5px strokes. Matching both so this
// component sits in the same visual grammar as every other icon.
const VIEW = 24;
const STROKE = 1.5;
const R = (VIEW - STROKE) / 2;
const C = 2 * Math.PI * R;

export const ContextRing: FC<ContextRingProps> = ({ percent, ...svgProps }) => {
  const clamped = Math.max(0, Math.min(100, percent));
  const offset = C * (1 - clamped / 100);

  return (
    <svg
      viewBox={`0 0 ${VIEW} ${VIEW}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={STROKE}
      strokeLinecap="round"
      strokeLinejoin="round"
      // Default to Lucide's default size (24px) — override with className
      // e.g. `size-4` (16px) or `size-5` (20px).
      width={VIEW}
      height={VIEW}
      {...svgProps}
    >
      {/* Dim background ring — the "empty" part of the capacity */}
      <circle cx={VIEW / 2} cy={VIEW / 2} r={R} opacity="0.25" />
      {/* Filled arc — rotated -90° so 0% starts at 12 o'clock and fills
          clockwise. strokeDasharray = full circumference, and we offset
          the dash by (1 - percent) × C to hide the "unfilled" portion. */}
      <circle
        cx={VIEW / 2}
        cy={VIEW / 2}
        r={R}
        strokeDasharray={C}
        strokeDashoffset={offset}
        transform={`rotate(-90 ${VIEW / 2} ${VIEW / 2})`}
      />
    </svg>
  );
};

/**
 * Threshold-based color class for a given context percent.
 * - < 80%: muted (quiet)
 * - 80-90%: primary (amber — slow down, think about handoff)
 * - >= 90%: destructive (red — handoff now)
 *
 * Thresholds tuned for 1M context window with auto-compaction at 95%.
 */
export function contextColorClass(percent: number): string {
  if (percent >= 90) return "text-destructive";
  if (percent >= 80) return "text-primary";
  return "text-muted-foreground";
}
