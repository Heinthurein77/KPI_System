import { expect, test } from "@playwright/test";
import { accounts, loginAs } from "./helpers.js";

test("valid login lands on the dashboard", async ({ page }) => {
  await loginAs(page, accounts.employee);
  await expect(page).toHaveURL(/\/dashboard/);
  // The login form is gone — we are inside the app shell.
  await expect(page.locator("#email")).toHaveCount(0);
});

test("wrong password shows an error and stays on login", async ({ page }) => {
  await page.goto("/login");
  await page.fill("#orgSlug", accounts.slug);
  await page.fill("#email", accounts.employee);
  await page.fill("#password", "WrongPass123!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/login/);
  // An error banner appears (exact wording comes from the backend).
  await expect(page.locator("form").locator("..")).toContainText(/invalid|incorrect|failed/i);
});

test("logout returns to the login page", async ({ page }) => {
  await loginAs(page, accounts.employee);
  await page.getByTitle("Sign out").click();
  await expect(page).toHaveURL(/\/login/);
});
