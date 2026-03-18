import type { FC } from "react";

/**
 * Topic states:
 *   "off"   — available but not active (dim outline)
 *   "armed" — clicked, will inject on next send (highlighted)
 *   "on"    — context has been injected (solid fill)
 */
type TopicState = "off" | "armed" | "on";

interface TopicPill {
  name: string;
  state: TopicState;
}

interface TopicBarProps {
  topics: TopicPill[];
  onToggle: (name: string) => void;
}

const stateStyles: Record<TopicState, string> = {
  off: "border-border text-muted/50 hover:border-muted hover:text-muted",
  armed: "border-primary/60 bg-primary/10 text-primary",
  on: "border-[#7A8C42]/60 bg-[#7A8C42]/15 text-[#7A8C42]",
};

/**
 * TopicBar — a flex-wrap row of topic pills above the composer input.
 *
 * Each pill is clickable: off → armed → off (toggle).
 * The "on" state is set externally when context has been injected.
 * Renders nothing when there are no topics.
 */
export const TopicBar: FC<TopicBarProps> = ({ topics, onToggle }) => {
  if (topics.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {topics.map((topic) => (
        <button
          type="button"
          key={topic.name}
          onClick={() => onToggle(topic.name)}
          className={`
            px-2.5 py-0.5 text-[11px] font-medium rounded-full
            border cursor-pointer transition-colors
            ${stateStyles[topic.state]}
          `}
          title={
            topic.state === "off"
              ? `Click to arm "${topic.name}" — context will inject on next send`
              : topic.state === "armed"
                ? `Armed — "${topic.name}" context will inject on next send. Click to disarm.`
                : `"${topic.name}" context is active`
          }
        >
          {topic.name}
        </button>
      ))}
    </div>
  );
};
