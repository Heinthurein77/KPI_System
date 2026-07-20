import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import EmptyState from "../ui/EmptyState";

function groupByEmployee(submissions) {
  const groups = new Map();
  for (const s of submissions) {
    const key = s.employee.name;
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
  const groups = groupByEmployee(data.submissions);

  return (
    <div>
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
        Array.from(groups.entries()).map(([employeeName, group]) => {
          const pendingCount = group.filter((s) => pendingStatuses.includes(s.status)).length;
          const first = group[0];
          return (
            <section key={employeeName} className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden mb-7">
              <div className="flex items-center justify-between gap-3 px-6 py-5 border-b border-slate-100">
                <div className="flex items-center gap-3.5">
                  <div className="h-10 w-10 shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center text-sm font-semibold shadow-sm">
                    {employeeName[0]?.toUpperCase()}
                  </div>
                  <div>
                    <h3 className="text-[15px] font-semibold text-slate-900 tracking-tight">{employeeName}</h3>
                    <p className="text-xs text-slate-500">
                      {showDepartmentColumn && (first.department ? `${first.department.name} · ` : "Unassigned · ")}
                      {group.length} metric{group.length !== 1 ? "s" : ""} · {data.active_period} {data.active_year}
                    </p>
                  </div>
                </div>
                {pendingCount > 0 && (
                  <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ring-1 ring-inset ${pendingChipClass}`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${pendingDotClass}`}></span>
                    {pendingCount} {pendingLabel}
                  </span>
                )}
              </div>

              <div className="px-6 py-5 bg-slate-50/60 border-b border-slate-100">
                <CombinedScoreCard combined={data.employee_combined[employeeName]} />
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
            </section>
          );
        })
      )}
    </div>
  );
}
