import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AppShell from "../../components/layout/AppShell";
import EmployeeCombobox from "../../components/ui/EmployeeCombobox";
import EmployeeTrendView from "../../components/ui/EmployeeTrendView";
import { useAuth } from "../../context/AuthContext";
import { listUsers } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

export default function UserKpiTrendPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const isDeptAdmin = currentUser.role === "dept_admin";

  const [roster, setRoster] = useState(null);
  const [error, setError] = useState(null);

  // The roster of employees this admin can browse trends for — same scoping
  // as the "Trend" icon's visibility on the Users page.
  useEffect(() => {
    listUsers()
      .then((d) => {
        // Tenant admins review employees AND dept admins, so both appear;
        // tenant admins themselves are not KPI owners and stay hidden.
        // Dept admins see employees only.
        const selectable = d.users
          .filter((u) => u.role !== "tenant_admin" && (!isDeptAdmin || u.role === "employee"))
          .sort((a, b) => a.name.localeCompare(b.name));
        setRoster(selectable);
      })
      .catch((err) => setError(getErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The sidebar's "KPI Trend" link has no employee in mind — land on the
  // first person in the roster once it's loaded.
  useEffect(() => {
    if (!id && roster && roster.length > 0) {
      navigate(`/admin/kpi-trend/${roster[0].id}`, { replace: true });
    }
  }, [id, roster, navigate]);

  const currentIndex = useMemo(
    () => (roster ? roster.findIndex((u) => String(u.id) === String(id)) : -1),
    [roster, id]
  );

  function goTo(userId) {
    // Re-selecting the employee already on screen must be a no-op: navigating
    // to the same URL would leave the view unchanged and feel broken.
    // (Double-clicking the current row in the combobox hit exactly this.)
    if (String(userId) === String(id)) return;
    navigate(`/admin/kpi-trend/${userId}`);
  }

  function goRelative(offset) {
    if (!roster || currentIndex === -1) return;
    const next = roster[currentIndex + offset];
    if (next) goTo(next.id);
  }

  return (
    <AppShell title="KPI Trend">
      <EmployeeTrendView
        userId={id}
        toolbar={
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => goRelative(-1)}
                disabled={!roster || currentIndex <= 0}
                title="Previous team member"
                className="inline-flex items-center justify-center h-9 w-9 rounded-lg border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition disabled:opacity-40 disabled:hover:bg-white"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                </svg>
              </button>

              <EmployeeCombobox roster={roster} value={id} onChange={goTo} />

              <button
                type="button"
                onClick={() => goRelative(1)}
                disabled={!roster || currentIndex === -1 || currentIndex >= roster.length - 1}
                title="Next team member"
                className="inline-flex items-center justify-center h-9 w-9 rounded-lg border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition disabled:opacity-40 disabled:hover:bg-white"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                </svg>
              </button>

              {roster && currentIndex !== -1 && (
                <span className="text-xs text-slate-400 tabular-nums">
                  {currentIndex + 1} of {roster.length}
                </span>
              )}
            </div>

            <Link
              to="/admin/users"
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition"
            >
              Back to Users
            </Link>
          </div>
        }
      />

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {roster && roster.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 py-16 text-center text-sm text-slate-400">
          No employees to show KPI trends for yet.
        </div>
      )}
    </AppShell>
  );
}
