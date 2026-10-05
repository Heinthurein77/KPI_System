import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

export default function ErrorCard({ statusCode, heading, message }) {
  const { user } = useAuth();
  // super_admin has no /dashboard (DashboardPage renders nothing for that
  // role — it only branches on employee/dept_admin/tenant_admin), so send
  // them back to the one page their role actually has, same as
  // AppRoutes.jsx's HomeRedirect.
  const backTo = user?.role === "super_admin" ? "/platform/tenants" : "/dashboard";

  return (
    <div className="rounded-2xl bg-white shadow-sm border border-slate-200 px-8 py-10 max-w-md w-full text-center">
      <p className="text-sm font-semibold text-brand-600">Error {statusCode}</p>
      <h1 className="mt-2 text-xl font-semibold text-slate-900">{heading}</h1>
      <p className="mt-2 text-sm text-slate-500">{message}</p>
      <Link
        to={backTo}
        className="mt-6 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 transition"
      >
        Back to {user?.role === "super_admin" ? "Tenants" : "Dashboard"}
      </Link>
    </div>
  );
}
