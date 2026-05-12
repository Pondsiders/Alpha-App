/**
 * Wire protocol schemas — commands (client → server), responses
 * (server → one client, command-correlated), events (server → all clients).
 *
 * See docs/wire-protocol.md for the design rationale. Key principles:
 * - Three envelopes: commands, responses, events.
 * - Flat payloads (no nested data/metadata/params).
 * - Required fields are required (Zod explodes on missing fields).
 * - `id` on a command means "I expect a response"; the response echoes it.
 * - `chatId` means "this belongs to a chat".
 */

import { z } from "zod/v4";

/**
 * A 21-character nanoid using the default URL-safe alphabet. Same shape
 * as chatId — the wire field name carries the role, the value's shape
 * just says "nanoid."
 */
export const NanoId = z.string().regex(/^[A-Za-z0-9_-]{21}$/);

// =============================================================================
// Commands (client → server)
// =============================================================================

export const HelloCommand = z.object({
  command: z.literal("hello"),
  id: z.string(),
}).strict();

export const JoinChatCommand = z.object({
  command: z.literal("join-chat"),
  id: z.string(),
  chatId: z.string(),
}).strict();

export const CreateChatCommand = z.object({
  command: z.literal("create-chat"),
  id: z.string(),
}).strict();

export const SendCommand = z.object({
  command: z.literal("send"),
  id: z.string().optional(),
  chatId: z.string(),
  /**
   * Frontend-minted correlation token for the user message. The backend
   * stamps this onto the broadcast `user-message` echo so the originating
   * client can find its optimistic placeholder. Other clients see the
   * same field and ignore it (no local placeholder to match). The ID is
   * in-flight only — persistent IDs are owned by the SDK's session
   * transcript, not by us.
   */
  messageId: NanoId,
  content: z.array(z.record(z.string(), z.unknown())),
}).strict();

export const InterruptCommand = z.object({
  command: z.literal("interrupt"),
  id: z.string().optional(),
  chatId: z.string(),
}).strict();

export type HelloCommand = z.infer<typeof HelloCommand>;
export type JoinChatCommand = z.infer<typeof JoinChatCommand>;
export type CreateChatCommand = z.infer<typeof CreateChatCommand>;
export type SendCommand = z.infer<typeof SendCommand>;
export type InterruptCommand = z.infer<typeof InterruptCommand>;

export type Command =
  | HelloCommand
  | JoinChatCommand
  | CreateChatCommand
  | SendCommand
  | InterruptCommand;

// =============================================================================
// Responses (server → one client, command-correlated)
// =============================================================================

/**
 * The chat's position in the turn lifecycle. Values:
 *
 * - `pending` — no Claude subprocess (reaped or never spawned).
 * - `ready` — subprocess alive and idle, awaiting input.
 * - `preprocessing` — backend has the message; recall/timestamp/normalize
 *   in flight. Claude has not received the message yet.
 * - `processing` — Claude has the message and is generating.
 * - `postprocessing` — post-turn work (reflection, etc.) is running.
 *
 * The composer is open when state is `pending`, `ready`, or
 * `postprocessing`; locked when `preprocessing` or `processing`. The
 * state machine is implemented in `backend/src/alpha/chat.py`.
 */
export const ChatStateValue = z.enum([
  "pending",
  "ready",
  "preprocessing",
  "processing",
  "postprocessing",
]);
export type ChatStateValue = z.infer<typeof ChatStateValue>;

export const ChatSummary = z.object({
  chatId: z.string(),
  createdAt: z.iso.datetime({ offset: true }),
  lastActive: z.iso.datetime({ offset: true }),
  state: ChatStateValue,
  tokenCount: z.number(),
  contextWindow: z.number(),
}).strict();

export const HiYourselfResponse = z.object({
  response: z.literal("hi-yourself"),
  id: z.string(),
  chats: z.array(ChatSummary),
  version: z.string(),
}).strict();

export const ChatJoinedResponse = z.object({
  response: z.literal("chat-joined"),
  id: z.string(),
  chatId: z.string(),
  createdAt: z.iso.datetime({ offset: true }),
  lastActive: z.iso.datetime({ offset: true }),
  state: ChatStateValue,
  tokenCount: z.number(),
  contextWindow: z.number(),
  messages: z.array(
    z.object({
      role: z.enum(["user", "assistant", "system"]),
      data: z.record(z.string(), z.unknown()),
    })
  ),
});

export const ErrorResponse = z.object({
  response: z.literal("error"),
  id: z.string(),
  chatId: z.string().optional(),
  code: z.string(),
  message: z.string(),
}).strict();

export const ServerResponse = z.discriminatedUnion("response", [
  HiYourselfResponse,
  ChatJoinedResponse,
  ErrorResponse,
]);

export type HiYourselfResponse = z.infer<typeof HiYourselfResponse>;
export type ChatJoinedResponse = z.infer<typeof ChatJoinedResponse>;
export type ErrorResponse = z.infer<typeof ErrorResponse>;
export type ServerResponse = z.infer<typeof ServerResponse>;

// =============================================================================
// Events (server → all clients, broadcast)
// =============================================================================

export const AppStateEvent = z.object({
  event: z.literal("app-state"),
  chats: z.array(ChatSummary),
  version: z.string(),
}).strict();

export const ChatCreatedEvent = z.object({
  event: z.literal("chat-created"),
  chatId: z.string(),
  createdAt: z.iso.datetime({ offset: true }),
  lastActive: z.iso.datetime({ offset: true }),
  state: ChatStateValue,
  tokenCount: z.number(),
  contextWindow: z.number(),
  archived: z.boolean(),
}).strict();

export const ChatStateEvent = z.object({
  event: z.literal("chat-state"),
  chatId: z.string(),
  state: ChatStateValue,
  tokenCount: z.number(),
  contextWindow: z.number(),
}).strict();

// -- Turn lifecycle -----------------------------------------------------------

export const TurnStartedEvent = z.object({
  event: z.literal("turn-started"),
  chatId: z.string(),
}).strict();

export const UserMessageEvent = z.object({
  event: z.literal("user-message"),
  chatId: z.string(),
  messageId: z.string(),
  content: z.array(z.record(z.string(), z.unknown())),
  memories: z.array(z.record(z.string(), z.unknown())).nullable().default([]),
  timestamp: z.iso.datetime({ offset: true }),
});

export const ThinkingDeltaEvent = z.object({
  event: z.literal("thinking-delta"),
  chatId: z.string(),
  messageId: z.string(),
  delta: z.string(),
});

export const TextDeltaEvent = z.object({
  event: z.literal("text-delta"),
  chatId: z.string(),
  messageId: z.string(),
  delta: z.string(),
});

export const ToolCallStartEvent = z.object({
  event: z.literal("tool-call-start"),
  chatId: z.string(),
  toolCallId: z.string(),
  name: z.string(),
});

export const ToolCallDeltaEvent = z.object({
  event: z.literal("tool-call-delta"),
  chatId: z.string(),
  toolCallId: z.string(),
  delta: z.string(),
});

export const ToolCallResultEvent = z.object({
  event: z.literal("tool-call-result"),
  chatId: z.string(),
  toolCallId: z.string(),
  name: z.string(),
  args: z.record(z.string(), z.unknown()),
  result: z.unknown(),
});

export const AssistantMessageEvent = z.object({
  event: z.literal("assistant-message"),
  chatId: z.string(),
  content: z.array(z.record(z.string(), z.unknown())),
});

export const TurnCompleteEvent = z.object({
  event: z.literal("turn-complete"),
  chatId: z.string(),
}).strict();

// -- Discriminated union of all events ----------------------------------------

export const ServerEvent = z.discriminatedUnion("event", [
  AppStateEvent,
  ChatCreatedEvent,
  ChatStateEvent,
  TurnStartedEvent,
  UserMessageEvent,
  ThinkingDeltaEvent,
  TextDeltaEvent,
  ToolCallStartEvent,
  ToolCallDeltaEvent,
  ToolCallResultEvent,
  AssistantMessageEvent,
  TurnCompleteEvent,
]);

export type AppStateEvent = z.infer<typeof AppStateEvent>;
export type ChatCreatedEvent = z.infer<typeof ChatCreatedEvent>;
export type ChatStateEvent = z.infer<typeof ChatStateEvent>;
export type TurnStartedEvent = z.infer<typeof TurnStartedEvent>;
export type UserMessageEvent = z.infer<typeof UserMessageEvent>;
export type ThinkingDeltaEvent = z.infer<typeof ThinkingDeltaEvent>;
export type TextDeltaEvent = z.infer<typeof TextDeltaEvent>;
export type ToolCallStartEvent = z.infer<typeof ToolCallStartEvent>;
export type ToolCallDeltaEvent = z.infer<typeof ToolCallDeltaEvent>;
export type ToolCallResultEvent = z.infer<typeof ToolCallResultEvent>;
export type AssistantMessageEvent = z.infer<typeof AssistantMessageEvent>;
export type TurnCompleteEvent = z.infer<typeof TurnCompleteEvent>;
export type ServerEvent = z.infer<typeof ServerEvent>;

/**
 * Parsed server-to-client message — either a Response (correlated to a
 * command via `id`) or an Event (broadcast, no `id`). The transport doesn't
 * know which; this wrapper branches on the top-level discriminator key.
 */
export type ServerMessage =
  | { kind: "response"; payload: ServerResponse }
  | { kind: "event"; payload: ServerEvent };

/**
 * Parse and validate a raw JSON object from the WebSocket. Branches on
 * `response` vs `event` keys and dispatches to the matching union.
 * Throws ZodError if the shape is wrong or required fields are missing.
 */
export function parseMessage(raw: unknown): ServerMessage {
  if (typeof raw === "object" && raw !== null) {
    if ("response" in raw) {
      return { kind: "response", payload: ServerResponse.parse(raw) };
    }
    if ("event" in raw) {
      return { kind: "event", payload: ServerEvent.parse(raw) };
    }
  }
  throw new Error(
    "incoming message has neither `response` nor `event` discriminator",
  );
}

// =============================================================================
// Command constructors — typed, validating builders
// =============================================================================

/**
 * Typed factories for outbound commands. Each factory:
 *  - Takes a named-args object (no positional ambiguity at call sites).
 *  - Stamps the `command` discriminator string itself (callers don't write it).
 *  - Runs the value through the matching Zod schema, throwing on bad shape.
 *
 * Call sites:
 *   wsSend(Commands.send({ chatId, messageId, content }));
 *   wsSend(Commands.hello({ id }));
 *
 * Raw `wsSend({...})` still works for one-off scripts; the factories are the
 * paved path, not a wall.
 */
export const Commands = {
  hello: (args: { id: string }): HelloCommand =>
    HelloCommand.parse({ command: "hello", ...args }),

  send: (args: {
    chatId: string;
    messageId: string;
    content: Array<Record<string, unknown>>;
  }): SendCommand =>
    SendCommand.parse({ command: "send", ...args }),

  createChat: (args: { id: string }): CreateChatCommand =>
    CreateChatCommand.parse({ command: "create-chat", ...args }),

  joinChat: (args: { id: string; chatId: string }): JoinChatCommand =>
    JoinChatCommand.parse({ command: "join-chat", ...args }),

  interrupt: (args: { chatId: string }): InterruptCommand =>
    InterruptCommand.parse({ command: "interrupt", ...args }),
};
