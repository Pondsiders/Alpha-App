/**
 * Wire protocol schemas — commands (client → server) and events (server → client).
 *
 * See PROTOCOL.md for the design rationale. Key principles:
 * - Commands and events are different shapes (asymmetric protocol)
 * - Flat payloads (no nested data/metadata/params)
 * - Required fields are required (Zod explodes on missing fields)
 * - id means "I expect a response" (absent = fire-and-forget)
 * - chatId means "this belongs to a chat"
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

export const JoinChatCommand = z.object({
  command: z.literal("join-chat"),
  id: z.string().optional(),
  chatId: z.string(),
}).strict();

export const CreateChatCommand = z.object({
  command: z.literal("create-chat"),
  id: z.string().optional(),
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

export type JoinChatCommand = z.infer<typeof JoinChatCommand>;
export type CreateChatCommand = z.infer<typeof CreateChatCommand>;
export type SendCommand = z.infer<typeof SendCommand>;
export type InterruptCommand = z.infer<typeof InterruptCommand>;

export type Command =
  | JoinChatCommand
  | CreateChatCommand
  | SendCommand
  | InterruptCommand;

// =============================================================================
// Events (server → client)
// =============================================================================

// -- Chat lifecycle -----------------------------------------------------------

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

export const AppStateEvent = z.object({
  event: z.literal("app-state"),
  chats: z.array(ChatSummary),
  version: z.string(),
}).strict();

export const ChatLoadedEvent = z.object({
  event: z.literal("chat-loaded"),
  id: z.string().optional(),
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

export const ChatCreatedEvent = z.object({
  event: z.literal("chat-created"),
  id: z.string().optional(),
  chatId: z.string(),
  createdAt: z.iso.datetime({ offset: true }),
  lastActive: z.iso.datetime({ offset: true }),
  archived: z.boolean(),
}).strict();

export const ChatStateEvent = z.object({
  event: z.literal("chat-state"),
  chatId: z.string(),
  state: ChatStateValue,
  tokenCount: z.number(),
  contextWindow: z.number(),
  percent: z.number(),
}).strict();

// -- Turn lifecycle -----------------------------------------------------------

export const SendAckEvent = z.object({
  event: z.literal("send-ack"),
  id: z.string().optional(),
  chatId: z.string(),
});

/**
 * Source of a user message — who initiated it. The composer-input rule
 * is a function of `chat.state` alone (see the Chat docstring in
 * backend/src/alpha/chat.py); `source` is metadata for rendering and
 * message-history filtering, not for input gating.
 */
export const UserMessageSource = z.enum(["human", "reflection"]);
export type UserMessageSource = z.infer<typeof UserMessageSource>;

export const UserMessageEvent = z.object({
  event: z.literal("user-message"),
  chatId: z.string(),
  messageId: z.string(),
  source: UserMessageSource,
  content: z.array(z.record(z.string(), z.unknown())),
  memories: z.array(z.record(z.string(), z.unknown())).nullable().default([]),
  timestamp: z.string(),
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
  messageId: z.string(),
  content: z.array(z.record(z.string(), z.unknown())),
});

export const TurnCompleteEvent = z.object({
  event: z.literal("turn-complete"),
  chatId: z.string(),
}).strict();

// -- Errors -------------------------------------------------------------------

export const ErrorEvent = z.object({
  event: z.literal("error"),
  id: z.string().optional(),
  code: z.string(),
  message: z.string(),
}).strict();

// -- Discriminated union of all events ----------------------------------------

export const ServerEvent = z.discriminatedUnion("event", [
  AppStateEvent,
  ChatLoadedEvent,
  ChatCreatedEvent,
  ChatStateEvent,
  SendAckEvent,
  UserMessageEvent,
  ThinkingDeltaEvent,
  TextDeltaEvent,
  ToolCallStartEvent,
  ToolCallDeltaEvent,
  ToolCallResultEvent,
  AssistantMessageEvent,
  TurnCompleteEvent,
  ErrorEvent,
]);

export type AppStateEvent = z.infer<typeof AppStateEvent>;
export type ChatLoadedEvent = z.infer<typeof ChatLoadedEvent>;
export type ChatCreatedEvent = z.infer<typeof ChatCreatedEvent>;
export type ChatStateEvent = z.infer<typeof ChatStateEvent>;
export type SendAckEvent = z.infer<typeof SendAckEvent>;
export type UserMessageEvent = z.infer<typeof UserMessageEvent>;
export type ThinkingDeltaEvent = z.infer<typeof ThinkingDeltaEvent>;
export type TextDeltaEvent = z.infer<typeof TextDeltaEvent>;
export type ToolCallStartEvent = z.infer<typeof ToolCallStartEvent>;
export type ToolCallDeltaEvent = z.infer<typeof ToolCallDeltaEvent>;
export type ToolCallResultEvent = z.infer<typeof ToolCallResultEvent>;
export type AssistantMessageEvent = z.infer<typeof AssistantMessageEvent>;
export type TurnCompleteEvent = z.infer<typeof TurnCompleteEvent>;
export type ErrorEvent = z.infer<typeof ErrorEvent>;
export type ServerEvent = z.infer<typeof ServerEvent>;

/**
 * Parse and validate a raw JSON object from the WebSocket as a ServerEvent.
 * Throws ZodError if the shape is wrong or required fields are missing.
 */
export function parseEvent(raw: unknown): ServerEvent {
  return ServerEvent.parse(raw);
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
 *   wsSend(Commands.createChat());
 *
 * Raw `wsSend({...})` still works for one-off scripts; the factories are the
 * paved path, not a wall.
 */
export const Commands = {
  send: (args: {
    chatId: string;
    messageId: string;
    content: Array<Record<string, unknown>>;
  }): SendCommand =>
    SendCommand.parse({ command: "send", ...args }),

  createChat: (): CreateChatCommand =>
    CreateChatCommand.parse({ command: "create-chat" }),

  joinChat: (args: { chatId: string }): JoinChatCommand =>
    JoinChatCommand.parse({ command: "join-chat", ...args }),

  interrupt: (args: { chatId: string }): InterruptCommand =>
    InterruptCommand.parse({ command: "interrupt", ...args }),
};
