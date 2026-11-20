// Shared data-loading hook (F-M1, senior review 2026-11-20).
//
// Every data page used to hand-roll the same lifecycle: a `data` /
// `loadError` state pair, a `loadIdRef` stale-response guard, and retry
// wiring — and the fixes drifted (the poll interval itself was defined
// twice under two names). This hook owns that lifecycle once:
//
//   const { data, error, loading, load } = useApi(fetcher, { onSuccess, onError });
//
// - `load(args, { silent })`: fetches via `fetcher(args)`. A response that
//   arrives after a newer request started is dropped (stale-response guard).
// - `silent` loads (background polls) never touch `error` and never clear
//   `data` — a failed tick keeps the old board and retries next tick.
// - `onSuccess` / `onError` are read through a ref, so passing fresh
//   closures (e.g. `toast.error`) never re-creates `load` or re-runs effects.
// - `load` resolves to the fetched data, or `undefined` on a stale/failed
//   response — callers that don't await it are unaffected.

import { useCallback, useRef, useState } from "react";
import { getErrorMessage } from "../api/errors";

// How often the dashboard boards silently re-check for new submissions,
// so a KPI filled on the employee side shows on the admin side without a
// manual refresh. One shared cadence — previously defined twice with the
// same value under different names (BOARD_POLL_INTERVAL_MS in
// DashboardPage.jsx, DASHBOARD_POLL_INTERVAL_MS in
// EmployeeSelfAssessment.jsx). Skipped while the tab is hidden.
export const DASHBOARD_POLL_INTERVAL_MS = 5 * 60 * 1000;

export function useApi(fetcher, { onSuccess, onError } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const loadIdRef = useRef(0);
  const callbacksRef = useRef({ onSuccess, onError });
  callbacksRef.current = { onSuccess, onError };

  const load = useCallback(
    (args, { silent = false } = {}) => {
      const requestId = ++loadIdRef.current;
      if (!silent) setLoading(true);
      return fetcher(args)
        .then((d) => {
          if (loadIdRef.current !== requestId) return undefined;
          setData(d);
          setError(null);
          if (!silent) setLoading(false);
          callbacksRef.current.onSuccess?.(d);
          return d;
        })
        .catch((err) => {
          if (loadIdRef.current !== requestId) return undefined;
          if (!silent) setLoading(false);
          if (silent) return undefined; // background tick: keep old data
          const message = getErrorMessage(err);
          setError(message);
          callbacksRef.current.onError?.(message, err);
          return undefined;
        });
    },
    [fetcher]
  );

  return { data, setData, error, setError, loading, load, reload: load };
}
