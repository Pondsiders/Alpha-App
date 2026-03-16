/**
 * StreamingBar — The breathing feather cursor.
 *
 * Always rendered as the last child of the last assistant message.
 * Visibility and animation indicate state:
 *
 *   Sleeping (idle): Dim, breathing slowly — Apple sleep light style.
 *   Busy (streaming/thinking/tools): Amber, visible, pulsing.
 *   Dead (reaped): display: none.
 *
 * The breathing animation is calibrated to ~3.5 second cycle,
 * matching the rate Apple used for MacBook sleep indicators.
 */

import { Feather } from "lucide-react";
import { useWorkshopStore } from "../store";

export function StreamingBar() {
  const activeChat = useWorkshopStore((s) =>
    s.activeChatId ? s.chats[s.activeChatId] : null
  );

  const state = activeChat?.state;

  // Dead or no chat — completely invisible, no space
  if (!state || state === "dead") {
    return null;
  }

  const isBusy = state === "busy" || state === "starting";

  return (
    <div className="mt-3 flex items-center justify-start">
      <div
        className={`
          inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full
          bg-primary/20 ${isBusy ? "streaming-bar-busy" : "streaming-bar-sleeping"}
        `}
      >
        <Feather
          size={13}
          className="text-primary"
        />
      </div>
    </div>
  );
}
