import { expect, test } from "@playwright/test";

// Smoke: an unauthenticated visit lands on the login form (the app shell mounts and
// the auth guard redirects). A full signed-in navigation flow needs the dev API with
// the seeded admin — gate that behind a real backend or a page.route("/api/**") mock.
test("unauthenticated visit shows the login form", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('input[type="password"]')).toBeVisible();
});
