import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import * as authApi from "../api/auth";
import { getErrorMessage } from "../api/errors";

export default function LoginPage() {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Prefill the org slug from the last successful login (S-m6): a
  // transient 401 no longer wipes it, so the user isn't forced to retype
  // it after e.g. a password change elsewhere bumped token_version.
  const [orgSlug, setOrgSlug] = useState(() => localStorage.getItem("tenant_slug") ?? "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // S-02: second login step for TOTP-enrolled accounts. The password step
  // returns totp_required and sets a short-lived pending cookie; the code
  // step below completes the login. The context login() is bypassed here
  // on purpose — it has no totp_required signal — and refreshUser() picks
  // the session up from /me once the second step succeeds.
  const [totpRequired, setTotpRequired] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);

  // Tenant users are bounced to their dashboard, as before. A platform
  // super_admin is NOT redirected: their browser session must not make a
  // shared link to this page useless — they can sign in below as an
  // organization user (which replaces the session) instead.
  if (user && user.role !== "super_admin") {
    return <Navigate to="/dashboard" replace />;
  }
  const isPlatformAdmin = user?.role === "super_admin";

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (totpRequired) {
        // Second step: 6-digit authenticator code, or a single-use
        // recovery code when the authenticator is unavailable.
        await authApi.verifyTotp(
          useRecoveryCode ? null : totpCode,
          useRecoveryCode ? totpCode : null
        );
        await refreshUser();
        navigate("/dashboard", { replace: true });
        return;
      }
      const data = await authApi.login(orgSlug, email, password);
      if (data.totp_required) {
        setTotpRequired(true);
        return;
      }
      await refreshUser();
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(getErrorMessage(err, "Invalid organization, email, or password."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* Brand panel */}
      <div className="hidden lg:flex lg:w-[45%] relative flex-col justify-between bg-gradient-to-br from-slate-900 via-slate-900 to-brand-900 px-12 py-12 overflow-hidden">
        <div
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage: "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
            backgroundSize: "28px 28px",
          }}
        ></div>
        <div className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-brand-500/20 blur-3xl"></div>
        <div className="absolute -bottom-32 -left-16 h-80 w-80 rounded-full bg-brand-600/10 blur-3xl"></div>

        <div className="relative flex items-center gap-2.5">
          <div className="h-9 w-9 shrink-0 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center font-bold text-white shadow-lg shadow-brand-900/40">
            K
          </div>
          <div className="leading-tight">
            <p className="text-[15px] font-bold text-white tracking-tight">abcMIB</p>
            <p className="text-[11px] text-slate-400">KPI Approval System</p>
          </div>
        </div>

        <div className="relative">
          <h1 className="text-3xl font-bold text-white leading-tight tracking-tight">
            Performance reviews,
            <br />
            without the spreadsheet chaos.
          </h1>
          <p className="mt-4 text-slate-400 text-[15px] leading-relaxed max-w-sm">
            One workflow from self-assessment to final approval — self-score, department review, and executive
            sign-off, all tracked in one place.
          </p>

          <div className="mt-10 space-y-4">
            {[
              "Role-based approval chain — self-score → department → final sign-off",
              "Strict department isolation, with full company-wide oversight for admins",
              "Weighted, combined KPI scores computed automatically",
            ].map((text) => (
              <div key={text} className="flex items-start gap-3">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/10">
                  <svg className="h-3.5 w-3.5 text-brand-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                </div>
                <p className="text-sm text-slate-300">{text}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-xs text-slate-500">© 2026 abcMIB · KPI Approval System</p>
      </div>

      {/* Form panel */}
      <div className="flex-1 flex items-center justify-center px-6 py-12 bg-slate-50 lg:bg-white">
        <div className="w-full max-w-sm">
          <div className="flex items-center justify-center gap-2.5 mb-8 lg:hidden">
            <div className="h-10 w-10 shrink-0 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center font-bold text-white text-lg">
              K
            </div>
            <div className="leading-tight text-left">
              <p className="text-lg font-bold text-slate-900 tracking-tight">abcMIB</p>
              <p className="text-xs text-slate-500">KPI Approval System</p>
            </div>
          </div>

          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Welcome back</h1>
          <p className="mt-1.5 text-sm text-slate-500">Sign in to manage your KPI submissions.</p>

          {isPlatformAdmin && (
            <div className="mt-5 flex items-start gap-2 rounded-lg bg-sky-50 border border-sky-200 px-3.5 py-2.5 text-sm text-sky-700">
              <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12v-.008Z" />
              </svg>
              <span>
                Signed in as Platform Admin. Sign in below as an organization user, or go to the{" "}
                <Link to="/platform/tenants" className="font-semibold underline underline-offset-2">
                  platform dashboard
                </Link>
                .
              </span>
            </div>
          )}
          {location.state?.passwordChanged && !error && (
            <div className="mt-5 flex items-start gap-2 rounded-lg bg-emerald-50 border border-emerald-200 px-3.5 py-2.5 text-sm text-emerald-700">
              <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
              Password changed. Please log in with your new password.
            </div>
          )}
          {error && (
            <div className="mt-5 flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3.5 py-2.5 text-sm text-red-700">
              <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
              </svg>
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            {totpRequired ? (
              <>
                <p className="text-sm text-slate-500">
                  Your account uses two-factor authentication. Enter the 6-digit code from your
                  authenticator app{useRecoveryCode ? ", or a recovery code" : ""}.
                </p>
                <div>
                  <label htmlFor="totpCode" className="block text-sm font-medium text-slate-700 mb-1.5">
                    {useRecoveryCode ? "Recovery code" : "Authenticator code"}
                  </label>
                  <input
                    id="totpCode"
                    type="text"
                    required
                    autoFocus
                    autoComplete="one-time-code"
                    inputMode={useRecoveryCode ? "text" : "numeric"}
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                    placeholder={useRecoveryCode ? "xxxx-xxxx" : "123456"}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setUseRecoveryCode((v) => !v);
                    setTotpCode("");
                    setError(null);
                  }}
                  className="text-sm font-medium text-brand-600 hover:text-brand-700 underline underline-offset-2"
                >
                  {useRecoveryCode ? "Use an authenticator code instead" : "Lost your authenticator? Use a recovery code"}
                </button>
              </>
            ) : (
              <>
            <div>
              <label htmlFor="orgSlug" className="block text-sm font-medium text-slate-700 mb-1.5">
                Organization
              </label>
              <input
                id="orgSlug"
                type="text"
                required
                autoFocus
                value={orgSlug}
                onChange={(e) => setOrgSlug(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                placeholder="your-company"
              />
            </div>
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1.5">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                placeholder="you@company.com"
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-1.5">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                placeholder="••••••••"
              />
            </div>
              </>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
            >
              {submitting ? "Signing in…" : totpRequired ? "Verify code" : "Sign in"}
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
              </svg>
            </button>
          </form>

        </div>
      </div>
    </div>
  );
}
