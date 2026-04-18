/**
 * SequentialParts — sequential part reveal with animation coordination.
 *
 * Replaces MessagePrimitive.Parts for assistant messages. Reads the parts
 * array from assistant-ui's store, but only renders up to revealedCount.
 * Each part calls onDone when its animation completes, advancing the gate.
 *
 * For completed messages (not streaming), renders all parts immediately
 * with no animation — historical messages shouldn't type-on.
 */

import { useAuiState, MessagePrimitive } from "@assistant-ui/react";
import { useEffect, useRef, type FC } from "react";
import { useStore } from "@/store";
import { useSequentialReveal } from "@/lib/useSequentialReveal";
import { DrainRateProvider } from "@/lib/DrainRateContext";
import { AnimatedText } from "./AnimatedText";
import { ToolFallback } from "./tool-fallback";
import { MarkdownText } from "./markdown-text";

// ---------------------------------------------------------------------------
// SequentialParts — the public component
// ---------------------------------------------------------------------------

export const SequentialParts: FC = () => {
  const parts = useAuiState((s) => s.message.content) as unknown as ContentPart[];
  const status = useAuiState((s) => s.message.status);
  const messageId = useAuiState((s) => s.message.id);
  const isComplete = status?.type !== "running";

  // Get chatId from the Zustand store
  const chatId = useStore((s) => s.currentChatId) ?? "";

  console.log("[SeqParts]", { partsLength: parts.length, isComplete, messageId, types: parts.map(p => p.type) });

  // Track whether this message was EVER streaming in this render lifecycle.
  // Once we enter the animated path, stay on it until the animation finishes.
  const wasStreamingRef = useRef(false);
  if (!isComplete) wasStreamingRef.current = true;

  // For messages that were never streaming (loaded from history),
  // use MessagePrimitive.Parts directly.
  if (isComplete && !wasStreamingRef.current) {
    return (
      <MessagePrimitive.Parts
        components={{
          Text: MarkdownText,
          tools: { Fallback: ToolFallback },
          ToolGroup: ({ children }) => (
            <div className="flex flex-col gap-4">{children}</div>
          ),
        }}
      />
    );
  }

  // For streaming or recently-streaming messages, use the animated path.
  // The animation will naturally complete and the parts will settle.
  return (
    <DrainRateProvider baseRate={0} chaseFactor={1.0}>
      <RevealingParts parts={parts} isComplete={isComplete} chatId={chatId} messageId={messageId} />
    </DrainRateProvider>
  );
};

// ---------------------------------------------------------------------------
// RevealingParts — the animated path (streaming messages only)
// ---------------------------------------------------------------------------

interface ContentPart {
  type: string;
  text?: string;
  thinking?: string;
  toolCallId?: string;
  toolName?: string;
  args?: unknown;
  argsText?: string;
  result?: unknown;
  [key: string]: unknown;
}

const RevealingParts: FC<{ parts: ContentPart[]; isComplete: boolean; chatId: string; messageId: string }> = ({ parts, isComplete, chatId, messageId }) => {
  const { revealedCount, markDone } = useSequentialReveal(parts.length);
  const visibleParts = parts.slice(0, revealedCount);

  // A part has stopped growing if: the message is complete, OR
  // there's a later part in the array (meaning this one is finalized).
  return (
    <>
      {visibleParts.map((part, i) => {
        const isStillGrowing = !isComplete && i === parts.length - 1;
        return (
          <AnimatedPartRenderer
            key={i}
            part={part}
            index={i}
            isStillGrowing={isStillGrowing}
            chatId={chatId}
            messageId={messageId}
            onDone={() => markDone(i)}
          />
        );
      })}
    </>
  );
};

// ---------------------------------------------------------------------------
// Part renderers
// ---------------------------------------------------------------------------

/** Render a part with animation (streaming path). */
const AnimatedPartRenderer: FC<{
  part: ContentPart;
  index: number;
  isStillGrowing: boolean;
  chatId: string;
  messageId: string;
  onDone: () => void;
}> = ({ part, index, isStillGrowing, chatId, messageId, onDone }) => {
  switch (part.type) {
    case "text":
      return (
        <AnimatedText
          text={part.text ?? ""}
          chatId={chatId}
          messageId={messageId}
          isStreaming={isStillGrowing}
          partIndex={index}
          onDone={onDone}
        />
      );

    case "reasoning":
      // For now, render thinking blocks immediately and mark done.
      // Can add animation later.
      return <ImmediateDone onDone={onDone}><ThinkingBlock content={part.text ?? ""} /></ImmediateDone>;

    case "tool-call":
      return (
        <ToolCallWithDone
          part={part}
          onDone={onDone}
        />
      );

    default:
      return <ImmediateDone onDone={onDone} />;
  }
};


// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------

/** Calls onDone immediately on mount. For parts with no animation. */
const ImmediateDone: FC<{ onDone: () => void; children?: React.ReactNode }> = ({
  onDone,
  children,
}) => {
  useEffect(() => {
    onDone();
  }, [onDone]);
  return <>{children}</>;
};

/** Tool card that calls onDone when the result arrives. */
const ToolCallWithDone: FC<{ part: ContentPart; onDone: () => void }> = ({
  part,
  onDone,
}) => {
  const hasResult = part.result != null;

  useEffect(() => {
    if (hasResult) {
      onDone();
    }
  }, [hasResult, onDone]);

  return (
    <ToolFallback
      toolName={part.toolName as string ?? "unknown"}
      toolCallId={part.toolCallId as string ?? ""}
      args={part.args}
      argsText={part.argsText as string | undefined}
      result={part.result}
    />
  );
};

/** Simple thinking block display. */
const ThinkingBlock: FC<{ content: string }> = ({ content }) => {
  if (!content) return null;
  return (
    <details className="group rounded-lg border border-border/50 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
      <summary className="cursor-pointer select-none font-medium">
        Thinking...
      </summary>
      <pre className="mt-2 whitespace-pre-wrap text-xs">{content}</pre>
    </details>
  );
};
