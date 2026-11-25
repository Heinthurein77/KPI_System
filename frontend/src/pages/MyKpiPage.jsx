import { useSearchParams } from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import EmployeeSelfAssessment from "../components/dashboard/EmployeeSelfAssessment";
import { getMyKpi } from "../api/dashboard";

export default function MyKpiPage() {
  const [searchParams] = useSearchParams();

  // Same deep-link handling as DashboardPage: a link elsewhere in the
  // app (e.g. a notification) can point straight at a specific month's
  // board (/my-kpi?year=2026&period=December) instead of always landing
  // on the current period. Absent params are omitted, so the backend
  // defaults to the current month; invalid values are rejected by the
  // backend (422) exactly as on /dashboard.
  const urlYear = searchParams.get("year");
  const urlPeriod = searchParams.get("period");
  const initialParams = {
    ...(urlYear ? { year: Number(urlYear) } : {}),
    ...(urlPeriod ? { period: urlPeriod } : {}),
  };
  // The query string itself: EmployeeSelfAssessment watches it so a
  // link navigates the already-mounted board to the linked month.
  const navKey = searchParams.toString();

  return (
    <AppShell title="My KPI Self-Assessment">
      <EmployeeSelfAssessment fetcher={getMyKpi} initialParams={initialParams} navKey={navKey} />
    </AppShell>
  );
}
