import { useEffect, useState } from "react";
import AppShell from "../components/layout/AppShell";
import EmployeeSelfAssessment from "../components/dashboard/EmployeeSelfAssessment";
import TeamReviewBoard from "../components/dashboard/TeamReviewBoard";
import FreshInstallWelcome from "../components/dashboard/FreshInstallWelcome";
import { useAuth } from "../context/AuthContext";
import { getDashboard } from "../api/dashboard";
import { deptApprove, deptSave, finalApprove, overrideScore, rejectSubmission } from "../api/kpi";

function DeptAdminDashboard() {
  const [data, setData] = useState(null);
  const [params, setParams] = useState({});

  function load(nextParams) {
    getDashboard(nextParams).then(setData);
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function reload() {
    load(params);
  }

  if (!data) return null;

  async function handleDeptSave(id, dept_score, remarks) {
    await deptSave(id, { year: data.active_year, period: data.active_period, dept_score, remarks });
    reload();
  }
  async function handleDeptApprove(id, dept_score, remarks) {
    await deptApprove(id, { year: data.active_year, period: data.active_period, dept_score, remarks });
    reload();
  }
  async function handleReject(id) {
    await rejectSubmission(id, { year: data.active_year, period: data.active_period });
    reload();
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

function SuperAdminDashboard({ user }) {
  const [data, setData] = useState(null);
  const [params, setParams] = useState({});

  function load(nextParams) {
    getDashboard(nextParams).then(setData);
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function reload() {
    load(params);
  }

  if (!data) return null;

  if (data.is_fresh_install) {
    return <FreshInstallWelcome userName={user.name} />;
  }

  async function handleFinalApprove(id, final_score) {
    await finalApprove(id, { year: data.active_year, period: data.active_period, final_score });
    reload();
  }
  async function handleOverride(id, final_score, remarks) {
    await overrideScore(id, { year: data.active_year, period: data.active_period, final_score, remarks });
    reload();
  }
  async function handleReject(id) {
    await rejectSubmission(id, { year: data.active_year, period: data.active_period });
    reload();
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

  const title =
    user.role === "employee"
      ? "My KPI Self-Assessment"
      : user.role === "dept_admin"
        ? "Department KPI Review"
        : "Company-wide KPI Overview";

  return (
    <AppShell title={title}>
      {user.role === "employee" && <EmployeeSelfAssessment fetcher={getDashboard} />}
      {user.role === "dept_admin" && <DeptAdminDashboard />}
      {user.role === "super_admin" && <SuperAdminDashboard user={user} />}
    </AppShell>
  );
}
