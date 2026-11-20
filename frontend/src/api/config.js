import client from "./client";

// Server-side app config (S-M3): the submission grace length and the
// server's Asia/Yangon calendar date, so the UI never hardcodes the
// grace window or trusts the browser clock.
export function getAppConfig() {
  return client.get("/api/app-config").then((r) => r.data);
}
