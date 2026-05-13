/**
 * Round-trip every implemented wire-payload fixture through its Zod schema.
 *
 * Mirrors `backend/tests/unit/test_wire_fixtures.py`. Both halves of the
 * wire read the same JSON files at `fixtures/wire-payloads/`; both must
 * round-trip cleanly. The fixture set tracks the spec; the test set
 * tracks what's currently implemented.
 *
 * Every fixture is accounted for: its discriminator is either in
 * `IMPLEMENTED` (round-tripped through its Zod schema) or in
 * `NOT_YET_IMPLEMENTED` (explicitly deferred). A fixture whose
 * discriminator is in neither fails the accounting test — there is no
 * silent third category.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import { z } from "zod/v4";
import {
  AppStateEvent,
  AssistantMessageEvent,
  ChatCreatedResponse,
  ChatJoinedResponse,
  ChatStateEvent,
  CreateChatCommand,
  ErrorResponse,
  HelloCommand,
  HiYourselfResponse,
  InterruptCommand,
  InterruptedResponse,
  JoinChatCommand,
  ReceivedResponse,
  SendCommand,
  TurnStartedEvent,
  UserMessageEvent,
} from "../src/lib/protocol";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = resolve(__dirname, "..", "..", "fixtures", "wire-payloads");

// Discriminators we round-trip through a Zod schema. The discriminator is
// the value of the `event` / `response` / `command` field in the fixture.
const IMPLEMENTED: Record<string, z.ZodTypeAny> = {
  hello: HelloCommand,
  "create-chat": CreateChatCommand,
  "join-chat": JoinChatCommand,
  send: SendCommand,
  interrupt: InterruptCommand,
  "hi-yourself": HiYourselfResponse,
  "chat-joined": ChatJoinedResponse,
  "chat-created": ChatCreatedResponse,
  received: ReceivedResponse,
  interrupted: InterruptedResponse,
  error: ErrorResponse,
  "app-state": AppStateEvent,
  "chat-state": ChatStateEvent,
  "turn-started": TurnStartedEvent,
  "user-message": UserMessageEvent,
  "assistant-message": AssistantMessageEvent,
};

// Discriminators that the spec defines and the fixture set carries, but
// the frontend hasn't implemented yet. Moving a discriminator out of this
// set into `IMPLEMENTED` is what "we implemented this shape" looks like
// in the test infrastructure.
const NOT_YET_IMPLEMENTED: Set<string> = new Set([
  "thinking-delta",
  "text-delta",
  "tool-call-start",
  "tool-call-delta",
  "tool-call-result",
  "turn-complete",
]);

function discriminator(payload: Record<string, unknown>): string {
  for (const key of ["event", "response", "command"] as const) {
    const value = payload[key];
    if (typeof value === "string") return value;
  }
  throw new Error(
    `fixture has no event/response/command discriminator: ${JSON.stringify(payload)}`,
  );
}

function fixturePaths(): string[] {
  return readdirSync(FIXTURES_DIR)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => join(FIXTURES_DIR, name));
}

function implementedFixturePaths(): string[] {
  return fixturePaths().filter((path) => {
    const payload: Record<string, unknown> = JSON.parse(
      readFileSync(path, "utf-8"),
    );
    return discriminator(payload) in IMPLEMENTED;
  });
}

describe("wire fixtures", () => {
  test("every fixture is accounted for", () => {
    const unaccounted: Array<{ file: string; discriminator: string }> = [];
    for (const path of fixturePaths()) {
      const payload: Record<string, unknown> = JSON.parse(
        readFileSync(path, "utf-8"),
      );
      const d = discriminator(payload);
      if (!(d in IMPLEMENTED) && !NOT_YET_IMPLEMENTED.has(d)) {
        unaccounted.push({ file: path.split("/").pop()!, discriminator: d });
      }
    }
    expect(
      unaccounted,
      "fixtures with unaccounted discriminators (add to either " +
        "`IMPLEMENTED` or `NOT_YET_IMPLEMENTED`)",
    ).toEqual([]);
  });

  test("implemented and deferred sets are disjoint", () => {
    const overlap = Object.keys(IMPLEMENTED).filter((k) =>
      NOT_YET_IMPLEMENTED.has(k),
    );
    expect(overlap, "discriminators appear in both sets").toEqual([]);
  });

  for (const path of implementedFixturePaths()) {
    const filename = path.split("/").pop()!;
    test(filename.replace(".json", ""), () => {
      const payload: Record<string, unknown> = JSON.parse(
        readFileSync(path, "utf-8"),
      );
      const schema = IMPLEMENTED[discriminator(payload)];
      const parsed = schema.parse(payload);
      expect(parsed).toEqual(payload);
    });
  }
});
