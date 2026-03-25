/**
 * SystemMessage — Renders system events in the message stream.
 *
 * Left-aligned, minimal, discreet. Not from the human, not from me.
 * From the infrastructure. A quiet note in the margin of the conversation.
 *
 * Used as the SystemMessage component in ThreadPrimitive.Messages.
 */

import { useMessage, MessagePrimitive } from "@assistant-ui/react";

export const SystemMessage = () => {
  const message = useMessage();
  const text =
    message.content
      ?.filter((p) => p.type === "text")
      .map((p) => ("text" in p ? p.text : ""))
      .join("") || "System event";

  return (
    <MessagePrimitive.Root>
      <div className="text-[12px] text-muted/60 leading-snug">
        {text}
      </div>
    </MessagePrimitive.Root>
  );
};
