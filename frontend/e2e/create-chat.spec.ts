import { expect, test } from "./fixtures";

test.describe("create-chat", () => {
  test("from an empty database, clicking New Chat produces one chat", async ({
    page,
  }) => {
    await page.goto("/");

    // Sidebar starts empty (the global setup wiped the dev database).
    const newChatButton = page.getByRole("button", { name: "New Chat" });
    await expect(newChatButton).toBeVisible();

    // No chat items yet — chat items render their creation time as a label
    // matching "H:MM AM" or "H:MM PM".
    const chatItemPattern = /^\d{1,2}:\d{2}\s+(AM|PM)$/;
    await expect(page.getByRole("button", { name: chatItemPattern })).toHaveCount(0);

    await newChatButton.click();

    // Exactly one chat appears.
    await expect(page.getByRole("button", { name: chatItemPattern })).toHaveCount(1);
  });
});
