import client from "./client";

export function login(orgSlug, email, password) {
  localStorage.setItem("tenant_slug", orgSlug.trim().toLowerCase());
  return client.post("/api/auth/login", { email, password }).then((r) => r.data);
}

export function platformLogin(email, password) {
  localStorage.removeItem("tenant_slug"); // platform routes are exempt anyway, but keep state clean
  return client.post("/api/platform/auth/login", { email, password }).then((r) => r.data);
}

export function me() {
  return client.get("/api/auth/me").then((r) => r.data);
}

export function changePassword(currentPassword, newPassword) {
  return client.post("/api/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}
