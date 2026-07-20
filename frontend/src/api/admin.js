import client from "./client";

export function listDepartments() {
  return client.get("/api/admin/departments").then((r) => r.data);
}

export function createDepartment(payload) {
  return client.post("/api/admin/departments", payload).then((r) => r.data);
}

export function deleteDepartment(id) {
  return client.delete(`/api/admin/departments/${id}`).then((r) => r.data);
}

export function listUsers() {
  return client.get("/api/admin/users").then((r) => r.data);
}

export function getUser(id) {
  return client.get(`/api/admin/users/${id}`).then((r) => r.data);
}

export function createUser(payload) {
  return client.post("/api/admin/users", payload).then((r) => r.data);
}

export function updateUser(id, payload) {
  return client.put(`/api/admin/users/${id}`, payload).then((r) => r.data);
}

export function toggleUserActive(id) {
  return client.post(`/api/admin/users/${id}/toggle-active`).then((r) => r.data);
}

export function deleteUser(id) {
  return client.delete(`/api/admin/users/${id}`).then((r) => r.data);
}

export function listTemplates() {
  return client.get("/api/admin/templates").then((r) => r.data);
}

export function createTemplate(payload) {
  return client.post("/api/admin/templates", payload).then((r) => r.data);
}

export function createCustomTemplate(payload) {
  return client.post("/api/admin/templates/custom", payload).then((r) => r.data);
}

export function deleteTemplate(id) {
  return client.delete(`/api/admin/templates/${id}`).then((r) => r.data);
}
