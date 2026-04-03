/**
 * RuntimeProvider — wires assistant-ui to a mock backend.
 *
 * Uses useRemoteThreadListRuntime for multi-thread support with an
 * in-memory thread store (will be replaced with Postgres endpoints).
 * The chat runtime uses useChatRuntime pointed at /api/chat (will be
 * the stream-json translator when the backend is ready).
 *
 * For now: mock data, no backend, pure UI validation.
 */

import type { ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  useRemoteThreadListRuntime,
  type RemoteThreadListAdapter,
} from "@assistant-ui/react";
import { useChatRuntime, AssistantChatTransport } from "@assistant-ui/react-ai-sdk";

// In-memory thread storage — simulates our Postgres thread table
const threadsStore = new Map<
  string,
  {
    remoteId: string;
    status: "regular" | "archived";
    title?: string;
  }
>();

const threadListAdapter: RemoteThreadListAdapter = {
  async list() {
    try {
      const res = await fetch("/api/threads");
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      return {
        threads: data.map((t: { chatId: string; title?: string }) => ({
          remoteId: t.chatId,
          status: "regular" as const,
          title: t.title || undefined,
        })),
      };
    } catch {
      // Fallback to in-memory if backend isn't available
      return {
        threads: Array.from(threadsStore.values()).map((thread) => ({
          remoteId: thread.remoteId,
          status: thread.status,
          title: thread.title,
        })),
      };
    }
  },

  async initialize(localId) {
    const remoteId = localId;
    threadsStore.set(remoteId, {
      remoteId,
      status: "regular",
    });
    return { remoteId, externalId: undefined };
  },

  async rename(remoteId, title) {
    const thread = threadsStore.get(remoteId);
    if (thread) {
      thread.title = title;
    }
  },

  async archive(remoteId) {
    const thread = threadsStore.get(remoteId);
    if (thread) {
      thread.status = "archived";
    }
  },

  async unarchive(remoteId) {
    const thread = threadsStore.get(remoteId);
    if (thread) {
      thread.status = "regular";
    }
  },

  async delete(remoteId) {
    threadsStore.delete(remoteId);
  },

  async fetch(remoteId) {
    const thread = threadsStore.get(remoteId);
    if (!thread) {
      throw new Error("Thread not found");
    }
    return {
      remoteId: thread.remoteId,
      status: thread.status,
      title: thread.title,
    };
  },

  async generateTitle(_remoteId, messages) {
    const { createAssistantStream } = await import("assistant-stream");
    return createAssistantStream(async (controller) => {
      const firstUserMessage = messages.find((m) => m.role === "user");
      if (firstUserMessage) {
        const content = firstUserMessage.content
          .filter((c) => c.type === "text")
          .map((c) => c.text)
          .join(" ");
        const title =
          content.slice(0, 50) + (content.length > 50 ? "..." : "");
        controller.appendText(title);
      } else {
        controller.appendText("New Chat");
      }
    });
  },
};

export function RuntimeProvider({
  children,
}: Readonly<{ children: ReactNode }>) {
  const runtime = useRemoteThreadListRuntime({
    runtimeHook: useChatRuntime,
    adapter: threadListAdapter,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
