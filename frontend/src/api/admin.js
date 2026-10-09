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

export function getUserKpiTrend(id, year) {
  return client
    .get(`/api/admin/users/${id}/kpi-trend`, { params: year ? { year } : {} })
    .then((r) => r.data);
}

export function getUserSubmissionsForPeriod(id, year, period) {
  return client
    .get(`/api/admin/users/${id}/submissions`, { params: { year, period } })
    .then((r) => r.data);
}

export function downloadEmployeeKpiReport(employeeId, month, year) {
  return client.get("/api/admin/kpi-export", {
    params: { employee_id: employeeId, month, year },
    responseType: "blob",
  }).then((r) => r.data);
}

export function downloadEmployeeAnnualKpiReport(employeeId, year) {
  return client.get("/api/admin/kpi-export/annual", {
    params: { employee_id: employeeId, year },
    responseType: "blob",
  }).then((r) => r.data);
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

export function updateTemplateWeight(id, payload) {
  return client.patch(`/api/admin/templates/${id}/weight`, payload).then((r) => r.data);
}

export function getAuditLog(limit) {
  return client.get("/api/admin/audit-log", { params: limit ? { limit } : {} }).then((r) => r.data);
}
