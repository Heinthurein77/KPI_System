import { useCallback, useEffect, useState } from "react";
import { QRCodeCanvas } from "qrcode.react";
import AppShell from "../components/layout/AppShell";
import {
  disablePlatformTotp,
  disableTotp,
  enrollPlatformTotp,
  enrollTotp,
  getPlatformTotpStatus,
  getTotpStatus,
  regeneratePlatformTotpRecoveryCodes,
  regenerateTotpRecoveryCodes,
  verifyPlatformTotpEnrolment,
  verifyTotpEnrolment,
} from "../api/auth";
import { getErrorMessage } from "../api/errors";
import { useAuth } from "../context/AuthContext";

// Bug 1 (2026-11-25 security fix-all follow-up): the S-02 TOTP backend
// shipped with enrolment API functions in api/auth.js but no page ever
// called them, so admins could only enrol via direct API calls. This
// page is the enrolment UI: QR code + manual secret, 6-digit verify,
// recovery codes shown once, and disable / regenerate.
//
// Eligibility mirrors the backend (app/core/security.py
// TOTP_ELIGIBLE_ROLES): super admins and tenant admins only. Platform
// super admins use the /api/platform/auth/totp/* variants — the
// tenant-scoped endpoints 401 for them (no tenant slug). Employees and
// dept admins get a not-available note instead of a 403 card.
const ELIGIBLE_ROLES = ["super_admin", "tenant_admin"];

export default function SecurityPage() {
  const { user } = useAuth();
  const isPlatform = user?.role === "super_admin";
  const eligible = ELIGIBLE_ROLES.includes(user?.role);

  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [busy, setBusy] = useState(false);

  // Enrolment in progress: { secret, otpauth_url } from enrollTotp().
  const [enrollData, setEnrollData] = useState(null);
  const [enrollCode, setEnrollCode] = useState("");
  const [secretCopied, setSecretCopied] = useState(false);

  // Recovery codes are shown ONCE — only hashes are stored server-side,
  // so once this state is cleared they can never be shown again.
  const [recoveryCodes, setRecoveryCodes] = useState(null);

  const [disablePassword, setDisablePassword] = useState("");
  const [showDisable, setShowDisable] = useState(false);
  const [regeneratePassword, setRegeneratePassword] = useState("");
  const [showRegenerate, setShowRegenerate] = useState(false);

  const loadStatus = useCallback(async () => {
    const getStatus = isPlatform ? getPlatformTotpStatus : getTotpStatus;
    try {
      setStatus(await getStatus());
    } catch (err) {
      setError(getErrorMessage(err, "Could not load two-factor authentication status."));
    } finally {
      setLoading(false);
    }
  }, [isPlatform]);

  useEffect(() => {
    if (!eligible) {
      setLoading(false);
      return;
    }
    loadStatus();
  }, [eligible, loadStatus]);

  async function startEnrolment() {
    setError(null);
    setInfo(null);
    setBusy(true);
    try {
      const enroll = isPlatform ? enrollPlatformTotp : enrollTotp;
      setEnrollData(await enroll());
    } catch (err) {
      setError(getErrorMessage(err, "Could not start two-factor setup."));
    } finally {
      setBusy(false);
    }
  }

  async function confirmEnrolment(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const verify = isPlatform ? verifyPlatformTotpEnrolment : verifyTotpEnrolment;
      const data = await verify(enrollCode);
      setRecoveryCodes(data.recovery_codes);
      setEnrollData(null);
      setEnrollCode("");
      await loadStatus();
    } catch (err) {
      setError(getErrorMessage(err, "Invalid authenticator code."));
    } finally {
      setBusy(false);
    }
  }

  async function handleDisable(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const disable = isPlatform ? disablePlatformTotp : disableTotp;
      await disable(disablePassword);
      setDisablePassword("");
      setShowDisable(false);
      setRecoveryCodes(null);
      setInfo("Two-factor authentication has been disabled for your account.");
      await loadStatus();
    } catch (err) {
      setError(getErrorMessage(err, "Could not disable two-factor authentication."));
    } finally {
      setBusy(false);
    }
  }

  async function handleRegenerate(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const regenerate = isPlatform
        ? regeneratePlatformTotpRecoveryCodes
        : regenerateTotpRecoveryCodes;
      const data = await regenerate(regeneratePassword);
      setRegeneratePassword("");
      setShowRegenerate(false);
      // The old codes are invalidated immediately; show the new ones once.
      setRecoveryCodes(data.recovery_codes);
      await loadStatus();
    } catch (err) {
      setError(getErrorMessage(err, "Could not generate new recovery codes."));
    } finally {
      setBusy(false);
    }
  }

  async function copySecret() {
    if (!enrollData) return;
    try {
      await navigator.clipboard.writeText(enrollData.secret);
      setSecretCopied(true);
      setTimeout(() => setSecretCopied(false), 2000);
    } catch {
      // Clipboard unavailable — the secret is still shown on screen.
    }
  }

  const inputClass =
    "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition";
  const labelClass =
    "block text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5";

  return (
    <AppShell title="Two-Factor Authentication">
      <div className="max-w-md">
        {error && (
          <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
        )}
        {info && (
          <div className="mb-4 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-700">{info}</div>
        )}

        {loading ? (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 text-sm text-slate-500">
            Loading…
          </div>
        ) : !eligible ? (
          <div
            data-testid="totp-not-available"
            className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6"
          >
            <p className="text-sm text-slate-700">
              Two-factor authentication is currently available to super admins and tenant admins only.
            </p>
            <p className="mt-2 text-sm text-slate-500">
              Your account ({user?.role?.replace(/_/g, " ")}) cannot enrol — ask a tenant admin if you
              need access to this setting.
            </p>
          </div>
        ) : recoveryCodes ? (
          // Shown ONCE after enrolment or regeneration. Dismissing clears
          // them from state; the server stores only hashes, so they can
          // never be shown again.
          <div
            data-testid="totp-recovery-codes"
            className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6"
          >
            <h2 className="text-base font-semibold text-slate-900">Save your recovery codes</h2>
            <p className="mt-2 text-sm text-slate-600">
              These 8 codes are shown <strong>only once</strong>. Each one can be used instead of an
              authenticator code at sign-in, and each works only once. Store them somewhere safe —
              if you lose both your authenticator app and these codes, you will be locked out.
            </p>
            <ul className="mt-4 grid grid-cols-2 gap-2">
              {recoveryCodes.map((code) => (
                <li
                  key={code}
                  className="rounded-lg bg-slate-50 border border-slate-200 px-3 py-2 font-mono text-sm text-slate-800 text-center"
                >
                  {code}
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setRecoveryCodes(null)}
              className="mt-5 inline-flex items-center rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
            >
              I’ve saved my recovery codes
            </button>
          </div>
        ) : enrollData ? (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
            <h2 className="text-base font-semibold text-slate-900">Scan with your authenticator app</h2>
            <p className="mt-2 text-sm text-slate-600">
              Scan this QR code with Google Authenticator, Microsoft Authenticator, or any TOTP app,
              then enter the 6-digit code it shows to finish setup.
            </p>
            <div data-testid="totp-qr" className="mt-4 flex justify-center rounded-lg border border-slate-200 bg-white p-4">
              <QRCodeCanvas value={enrollData.otpauth_url} size={192} />
            </div>
            <p className="mt-4 text-sm text-slate-600">
              Can’t scan the code? Enter this secret manually in your authenticator app instead:
            </p>
            <div className="mt-2 flex items-center gap-2">
              <code
                data-testid="totp-secret"
                className="flex-1 break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-sm text-slate-800"
              >
                {enrollData.secret}
              </code>
              <button
                type="button"
                onClick={copySecret}
                className="shrink-0 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50 transition"
              >
                {secretCopied ? "Copied" : "Copy"}
              </button>
            </div>
            <details className="mt-3">
              <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700">
                Show the setup link instead
              </summary>
              <code className="mt-2 block break-all rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600">
                {enrollData.otpauth_url}
              </code>
            </details>
            <form onSubmit={confirmEnrolment} className="mt-5 space-y-4">
              <div>
                <label htmlFor="totp-enroll-code" className={labelClass}>
                  Authenticator code
                </label>
                <input
                  id="totp-enroll-code"
                  type="text"
                  required
                  autoComplete="one-time-code"
                  inputMode="numeric"
                  maxLength={6}
                  placeholder="6-digit code"
                  value={enrollCode}
                  onChange={(e) => setEnrollCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  className={`${inputClass} tracking-[0.3em]`}
                />
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="submit"
                  disabled={busy || enrollCode.length !== 6}
                  className="inline-flex items-center rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
                >
                  {busy ? "Verifying…" : "Verify and enable"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setEnrollData(null);
                    setEnrollCode("");
                  }}
                  className="inline-flex items-center rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 transition disabled:opacity-60"
                >
                  Cancel setup
                </button>
              </div>
            </form>
          </div>
        ) : status?.enabled ? (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
            <div data-testid="totp-enabled" className="flex items-center gap-2">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              </span>
              <h2 className="text-base font-semibold text-slate-900">
                Two-factor authentication is enabled
              </h2>
            </div>
            <p className="mt-2 text-sm text-slate-600">
              You will be asked for a 6-digit code from your authenticator app (or a recovery code)
              at every sign-in.
            </p>
            <p className="mt-2 text-sm text-slate-600">
              Recovery codes remaining:{" "}
              <span className="font-semibold text-slate-900">{status.recovery_codes_remaining}</span>
              {status.recovery_codes_remaining === 0 && (
                <span className="text-amber-700"> — generate new ones below before you lose access.</span>
              )}
            </p>
            {status.enforced_for_admins && (
              <p className="mt-2 text-sm text-amber-700">
                Two-factor authentication is required for admin accounts.
              </p>
            )}

            <div className="mt-5 border-t border-slate-200 pt-5">
              {!showRegenerate ? (
                <button
                  type="button"
                  onClick={() => {
                    setShowRegenerate(true);
                    setShowDisable(false);
                    setError(null);
                  }}
                  className="inline-flex items-center rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 transition"
                >
                  Generate new recovery codes
                </button>
              ) : (
                <form onSubmit={handleRegenerate} className="space-y-4">
                  <p className="text-sm text-slate-600">
                    Your current recovery codes will stop working immediately.
                  </p>
                  <div>
                    <label htmlFor="totp-regenerate-password" className={labelClass}>
                      Current password
                    </label>
                    <input
                      id="totp-regenerate-password"
                      type="password"
                      required
                      autoComplete="current-password"
                      value={regeneratePassword}
                      onChange={(e) => setRegeneratePassword(e.target.value)}
                      className={inputClass}
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="submit"
                      disabled={busy}
                      className="inline-flex items-center rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
                    >
                      {busy ? "Generating…" : "Generate new codes"}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setShowRegenerate(false)}
                      className="inline-flex items-center rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 transition disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </div>

            <div className="mt-5 border-t border-slate-200 pt-5">
              {!showDisable ? (
                <button
                  type="button"
                  onClick={() => {
                    setShowDisable(true);
                    setShowRegenerate(false);
                    setError(null);
                  }}
                  className="inline-flex items-center rounded-lg border border-red-200 px-4 py-2.5 text-sm font-medium text-red-700 hover:bg-red-50 transition"
                >
                  Disable two-factor authentication
                </button>
              ) : (
                <form onSubmit={handleDisable} className="space-y-4">
                  <p className="text-sm text-slate-600">
                    Your authenticator app will no longer be asked for at sign-in.
                  </p>
                  <div>
                    <label htmlFor="totp-disable-password" className={labelClass}>
                      Current password
                    </label>
                    <input
                      id="totp-disable-password"
                      type="password"
                      required
                      autoComplete="current-password"
                      value={disablePassword}
                      onChange={(e) => setDisablePassword(e.target.value)}
                      className={inputClass}
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="submit"
                      disabled={busy}
                      className="inline-flex items-center rounded-lg bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-red-700 transition disabled:opacity-60"
                    >
                      {busy ? "Disabling…" : "Disable"}
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setShowDisable(false)}
                      className="inline-flex items-center rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 transition disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
            </div>
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
            <h2 className="text-base font-semibold text-slate-900">
              Two-factor authentication is not enabled
            </h2>
            <p className="mt-2 text-sm text-slate-600">
              Add an authenticator app as a second step at sign-in. Setting it up takes a minute:
              scan a code with your authenticator app and confirm with a 6-digit code.
            </p>
            {status?.enrolled && (
              <p className="mt-2 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
                A previous setup was started but never confirmed. Starting again replaces it with a
                fresh QR code and secret.
              </p>
            )}
            {!status?.enforced_for_admins && (
              <p className="mt-2 text-sm text-slate-500">
                Enrolment is optional for admin accounts today.
              </p>
            )}
            {status?.enforced_for_admins && (
              <p className="mt-2 text-sm text-amber-700">
                Two-factor authentication is required for admin accounts.
              </p>
            )}
            <button
              type="button"
              onClick={startEnrolment}
              disabled={busy}
              className="mt-5 inline-flex items-center rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
            >
              {busy ? "Starting…" : "Set up two-factor authentication"}
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
