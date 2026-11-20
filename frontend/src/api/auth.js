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
