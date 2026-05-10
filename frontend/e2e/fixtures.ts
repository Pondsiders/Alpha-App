/**
 * Shared fixtures for the e2e suite.
 *
 * `test` extends Playwright's base test with a beforeEach that resets
 * the dev database. Use this `test` import instead of the one from
 * `@playwright/test` so every spec gets a clean schema before each
 * test case.
 */

import { test as base } from "@playwright/test";
import { resetDevDatabase } from "./reset-db";

export const test = base.extend({
  page: async ({ page }, use) => {
    await resetDevDatabase();
    await use(page);
  },
});

export { expect } from "@playwright/test";
