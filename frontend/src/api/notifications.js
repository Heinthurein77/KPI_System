import client from "./client";

export async function listNotifications() {
  const { data } = await client.get("/api/notifications");
  return data;
}

export async function markNotificationRead(id) {
  const { data } = await client.post(`/api/notifications/${id}/read`);
  return data;
}

export async function markAllNotificationsRead() {
  const { data } = await client.post("/api/notifications/read-all");
  return data;
}

export async function getNotificationSettings() {
  const { data } = await client.get("/api/notifications/settings");
  return data;
}

export async function updateNotificationSettings(email_enabled) {
  const { data } = await client.put("/api/notifications/settings", { email_enabled });
  return data;
}
