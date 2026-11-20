import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import EmployeeSelfAssessment from "../components/dashboard/EmployeeSelfAssessment";
import TeamReviewBoard from "../components/dashboard/TeamReviewBoard";
import FreshInstallWelcome from "../components/dashboard/FreshInstallWelcome";
import PageLoading from "../components/ui/PageLoading";
import LoadError from "../components/ui/LoadError";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { getDashboard } from "../api/dashboard";
import { deptApprove, deptSave, finalApprove, overrideScore, rejectSubmission } from "../api/kpi";
import { getErrorMessage } from "../api/errors";
import { DASHBOARD_POLL_INTERVAL_MS, useApi } from "../hooks/useApi";

// One admin dashboard for both roles (F-M1). DeptAdminDashboard and
// TenantAdminDashboard used to be two near-identical copies of the same
// component — same load lifecycle, same poll, same navKey effect —
// differing only in which approve handlers they pass to TeamReviewBoard.
// The role-specific bits are branched below instead of duplicated.
function AdminDashboard({ user, initialParams, navKey, isTenantAdmin }) {
  const toast = useToast();
  const { data, error: loadError, load } = useApi(getDashboard, {
    onError: (message) => toast.error(message),
  });
  const [params, setParams] = useState(initialParams);
  const paramsRef = useRef(params);
  const firstNavRef = useRef(true);

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live board: re-check the current period in the background so submissions
  // filled on the employee side appear here without a manual refresh. Same
  // period only (paramsRef), so an admin mid-edit is never disturbed: cards
  // are keyed by submission id and the board's reset effect only runs when
  // the period/filter/department itself changes.
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.hidden) return;
      load(paramsRef.current, { silent: true });
    }, DASHBOARD_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A notification link (e.g. /dashboard?year=2026&period=October) navigates
  // inside the SPA, so this component does NOT remount — its mount-only load
  // above would keep showing the old period while the URL says October, and
  // the newly submitted approval forms never appear. Follow the URL instead:
  // whenever the query string changes, merge its year/period over the current
  // params (filters preserved) and reload. The first run is the mount load.
  useEffect(() => {
    if (firstNavRef.current) {
      firstNavRef.current = false;
      return;
    }
    const next = { ...paramsRef.current, ...initialParams };
    paramsRef.current = next;
    setParams(next);
    load(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navKey]);

  function reload() {
    load(params);
  }

  function applyParams(next) {
    paramsRef.current = next;
    setParams(next);
    load(next);
  }

  if (!data) {
    if (loadError) return <LoadError message={loadError} onRetry={() => load(params)} />;
    return <PageLoading />;
  }

  if (isTenantAdmin && data.is_fresh_install) {
    return <FreshInstallWelcome userName={user.name} />;
  }

  async function handleDeptSave(id, dept_score, remarks) {
    try {
      await deptSave(id, { year: data.active_year, period: data.active_period, dept_score, remarks });
      toast.success("Score saved.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }
  async function handleDeptApprove(id, dept_score, remarks) {
    try {
      await deptApprove(id, { year: data.active_year, period: data.active_period, dept_score, remarks });
      toast.success("Submission approved.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }
  async function handleFinalApprove(id, final_score) {
    try {
      await finalApprove(id, { year: data.active_year, period: data.active_period, final_score });
      toast.success("Final approval recorded.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }
  async function handleOverride(id, final_score, remarks) {
    try {
      await overrideScore(id, { year: data.active_year, period: data.active_period, final_score, remarks });
      toast.success("Score overridden and approved.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }
  async function handleReject(id, remarks) {
    try {
      await rejectSubmission(id, { year: data.active_year, period: data.active_period, remarks: remarks || undefined });
      toast.success("Submission rejected.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }

  return (
    <TeamReviewBoard
      data={data}
      onApply={applyParams}
      pendingStatuses={isTenantAdmin ? ["pending_dept_approval", "pending_final_approval"] : ["pending_dept_approval"]}
      pendingLabel={isTenantAdmin ? "in review" : "awaiting your review"}
      pendingChipClass={isTenantAdmin ? "bg-blue-50 text-blue-700 ring-blue-200" : "bg-amber-50 text-amber-700 ring-amber-200"}
      pendingDotClass={isTenantAdmin ? "bg-blue-500" : "bg-amber-500"}
      showDepartmentColumn={isTenantAdmin || undefined}
      onReload={reload}
      onDeptSave={isTenantAdmin ? undefined : handleDeptSave}
      onDeptApprove={isTenantAdmin ? undefined : handleDeptApprove}
      onFinalApprove={isTenantAdmin ? handleFinalApprove : undefined}
      onOverride={isTenantAdmin ? handleOverride : undefined}
      onReject={handleReject}
    />
  );
}

export default function DashboardPage() {
  const { user } = useAuth();
  const [searchParams] = useSearchParams();

  // Lets a link elsewhere in the app (e.g. the KPI Trend page's chart) deep-link
  // straight into a specific month's dashboard view instead of always landing
  // on the current period.
  const urlYear = searchParams.get("year");
  const urlPeriod = searchParams.get("period");
  const initialParams = {
    ...(urlYear ? { year: Number(urlYear) } : {}),
    ...(urlPeriod ? { period: urlPeriod } : {}),
  };
  // The query string itself: dashboards watch it so a notification link
  // navigates the already-mounted dashboard to the linked month.
  const navKey = searchParams.toString();

  const title =
    user.role === "employee"
      ? "My KPI Self-Assessment"
      : user.role === "dept_admin"
        ? "Department KPI Review"
        : "Organization-wide KPI Overview";

  return (
    <AppShell title={title}>
      {user.role === "employee" && <EmployeeSelfAssessment fetcher={getDashboard} initialParams={initialParams} navKey={navKey} />}
      {user.role === "dept_admin" && <AdminDashboard user={user} initialParams={initialParams} navKey={navKey} />}
      {user.role === "tenant_admin" && <AdminDashboard user={user} initialParams={initialParams} navKey={navKey} isTenantAdmin />}
      {user.role === "super_admin" && (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-10 text-center">
          <p className="text-sm text-slate-500">
            Platform admins manage tenants from the platform portal.
          </p>
          <Link
            to="/platform/tenants"
            className="mt-4 inline-flex items-center rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
          >
            Go to platform portal
          </Link>
        </div>
      )}
    </AppShell>
  );
}
