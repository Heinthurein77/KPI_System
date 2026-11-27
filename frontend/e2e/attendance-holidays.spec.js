import { expect, test } from "@playwright/test";

// Holidays are managed one month at a time in Attendance settings:
// the admin picks a month, works on that month's holidays, and at month
// end moves on to the next month. Past months stay viewable and nothing
// is deleted by switching months. The WiFi IP whitelist is global and
// must not move when the holiday month changes.
//
// Runs as the seeded tenant admin (admin@local.test) from the saved
// auth state. December 2026 is a future month and October 2026 a past
// month at the time of writing (2026-11-27), so both start empty in the
// scratch e2e database; holiday names carry a timestamp so reruns never
// collide, and every test removes what it added.

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

async function newAdminPage(browser) {
  const page = await (
    await browser.newContext({ storageState: "e2e/.auth/tenant-admin.json" })
  ).newPage();
  await page.goto("/attendance");
  await expect(page.getByLabel("Holiday month")).toBeVisible();
  return page;
}

async function selectHolidayMonth(page, year, month) {
  await page.getByLabel("Holiday month").selectOption(String(month));
  await page.getByLabel("Holiday year").selectOption(String(year));
}

async function saveSettings(page) {
  await page.getByRole("button", { name: "Save settings" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Settings saved")).toBeVisible();
}

// Remove every row in the currently shown month whose name matches,
// working backwards so the indices never shift under us.
async function removeHolidayByName(page, name) {
  const editor = page.locator("section", { hasText: "Attendance settings" });
  const names = editor.locator('input[placeholder*="Holiday name"]');
  for (let i = (await names.count()) - 1; i >= 0; i--) {
    if ((await names.nth(i).inputValue()) === name) {
      await editor.getByLabel(`Remove holiday ${i + 1}`).click();
    }
  }
}

test("holiday month: December holiday is hidden in November, visible again in December", async ({
  browser,
}) => {
  const stamp = Date.now();
  const name = `E2E Xmas ${stamp}`;
  const page = await newAdminPage(browser);
  try {
    await selectHolidayMonth(page, 2026, 12);
    await expect(page.getByText("Holidays in December 2026 (0 dates)")).toBeVisible();

    // A new row starts on the 1st of the selected month.
    await page.getByRole("button", { name: "+ Add holiday" }).click();
    await expect(page.getByLabel("Holiday 1 date")).toHaveValue("2026-12-01");
    await page.getByLabel("Holiday 1 date").fill("2026-12-25");
    await page.getByLabel("Holiday 1 name").fill(name);
    await expect(page.getByText("Holidays in December 2026 (1 date)")).toBeVisible();
    await saveSettings(page);

    // November shows none of it…
    await selectHolidayMonth(page, 2026, 11);
    await expect(page.getByText("No holidays in November 2026 yet.")).toBeVisible();
    await expect(page.getByText(name)).toHaveCount(0);

    // …and switching back to December shows it again (still saved).
    await selectHolidayMonth(page, 2026, 12);
    await expect(page.getByText("Holidays in December 2026 (1 date)")).toBeVisible();
    await expect(page.getByLabel("Holiday 1 name")).toHaveValue(name);
  } finally {
    try {
      await selectHolidayMonth(page, 2026, 12);
      await removeHolidayByName(page, name);
      await saveSettings(page);
    } catch {
      // Best-effort cleanup must never fail the test.
    }
    await page.context().close();
  }
});

test("holiday month: a past month's holidays stay viewable", async ({
  browser,
}) => {
  const stamp = Date.now();
  const name = `E2E Past ${stamp}`;
  const page = await newAdminPage(browser);
  try {
    await selectHolidayMonth(page, 2026, 10);
    await expect(page.getByText("Holidays in October 2026 (0 dates)")).toBeVisible();
    await page.getByRole("button", { name: "+ Add holiday" }).click();
    await page.getByLabel("Holiday 1 date").fill("2026-10-15");
    await page.getByLabel("Holiday 1 name").fill(name);
    await saveSettings(page);

    // The current month (November) does not show it…
    await selectHolidayMonth(page, 2026, 11);
    await expect(page.getByText(name)).toHaveCount(0);
    // …but October still does — history is preserved.
    await selectHolidayMonth(page, 2026, 10);
    await expect(page.getByLabel("Holiday 1 name")).toHaveValue(name);
  } finally {
    try {
      await selectHolidayMonth(page, 2026, 10);
      await removeHolidayByName(page, name);
      await saveSettings(page);
    } catch {
      // Best-effort cleanup must never fail the test.
    }
    await page.context().close();
  }
});

test("holiday range: a range inside the month works, a range spilling out is rejected", async ({
  browser,
}) => {
  const page = await newAdminPage(browser);
  try {
    await selectHolidayMonth(page, 2026, 12);
    await expect(page.getByText("Holidays in December 2026 (0 dates)")).toBeVisible();

    // Spills into January — rejected, nothing added.
    await page.getByLabel("Range start date").fill("2026-12-28");
    await page.getByLabel("Range end date").fill("2027-01-02");
    await page.getByRole("button", { name: "Add range" }).click();
    await expect(page.getByText(/leaves December 2026/)).toBeVisible();
    await expect(page.getByText("Holidays in December 2026 (0 dates)")).toBeVisible();

    // Inside December — three days added.
    await page.getByLabel("Range start date").fill("2026-12-28");
    await page.getByLabel("Range end date").fill("2026-12-30");
    await page.getByRole("button", { name: "Add range" }).click();
    await expect(page.getByText("Holidays in December 2026 (3 dates)")).toBeVisible();
    await expect(page.getByLabel("Holiday 1 date")).toHaveValue("2026-12-28");
    await expect(page.getByLabel("Holiday 3 date")).toHaveValue("2026-12-30");
    await saveSettings(page);

    // Still there after reload of the month.
    await selectHolidayMonth(page, 2026, 11);
    await selectHolidayMonth(page, 2026, 12);
    await expect(page.getByText("Holidays in December 2026 (3 dates)")).toBeVisible();
  } finally {
    try {
      await selectHolidayMonth(page, 2026, 12);
      for (let i = 0; i < 3; i++) {
        await page.getByLabel("Remove holiday 1").click();
      }
      await saveSettings(page);
    } catch {
      // Best-effort cleanup must never fail the test.
    }
    await page.context().close();
  }
});

test("holiday month switching leaves the office WiFi IPs untouched", async ({
  browser,
}) => {
  const page = await newAdminPage(browser);
  try {
    const ips = page.getByLabel(/Office WiFi public IPs/);
    const before = await ips.inputValue();
    await selectHolidayMonth(page, 2026, 12);
    await selectHolidayMonth(page, 2026, 11);
    await selectHolidayMonth(page, 2026, 10);
    await expect(ips).toHaveValue(before);
  } finally {
    await page.context().close();
  }
});
