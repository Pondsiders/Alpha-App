/**
 * RuntimeProvider — wires assistant-ui to our backend.
 *
 * Uses useRemoteThreadListRuntime for multi-thread support with
 * thread list from GET /api/threads (Postgres).
 *
 * Thread history loaded via ThreadHistoryAdapter — fetches
 * GET /api/threads/{id}/messages when a thread is opened.
 * Messages render all at once (no SSE replay).
 */

import { type ReactNode, useMemo } from "react";
import {
  AssistantRuntimeProvider,
  RuntimeAdapterProvider,
  useRemoteThreadListRuntime,
  useLocalRuntime,
  useAui,
  type RemoteThreadListAdapter,
  type ThreadHistoryAdapter,
  type ChatModelAdapter,
} from "@assistant-ui/react";

// Build a full ThreadMessage from our API's ThreadMessageLike format.
// Mirrors what fromThreadMessageLike() produces internally.
function toThreadMessage(m: { id: string; role: string; content: unknown[] }) {
  const common = { id: m.id, createdAt: new Date() };
  if (m.role === "assistant") {
    return {
      ...common,
      role: "assistant" as const,
      content: m.content,
      status: { type: "complete" as const, reason: "unknown" as const },
      metadata: {
        unstable_state: null,
        unstable_annotations: [] as unknown[],
        unstable_data: [] as unknown[],
        custom: {},
        steps: [] as unknown[],
      },
    };
  }
  return {
    ...common,
    role: m.role as "user",
    content: m.content,
    attachments: [],
    metadata: { custom: {} },
  };
}

// Stub model adapter — will be replaced with our Claude Code translator
const ModelAdapter: ChatModelAdapter = {
  async *run({ messages }) {
    // TODO: POST to /api/chat, translate stream-json to assistant-ui format
    yield {
      content: [{ type: "text", text: "🦆 Chat is not connected yet. This is a UI preview." }],
    };
  },
};

// In-memory thread storage — fallback when backend isn't available
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

  // Per-thread Provider — injects the history adapter that loads
  // messages from our backend when a thread is opened.
  unstable_Provider: ThreadProvider,
};

/**
 * ThreadProvider — wraps each thread with a history adapter.
 *
 * When a thread is opened, the history adapter's load() fetches
 * the thread's messages from GET /api/threads/{id}/messages.
 * assistant-ui renders them all at once — no SSE, no replay.
 */
function ThreadProvider({ children }: { children: ReactNode }) {
  const aui = useAui();

  const history = useMemo<ThreadHistoryAdapter>(
    () => ({
      async load() {
        const { remoteId } = aui.threadListItem().getState();
        console.log("[history] load() called, remoteId:", remoteId);
        if (!remoteId) return { messages: [] };

        try {
          const res = await fetch(`/api/threads/${remoteId}/messages`);
          if (!res.ok) return { messages: [] };
          const raw = await res.json();
          console.log("[history] fetched", raw.length, "messages from API");

          // Convert to repository import format with full ThreadMessage objects
          let prevId: string | null = null;
          const repoMessages = raw.map((m: { id: string; role: string; content: unknown[] }) => {
            const message = toThreadMessage(m);
            const entry = { parentId: prevId, message };
            prevId = m.id;
            return entry;
          });

          console.log("[history] returning", repoMessages.length, "repo messages");
          return {
            headId: prevId,
            messages: repoMessages,
          };
        } catch (e) {
          console.error("[history] load error:", e);
          return { messages: [] };
        }
      },
      async append() {
        // Message persistence — wire up later when sending works
      },
    }),
    [aui],
  );

  const adapters = useMemo(() => {
    console.log("[ThreadProvider] creating adapters with history:", !!history);
    return { history };
  }, [history]);

  console.log("[ThreadProvider] rendering, adapters:", adapters);

  return (
    <RuntimeAdapterProvider adapters={adapters}>
      {children}
    </RuntimeAdapterProvider>
  );
}

export function RuntimeProvider({
  children,
}: Readonly<{ children: ReactNode }>) {
  const runtime = useRemoteThreadListRuntime({
    runtimeHook: () => useLocalRuntime(ModelAdapter),
    adapter: threadListAdapter,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
