import AppShell from "../components/layout/AppShell";
import EmployeeSelfAssessment from "../components/dashboard/EmployeeSelfAssessment";
import { getMyKpi } from "../api/dashboard";

export default function MyKpiPage() {
  return (
    <AppShell title="My KPI Self-Assessment">
      <EmployeeSelfAssessment fetcher={getMyKpi} />
    </AppShell>
  );
}
