import { Navigate, Route, Routes } from "react-router-dom";
import ProtectedRoute from "./ProtectedRoute";
import { useAuth } from "../context/AuthContext";
import LoginPage from "../pages/LoginPage";
import DashboardPage from "../pages/DashboardPage";
import ChangePasswordPage from "../pages/ChangePasswordPage";
import MyKpiPage from "../pages/MyKpiPage";
import DepartmentsPage from "../pages/admin/DepartmentsPage";
import UsersPage from "../pages/admin/UsersPage";
import UserEditPage from "../pages/admin/UserEditPage";
import UserKpiTrendPage from "../pages/admin/UserKpiTrendPage";
import KpiDownloadPage from "../pages/admin/KpiDownloadPage";
import TemplatesPage from "../pages/admin/TemplatesPage";
import AuditLogPage from "../pages/admin/AuditLogPage";
import PlatformLoginPage from "../pages/platform/PlatformLoginPage";
import TenantsPage from "../pages/platform/TenantsPage";
import NotFoundPage from "../pages/NotFoundPage";

function HomeRedirect() {
  const { user } = useAuth();
  const target = user?.role === "super_admin" ? "/platform/tenants" : "/dashboard";
  return <Navigate to={target} replace />;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/platform/login" element={<PlatformLoginPage />} />
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

      {/* Platform Portal (platform-level Super Admin only) */}
      <Route
        path="/platform/tenants"
        element={
          <ProtectedRoute roles={["super_admin"]}>
            <TenantsPage />
          </ProtectedRoute>
        }
      />

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
