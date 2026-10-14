import client from "./client";

export function getAnalyticsOverview(year, period) {
  return client
    .get("/api/analytics/overview", { params: { year, period } })
    .then((r) => r.data);
}

export function getAnalyticsTrend(periods = 6) {
  return client
    .get("/api/analytics/trend", { params: { periods } })
    .then((r) => r.data);
}
