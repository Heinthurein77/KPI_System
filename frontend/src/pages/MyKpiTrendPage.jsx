import AppShell from "../components/layout/AppShell";
import EmployeeTrendView from "../components/ui/EmployeeTrendView";
import { useAuth } from "../context/AuthContext";

/** An employee's (or dept admin's) own KPI trend — same view as the admin
 * trend page, scoped to themselves (the backend 403s any other user id). */
export default function MyKpiTrendPage() {
  const { user } = useAuth();

  return (
    <AppShell title="My KPI Trend">
      <EmployeeTrendView userId={user?.id} />
    </AppShell>
  );
}
