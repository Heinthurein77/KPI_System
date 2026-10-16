import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import EmptyState from "../ui/EmptyState";
import TeamOverview from "./TeamOverview";

// Review queue grouped by STATUS, not by employee: the first thing an admin
// sees is what needs their action, then what's waiting on others, then the
// settled history. "pending" holds exactly the statuses in the
// pendingStatuses prop (what THIS viewer must act on); a submission that's
// pending for someone else lands in "waiting".
const BUCKET_ORDER = ["pending", "waiting", "approved", "rejected", "draft"];

const BUCKET_STYLE = {
  waiting: {
    title: "Waiting on others",
    bar: "bg-violet-400",
    badge: "bg-violet-50 text-violet-700 ring-violet-200",
    dot: "bg-violet-400",
  },
  approved: {
    title: "Approved",
    bar: "bg-emerald-500",
    badge: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    dot: "bg-emerald-500",
  },
  rejected: {
    title: "Rejected",
    bar: "bg-red-400",
    badge: "bg-red-50 text-red-700 ring-red-200",
    dot: "bg-red-400",
  },
  draft: {
    title: "Drafts",
    bar: "bg-slate-300",
    badge: "bg-slate-100 text-slate-600 ring-slate-200",
    dot: "bg-slate-400",
  },
};

function bucketFor(status, pendingStatuses) {
  if (pendingStatuses.includes(status)) return "pending";
  if (status === "approved") return "approved";
  if (status === "rejected") return "rejected";
  if (status === "draft") return "draft";
  return "waiting";
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
  // Status groups the viewer explicitly opened/closed. "pending" starts open;
  // the rest start collapsed so the settled history doesn't push the work
  // queue off screen.
  const [openGroups, setOpenGroups] = useState(() => new Set(["pending"]));
  // Explicit per-employee toggles, keyed `${bucket}:${employeeId}`. Untouched
  // employees in the pending bucket start open (they need action); everywhere
  // else they start collapsed.
  const [openEmployees, setOpenEmployees] = useState(() => new Map());

  // bucket -> [{ employee, department, items }]
  const buckets = useMemo(() => {
    const grouped = { pending: new Map(), waiting: new Map(), approved: new Map(), rejected: new Map(), draft: new Map() };
    for (const s of data.submissions) {
      const b = bucketFor(s.status, pendingStatuses);
      const key = s.employee.id;
      if (!grouped[b].has(key)) {
        grouped[b].set(key, { employee: s.employee, department: s.department?.name ?? null, items: [] });
      }
      grouped[b].get(key).items.push(s);
    }
    const q = query.trim().toLowerCase();
    const result = {};
    for (const b of BUCKET_ORDER) {
      let emps = Array.from(grouped[b].values());
      if (q) {
        emps = emps.filter(
          (e) => e.employee.name.toLowerCase().includes(q) || e.department?.toLowerCase().includes(q)
        );
      }
      emps.sort((a, b2) => a.employee.name.localeCompare(b2.employee.name));
      result[b] = emps;
    }
    return result;
  }, [data.submissions, query, pendingStatuses]);

  // A new period/filter/department is a new view — drop manual toggles.
  useEffect(() => {
    setOpenGroups(new Set(["pending"]));
    setOpenEmployees(new Map());
  }, [data.active_year, data.active_period, data.status_filter, data.active_department_id]);

  function toggleGroup(bucket) {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(bucket)) next.delete(bucket);
      else next.add(bucket);
      return next;
    });
  }

  function toggleEmployee(bucket, employeeId, isCurrentlyOpen) {
    const key = `${bucket}:${employeeId}`;
    setOpenEmployees((prev) => new Map(prev).set(key, !isCurrentlyOpen));
  }

  const totalPeople = BUCKET_ORDER.reduce((n, b) => n + buckets[b].length, 0);
  const totalItems = BUCKET_ORDER.reduce((n, b) => n + buckets[b].reduce((m, e) => m + e.items.length, 0), 0);

  // The pending bucket reuses the role's accent (amber for dept admins, blue
  // for tenant admins) so each dashboard keeps its identity.
  const pendingMeta = { title: "Needs your review", bar: pendingDotClass, badge: pendingChipClass, dot: pendingDotClass };
  const groupMeta = (bucket) => (bucket === "pending" ? pendingMeta : BUCKET_STYLE[bucket]);

  return (
    <div>
      <TeamOverview
        data={data}
        pendingStatuses={pendingStatuses}
        pendingLabel={pendingLabel}
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
              {totalPeople} {totalPeople === 1 ? "person" : "people"} · {totalItems} {totalItems === 1 ? "item" : "items"}
            </span>
          </div>

          {totalPeople === 0 ? (
            <EmptyState title="No matches" message={`No one matches "${query}".`} />
          ) : (
            BUCKET_ORDER.map((bucket) => {
              const emps = buckets[bucket];
              const itemCount = emps.reduce((n, e) => n + e.items.length, 0);
              // Always show the pending bucket (its empty state is the "all
              // clear" confirmation); hide other buckets when empty.
              if (bucket !== "pending" && emps.length === 0) return null;
              const meta = groupMeta(bucket);
              const isGroupOpen = openGroups.has(bucket);
              return (
                <section
                  key={bucket}
                  className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden mb-4"
                >
                  <button
                    type="button"
                    onClick={() => toggleGroup(bucket)}
                    aria-expanded={isGroupOpen}
                    className="flex w-full items-center gap-3 px-5 py-4 text-left hover:bg-slate-50/60 transition"
                  >
                    <span className={`h-8 w-1.5 shrink-0 rounded-full ${meta.bar}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-[15px] font-semibold text-slate-900 tracking-tight">
                        {meta.title}
                      </span>
                      {bucket === "pending" && emps.length === 0 && (
                        <span className="block text-xs text-slate-500 mt-0.5">
                          Nothing waiting for you. Nice work.
                        </span>
                      )}
                    </span>
                    <span
                      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ring-1 ring-inset ${meta.badge}`}
                    >
                      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                      {itemCount} {itemCount === 1 ? "item" : "items"}
                    </span>
                    <svg
                      className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${isGroupOpen ? "rotate-180" : ""}`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                    </svg>
                  </button>

                  {isGroupOpen && (
                    <div className="border-t border-slate-100">
                      {emps.map(({ employee, department, items }) => {
                        const empKey = `${bucket}:${employee.id}`;
                        const isEmpOpen = openEmployees.get(empKey) ?? bucket === "pending";
                        return (
                          <div key={employee.id} className="border-b border-slate-100 last:border-b-0">
                            <button
                              type="button"
                              onClick={() => toggleEmployee(bucket, employee.id, isEmpOpen)}
                              aria-expanded={isEmpOpen}
                              className="flex w-full items-center gap-3 px-5 py-3.5 text-left hover:bg-slate-50/60 transition"
                            >
                              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-xs font-semibold text-white">
                                {employee.name[0]?.toUpperCase()}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm font-semibold text-slate-900">
                                  {employee.name}
                                </span>
                                <span className="block truncate text-xs text-slate-500">
                                  {showDepartmentColumn && `${department ?? "Unassigned"} · `}
                                  {items.length} {items.length === 1 ? "metric" : "metrics"}
                                  {data.employee_combined?.[String(employee.id)]?.attainment != null && (
                                    <> · {data.employee_combined[String(employee.id)].attainment.toFixed(0)}% combined</>
                                  )}
                                </span>
                              </span>
                              <svg
                                className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${isEmpOpen ? "rotate-90" : ""}`}
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="2"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                              </svg>
                            </button>

                            {isEmpOpen && (
                              <div className="bg-slate-50/50 px-5 pb-5 pt-1">
                                <div className="mb-3 flex items-center justify-between gap-3">
                                  <div className="min-w-0 flex-1">
                                    <CombinedScoreCard combined={data.employee_combined?.[String(employee.id)]} />
                                  </div>
                                  <Link
                                    to={`/admin/kpi-trend/${employee.id}`}
                                    title={`View ${employee.name}'s KPI trend`}
                                    className="ml-3 inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-800 hover:border-slate-300 transition"
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
                                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                                  {items.map((s) => (
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
                          </div>
                        );
                      })}
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
