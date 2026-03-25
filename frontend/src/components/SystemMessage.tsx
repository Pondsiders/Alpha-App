/**
 * SystemMessage — Renders system events in the message stream.
 *
 * A quiet, informational card that marks where a system event occurred
 * (task notification, post-turn activity, etc.). Visual separator between
 * what came before and what the system triggered.
 *
 * Used as the SystemMessage component in ThreadPrimitive.Messages.
 */

import { CheckCircle, XCircle, Bell } from "lucide-react";
import { useMessage, MessagePrimitive } from "@assistant-ui/react";

export const SystemMessage = () => {
  const message = useMessage();
  // System messages have TextMessagePart content (converted by convertMessage)
  const text =
    message.content
      ?.filter((p) => p.type === "text")
      .map((p) => ("text" in p ? p.text : ""))
      .join("") || "System event";

  // Infer status from text content
  const isError = /failed|error/i.test(text);
  const isCompleted = /completed|exit code 0/i.test(text);
  const Icon = isError ? XCircle : isCompleted ? CheckCircle : Bell;
  const dotColor = isError ? "var(--theme-error)" : "var(--theme-success)";

  return (
    <MessagePrimitive.Root className="my-2 px-2">
      <div className="w-full rounded-lg border border-border/50 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-surface/50">
          <Icon
            size={14}
            className="shrink-0"
            style={{ color: "var(--theme-muted)" }}
          />
          <span className="text-[12px] text-muted leading-snug flex-1">
            {text}
          </span>
          <span
            className="w-2 h-2 rounded-full shrink-0"
            style={{ backgroundColor: dotColor }}
          />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
};
