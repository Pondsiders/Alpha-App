/**
 * MemoryCard — A compact card showing a recalled memory with hover preview.
 *
 * The card is a small chip: memory ID left, score right, one-line preview.
 * Hovering shows a HoverCard with the full memory content, timestamp,
 * and metadata.
 */

import { motion } from "framer-motion";
import { Calendar } from "lucide-react";
import type { RecalledMemory } from "../store";
import {
  HoverCard,
  HoverCardTrigger,
  HoverCardContent,
} from "./ui/hover-card";

interface MemoryCardProps {
  memory: RecalledMemory;
  /** Suppress shadows — for use inside inset containers like MemoryTray. */
  flat?: boolean;
}

/** Format created_at as PSO-8601 (our bastard timezone format). */
function formatPSO(isoString: string): string {
  try {
    const date = new Date(isoString);
    // PSO-8601: "Fri Mar 20 2026, 3:11 PM"
    return date.toLocaleString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  } catch {
    return isoString;
  }
}

/** Format created_at into a human-readable relative age. */
function formatAge(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return "today";
    if (diffDays === 1) return "yesterday";
    if (diffDays < 7) return `${diffDays} days ago`;
    if (diffDays < 30) {
      const weeks = Math.floor(diffDays / 7);
      return weeks === 1 ? "1 week ago" : `${weeks} weeks ago`;
    }
    if (diffDays < 365) {
      const months = Math.floor(diffDays / 30);
      return months === 1 ? "1 month ago" : `${months} months ago`;
    }
    const years = Math.floor(diffDays / 365);
    return years === 1 ? "1 year ago" : `${years} years ago`;
  } catch {
    return "";
  }
}

export function MemoryCard({ memory, flat = false }: MemoryCardProps) {
  // Let CSS line-clamp handle truncation — no JS slicing needed.
  const preview = memory.content || "";

  const pso = formatPSO(memory.created_at);
  const age = formatAge(memory.created_at);

  return (
    <HoverCard openDelay={300} closeDelay={100}>
      <HoverCardTrigger asChild>
        <motion.div
          whileHover={flat ? { y: -2 } : { y: -3, boxShadow: "0 4px 12px rgba(0,0,0,0.5)" }}
          transition={{ type: "tween", duration: 0.15 }}
          className={`flex-shrink-0 px-3 py-2 rounded-lg border border-border bg-surface
                     max-w-[220px] min-w-[160px] cursor-default select-none
                     hover:border-primary/30 transition-colors duration-150
                     ${flat ? "" : "shadow-[0_2px_8px_rgba(0,0,0,0.4)]"}`}
        >
          <div className="flex items-center justify-between text-[11px] text-muted mb-0.5">
            <span className="font-mono">#{memory.id}</span>
            <span className="font-mono">{memory.score.toFixed(2)}</span>
          </div>
          {preview && (
            <div className="text-[12px] text-text/70 leading-tight line-clamp-2">
              {preview}
            </div>
          )}
        </motion.div>
      </HoverCardTrigger>
      <HoverCardContent className="w-80" side="top" align="center">
        {/* Header — ID + score */}
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono text-[12px] text-primary font-semibold">
            #{memory.id}
          </span>
          <span className="font-mono text-[12px] text-muted">
            {memory.score.toFixed(2)}
          </span>
        </div>

        {/* Memory content */}
        <div className="text-[13px] text-text/90 leading-relaxed max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words">
          {memory.content}
        </div>

        {/* Footer — PSO-8601 timestamp + relative age */}
        <div className="mt-2 pt-2 border-t border-border text-[11px] text-muted">
          <div className="flex items-center gap-1.5">
            <Calendar size={11} className="shrink-0 text-muted/60" />
            <span className="font-mono">{pso}</span>
          </div>
          {age && (
            <div className="ml-[17px] text-muted/60">{age}</div>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}
