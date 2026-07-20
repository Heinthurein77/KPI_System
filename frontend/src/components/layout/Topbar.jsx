import { useAuth } from "../../context/AuthContext";

export default function Topbar({ title }) {
  const { user, logout } = useAuth();

  return (
    <header className="flex items-center justify-between border-b border-slate-200 bg-white/80 backdrop-blur px-4 md:px-8 py-4 sticky top-0 z-10">
      <div>
        <h1 className="text-lg font-semibold text-slate-900 tracking-tight">{title}</h1>
        {user.department && <p className="text-xs text-slate-500 mt-0.5">{user.department.name} Department</p>}
      </div>
      <button
        type="button"
        onClick={logout}
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-600 shadow-sm hover:bg-slate-50 hover:text-slate-900 transition"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M17 16l4-4m0 0-4-4m4 4H7m6 5v1a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3h4a3 3 0 0 1 3 3v1"
          />
        </svg>
        Sign out
      </button>
    </header>
  );
}
