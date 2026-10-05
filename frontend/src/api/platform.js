import client from "./client";

export function listTenants() {
  return client.get("/api/platform/tenants").then((r) => r.data);
}

export function createTenant(payload) {
  return client.post("/api/platform/tenants", payload).then((r) => r.data);
}

export function suspendTenant(id) {
  return client.post(`/api/platform/tenants/${id}/suspend`).then((r) => r.data);
}

export function activateTenant(id) {
  return client.post(`/api/platform/tenants/${id}/activate`).then((r) => r.data);
}

export function deleteTenant(id) {
  return client.delete(`/api/platform/tenants/${id}`).then((r) => r.data);
}
