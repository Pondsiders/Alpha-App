/**
 * ApproachLight — inline annotation for context window warnings.
 *
 * A stage direction, not a message bubble. Renders as a subtle divider
 * with the warning text — the exact same bytes Alpha receives, so
 * Jeffery sees what she sees.
 *
 * Yellow (65%): amber, start wrapping up.
 * Red (75%): corrupted red, compaction imminent.
 */

import type { ApproachLight as ApproachLightType } from "@/store";

const LEVEL_STYLES: Record<string, { color: string; borderColor: string; label: string }> = {
  yellow: {
    color: "var(--theme-primary)",       // amber #d4a574
    borderColor: "var(--theme-primary)",
    label: "yellow",
  },
  red: {
    color: "var(--theme-error)",         // corrupted red #C4504A
    borderColor: "var(--theme-error)",
    label: "red",
  },
};

export function ApproachLight({ level, text }: ApproachLightType) {
  const style = LEVEL_STYLES[level] ?? LEVEL_STYLES.yellow;

  return (
    <div
      className="my-4 flex items-center gap-3"
      data-testid={`approach-light-${level}`}
      aria-label={`approach light ${style.label}`}
    >
      {/* Left rule */}
      <div
        className="flex-1 h-px opacity-40"
        style={{ backgroundColor: style.borderColor }}
      />

      {/* Annotation text */}
      <span
        className="text-xs italic whitespace-nowrap select-none"
        style={{ color: style.color }}
      >
        {text}
      </span>

      {/* Right rule */}
      <div
        className="flex-1 h-px opacity-40"
        style={{ backgroundColor: style.borderColor }}
      />
    </div>
  );
}
