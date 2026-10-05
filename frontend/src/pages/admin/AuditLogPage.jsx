import { useEffect, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageLoading from "../../components/ui/PageLoading";
import EmptyState from "../../components/ui/EmptyState";
import { getAuditLog } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

const ROLE_LABELS = {
  super_admin: "Super Admin",
  tenant_admin: "Tenant Admin",
  dept_admin: "Dept Admin",
  employee: "Employee",
};

const ACTION_META = {
  kpi_dept_score_saved: { label: "Score Saved", style: "bg-slate-100 text-slate-700" },
  kpi_dept_approved: { label: "Dept Approved", style: "bg-amber-100 text-amber-700" },
  kpi_final_approved: { label: "Final Approved", style: "bg-emerald-100 text-emerald-700" },
  kpi_overridden: { label: "Overridden", style: "bg-purple-100 text-purple-700" },
  kpi_rejected: { label: "Rejected", style: "bg-red-100 text-red-700" },
  kpi_template_created: { label: "Metric Created", style: "bg-blue-100 text-blue-700" },
  kpi_template_deleted: { label: "Metric Deleted", style: "bg-red-100 text-red-700" },
};

function formatTimestamp(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function AuditLogPage() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    getAuditLog().then(setEntries).catch((err) => setError(getErrorMessage(err)));
  }, []);

  if (error) {
    return (
      <AppShell title="Audit Log">
        <div className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      </AppShell>
    );
  }

  if (!entries) return <AppShell title="Audit Log"><PageLoading /></AppShell>;

  const q = query.trim().toLowerCase();
  const filtered = q
    ? entries.filter(
        (e) =>
          e.actor_name.toLowerCase().includes(q) ||
          e.entity_label.toLowerCase().includes(q) ||
          e.summary.toLowerCase().includes(q)
      )
    : entries;

  return (
    <AppShell title="Audit Log">
      <p className="text-sm text-slate-500 mb-4">
        Every KPI score change, approval, override, rejection, and metric edit — who did it and when.
      </p>

      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="relative w-full max-w-xs">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
            />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by person or KPI…"
            className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
          />
        </div>
        <span className="shrink-0 text-xs text-slate-400 tabular-nums">
          {filtered.length} of {entries.length}
        </span>
      </div>

      {entries.length === 0 ? (
        <EmptyState title="No activity yet" message="KPI changes, approvals, and overrides will show up here." />
      ) : filtered.length === 0 ? (
        <EmptyState title="No matches" message={`No activity matches "${query}".`} />
      ) : (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    When
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Who
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Action
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    KPI / Target
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Change
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((e) => {
                  const meta = ACTION_META[e.action] || { label: e.action, style: "bg-slate-100 text-slate-600" };
                  return (
                    <tr key={e.id} className="hover:bg-slate-50/80 transition-colors align-top">
                      <td className="px-6 py-4 text-xs text-slate-500 whitespace-nowrap tabular-nums">
                        {formatTimestamp(e.created_at)}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        <div className="flex items-center gap-2">
                          <div className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center text-[11px] font-semibold">
                            {e.actor_name[0]?.toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <p className="font-medium text-slate-800 truncate">{e.actor_name}</p>
                            <p className="text-[11px] text-slate-400">{ROLE_LABELS[e.actor_role] || e.actor_role}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold whitespace-nowrap ${meta.style}`}
                        >
                          {meta.label}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm font-medium text-slate-700 max-w-[220px] truncate" title={e.entity_label}>
                        {e.entity_label}
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-600 max-w-md">{e.summary}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </AppShell>
  );
}
