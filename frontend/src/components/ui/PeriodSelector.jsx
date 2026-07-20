import { useState } from "react";

const DEFAULT_MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

export default function PeriodSelector({
  activeYear,
  activePeriod,
  statusFilter,
  statuses,
  departments,
  activeDepartmentId,
  months,
  onApply,
}) {
  const monthNames = months || DEFAULT_MONTHS;
  const [year, setYear] = useState(activeYear);
  const [period, setPeriod] = useState(activePeriod);
  const [status, setStatus] = useState(statusFilter || "");
  const [departmentId, setDepartmentId] = useState(activeDepartmentId ?? "");

  function submit(e) {
    e.preventDefault();
    onApply({
      year: Number(year),
      period,
      status_filter: status || undefined,
      department_id: departmentId || undefined,
    });
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-wrap items-end gap-4 rounded-xl border border-slate-200 bg-white shadow-sm px-5 py-4 mb-6"
    >
      <div>
        <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Year</label>
        <select
          value={year}
          onChange={(e) => setYear(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
        >
          {Array.from({ length: 4 }, (_, i) => activeYear - 2 + i).map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Month</label>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
        >
          {monthNames.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>
      {statuses && (
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Status</label>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
          >
            <option value="">All statuses</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </select>
        </div>
      )}
      {departments && (
        <div>
          <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
            Department
          </label>
          <select
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
          >
            <option value="">All departments</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </div>
      )}
      <button
        type="submit"
        className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 transition ml-auto"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M6.5 12h11M10 18h4" />
        </svg>
        Apply
      </button>
    </form>
  );
}
