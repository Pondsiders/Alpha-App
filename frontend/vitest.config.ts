/**
 * Vitest configuration.
 *
 * Vitest's default discovery picks up both *.test.ts and *.spec.ts anywhere
 * under the project, which collides with Playwright's e2e suite (also
 * .spec.ts). The two runners can't share files — Playwright's `test()` /
 * `describe()` fail to load outside a Playwright runtime.
 *
 * House conventions:
 *   - Vitest tests live in `tests/` or `src/`, named `*.test.ts(x)`.
 *   - Playwright tests live in `e2e/`, named `*.spec.ts`.
 *   - Each runner stays in its own directory; they do not overlap.
 */

import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    include: ["tests/**/*.test.{ts,tsx}", "src/**/*.test.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
});
