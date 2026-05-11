/**
 * Round-trip every implemented wire-payload fixture through its Zod schema.
 *
 * Mirrors `backend/tests/test_wire_fixtures.py`. Both halves of the wire
 * read the same JSON files at `fixtures/wire-payloads/`; both must
 * round-trip cleanly. The fixture set tracks the spec; the test set
 * tracks what's currently implemented.
 *
 * Fixtures whose discriminator isn't in the registry below are filtered
 * out at collection time; they'll start running automatically the moment
 * their Zod schema is registered.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";
import { z } from "zod/v4";
import {
  AppStateEvent,
  AssistantMessageEvent,
  ChatCreatedEvent,
  ChatStateEvent,
  CreateChatCommand,
  InterruptCommand,
  JoinChatCommand,
  SendCommand,
} from "../src/lib/protocol";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = resolve(__dirname, "..", "..", "fixtures", "wire-payloads");

// Map a fixture's discriminator value to its Zod schema. Fixtures whose
// discriminator isn't in one of these objects are skipped — they describe
// wire shapes the spec defines but the frontend hasn't implemented yet.
const EVENT_SCHEMAS: Record<string, z.ZodTypeAny> = {
  "chat-created": ChatCreatedEvent,
  "app-state": AppStateEvent,
  "chat-state": ChatStateEvent,
  "assistant-message": AssistantMessageEvent,
};

const RESPONSE_SCHEMAS: Record<string, z.ZodTypeAny> = {};

const COMMAND_SCHEMAS: Record<string, z.ZodTypeAny> = {
  "create-chat": CreateChatCommand,
  "join-chat": JoinChatCommand,
  send: SendCommand,
  interrupt: InterruptCommand,
};

interface ImplementedFixture {
  path: string;
  schema: z.ZodTypeAny;
}

function implementedFixtures(): ImplementedFixture[] {
  const fixtures: ImplementedFixture[] = [];
  const names = readdirSync(FIXTURES_DIR)
    .filter((name) => name.endsWith(".json"))
    .sort();
  for (const name of names) {
    const path = join(FIXTURES_DIR, name);
    const payload: Record<string, unknown> = JSON.parse(
      readFileSync(path, "utf-8"),
    );
    let schema: z.ZodTypeAny | undefined;
    if (typeof payload.event === "string") {
      schema = EVENT_SCHEMAS[payload.event];
    } else if (typeof payload.response === "string") {
      schema = RESPONSE_SCHEMAS[payload.response];
    } else if (typeof payload.command === "string") {
      schema = COMMAND_SCHEMAS[payload.command];
    }
    if (schema !== undefined) {
      fixtures.push({ path, schema });
    }
  }
  return fixtures;
}

describe("wire fixtures", () => {
  for (const { path, schema } of implementedFixtures()) {
    test(path.split("/").pop()!.replace(".json", ""), () => {
      const payload: Record<string, unknown> = JSON.parse(
        readFileSync(path, "utf-8"),
      );
      const parsed = schema.parse(payload);
      expect(parsed).toEqual(payload);
    });
  }
});
