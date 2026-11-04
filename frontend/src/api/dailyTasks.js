import client from "./client";

export function listDailyTasks(year, month, userId) {
  const params = { year, month };
  if (userId) params.user_id = userId;
  return client.get("/api/daily-tasks", { params }).then((r) => r.data);
}

export function createDailyTask(payload) {
  return client.post("/api/daily-tasks", payload).then((r) => r.data);
}

export function updateDailyTask(id, payload) {
  return client.patch(`/api/daily-tasks/${id}`, payload).then((r) => r.data);
}

export function deleteDailyTask(id) {
  return client.delete(`/api/daily-tasks/${id}`).then((r) => r.data);
}

export function giveDailyFeedback(id, supervisor_feedback) {
  return client
    .post(`/api/daily-tasks/${id}/feedback`, { supervisor_feedback })
    .then((r) => r.data);
}

export function uploadDailyEvidence(id, file) {
  const form = new FormData();
  form.append("file", file);
  return client
    .post(`/api/daily-tasks/${id}/evidence`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
}

export function dailyEvidenceUrl(id) {
  return `${client.defaults.baseURL}/api/daily-tasks/${id}/evidence`;
}

export function monthlyDailySummary(year, month) {
  return client
    .get("/api/daily-tasks/summary/month", { params: { year, month } })
    .then((r) => r.data);
}

export function getMonthlyReview(year, month, userId) {
  return client
    .get("/api/daily-tasks/review/month", { params: { year, month, user_id: userId } })
    .then((r) => r.data);
}

export function saveMonthlyReview(year, month, userId, payload) {
  return client
    .put("/api/daily-tasks/review/month", payload, {
      params: { year, month, user_id: userId },
    })
    .then((r) => r.data);
}

export function deleteMonthlyReview(year, month, userId) {
  return client
    .delete("/api/daily-tasks/review/month", {
      params: { year, month, user_id: userId },
    })
    .then((r) => r.data);
}

export function getDailyAuditLog(year, month, userId) {
  const params = {};
  if (year) params.year = year;
  if (month) params.month = month;
  if (userId) params.user_id = userId;
  return client.get("/api/daily-tasks/audit-log", { params }).then((r) => r.data);
}

export function exportDailyDiary(year, month, userId) {
  const params = { year, month };
  if (userId) params.user_id = userId;
  return client
    .get("/api/daily-tasks/export/file", { params, responseType: "blob" })
    .then((r) => {
      const url = window.URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = `daily-diary-${year}-${String(month).padStart(2, "0")}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
    });
}
