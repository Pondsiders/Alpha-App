/**
 * RuntimeProvider — bridges our Zustand store to assistant-ui.
 *
 * Reads the current chat's messages from the store, converts them to
 * assistant-ui's ThreadMessageLike format via `convertMessage`, and hands
 * them to `useExternalStoreRuntime`. The runtime has zero state of its
 * own — everything lives in the store, populated by the WebSocket handler
 * (see src/hooks/useAlphaWebSocket.ts).
 *
 * This replaces the previous LocalRuntime + ThreadHistoryAdapter +
 * useRemoteThreadListRuntime architecture, which had documented race
 * conditions around the adapter's load() callback (remoteId: undefined).
 *
 * Phase 1: read-only. `onNew` is a stub — we're rendering seeded data,
 * not sending messages yet. Phase 2 will wire `onNew` through to the
 * WebSocket's `send` function to push user messages to the backend.
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
} from "@/store";

// Stable empty-array reference so render-time reads don't churn when
// no chat is selected. useExternalStoreRuntime compares the `messages`
// prop by reference; a fresh `[]` on every render would look like churn.
const EMPTY_MESSAGES: readonly Message[] = [];

/**
 * Stub onNew handler for Phase 1. In Phase 2 this will call
 * useAlphaWebSocket's `send` to push a user-message event over the
 * WebSocket.
 */
async function onNewStub(message: AppendMessage) {
  console.warn(
    "[Alpha RuntimeProvider] onNew called but sending is not implemented yet",
    message,
  );
}

export function RuntimeProvider({
  children,
}: Readonly<{ children: ReactNode }>) {
  // Read the current chat — Zustand + Immer gives us structural sharing,
  // so this reference only changes when the current chat actually changes.
  const currentChat = useStore(selectCurrentChat);
  const messages = currentChat?.messages ?? EMPTY_MESSAGES;
  const isRunning = currentChat?.isRunning ?? false;

  const runtime = useExternalStoreRuntime<Message>({
    messages,
    isRunning,
    convertMessage,
    onNew: onNewStub,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
