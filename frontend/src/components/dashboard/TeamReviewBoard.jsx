import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import EmptyState from "../ui/EmptyState";
import TeamOverview from "./TeamOverview";

function groupByEmployee(submissions) {
  // Keyed by employee id, not name — two employees can share a display name,
  // and a name key would silently merge their submissions into one section.
  const groups = new Map();
  for (const s of submissions) {
    const key = s.employee.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  return groups;
}

export default function TeamReviewBoard({
  data,
  onApply,
  pendingStatuses,
  pendingLabel,
  pendingChipClass,
  pendingDotClass,
  showDepartmentColumn = false,
  onDeptSave,
  onDeptApprove,
  onFinalApprove,
  onOverride,
  onReject,
}) {
  const [query, setQuery] = useState("");
  // Only records an explicit click — open/collapsed state a person needing
  // action stays open by default, and someone already fully reviewed
  // collapses to a single scannable row, WITHOUT this map ever saying so.
  // That default is derived fresh from pendingCount on every render instead
  // of being computed once and stored, so approving someone's last pending
  // item collapses them immediately rather than only after the period/filter
  // changes and this effect re-runs.
  const [manualOverride, setManualOverride] = useState(new Map());

  const groups = useMemo(() => groupByEmployee(data.submissions), [data.submissions]);

  useEffect(() => {
    setManualOverride(new Map());
  }, [data.active_year, data.active_period, data.status_filter, data.active_department_id]);

  function toggle(employeeId, isCurrentlyOpen) {
    setManualOverride((prev) => new Map(prev).set(employeeId, !isCurrentlyOpen));
  }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return Array.from(groups.entries())
      .map(([employeeId, group]) => ({
        employeeId,
        group,
        name: group[0].employee.name,
        department: group[0].department?.name ?? null,
        pendingCount: group.filter((s) => pendingStatuses.includes(s.status)).length,
      }))
      .filter(
        (r) => !q || r.name.toLowerCase().includes(q) || r.department?.toLowerCase().includes(q)
      )
      .sort((a, b) => {
        // People needing action first, then alphabetical within each group —
        // this is a queue to work through, not just a report to read.
        if ((b.pendingCount > 0) !== (a.pendingCount > 0)) return b.pendingCount > 0 ? 1 : -1;
        return a.name.localeCompare(b.name);
      });
  }, [groups, query, pendingStatuses]);

  return (
    <div>
      <TeamOverview
        data={data}
        pendingStatuses={pendingStatuses}
        pendingLabel={pendingLabel}
        showDepartmentColumn={showDepartmentColumn}
      />

      <PeriodSelector
        activeYear={data.active_year}
        activePeriod={data.active_period}
        statusFilter={data.status_filter}
        statuses={data.statuses}
        departments={showDepartmentColumn ? data.departments : undefined}
        activeDepartmentId={data.active_department_id}
        months={data.months}
        onApply={onApply}
      />

      {data.submissions.length === 0 ? (
        <EmptyState
          title="No KPI submissions found"
          message={`Try a different month${showDepartmentColumn ? ", department," : ""} or clear the status filter.`}
        />
      ) : (
        <>
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
                placeholder={showDepartmentColumn ? "Filter by name or department…" : "Filter by name…"}
                className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>
            <span className="shrink-0 text-xs text-slate-400 tabular-nums">
              {rows.length} of {groups.size}
            </span>
          </div>

          {rows.length === 0 ? (
            <EmptyState title="No matches" message={`No one matches "${query}".`} />
          ) : (
            rows.map(({ employeeId, group, name: employeeName, department, pendingCount }) => {
              const isOpen = manualOverride.get(employeeId) ?? pendingCount > 0;
              const first = group[0];
              return (
                <section
                  key={employeeId}
                  className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden mb-4"
                >
                  <button
                    type="button"
                    onClick={() => toggle(employeeId, isOpen)}
                    aria-expanded={isOpen}
                    className="flex w-full items-center justify-between gap-3 px-6 py-4 text-left hover:bg-slate-50/60 transition"
                  >
                    <div className="flex min-w-0 items-center gap-3.5">
                      <div className="h-10 w-10 shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center text-sm font-semibold shadow-sm">
                        {employeeName[0]?.toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-[15px] font-semibold text-slate-900 tracking-tight truncate">
                          {employeeName}
                        </h3>
                        <p className="text-xs text-slate-500 truncate">
                          {showDepartmentColumn && `${department ?? "Unassigned"} · `}
                          {group.length} metric{group.length !== 1 ? "s" : ""}
                          {!isOpen && data.employee_combined[String(employeeId)] && (
                            <> · {data.employee_combined[String(employeeId)].attainment.toFixed(0)}% combined</>
                          )}
                        </p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {pendingCount > 0 && (
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ring-1 ring-inset ${pendingChipClass}`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${pendingDotClass}`}></span>
                          {pendingCount} {pendingLabel}
                        </span>
                      )}
                      {pendingCount === 0 && (
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-200">
                          All reviewed
                        </span>
                      )}
                      <svg
                        className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                      </svg>
                    </div>
                  </button>

                  {isOpen && (
                    <div className="border-t border-slate-100">
                      <div className="flex items-center justify-between gap-3 px-6 py-3 bg-slate-50/60 border-b border-slate-100">
                        <div className="flex-1">
                          <CombinedScoreCard combined={data.employee_combined[String(employeeId)]} />
                        </div>
                        <Link
                          to={`/admin/kpi-trend/${first.employee.id}`}
                          title={`View ${employeeName}'s KPI trend`}
                          className="ml-4 inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-800 hover:border-slate-300 transition"
                        >
                          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M3 3v16.5A1.5 1.5 0 0 0 4.5 21H21M7 15.5 11 10l3 3 5.5-6.5"
                            />
                          </svg>
                          KPI Trend
                        </Link>
                      </div>

                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 p-6">
                        {group.map((s) => (
                          <KpiCard
                            key={s.id}
                            submission={s}
                            onDeptSave={onDeptSave}
                            onDeptApprove={onDeptApprove}
                            onFinalApprove={onFinalApprove}
                            onOverride={onOverride}
                            onReject={onReject}
                          />
                        ))}
                      </div>
                    </div>
                  )}
                </section>
              );
            })
          )}
        </>
      )}
    </div>
  );
}
