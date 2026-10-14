import client from "./client";

export async function listNotes(submissionId) {
  const { data } = await client.get(`/api/kpi/submissions/${submissionId}/notes`);
  return data;
}

export async function addNote(submissionId, body) {
  const { data } = await client.post(`/api/kpi/submissions/${submissionId}/notes`, { body });
  return data;
}
