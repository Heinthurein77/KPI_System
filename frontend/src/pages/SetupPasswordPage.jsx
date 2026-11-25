import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { setupPassword } from "../api/auth";
import { getErrorMessage } from "../api/errors";

// S-05: the one-time set-password page behind the welcome-email link
// (/setup-password?token=...). Public — no login needed; the signed token
// is the credential. Valid 72h, single-use: setting the password here
// clears must_change_password, so a replayed link fails.
export default function SetupPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") || "";
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("New password and confirmation do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await setupPassword(token, newPassword);
      setDone(true);
    } catch (err) {
      setError(getErrorMessage(err, "Invalid or expired setup link."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      <div className="hidden lg:flex lg:w-[45%] relative flex-col justify-between bg-gradient-to-br from-slate-900 via-slate-900 to-brand-900 px-12 py-12 overflow-hidden">
        <div className="flex items-center gap-2.5">
          <div className="h-10 w-10 shrink-0 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center font-bold text-white text-lg">
            K
          </div>
          <div className="leading-tight">
            <p className="text-lg font-bold text-white tracking-tight">abcMIB</p>
            <p className="text-xs text-slate-400">KPI Approval System</p>
          </div>
        </div>
        <p className="text-sm text-slate-400">Set a password for your new account.</p>
      </div>

      <div className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <h1 className="text-xl font-bold text-slate-900 tracking-tight">Set your password</h1>
          <p className="mt-1.5 text-sm text-slate-500">
            Choose a password for your KPI account. The link expires in 72 hours and works once.
          </p>

          {error && (
            <div className="mt-5 flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3.5 py-2.5 text-sm text-red-700">
              <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
              </svg>
              {error}
            </div>
          )}

          {done ? (
            <div className="mt-5 rounded-lg bg-emerald-50 border border-emerald-200 px-4 py-3 text-sm text-emerald-800">
              Your password is set.{" "}
              <Link to="/login" className="font-medium underline underline-offset-2">
                Sign in
              </Link>{" "}
              with your new password.
            </div>
          ) : !token ? (
            <div className="mt-5 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
              This link is missing its setup token. Please use the full link from your welcome
              email, or ask your admin to send a new one.
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="mt-7 space-y-4">
              <div>
                <label htmlFor="newPassword" className="block text-sm font-medium text-slate-700 mb-1.5">
                  New password
                </label>
                <input
                  id="newPassword"
                  type="password"
                  required
                  autoFocus
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                  placeholder="••••••••"
                />
                <p className="mt-1.5 text-xs text-slate-500">
                  At least 8 characters, with a letter and a number.
                </p>
              </div>
              <div>
                <label htmlFor="confirmPassword" className="block text-sm font-medium text-slate-700 mb-1.5">
                  Confirm new password
                </label>
                <input
                  id="confirmPassword"
                  type="password"
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                  placeholder="••••••••"
                />
              </div>
              <button
                type="submit"
                disabled={submitting}
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
              >
                {submitting ? "Setting password…" : "Set password"}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
