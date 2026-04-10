/**
 * RuntimeProvider — bridges our Zustand store to assistant-ui.
 *
 * Reads the current chat's messages from the store, converts them to
 * assistant-ui's ThreadMessageLike format via `convertMessage`, and hands
 * them to `useExternalStoreRuntime`. The runtime has zero state of its
 * own — everything lives in the store, populated by the WebSocket handler
 * (see src/hooks/useAlphaWebSocket.ts).
 */

import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
} from "@assistant-ui/react";

import {
  convertMessage,
  selectCurrentChat,
  useStore,
  type Message,
  type UserMessage,
} from "@/store";

// Stable empty-array reference so render-time reads don't churn when
// no chat is selected. useExternalStoreRuntime compares the `messages`
// prop by reference; a fresh `[]` on every render would look like churn.
const EMPTY_MESSAGES: readonly Message[] = [];

export function RuntimeProvider({
  children,
}: Readonly<{ children: ReactNode }>) {
  const currentChat = useStore(selectCurrentChat);
  const messages = currentChat?.messages ?? EMPTY_MESSAGES;
  const isRunning = currentChat?.isRunning ?? false;

  const wsSend = useStore((s) => s.wsSend);
  const currentChatId = useStore((s) => s.currentChatId);

  const runtime = useExternalStoreRuntime<Message>({
    messages,
    isRunning,
    convertMessage,
    onNew: async (message: AppendMessage) => {
      if (!wsSend || !currentChatId) return;

      // Extract text from assistant-ui's AppendMessage content blocks
      const content = message.content
        .filter((block): block is { type: "text"; text: string } => block.type === "text")
        .map((block) => ({ type: "text" as const, text: block.text }));

      if (content.length === 0) return;

      // Generate message ID at the first instant it exists.
      // This ID follows the message everywhere: store, WebSocket, backend, echo.
      const messageId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

      // Optimistic add — message appears instantly.
      const appendMessage = useStore.getState().appendMessage;
      appendMessage(currentChatId, {
        role: "user",
        data: {
          id: messageId,
          source: "human",
          content,
          timestamp: null,
        } as UserMessage,
      });

      // Send with the ID so the backend echoes it back for reconciliation.
      wsSend({
        command: "send",
        chatId: currentChatId,
        messageId,
        content,
      });
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
