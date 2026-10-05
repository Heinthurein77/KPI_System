import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AppShell from "../../components/layout/AppShell";
import KpiTrendChart from "../../components/ui/KpiTrendChart";
import KpiCard from "../../components/ui/KpiCard";
import CombinedScoreCard from "../../components/ui/CombinedScoreCard";
import EmployeeCombobox from "../../components/ui/EmployeeCombobox";
import { STYLES as STATUS_STYLES, LABELS as STATUS_LABELS } from "../../components/ui/statusMeta";
import { useAuth } from "../../context/AuthContext";
import { getUserKpiTrend, getUserSubmissionsForPeriod, listUsers } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

const STATUS_CHIP = {
  good: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-700 ring-amber-200",
  critical: "bg-red-50 text-red-700 ring-red-200",
};
const STATUS_LABEL = { good: "On Target", warning: "Near Target", critical: "Below Target" };

function SummaryCard({ label, value, detail, accent, icon }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className={`pointer-events-none absolute -top-8 -right-8 h-24 w-24 rounded-full blur-2xl ${accent.glow}`}></div>
      <div className="relative flex items-start gap-3">
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${accent.iconBg}`}>
          <svg className={`h-4.5 w-4.5 ${accent.iconText}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            {icon}
          </svg>
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">{label}</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 leading-none">{value}</p>
          <p className="mt-1.5 text-xs text-slate-500 truncate">{detail}</p>
        </div>
      </div>
    </div>
  );
}

const ACCENT = {
  peak: { glow: "bg-emerald-400/25", iconBg: "bg-emerald-50", iconText: "text-emerald-600" },
  lowest: { glow: "bg-red-400/25", iconBg: "bg-red-50", iconText: "text-red-600" },
  average: { glow: "bg-brand-400/25", iconBg: "bg-brand-50", iconText: "text-brand-600" },
};

const ICON = {
  peak: <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0-5.94-2.281m5.94 2.28-2.28 5.941" />,
  lowest: <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 6 9 12.75l4.286-4.286a11.948 11.948 0 0 1 4.306 6.43l.776 2.898m0 0 3.182-5.511m-3.182 5.51-5.511-3.181" />,
  average: <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />,
};

export default function UserKpiTrendPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const isDeptAdmin = currentUser.role === "dept_admin";

  const [roster, setRoster] = useState(null);
  const [year, setYear] = useState(undefined);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const [expandedMonth, setExpandedMonth] = useState(null);
  const [monthDetail, setMonthDetail] = useState({});
  const [monthDetailLoading, setMonthDetailLoading] = useState(false);
  const [monthDetailError, setMonthDetailError] = useState(null);
  const trendRequestRef = useRef(0);

  // The roster of employees this admin can browse trends for — same scoping
  // as the "Trend" icon's visibility on the Users page.
  useEffect(() => {
    listUsers()
      .then((d) => {
        const selectable = d.users
          .filter((u) => !isDeptAdmin || u.role === "employee")
          .sort((a, b) => a.name.localeCompare(b.name));
        setRoster(selectable);
      })
      .catch((err) => setError(getErrorMessage(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The sidebar's "KPI Trend" link has no employee in mind — land on the
  // first person in the roster once it's loaded.
  useEffect(() => {
    if (!id && roster && roster.length > 0) {
      navigate(`/admin/kpi-trend/${roster[0].id}`, { replace: true });
    }
  }, [id, roster, navigate]);

  useEffect(() => {
    if (!id) return;
    setError(null);
    // Guard against out-of-order responses: switching employee (or year)
    // again before this request lands must not let a stale response for the
    // previous employee overwrite the newer one.
    const requestId = ++trendRequestRef.current;
    getUserKpiTrend(id, year)
      .then((d) => {
        if (trendRequestRef.current !== requestId) return;
        setData(d);
        // The backend falls back to the employee's most recent year if the
        // requested one has no data, so keep the selector in sync with reality.
        setYear(d.year);
      })
      .catch((err) => {
        if (trendRequestRef.current !== requestId) return;
        setError(getErrorMessage(err));
      });
  }, [id, year]);

  // Switching employee or year invalidates any expanded month's cached detail.
  useEffect(() => {
    setExpandedMonth(null);
    setMonthDetail({});
    setMonthDetailError(null);
  }, [id, year]);

  function toggleMonth(point) {
    if (point.total_count === 0) return;
    const month = point.month_or_quarter;
    if (expandedMonth === month) {
      setExpandedMonth(null);
      return;
    }
    setExpandedMonth(month);
    if (!monthDetail[month]) {
      setMonthDetailLoading(true);
      setMonthDetailError(null);
      getUserSubmissionsForPeriod(id, point.year, month)
        .then((d) => setMonthDetail((prev) => ({ ...prev, [month]: d.submissions })))
        .catch((err) => setMonthDetailError(getErrorMessage(err)))
        .finally(() => setMonthDetailLoading(false));
    }
  }

  // Clicking a point on the chart shows that one month's KPI result for this
  // employee only (same as clicking its row below) — not the full multi-employee
  // dashboard, which would lose the single-employee, single-month focus this
  // page is for. Only one month is expanded at a time.
  function handleChartPointClick(point) {
    toggleMonth(point);
    document
      .getElementById(`month-row-${point.month_or_quarter}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const currentIndex = useMemo(
    () => (roster ? roster.findIndex((u) => String(u.id) === String(id)) : -1),
    [roster, id]
  );

  function goTo(userId) {
    setData(null);
    navigate(`/admin/kpi-trend/${userId}`);
  }

  function goRelative(offset) {
    if (!roster || currentIndex === -1) return;
    const next = roster[currentIndex + offset];
    if (next) goTo(next.id);
  }

  const fmtPct = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(0)}%`);

  return (
    <AppShell title="KPI Trend">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => goRelative(-1)}
            disabled={!roster || currentIndex <= 0}
            title="Previous team member"
            className="inline-flex items-center justify-center h-9 w-9 rounded-lg border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition disabled:opacity-40 disabled:hover:bg-white"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
            </svg>
          </button>

          <EmployeeCombobox roster={roster} value={id} onChange={goTo} />

          <button
            type="button"
            onClick={() => goRelative(1)}
            disabled={!roster || currentIndex === -1 || currentIndex >= roster.length - 1}
            title="Next team member"
            className="inline-flex items-center justify-center h-9 w-9 rounded-lg border border-slate-300 bg-white text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition disabled:opacity-40 disabled:hover:bg-white"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
            </svg>
          </button>

          {roster && currentIndex !== -1 && (
            <span className="text-xs text-slate-400 tabular-nums">
              {currentIndex + 1} of {roster.length}
            </span>
          )}

          {data && (
            <select
              value={data.year}
              onChange={(e) => setYear(Number(e.target.value))}
              title="Year"
              className="ml-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            >
              {data.available_years.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          )}
        </div>

        <Link
          to="/admin/users"
          className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition"
        >
          Back to Users
        </Link>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {roster && roster.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 py-16 text-center text-sm text-slate-400">
          No employees to show KPI trends for yet.
        </div>
      )}

      {data && (
        <>
          <div className="mb-5">
            <h2 className="text-base font-semibold text-slate-900">{data.employee.name}</h2>
            <p className="text-sm text-slate-500">
              {data.employee.email} · {data.year}
            </p>
          </div>

          <div className="mb-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
            <SummaryCard
              label="Peak month"
              value={fmtPct(data.peak?.attainment)}
              detail={data.peak ? `${data.peak.month_or_quarter} ${data.year}` : "No finalized months yet"}
              accent={ACCENT.peak}
              icon={ICON.peak}
            />
            <SummaryCard
              label="Lowest month"
              value={fmtPct(data.lowest?.attainment)}
              detail={data.lowest ? `${data.lowest.month_or_quarter} ${data.year}` : "No finalized months yet"}
              accent={ACCENT.lowest}
              icon={ICON.lowest}
            />
            <SummaryCard
              label="Annual average"
              value={fmtPct(data.annual_average)}
              detail={
                data.annual_average !== null
                  ? `Across ${data.points.filter((p) => p.attainment !== null).length} finalized month${
                      data.points.filter((p) => p.attainment !== null).length === 1 ? "" : "s"
                    }`
                  : "No finalized months yet"
              }
              accent={ACCENT.average}
              icon={ICON.average}
            />
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-6">
            <h3 className="text-sm font-semibold text-slate-800 mb-4">Combined KPI attainment, January – December {data.year}</h3>
            <KpiTrendChart points={data.points} onPointClick={handleChartPointClick} />
          </div>

          <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Period
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Attainment
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                    Metrics finalized
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.points.map((p) => {
                  const clickable = p.total_count > 0;
                  const isOpen = expandedMonth === p.month_or_quarter;
                  return (
                    <Fragment key={`${p.year}-${p.month_or_quarter}`}>
                      <tr
                        id={`month-row-${p.month_or_quarter}`}
                        onClick={() => toggleMonth(p)}
                        className={`${clickable ? "cursor-pointer hover:bg-slate-50/80" : ""} ${
                          p.total_count === 0 ? "text-slate-400" : ""
                        } ${isOpen ? "bg-slate-50/80" : ""}`}
                      >
                        <td className="px-6 py-3 text-sm font-medium text-slate-800">
                          <span className="inline-flex items-center gap-1.5">
                            {clickable && (
                              <svg
                                className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${isOpen ? "rotate-90" : ""}`}
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="2"
                              >
                                <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                              </svg>
                            )}
                            {p.month_or_quarter} {p.year}
                          </span>
                        </td>
                        <td className="px-6 py-3 text-sm tabular-nums text-slate-700">{fmtPct(p.attainment)}</td>
                        <td className="px-6 py-3 text-sm">
                          {p.status ? (
                            <span
                              className={`inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider ring-1 ring-inset ${STATUS_CHIP[p.status]}`}
                            >
                              {STATUS_LABEL[p.status]}
                            </span>
                          ) : (
                            <span className="text-slate-400">{p.total_count === 0 ? "No submissions" : "—"}</span>
                          )}
                        </td>
                        <td className="px-6 py-3 text-sm text-slate-500">
                          {p.total_count === 0 ? "—" : `${p.scored_count} of ${p.total_count}`}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td colSpan={4} className="bg-slate-50/60 px-6 py-5">
                            {monthDetailLoading && !monthDetail[p.month_or_quarter] ? (
                              <p className="text-sm text-slate-400">Loading…</p>
                            ) : monthDetailError ? (
                              <p className="text-sm text-red-600">{monthDetailError}</p>
                            ) : (
                              <>
                                <div className="mb-4 flex flex-wrap items-center gap-2">
                                  <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide mr-1">
                                    {(monthDetail[p.month_or_quarter] ?? []).length} metric
                                    {(monthDetail[p.month_or_quarter] ?? []).length !== 1 ? "s" : ""}
                                  </span>
                                  {Object.entries(
                                    (monthDetail[p.month_or_quarter] ?? []).reduce((acc, s) => {
                                      acc[s.status] = (acc[s.status] || 0) + 1;
                                      return acc;
                                    }, {})
                                  ).map(([status, count]) => (
                                    <span
                                      key={status}
                                      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLES[status] || "bg-slate-100 text-slate-600"}`}
                                    >
                                      {count} {STATUS_LABELS[status] || status}
                                    </span>
                                  ))}
                                </div>

                                <div className="mb-5">
                                  <CombinedScoreCard combined={p.attainment !== null ? p : null} />
                                </div>

                                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                                  {(monthDetail[p.month_or_quarter] ?? []).map((s) => (
                                    <KpiCard key={s.id} submission={s} />
                                  ))}
                                </div>
                              </>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AppShell>
  );
}
