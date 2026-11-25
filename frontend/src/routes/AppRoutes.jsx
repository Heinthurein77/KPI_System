import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import ProtectedRoute from "./ProtectedRoute";
import { useAuth } from "../context/AuthContext";
import PageLoading from "../components/ui/PageLoading";

// F-m2: one lazy chunk per route — previously every page (including the
// recharts-heavy AnalyticsPage and the 1,400-line TemplatesPage) shipped
// in the initial bundle, so even an employee who only ever sees the
// self-assessment downloaded the admin and platform code. Login pages
// stay eager so the first paint never waits on a chunk.
import LoginPage from "../pages/LoginPage";
import PlatformLoginPage from "../pages/platform/PlatformLoginPage";
import SetupPasswordPage from "../pages/SetupPasswordPage";

const DashboardPage = lazy(() => import("../pages/DashboardPage"));
const ChangePasswordPage = lazy(() => import("../pages/ChangePasswordPage"));
const MyKpiPage = lazy(() => import("../pages/MyKpiPage"));
const MyKpiTrendPage = lazy(() => import("../pages/MyKpiTrendPage"));
const AttendancePage = lazy(() => import("../pages/AttendancePage"));
const DepartmentsPage = lazy(() => import("../pages/admin/DepartmentsPage"));
const UsersPage = lazy(() => import("../pages/admin/UsersPage"));
const UserEditPage = lazy(() => import("../pages/admin/UserEditPage"));
const UserKpiTrendPage = lazy(() => import("../pages/admin/UserKpiTrendPage"));
const KpiDownloadPage = lazy(() => import("../pages/admin/KpiDownloadPage"));
const TemplatesPage = lazy(() => import("../pages/admin/TemplatesPage"));
const AuditLogPage = lazy(() => import("../pages/admin/AuditLogPage"));
const AnalyticsPage = lazy(() => import("../pages/admin/AnalyticsPage"));
const BulkImportPage = lazy(() => import("../pages/admin/BulkImportPage"));
const TenantsPage = lazy(() => import("../pages/platform/TenantsPage"));
const PlatformAuditLogPage = lazy(() => import("../pages/platform/PlatformAuditLogPage"));
const NotFoundPage = lazy(() => import("../pages/NotFoundPage"));

function HomeRedirect() {
  const { user } = useAuth();
  const target = user?.role === "super_admin" ? "/platform/tenants" : "/dashboard";
  return <Navigate to={target} replace />;
}

export default function AppRoutes() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/platform/login" element={<PlatformLoginPage />} />
        <Route path="/setup-password" element={<SetupPasswordPage />} />
        <Route path="/" element={<HomeRedirect />} />

        {/* User Portal (Employee self-assessment, Dept Admin's own KPI) */}
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/my-kpi"
          element={
            <ProtectedRoute roles={["dept_admin"]}>
              <MyKpiPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/my-trend"
          element={
            <ProtectedRoute roles={["employee", "dept_admin"]}>
              <MyKpiTrendPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/attendance"
          element={
            <ProtectedRoute roles={["employee", "dept_admin", "tenant_admin"]}>
              <AttendancePage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/account/password"
          element={
            <ProtectedRoute>
              <ChangePasswordPage />
            </ProtectedRoute>
          }
        />

        {/* Admin Portal (Tenant Admin + Dept Admin) */}
        <Route
          path="/admin/departments"
          element={
            <ProtectedRoute roles={["tenant_admin"]}>
              <DepartmentsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/users"
          element={
            <ProtectedRoute roles={["tenant_admin", "dept_admin"]}>
              <UsersPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/users/:id/edit"
          element={
            <ProtectedRoute roles={["tenant_admin"]}>
              <UserEditPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/kpi-trend"
          element={
            <ProtectedRoute roles={["tenant_admin", "dept_admin"]}>
              <UserKpiTrendPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/kpi-trend/:id"
          element={
            <ProtectedRoute roles={["tenant_admin", "dept_admin"]}>
              <UserKpiTrendPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/kpi-download"
          element={
            <ProtectedRoute roles={["tenant_admin", "dept_admin"]}>
              <KpiDownloadPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/templates"
          element={
            <ProtectedRoute roles={["tenant_admin", "dept_admin"]}>
              <TemplatesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/audit-log"
          element={
            <ProtectedRoute roles={["tenant_admin"]}>
              <AuditLogPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/analytics"
          element={
            <ProtectedRoute roles={["tenant_admin"]}>
              <AnalyticsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/admin/bulk-import"
          element={
            <ProtectedRoute roles={["dept_admin"]}>
              <BulkImportPage />
            </ProtectedRoute>
          }
        />

        {/* Platform Portal (platform-level Super Admin only) */}
        <Route
          path="/platform/tenants"
          element={
            <ProtectedRoute roles={["super_admin"]}>
              <TenantsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/platform/audit-log"
          element={
            <ProtectedRoute roles={["super_admin"]}>
              <PlatformAuditLogPage />
            </ProtectedRoute>
          }
        />

        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
}
