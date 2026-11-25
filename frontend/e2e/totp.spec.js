import { createHmac } from "node:crypto";
import { expect, test } from "@playwright/test";
import { accounts, apiHeaders, loginAs } from "./helpers.js";

// TOTP enrolment UI (bug 1): the seeded tenant admin enrols through
// /account/security, verifies a 6-digit code, sees the 8 recovery
// codes once, and then signs in through the login second step.
//
// The spec drives the real UI end to end — including computing the
// 6-digit code from the displayed secret with a small RFC 6238
// implementation below (no extra dependency; the secret is base32,
// SHA-1, 30-second steps, 6 digits, same as pyotp on the server).
// The server accepts ±1 step (valid_window=1), so a code computed
// just before filling is valid.
//
// Cleanup matters: enabling TOTP on the seed tenant admin would break
// the next suite run — auth.setup.js logs that admin in with
// loginAs(), which waits for /dashboard and would instead land on the
// TOTP second step. Every test therefore disables TOTP again in a
// `finally` (POST /api/auth/totp/disable with the seed password).
// If a run is killed before cleanup, re-seed the dev database
// (scripts/seed_dev.py) to reset the seed admin before rerunning.

const API_BASE = process.env.E2E_API_BASE_URL || "http://localhost:8000";

/** Base32 decode (RFC 4648, no padding assumed) for pyotp secrets. */
function base32Decode(input) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = [...input.toUpperCase().replace(/=+$/, "")]
    .map((c) => alphabet.indexOf(c).toString(2).padStart(5, "0"))
    .join("");
  const bytes = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  }
  return Buffer.from(bytes);
}

/** Current 6-digit TOTP code for a base32 secret. */
function totpCode(secret, when = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(when / 1000 / 30)));
  const hmac = createHmac("sha1", base32Decode(secret)).update(counter).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const value = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(value).padStart(6, "0");
}

/** Disable TOTP for the signed-in admin via the API (cleanup). Safe
 * to call even when TOTP is not enabled — the endpoint just clears
 * the stored secret. Best-effort on purpose: a cleanup failure must
 * not mask the test body's own failure, but a TOTP enrolment left
 * behind breaks the next suite run, so a still-enabled status after
 * the disable is logged loudly. */
async function disableTotpViaApi(page) {
  try {
    const headers = await apiHeaders(page);
    const resp = await page.request.post(`${API_BASE}/api/auth/totp/disable`, {
      headers,
      data: { password: accounts.password },
    });
    if (resp.status() !== 204) {
      console.warn(`TOTP cleanup: disable returned ${resp.status()}`);
      return;
    }
    const status = await page.request.get(`${API_BASE}/api/auth/totp/status`, {
      headers,
    });
    if (status.ok() && (await status.json()).enabled) {
      console.warn("TOTP cleanup FAILED: TOTP is still enabled — re-seed the dev database before rerunning the suite.");
    }
  } catch (err) {
    console.warn(`TOTP cleanup failed: ${err.message}`);
  }
}

/** Start enrolment through the UI and return the displayed secret.
 * Asserts the QR code canvas and the manual secret are both shown. */
async function enrolViaUi(page) {
  await page.goto("/account/security");
  await page.getByRole("button", { name: "Set up two-factor authentication" }).click();
  const qr = page.getByTestId("totp-qr");
  await expect(qr).toBeVisible();
  await expect(qr.locator("canvas")).toBeVisible();
  const secret = page.getByTestId("totp-secret");
  await expect(secret).toBeVisible();
  const secretText = (await secret.textContent())?.trim();
  expect(secretText, "manual secret must be shown").toMatch(/^[A-Z2-7]{16,}$/);
  return secretText;
}

/** Fill the 6-digit code from the secret and submit the enrolment
 * verification form. */
async function verifyEnrolment(page, secret) {
  await page.fill("#totp-enroll-code", totpCode(secret));
  await page.getByRole("button", { name: "Verify and enable" }).click();
}

test("tenant admin enrols via the Security page: QR + secret, verifies a code, sees 8 recovery codes once, then enabled state", async ({
  page,
}) => {
  await loginAs(page, accounts.tenantAdmin);
  try {
    const secret = await enrolViaUi(page);
    await verifyEnrolment(page, secret);

    // Recovery codes: exactly 8, format xxxx-xxxx, shown once.
    const codesPanel = page.getByTestId("totp-recovery-codes");
    await expect(codesPanel).toBeVisible();
    const codes = await codesPanel.locator("li").allTextContents();
    expect(codes).toHaveLength(8);
    for (const code of codes) {
      expect(code.trim()).toMatch(/^[a-z2-9]{4}-[a-z2-9]{4}$/);
    }

    // Acknowledge them: the enabled state appears, and a reload must
    // NOT bring the codes back — they are shown once only.
    await page.getByRole("button", { name: "I’ve saved my recovery codes" }).click();
    await expect(page.getByTestId("totp-enabled")).toBeVisible();
    await expect(page.getByText("Recovery codes remaining: 8")).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("totp-enabled")).toBeVisible();
    await expect(page.getByTestId("totp-recovery-codes")).toHaveCount(0);
  } finally {
    await disableTotpViaApi(page);
  }
});

test("employee sees that two-factor authentication is not available for their role", async ({
  page,
}) => {
  await loginAs(page, accounts.employee);
  await page.goto("/account/security");
  await expect(page.getByTestId("totp-not-available")).toBeVisible();
  await expect(page.getByTestId("totp-not-available")).toContainText(
    "available to super admins and tenant admins only",
  );
  // The nav link is hidden for ineligible roles too.
  await expect(
    page.getByRole("link", { name: "Two-factor authentication" }),
  ).toHaveCount(0);
});

test("enrolled tenant admin signs in through the TOTP second step", async ({
  page,
}) => {
  test.setTimeout(120 * 1000);
  await loginAs(page, accounts.tenantAdmin);
  try {
    const secret = await enrolViaUi(page);
    await verifyEnrolment(page, secret);
    await expect(page.getByTestId("totp-recovery-codes")).toBeVisible();
    await page.getByRole("button", { name: "I’ve saved my recovery codes" }).click();
    await expect(page.getByTestId("totp-enabled")).toBeVisible();

    // Sign out, then sign in again: the password step must now ask
    // for the second step instead of landing on the dashboard.
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForURL(/\/login/);
    await page.fill("#orgSlug", accounts.slug);
    await page.fill("#email", accounts.tenantAdmin);
    await page.fill("#password", accounts.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator("#totpCode")).toBeVisible();
    await expect(
      page.getByText("Your account uses two-factor authentication."),
    ).toBeVisible();

    await page.fill("#totpCode", totpCode(secret));
    await page.getByRole("button", { name: "Verify code" }).click();
    await page.waitForURL(/\/dashboard/, { timeout: 15 * 1000 });
  } finally {
    await disableTotpViaApi(page);
  }
});
