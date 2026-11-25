import { expect, test } from "@playwright/test";
import { openFillablePeriod } from "./helpers.js";

// /my-kpi deep links (bug 3 of 4): visiting
// /my-kpi?year=2026&period=December must show the December board, the
// way /dashboard honours the same params — not silently fall back to
// the current month.
//
// /my-kpi is dept-admin-only (AppRoutes ProtectedRoute roles
// ["dept_admin"], and /api/my-kpi 403s every other role), so the spec
// runs as the seeded dept admin (manager@local.test) from the saved
// auth state, like workflow.spec.js — no loginAs() calls.
//
// December 2026 is a future month at the time of writing (2026-11-25),
// so the board has no submissions and the period shows in the
// PeriodSelector month dropdown, the empty state ("Nothing found for
// December 2026.") and the future-month banner. The dropdown value is
// the assertion that holds with or without submissions; the text
// assertions pin the rendered board to December, not November.

test("my-kpi honours ?year=&period=: December board renders for December params", async ({
  browser,
}) => {
  const page = await (
    await browser.newContext({ storageState: "e2e/.auth/dept-admin.json" })
  ).newPage();
  try {
    // The fetch itself must carry the params — a board that merely
    // re-labels itself while fetching the default period would still
    // show November data.
    const decemberFetch = page.waitForResponse(
      (resp) =>
        resp.url().includes("/api/my-kpi") &&
        resp.url().includes("year=2026") &&
        resp.url().includes("period=December"),
    );
    await page.goto("/my-kpi?year=2026&period=December");
    await decemberFetch;

    const monthSelect = page
      .locator("form", {
        has: page.getByRole("button", { name: "Apply", exact: true }),
      })
      .locator("select")
      .nth(1);
    await expect(monthSelect).toHaveValue("December");
    await expect(page.getByText("December 2026").first()).toBeVisible();
    await expect(page.getByText("November 2026")).toHaveCount(0);
  } finally {
    await page.context().close();
  }
});

test("my-kpi without params still shows the currently open period", async ({
  browser,
}) => {
  const { year, period } = openFillablePeriod();
  const page = await (
    await browser.newContext({ storageState: "e2e/.auth/dept-admin.json" })
  ).newPage();
  try {
    await page.goto("/my-kpi");
    const monthSelect = page
      .locator("form", {
        has: page.getByRole("button", { name: "Apply", exact: true }),
      })
      .locator("select")
      .nth(1);
    await expect(monthSelect).toHaveValue(period);
    await expect(page.getByText(`${period} ${year}`).first()).toBeVisible();
  } finally {
    await page.context().close();
  }
});
