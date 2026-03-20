import { expect, test } from "@playwright/test";

const username = String(process.env.PLAYWRIGHT_USERNAME || "").trim();
const password = String(process.env.PLAYWRIGHT_PASSWORD || "").trim();
const templateName = String(process.env.PLAYWRIGHT_TEMPLATE_NAME || "Windows Lab").trim();
const connectTimeoutSeconds = Number.parseInt(process.env.PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS || "900", 10);
const connectTimeoutMs = Math.max(120, Number.isFinite(connectTimeoutSeconds) ? connectTimeoutSeconds : 900) * 1000;

test("guacamole rdp launch + connect smoke", async ({ page, context }) => {
  test.skip(!username || !password, "PLAYWRIGHT_USERNAME and PLAYWRIGHT_PASSWORD must be set.");

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Available Virtual Labs" })).toBeVisible({ timeout: 60_000 });

  const templateTile = page
    .locator(".template-tile")
    .filter({ has: page.getByRole("heading", { name: templateName, exact: true }) })
    .first();
  await expect(templateTile).toBeVisible({ timeout: 60_000 });
  await templateTile.getByRole("button", { name: "Start Lab" }).click();

  const vmTile = page
    .locator(".pod-tile")
    .filter({ has: page.getByRole("heading", { name: templateName, exact: true }) })
    .first();
  await expect(vmTile).toBeVisible({ timeout: 120_000 });
  const connectButton = vmTile.getByRole("button", { name: "Connect" });
  await expect(connectButton).toBeEnabled({ timeout: connectTimeoutMs });

  const popupPromise = context.waitForEvent("page");
  await connectButton.click();
  const popup = await popupPromise;
  await popup.waitForLoadState("domcontentloaded");
  await expect(popup).toHaveURL(/\/connect\/.*rdp/i, { timeout: 60_000 });
  await popup.waitForSelector("#display canvas", { timeout: connectTimeoutMs });

  const statusText = (await popup.locator("#status").textContent()) || "";
  expect(statusText.toLowerCase()).not.toContain("disconnected");

  await page.bringToFront();
  const deleteButton = vmTile.getByRole("button", { name: "Delete" });
  if (await deleteButton.isVisible()) {
    await deleteButton.click();
  }
});
