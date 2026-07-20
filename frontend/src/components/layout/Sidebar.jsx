import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

function NavLink({ to, active, children, icon }) {
  return (
    <Link
      to={to}
      className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
        active ? "bg-brand-600/90 text-white shadow-sm" : "text-slate-300 hover:bg-white/5 hover:text-white"
      }`}
    >
      <svg className="h-[18px] w-[18px] shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
        {icon}
      </svg>
      {children}
    </Link>
  );
}

export default function Sidebar() {
  const { user } = useAuth();
  const { pathname } = useLocation();

  return (
    <aside className="hidden md:flex md:w-64 md:flex-col bg-slate-900 text-slate-200">
      <div className="flex items-center gap-2.5 px-6 py-5">
        <div className="h-8 w-8 shrink-0 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center font-bold text-white shadow-sm shadow-brand-900/40">
          K
        </div>
        <div className="min-w-0 leading-tight">
          <p className="text-[15px] font-bold text-white tracking-tight truncate">abcMIB</p>
          <p className="text-[11px] text-slate-400 truncate">KPI Approval System</p>
        </div>
      </div>
      <div className="mx-4 border-t border-white/10"></div>

      <nav className="flex-1 px-3 py-5 space-y-0.5">
        <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">Workspace</p>
        <NavLink
          to="/dashboard"
          active={pathname === "/dashboard"}
          icon={<path strokeLinecap="round" strokeLinejoin="round" d="M3 13h8V3H3v10Zm10 8h8V11h-8v10Zm0-18v6h8V3h-8ZM3 21h8v-6H3v6Z" />}
        >
          Dashboard
        </NavLink>
        {user.role === "dept_admin" && (
          <NavLink
            to="/my-kpi"
            active={pathname === "/my-kpi"}
            icon={
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
              />
            }
          >
            My KPI
          </NavLink>
        )}

        {(user.role === "super_admin" || user.role === "dept_admin") && (
          <p className="px-3 pt-4 pb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">Manage</p>
        )}
        {user.role === "super_admin" && (
          <NavLink
            to="/admin/departments"
            active={pathname.startsWith("/admin/departments")}
            icon={<path strokeLinecap="round" strokeLinejoin="round" d="M3 21V7l9-4 9 4v14M9 21v-6h6v6" />}
          >
            Departments
          </NavLink>
        )}
        {(user.role === "super_admin" || user.role === "dept_admin") && (
          <>
            <NavLink
              to="/admin/users"
              active={pathname.startsWith("/admin/users")}
              icon={
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M17 20h5v-2a4 4 0 0 0-3-3.87M9 20H4v-2a4 4 0 0 1 3-3.87m5-8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm6 3a4 4 0 1 1-2.6-3.75"
                />
              }
            >
              {user.role === "dept_admin" ? "My Team" : "Users"}
            </NavLink>
            <NavLink
              to="/admin/templates"
              active={pathname.startsWith("/admin/templates")}
              icon={
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12h6m-6 3.75h6M9 8.25h1.5M8.25 6h7.5A2.25 2.25 0 0 1 18 8.25v10.5A2.25 2.25 0 0 1 15.75 21H8.25A2.25 2.25 0 0 1 6 18.75V8.25A2.25 2.25 0 0 1 8.25 6Zm2.4-3h2.7a.9.9 0 0 1 .9.9v.6a.9.9 0 0 1-.9.9h-2.7a.9.9 0 0 1-.9-.9v-.6a.9.9 0 0 1 .9-.9Z"
                />
              }
            >
              KPI Metrics
            </NavLink>
          </>
        )}
      </nav>

      <div className="px-3 py-4">
        <div className="mx-1 mb-3 border-t border-white/10"></div>
        <div className="flex items-center gap-2.5 rounded-lg px-2 py-2">
          <div className="h-8 w-8 shrink-0 rounded-full bg-slate-700 flex items-center justify-center text-xs font-semibold text-white">
            {user.name ? user.name[0].toUpperCase() : ""}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-white truncate">{user.name}</p>
            <p className="text-[11px] text-slate-400 truncate">{user.email}</p>
          </div>
        </div>
        <span className="mt-2 inline-flex items-center rounded-full bg-brand-500/15 px-2.5 py-1 text-[10px] font-semibold text-brand-200 uppercase tracking-wider">
          {user.role.replace(/_/g, " ")}
        </span>
      </div>
    </aside>
  );
}
