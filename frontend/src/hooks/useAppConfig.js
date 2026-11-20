// Server-side app config (S-M3, senior review 2026-11-20).
//
// The submission grace window used to be hardcoded as 7 in three places
// (TemplatesPage, BulkImportPage, EmployeeSelfAssessment) and read off the
// browser clock, while the backend reads SUBMISSION_GRACE_DAYS and the
// Asia/Yangon date. This hook reads both from GET /api/app-config and
// falls back to the local date + the default 7-day grace until the config
// loads, so the UI is never worse than before while it waits.
import { useEffect, useMemo } from "react";
import { getAppConfig } from "../api/config";
import { useApi } from "./useApi";

// "YYYY-MM-DD" -> local-midnight Date. Constructed from parts (not
// `new Date(iso)`) so no timezone shift can move it a day.
export function parseServerDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function useAppConfig() {
  const { data, load } = useApi(getAppConfig);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Memoised so the returned object (and its `today` Date) keep a stable
  // identity across renders — callers use it in effect/useMemo deps.
  return useMemo(
    () => ({
      graceDays: data?.submission_grace_days ?? 7,
      today: data?.today ? parseServerDate(data.today) : null,
      timezone: data?.timezone ?? "Asia/Yangon",
      loaded: !!data,
    }),
    [data]
  );
}
