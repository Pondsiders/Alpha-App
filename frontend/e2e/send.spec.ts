import { expect, test } from "./fixtures";

test.describe("send", () => {
  test("user sends a message and gets an assistant response", async ({
    page,
  }) => {
    await page.goto("/");

    // Create a chat.
    const newChatButton = page.getByRole("button", { name: "New Chat" });
    await expect(newChatButton).toBeVisible();
    await newChatButton.click();

    // Wait for the new chat to appear in the sidebar — chat items render
    // their creation time as a label matching "H:MM AM" or "H:MM PM".
    const chatItemPattern = /^\d{1,2}:\d{2}\s+(AM|PM)$/;
    await expect(page.getByRole("button", { name: chatItemPattern })).toHaveCount(1);

    // No assistant message yet — the chat is empty.
    const assistantMessages = page.locator('[data-role="assistant"]');
    await expect(assistantMessages).toHaveCount(0);

    // Type and send.
    await page.getByLabel("Message input").fill("Hello, Alpha.");
    await page.getByLabel("Send message").click();

    // The user message we sent appears.
    const userMessages = page.locator('[data-role="user"]');
    await expect(userMessages).toHaveCount(1);

    // An assistant message appears in response. We assert presence, not
    // content — when MockAnthropic is replaced by a real model, the test
    // survives because the structural shape ("a response arrived") is
    // what we care about, not what the response said.
    await expect(assistantMessages).toHaveCount(1);
  });
});
