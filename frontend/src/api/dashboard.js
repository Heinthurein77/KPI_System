import client from "./client";

export function getDashboard(params) {
  return client.get("/api/dashboard", { params }).then((r) => r.data);
}

export function getMyKpi(params) {
  return client.get("/api/my-kpi", { params }).then((r) => r.data);
}
