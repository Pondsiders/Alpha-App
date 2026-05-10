import { defineConfig, devices } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "https://alpha.tail8bd569.ts.net:5443";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["**/reset-db.ts", "**/fixtures.ts"],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    ignoreHTTPSErrors: true,
  },
  projects: [
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
