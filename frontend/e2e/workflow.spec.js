import { expect, test } from "@playwright/test";
import { accounts, openFillablePeriod } from "./helpers.js";

// Core KPI workflow (T-M1): the actual submit → dept-approve →
// final-approve flow, which previously had zero browser coverage.
//
// The seeded employee (aung@local.test) has one department KPI,
// "E2E Workflow KPI" (target 100, weight 100), created by
// scripts/seed_dev.py. The spec resets that template first (tenant
// admin force-delete, which also deletes its submissions, then
// re-creates it), so reruns start from a clean DRAFT even if a
// previous run got all the way to Approved. The template is deleted
// again in cleanup, so nothing from the run lingers.
//
// The period under test is the currently-open one (current month from
// day 8, else the previous month in its grace window) — the same rule
// the backend enforces — so the spec works on any day of the month.
//
// Each role acts in its own browser context loaded from the saved
// auth state (auth.setup.js) — no loginAs() calls at all. Besides
// speed, that keeps the suite under the login endpoint's rate limit
// (10 attempts/minute), which the suite exceeds when every spec logs
// in from scratch.

const API_BASE = process.env.E2E_API_BASE_URL || "http://localhost:8000";
const METRIC = "E2E Workflow KPI";
const DEPARTMENT = "General";
const tenantHeaders = { "X-Tenant-Slug": accounts.slug };

async function resetTemplate(page) {
  const listed = await page.request.get(`${API_BASE}/api/admin/templates`, {
    headers: tenantHeaders,
  });
  expect(listed.ok()).toBeTruthy();
  const templates = await listed.json();
  const mine = (templates.kpi_templates || templates).find(
    (t) => t.metric_name === METRIC,
  );
  if (mine) {
    const deleted = await page.request.delete(
      `${API_BASE}/api/admin/templates/${mine.id}`,
      { headers: tenantHeaders },
    );
    expect(deleted.ok()).toBeTruthy();
  }

  const depts = await page.request.get(`${API_BASE}/api/admin/departments`, {
    headers: tenantHeaders,
  });
  expect(depts.ok()).toBeTruthy();
  const deptList = await depts.json();
  const dept = (deptList.departments || deptList).find((d) => d.name === DEPARTMENT);
  expect(dept, `seed department ${DEPARTMENT} must exist`).toBeTruthy();

  const created = await page.request.post(`${API_BASE}/api/admin/templates`, {
    headers: tenantHeaders,
    data: {
      metric_name: METRIC,
      target: 100,
      weight: 100,
      department_id: dept.id,
    },
  });
  expect(created.ok()).toBeTruthy();
}

async function deleteTemplate(page) {
  const listed = await page.request.get(`${API_BASE}/api/admin/templates`, {
    headers: tenantHeaders,
  });
  if (!listed.ok()) return;
  const templates = await listed.json();
  const mine = (templates.kpi_templates || templates).find(
    (t) => t.metric_name === METRIC,
  );
  if (mine) {
    await page.request.delete(`${API_BASE}/api/admin/templates/${mine.id}`, {
      headers: tenantHeaders,
    });
  }
}

/** The review board groups cards into collapsible sections ("Needs your
 * review", "Waiting on others", ...) and, inside each, per-employee
 * groups. After an approval the card moves to another section, which
 * starts collapsed — expand it first or the card is not in the DOM at
 * all. The expect() waits for the board to reload into its
 * post-approval state first: expanding before the reload would be
 * undone by it. */
async function expandBoardSection(page, namePattern) {
  const section = page.getByRole("button", { name: namePattern });
  await expect(section.first()).toBeVisible();
  if ((await section.first().getAttribute("aria-expanded")) !== "true") {
    await section.first().click();
  }
}

async function submissionStatus(page, year, period) {
  const resp = await page.request.get(
    `${API_BASE}/api/dashboard?year=${year}&period=${period}`,
    { headers: tenantHeaders },
  );
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  const mine = (body.submissions || []).find(
    (s) => s.kpi_template?.metric_name === METRIC,
  );
  expect(mine, `submission for ${METRIC} must exist`).toBeTruthy();
  return mine;
}

test("core workflow: employee submits, dept admin approves, tenant admin finally approves", async ({
  browser,
}) => {
  // Three dashboard loads plus the setup API calls: the default 30s
  // test timeout is too tight for the full chain.
  test.setTimeout(120 * 1000);
  const { year, period } = openFillablePeriod();
  const boardUrl = `/dashboard?year=${year}&period=${period}`;

  const tenantAdminPage = await (
    await browser.newContext({ storageState: "e2e/.auth/tenant-admin.json" })
  ).newPage();
  const employeePage = await (
    await browser.newContext({ storageState: "e2e/.auth/employee.json" })
  ).newPage();
  const deptAdminPage = await (
    await browser.newContext({ storageState: "e2e/.auth/dept-admin.json" })
  ).newPage();

  try {
    // Setup as tenant admin: reset the seeded template to a clean DRAFT.
    await resetTemplate(tenantAdminPage);

    // 1. Employee self-scores and submits for department approval.
    const page = employeePage;
    await page.goto(boardUrl);
    await expect(
      page.getByRole("heading", { name: METRIC }),
    ).toBeVisible();
    const draft = await submissionStatus(page, year, period);
    expect(draft.status).toBe("draft");
    await page.locator(`#self-score-${draft.id}`).fill("80");
    await page
      .getByRole("button", { name: "Submit for Department Approval", exact: true })
      .click();
    await expect(
      page.getByRole("alertdialog").getByText("Submit for department approval?"),
    ).toBeVisible();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Submit", exact: true })
      .click();
    await expect(
      page.getByText("Submitted for department approval."),
    ).toBeVisible();
    const submitted = await submissionStatus(page, year, period);
    expect(submitted.status).toBe("pending_dept_approval");
    expect(submitted.self_score).toBe(80);

    // 2. Dept admin approves (dept score defaults to the self score) and
    // the submission moves to pending final approval.
    const deptPage = deptAdminPage;
    await deptPage.goto(boardUrl);
    const deptCard = deptPage.locator("div.rounded-2xl", { hasText: METRIC });
    await expect(deptCard).toBeVisible();
    await deptCard
      .getByRole("button", { name: "Approve", exact: true })
      .click();
    await expect(
      deptPage.getByRole("alertdialog").getByText("Approve this submission?"),
    ).toBeVisible();
    await deptPage
      .getByRole("alertdialog")
      .getByRole("button", { name: "Approve", exact: true })
      .click();
    await expandBoardSection(deptPage, /Waiting on others 1 item/);
    await expandBoardSection(deptPage, /Aung.*1 metric/);
    await expect(deptCard.getByText("Pending Final Approval")).toBeVisible();
    const deptApproved = await submissionStatus(deptPage, year, period);
    expect(deptApproved.status).toBe("pending_final_approval");

    // 3. Tenant admin gives final approval; the card shows Approved.
    const adminPage = tenantAdminPage;
    await adminPage.goto(boardUrl);
    const finalCard = adminPage.locator("div.rounded-2xl", { hasText: METRIC });
    await expect(finalCard).toBeVisible();
    await finalCard
      .getByRole("button", { name: "Approve", exact: true })
      .click();
    await expect(
      adminPage.getByRole("alertdialog").getByText("Approve this submission?"),
    ).toBeVisible();
    await adminPage
      .getByRole("alertdialog")
      .getByRole("button", { name: "Approve", exact: true })
      .click();
    await expandBoardSection(adminPage, /Approved 1 item/);
    await expandBoardSection(adminPage, /Aung.*1 metric/);
    await expect(finalCard.getByText("Approved")).toBeVisible();
    const finalApproved = await submissionStatus(adminPage, year, period);
    expect(finalApproved.status).toBe("approved");
    expect(finalApproved.final_score).toBe(80);
  } finally {
    // Cleanup: delete the template (also deletes its submissions), so
    // reruns and manual e2e runs start clean. The tenant-admin page
    // is still authenticated, so no re-login is needed.
    await deleteTemplate(tenantAdminPage);
    await tenantAdminPage.context().close();
    await employeePage.context().close();
    await deptAdminPage.context().close();
  }
});
