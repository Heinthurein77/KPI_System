import client from "./client";

export function getAttendanceSettings() {
  return client.get("/api/attendance/settings").then((r) => r.data);
}

export function updateAttendanceSettings(payload) {
  return client.patch("/api/attendance/settings", payload).then((r) => r.data);
}

export function getAttendanceToday() {
  return client.get("/api/attendance/today").then((r) => r.data);
}

export function checkIn() {
  return client.post("/api/attendance/check-in").then((r) => r.data);
}

export function checkOut() {
  return client.post("/api/attendance/check-out").then((r) => r.data);
}

export function markLeave(recordDate) {
  return client
    .post("/api/attendance/leave", { record_date: recordDate })
    .then((r) => r.data);
}

export function getAttendanceSummary(year, month, userId) {
  const params = { year, month };
  if (userId) params.user_id = userId;
  return client.get("/api/attendance/summary", { params }).then((r) => r.data);
}

export function getAttendanceReport(year, month) {
  return client
    .get("/api/attendance/report", { params: { year, month } })
    .then((r) => r.data);
}

export function downloadAttendanceReportExcel(year, month, departmentId) {
  const params = { year, month };
  if (departmentId) params.department_id = departmentId;
  return client
    .get("/api/attendance/report/export", { params, responseType: "blob" })
    .then((r) => r.data);
}

export function downloadAttendancePersonExcel(year, month, userId) {
  return client
    .get("/api/attendance/report/export", {
      params: { year, month, user_id: userId },
      responseType: "blob",
    })
    .then((r) => r.data);
}
