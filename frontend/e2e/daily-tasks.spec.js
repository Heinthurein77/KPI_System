import { expect, test } from "@playwright/test";
import { accounts, loginAs, uniqueWork } from "./helpers.js";

// The diary form's first textarea is Work Done (date/plan/status come
// first as inputs/select). Scoped to the form so later textareas
// (difficulties, support, reflection) never match by accident.
const workDone = (page) => page.locator("form textarea").first();

test("employee diary: submit confirm shows the entry, confirm saves it", async ({ page }) => {
  const text = uniqueWork("E2E diary entry");
  await loginAs(page, accounts.employee);
  await page.goto("/daily-tasks");
  await expect(page.getByRole("button", { name: /Fill Form/ })).toBeVisible();

  await workDone(page).fill(text);
  await page.getByRole("button", { name: "Entry ထည့်ရန်" }).click();

  // Submit confirm dialog: shows what is about to be saved.
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Diary entry ထည့်မလား?");
  await expect(dialog).toContainText(text);
  await dialog.getByRole("button", { name: "သေချာတယ်၊ သိမ်းမယ်" }).click();

  await expect(page.getByText("Diary entry သိမ်းပြီးပါပြီ")).toBeVisible();

  // The entry is visible in history.
  await page.getByRole("button", { name: /Filled Entries/ }).click();
  await expect(page.getByText(text)).toBeVisible();

  // Delete confirm flow cleans up, so reruns never collide.
  const entry = page.locator("article", { hasText: text });
  await entry.getByRole("button", { name: "ဖျက်ရန်" }).click();
  const delDialog = page.getByRole("alertdialog");
  await expect(delDialog).toContainText("ဒီ entry ကို ဖျက်မလား?");
  await delDialog.getByRole("button", { name: "ဖျက်မယ်" }).click();
  await expect(page.getByText(text)).toHaveCount(0);
});

test("employee diary: cancelling the submit confirm creates nothing", async ({ page }) => {
  const text = uniqueWork("E2E cancelled entry");
  await loginAs(page, accounts.employee);
  await page.goto("/daily-tasks");
  await workDone(page).fill(text);
  await page.getByRole("button", { name: "Entry ထည့်ရန်" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);

  await page.getByRole("button", { name: /Filled Entries/ }).click();
  await expect(page.getByText(text)).toHaveCount(0);
});

test("dept admin sees mine and team dashboards", async ({ page }) => {
  await loginAs(page, accounts.deptAdmin);
  await page.goto("/daily-tasks");
  await expect(page.getByRole("button", { name: /ကိုယ့် Diary/ })).toBeVisible();
  await page.getByRole("button", { name: /ဌာန Diary/ }).click();
  await expect(page.getByText("ဌာန Diary Entries")).toBeVisible();
});

test("tenant admin sees team view only, never a fill form", async ({ page }) => {
  await loginAs(page, accounts.tenantAdmin);
  await page.goto("/daily-tasks");
  await expect(page.getByRole("button", { name: /ကိုယ့် Diary/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Entry ထည့်ရန်" })).toHaveCount(0);
  await expect(page.getByText("ဌာန Diary Entries")).toBeVisible();
});
