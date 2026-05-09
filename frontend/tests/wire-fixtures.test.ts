/**
 * Round-trip every wire-payload fixture through its Zod schema.
 *
 * Mirrors `backend/tests/test_wire_fixtures.py`. Both halves of the wire
 * read the same JSON files at `fixtures/wire-payloads/`; both must
 * round-trip cleanly. Adding a new shape: drop a `<name>.json` file and
 * register the schema below.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import { z } from "zod/v4";
import {
  AppStateEvent,
  ChatCreatedEvent,
  CreateChatCommand,
  ErrorEvent,
  InterruptCommand,
  JoinChatCommand,
  SendCommand,
} from "../src/lib/protocol";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = resolve(__dirname, "..", "..", "fixtures", "wire-payloads");

// Map a fixture's discriminator value to its Zod schema. Adding a new
// wire shape means a Zod schema in `src/lib/protocol.ts` and one entry here.
const EVENT_SCHEMAS: Record<string, z.ZodTypeAny> = {
  error: ErrorEvent,
  "chat-created": ChatCreatedEvent,
  "app-state": AppStateEvent,
};

const COMMAND_SCHEMAS: Record<string, z.ZodTypeAny> = {
  "create-chat": CreateChatCommand,
  "join-chat": JoinChatCommand,
  send: SendCommand,
  interrupt: InterruptCommand,
};

function fixturePaths(): string[] {
  return readdirSync(FIXTURES_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => join(FIXTURES_DIR, name))
    .sort();
}

describe("wire fixtures", () => {
  for (const path of fixturePaths()) {
    test(path.split("/").pop()!.replace(".json", ""), () => {
      const payload: Record<string, unknown> = JSON.parse(
        readFileSync(path, "utf-8"),
      );

      let schema: z.ZodTypeAny;
      if ("event" in payload && typeof payload.event === "string") {
        schema = EVENT_SCHEMAS[payload.event];
        expect(schema, `no Zod schema registered for event ${payload.event}`).toBeDefined();
      } else if ("command" in payload && typeof payload.command === "string") {
        schema = COMMAND_SCHEMAS[payload.command];
        expect(schema, `no Zod schema registered for command ${payload.command}`).toBeDefined();
      } else {
        throw new Error(`fixture has neither 'event' nor 'command': ${path}`);
      }

      const parsed = schema.parse(payload);
      expect(parsed).toEqual(payload);
    });
  }
});
