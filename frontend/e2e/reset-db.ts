/**
 * Reset the dev database's `app` schema.
 *
 * Drops `app` cascade and the alembic_version table, then runs Alembic
 * to recreate them. The backend's connection pool stays alive; only
 * the schema contents change.
 *
 * Reads DATABASE_URL from the process env, or from `backend/.env` if
 * not set. BACKEND_DIR (default `../backend`) is where alembic runs.
 */

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Client } from "pg";

const BACKEND_DIR = process.env.BACKEND_DIR ?? "../backend";

function readDatabaseUrl(): string {
  if (process.env.DATABASE_URL) {
    return process.env.DATABASE_URL;
  }
  const envPath = resolve(BACKEND_DIR, ".env");
  const contents = readFileSync(envPath, "utf-8");
  for (const line of contents.split("\n")) {
    const m = line.match(/^DATABASE_URL\s*=\s*(.+)$/);
    if (m) {
      return m[1].replace(/^["']|["']$/g, "");
    }
  }
  throw new Error(
    `DATABASE_URL not in env and not in ${envPath}; cannot reset dev DB`,
  );
}

export async function resetDevDatabase(): Promise<void> {
  const databaseUrl = readDatabaseUrl();

  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    await client.query("DROP SCHEMA IF EXISTS app CASCADE");
    await client.query("DROP TABLE IF EXISTS alembic_version");
  } finally {
    await client.end();
  }

  execSync("uv run alembic upgrade head", {
    cwd: BACKEND_DIR,
    stdio: "inherit",
  });
}
