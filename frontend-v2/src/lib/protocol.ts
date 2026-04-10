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

// =============================================================================
// Commands (client → server)
// =============================================================================

export const JoinChatCommand = z.object({
  command: z.literal("join-chat"),
  id: z.string().optional(),
  chatId: z.string(),
});

export const CreateChatCommand = z.object({
  command: z.literal("create-chat"),
  id: z.string().optional(),
});

export const SendCommand = z.object({
  command: z.literal("send"),
  id: z.string().optional(),
  chatId: z.string(),
  messageId: z.string().optional(),
  content: z.array(z.record(z.string(), z.unknown())),
});

export const InterruptCommand = z.object({
  command: z.literal("interrupt"),
  chatId: z.string(),
});

export const BuzzCommand = z.object({
  command: z.literal("buzz"),
  id: z.string().optional(),
  chatId: z.string(),
});

export type JoinChatCommand = z.infer<typeof JoinChatCommand>;
export type CreateChatCommand = z.infer<typeof CreateChatCommand>;
export type SendCommand = z.infer<typeof SendCommand>;
export type InterruptCommand = z.infer<typeof InterruptCommand>;
export type BuzzCommand = z.infer<typeof BuzzCommand>;

export type Command =
  | JoinChatCommand
  | CreateChatCommand
  | SendCommand
  | InterruptCommand
  | BuzzCommand;

// =============================================================================
// Events (server → client)
// =============================================================================

// -- Chat lifecycle -----------------------------------------------------------

export const AppStateEvent = z.object({
  event: z.literal("app-state"),
  chats: z.array(
    z.object({
      chatId: z.string(),
      title: z.string(),
      createdAt: z.number(),
      updatedAt: z.number(),
      state: z.string(),
      tokenCount: z.number(),
      contextWindow: z.number(),
    })
  ),
  solitude: z.boolean().default(false),
  version: z.string().default(""),
});

export const ChatLoadedEvent = z.object({
  event: z.literal("chat-loaded"),
  id: z.string().optional(),
  chatId: z.string(),
  title: z.string(),
  createdAt: z.number(),
  updatedAt: z.number(),
  state: z.string(),
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
  title: z.string().default(""),
  createdAt: z.number(),
});

export const ChatStateEvent = z.object({
  event: z.literal("chat-state"),
  chatId: z.string(),
  state: z.string(),
});

// -- Turn lifecycle -----------------------------------------------------------

export const SendAckEvent = z.object({
  event: z.literal("send-ack"),
  id: z.string().optional(),
  chatId: z.string(),
});

export const UserMessageEvent = z.object({
  event: z.literal("user-message"),
  chatId: z.string(),
  messageId: z.string(),
  content: z.array(z.record(z.string(), z.unknown())),
  memories: z.array(z.record(z.string(), z.unknown())).nullable().default([]),
  timestamp: z.string().default(""),
});

export const ThinkingDeltaEvent = z.object({
  event: z.literal("thinking-delta"),
  chatId: z.string(),
  delta: z.string(),
});

export const TextDeltaEvent = z.object({
  event: z.literal("text-delta"),
  chatId: z.string(),
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
  tokenCount: z.number(),
  contextWindow: z.number(),
  percent: z.number(),
});

// -- Context ------------------------------------------------------------------

export const ContextUpdateEvent = z.object({
  event: z.literal("context-update"),
  chatId: z.string(),
  tokenCount: z.number(),
  contextWindow: z.number(),
  percent: z.number(),
});

// -- Errors -------------------------------------------------------------------

export const ErrorEvent = z.object({
  event: z.literal("error"),
  id: z.string().optional(),
  chatId: z.string().optional(),
  code: z.string(),
  message: z.string(),
});

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
  ContextUpdateEvent,
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
export type ContextUpdateEvent = z.infer<typeof ContextUpdateEvent>;
export type ErrorEvent = z.infer<typeof ErrorEvent>;
export type ServerEvent = z.infer<typeof ServerEvent>;

/**
 * Parse and validate a raw JSON object from the WebSocket as a ServerEvent.
 * Throws ZodError if the shape is wrong or required fields are missing.
 */
export function parseEvent(raw: unknown): ServerEvent {
  return ServerEvent.parse(raw);
}
