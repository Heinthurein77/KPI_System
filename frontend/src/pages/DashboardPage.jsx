import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import AppShell from "../components/layout/AppShell";
import EmployeeSelfAssessment from "../components/dashboard/EmployeeSelfAssessment";
import TeamReviewBoard from "../components/dashboard/TeamReviewBoard";
import FreshInstallWelcome from "../components/dashboard/FreshInstallWelcome";
import PageLoading from "../components/ui/PageLoading";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { getDashboard } from "../api/dashboard";
import { deptApprove, deptSave, finalApprove, overrideScore, rejectSubmission } from "../api/kpi";
import { getErrorMessage } from "../api/errors";

function DeptAdminDashboard({ initialParams }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [params, setParams] = useState(initialParams);
  const loadIdRef = useRef(0);

  function load(nextParams) {
    // Guard against out-of-order responses: if the period/filter is changed
    // again before this request lands, a stale response must not overwrite
    // the newer one.
    const requestId = ++loadIdRef.current;
    getDashboard(nextParams)
      .then((d) => {
        if (loadIdRef.current !== requestId) return;
        setData(d);
      })
      .catch((err) => {
        if (loadIdRef.current !== requestId) return;
        toast.error(getErrorMessage(err));
      });
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function reload() {
    load(params);
  }

  if (!data) return <PageLoading />;

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
  async function handleReject(id) {
    try {
      await rejectSubmission(id, { year: data.active_year, period: data.active_period });
      toast.success("Submission rejected.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }

  return (
    <TeamReviewBoard
      data={data}
      onApply={(next) => {
        setParams(next);
        load(next);
      }}
      pendingStatuses={["pending_dept_approval"]}
      pendingLabel="awaiting your review"
      pendingChipClass="bg-amber-50 text-amber-700 ring-amber-200"
      pendingDotClass="bg-amber-500"
      onDeptSave={handleDeptSave}
      onDeptApprove={handleDeptApprove}
      onReject={handleReject}
    />
  );
}

function TenantAdminDashboard({ user, initialParams }) {
  const toast = useToast();
  const [data, setData] = useState(null);
  const [params, setParams] = useState(initialParams);
  const loadIdRef = useRef(0);

  function load(nextParams) {
    // Same stale-response guard as DeptAdminDashboard.load — see comment there.
    const requestId = ++loadIdRef.current;
    getDashboard(nextParams)
      .then((d) => {
        if (loadIdRef.current !== requestId) return;
        setData(d);
      })
      .catch((err) => {
        if (loadIdRef.current !== requestId) return;
        toast.error(getErrorMessage(err));
      });
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function reload() {
    load(params);
  }

  if (!data) return <PageLoading />;

  if (data.is_fresh_install) {
    return <FreshInstallWelcome userName={user.name} />;
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
  async function handleReject(id) {
    try {
      await rejectSubmission(id, { year: data.active_year, period: data.active_period });
      toast.success("Submission rejected.");
      reload();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }

  return (
    <TeamReviewBoard
      data={data}
      onApply={(next) => {
        setParams(next);
        load(next);
      }}
      pendingStatuses={["pending_dept_approval", "pending_final_approval"]}
      pendingLabel="in review"
      pendingChipClass="bg-blue-50 text-blue-700 ring-blue-200"
      pendingDotClass="bg-blue-500"
      showDepartmentColumn
      onFinalApprove={handleFinalApprove}
      onOverride={handleOverride}
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

  const title =
    user.role === "employee"
      ? "My KPI Self-Assessment"
      : user.role === "dept_admin"
        ? "Department KPI Review"
        : "Organization-wide KPI Overview";

  return (
    <AppShell title={title}>
      {user.role === "employee" && <EmployeeSelfAssessment fetcher={getDashboard} />}
      {user.role === "dept_admin" && <DeptAdminDashboard initialParams={initialParams} />}
      {user.role === "tenant_admin" && <TenantAdminDashboard user={user} initialParams={initialParams} />}
    </AppShell>
  );
}
