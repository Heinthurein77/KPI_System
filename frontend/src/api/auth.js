import client from "./client";

export function login(orgSlug, email, password) {
  const slug = orgSlug.trim().toLowerCase();
  const previousSlug = localStorage.getItem("tenant_slug");
  // Stored BEFORE the POST on purpose: the request interceptor reads it to
  // set the X-Tenant-Slug header the login endpoint resolves the tenant
  // from. On failure the typed slug is NOT kept — restore whatever was
  // there before (or nothing), so a typo'd slug is never persisted.
  localStorage.setItem("tenant_slug", slug);
  return client.post("/api/auth/login", { email, password }).then(
    (r) => r.data,
    (err) => {
      if (previousSlug === null || previousSlug === slug) {
        localStorage.removeItem("tenant_slug");
      } else {
        localStorage.setItem("tenant_slug", previousSlug);
      }
      throw err;
    }
  );
}

export function platformLogin(email, password) {
  localStorage.removeItem("tenant_slug"); // platform routes are exempt anyway, but keep state clean
  return client.post("/api/platform/auth/login", { email, password }).then((r) => r.data);
}

export function me() {
  return client.get("/api/auth/me").then((r) => r.data);
}

export function platformMe() {
  return client.get("/api/platform/auth/me").then((r) => r.data);
}

export function logout() {
  return client.post("/api/auth/logout").then((r) => r.data);
}

// S-03: rotating refresh. The refresh token lives in an httpOnly cookie;
// this call swaps it (and the access cookie) for a fresh pair. In-flight
// refreshes are shared so parallel 401s don't each mint a new token and
// invalidate each other through rotation.
let refreshPromise = null;
export function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = client
      .post("/api/auth/refresh")
      .then((r) => r.data)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// S-02: second login step for TOTP-enrolled accounts. The password step
// returns { totp_required: true } and sets a short-lived pending cookie;
// this call completes the login. One endpoint serves tenant + platform
// logins (the pending token carries the tenant). Pass a recovery code
// instead of a 6-digit code with { recoveryCode }.
export function verifyTotp(code, recoveryCode) {
  const body = recoveryCode ? { recovery_code: recoveryCode } : { code };
  return client.post("/api/auth/login/totp", body).then((r) => r.data);
}

// S-02: TOTP enrolment + management (tenant session). Platform admins
// use the /api/platform/auth/totp/* variants below.
export function getTotpStatus() {
  return client.get("/api/auth/totp/status").then((r) => r.data);
}

export function enrollTotp() {
  return client.post("/api/auth/totp/enroll").then((r) => r.data);
}

export function verifyTotpEnrolment(code) {
  return client.post("/api/auth/totp/verify-enrolment", { code }).then((r) => r.data);
}

export function disableTotp(password) {
  return client.post("/api/auth/totp/disable", { password });
}

export function regenerateTotpRecoveryCodes(password) {
  return client.post("/api/auth/totp/recovery-codes/regenerate", { password }).then((r) => r.data);
}

export function getPlatformTotpStatus() {
  return client.get("/api/platform/auth/totp/status").then((r) => r.data);
}

export function enrollPlatformTotp() {
  return client.post("/api/platform/auth/totp/enroll").then((r) => r.data);
}

export function verifyPlatformTotpEnrolment(code) {
  return client.post("/api/platform/auth/totp/verify-enrolment", { code }).then((r) => r.data);
}

export function disablePlatformTotp(password) {
  return client.post("/api/platform/auth/totp/disable", { password });
}

export function regeneratePlatformTotpRecoveryCodes(password) {
  return client.post("/api/platform/auth/totp/recovery-codes/regenerate", { password }).then((r) => r.data);
}

// S-03: revoke every session for the current user (they log in again) or,
// for a tenant admin, for one of their tenant's users.
export function revokeAllSessions() {
  return client.post("/api/auth/revoke-all-sessions").then((r) => r.data);
}

export function revokeUserSessions(userId) {
  return client.post(`/api/auth/revoke-user-sessions/${userId}`).then((r) => r.data);
}

export function platformRevokeUserSessions(userId) {
  return client.post(`/api/platform/auth/revoke-user-sessions/${userId}`).then((r) => r.data);
}

export function changePassword(currentPassword, newPassword) {
  return client.post("/api/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export function platformChangePassword(currentPassword, newPassword) {
  return client.post("/api/platform/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}
