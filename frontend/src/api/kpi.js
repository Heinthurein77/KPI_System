import client from "./client";

export function employeeSaveScores(payload) {
  return client.post("/api/kpi/employee/save", payload).then((r) => r.data);
}

export function employeeSubmit(payload) {
  return client.post("/api/kpi/employee/submit", payload).then((r) => r.data);
}

export function deptSave(submissionId, payload) {
  return client.post(`/api/kpi/${submissionId}/dept-save`, payload).then((r) => r.data);
}

export function deptApprove(submissionId, payload) {
  return client.post(`/api/kpi/${submissionId}/dept-approve`, payload).then((r) => r.data);
}

export function finalApprove(submissionId, payload) {
  return client.post(`/api/kpi/${submissionId}/final-approve`, payload).then((r) => r.data);
}

export function overrideScore(submissionId, payload) {
  return client.post(`/api/kpi/${submissionId}/override`, payload).then((r) => r.data);
}

export function rejectSubmission(submissionId, payload) {
  return client.post(`/api/kpi/${submissionId}/reject`, payload).then((r) => r.data);
}
