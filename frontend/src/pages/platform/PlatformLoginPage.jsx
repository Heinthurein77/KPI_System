import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import * as authApi from "../../api/auth";
import { getErrorMessage } from "../../api/errors";

export default function PlatformLoginPage() {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // S-02: second login step for TOTP-enrolled platform accounts — mirrors
  // LoginPage. The password step returns totp_required and sets a
  // short-lived pending cookie; the code step below completes the login.
  // The context platformLogin() is bypassed here on purpose — it has no
  // totp_required signal — and refreshUser() picks the session up from
  // /platform/auth/me once the second step succeeds.
  const [totpRequired, setTotpRequired] = useState(false);
  const [totpCode, setTotpCode] = useState("");
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);

  if (user) {
    return <Navigate to={user.role === "super_admin" ? "/platform/tenants" : "/dashboard"} replace />;
  }

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
        navigate("/platform/tenants", { replace: true });
        return;
      }
      const data = await authApi.platformLogin(email, password);
      if (data.totp_required) {
        setTotpRequired(true);
        return;
      }
      await refreshUser();
      navigate("/platform/tenants", { replace: true });
    } catch (err) {
      setError(getErrorMessage(err, "Invalid email or password."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900 px-6 py-12">
      <div className="w-full max-w-sm">
        <div className="flex items-center justify-center gap-2.5 mb-8">
          <div className="h-10 w-10 shrink-0 rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center font-bold text-white text-lg">
            K
          </div>
          <div className="leading-tight text-left">
            <p className="text-lg font-bold text-white tracking-tight">abcMIB</p>
            <p className="text-xs text-slate-400">Platform Administration</p>
          </div>
        </div>

        <h1 className="text-xl font-bold text-white tracking-tight text-center">Platform sign in</h1>
        <p className="mt-1.5 text-sm text-slate-400 text-center">For platform Super Admins only.</p>

        {location.state?.passwordChanged && !error && (
          <div className="mt-5 flex items-start gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 px-3.5 py-2.5 text-sm text-emerald-300">
            <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
            </svg>
            Password changed. Please log in with your new password.
          </div>
        )}
        {error && (
          <div className="mt-5 flex items-start gap-2 rounded-lg bg-red-500/10 border border-red-500/30 px-3.5 py-2.5 text-sm text-red-300">
            <svg className="h-4 w-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
            </svg>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-7 space-y-4">
          {totpRequired ? (
            <>
              <p className="text-sm text-slate-400">
                Your account uses two-factor authentication. Enter the 6-digit code from your
                authenticator app{useRecoveryCode ? ", or a recovery code" : ""}.
              </p>
              <div>
                <label htmlFor="totpCode" className="block text-sm font-medium text-slate-300 mb-1.5">
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
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-white shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
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
                className="text-sm font-medium text-brand-400 hover:text-brand-300 underline underline-offset-2"
              >
                {useRecoveryCode ? "Use an authenticator code instead" : "Lost your authenticator? Use a recovery code"}
              </button>
            </>
          ) : (
            <>
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-slate-300 mb-1.5">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-white shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              placeholder="platform-admin@kpi.com"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-slate-300 mb-1.5">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3.5 py-2.5 text-sm text-white shadow-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
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
          </button>
        </form>

      </div>
    </div>
  );
}
