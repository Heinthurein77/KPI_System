import { useEffect, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import { useAuth } from "../../context/AuthContext";
import { createCustomTemplate, createTemplate, deleteTemplate, listTemplates } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

export default function TemplatesPage() {
  const { user } = useAuth();
  const isDeptAdmin = user.role === "dept_admin";

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const [metricName, setMetricName] = useState("");
  const [target, setTarget] = useState("");
  const [weight, setWeight] = useState("");
  const [departmentId, setDepartmentId] = useState("");

  const [customEmployeeId, setCustomEmployeeId] = useState("");
  const [customMetricName, setCustomMetricName] = useState("");
  const [customTarget, setCustomTarget] = useState("");
  const [customWeight, setCustomWeight] = useState("");
  const [customYear, setCustomYear] = useState(null);
  const [customPeriod, setCustomPeriod] = useState(null);

  function load() {
    listTemplates().then((d) => {
      setData(d);
      setCustomYear((y) => y ?? d.default_year);
      setCustomPeriod((p) => p ?? d.default_period);
    });
  }

  useEffect(load, []);

  async function handleCreateTemplate(e) {
    e.preventDefault();
    setError(null);
    try {
      await createTemplate({
        metric_name: metricName.trim(),
        target: Number(target),
        weight: Number(weight),
        department_id: isDeptAdmin ? undefined : departmentId || undefined,
      });
      setMetricName("");
      setTarget("");
      setWeight("");
      load();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function handleCreateCustom(e) {
    e.preventDefault();
    setError(null);
    try {
      await createCustomTemplate({
        employee_id: Number(customEmployeeId),
        metric_name: customMetricName.trim(),
        target: Number(customTarget),
        weight: Number(customWeight),
        year: Number(customYear),
        period: customPeriod,
      });
      setCustomEmployeeId("");
      setCustomMetricName("");
      setCustomTarget("");
      setCustomWeight("");
      load();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function handleDelete(t) {
    const message = isDeptAdmin
      ? "Delete this metric?"
      : `Delete "${t.metric_name}"?\n\nThis also permanently removes all KPI submissions recorded against it. This cannot be undone.`;
    if (!confirm(message)) return;
    setError(null);
    try {
      await deleteTemplate(t.id);
      load();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  if (!data) return <AppShell title="KPI Metrics">{null}</AppShell>;

  const yearOptions = Array.from({ length: 3 }, (_, i) => data.default_year - 1 + i);

  return (
    <AppShell title={isDeptAdmin ? "My Department Metrics" : "KPI Metric Templates"}>
      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Metric</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Target</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Weight</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Scope</th>
                  <th className="px-6 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.kpi_templates.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-400">
                      No KPI metrics configured yet.
                    </td>
                  </tr>
                ) : (
                  data.kpi_templates.map((t) => (
                    <tr key={t.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4 text-sm font-medium text-slate-800">{t.metric_name}</td>
                      <td className="px-6 py-4 text-sm text-slate-500 tabular-nums">{t.target}</td>
                      <td className="px-6 py-4 text-sm text-slate-500 tabular-nums">{t.weight}</td>
                      <td className="px-6 py-4 text-sm">
                        {t.is_custom ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-violet-50 px-2.5 py-1 text-xs font-semibold text-violet-700 ring-1 ring-inset ring-violet-200">
                            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
                              />
                            </svg>
                            {t.employee?.name} · {t.locked_period} {t.locked_year}
                          </span>
                        ) : (
                          <span className="text-slate-600">{t.department ? t.department.name : "All Departments"}</span>
                        )}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {!isDeptAdmin || t.department_id === user.department_id ? (
                            <button
                              type="button"
                              onClick={() => handleDelete(t)}
                              title={!isDeptAdmin ? "Delete (also removes its KPI submission history)" : undefined}
                              className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 hover:border-red-300 transition"
                            >
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="m14.74 9-.346 9m-4.788 0L9.26 9M19.228 5.79c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                                />
                              </svg>
                              Delete
                            </button>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 9.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z"
                                />
                              </svg>
                              Company-wide
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-6">
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Add Metric</h2>
            <p className="text-xs text-slate-500 mb-3">
              Applies every month to {isDeptAdmin ? "your whole team" : "the selected scope"}.
            </p>
            <form onSubmit={handleCreateTemplate} className="space-y-3">
              <input
                type="text"
                required
                placeholder="e.g. Customer Satisfaction Score"
                value={metricName}
                onChange={(e) => setMetricName(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="number"
                  step="0.1"
                  required
                  placeholder="Target"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                />
                <input
                  type="number"
                  step="0.1"
                  required
                  placeholder="Weight"
                  value={weight}
                  onChange={(e) => setWeight(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                />
              </div>
              {isDeptAdmin ? (
                <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
                  Department: <span className="font-medium text-slate-700">{user.department?.name}</span>
                </p>
              ) : (
                <select
                  value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                >
                  <option value="">All Departments</option>
                  {data.departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="submit"
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                Create Metric
              </button>
            </form>
          </div>

          <div className="bg-white rounded-2xl border border-violet-200 shadow-sm p-6">
            <div className="flex items-center gap-2 mb-1">
              <svg className="h-4 w-4 text-violet-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z"
                />
              </svg>
              <h2 className="text-sm font-semibold text-slate-900">Add Custom KPI</h2>
            </div>
            <p className="text-xs text-slate-500 mb-4">
              A one-off metric for a single {isDeptAdmin ? "employee" : "person"}, for one month only — it won't
              repeat in other periods.
              {!isDeptAdmin && " A Dept Admin's own KPI skips department review and goes straight to you for final approval."}
            </p>
            <form onSubmit={handleCreateCustom} className="space-y-3">
              <select
                required
                value={customEmployeeId}
                onChange={(e) => setCustomEmployeeId(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              >
                <option value="" disabled>
                  {isDeptAdmin ? "Select employee…" : "Select person…"}
                </option>
                {data.team.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name}
                    {!isDeptAdmin && ` — ${member.role === "dept_admin" ? "Dept Admin" : "Employee"}`}
                  </option>
                ))}
              </select>
              <input
                type="text"
                required
                placeholder="e.g. Q3 Product Launch Readiness"
                value={customMetricName}
                onChange={(e) => setCustomMetricName(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
              <div className="grid grid-cols-2 gap-3">
                <input
                  type="number"
                  step="0.1"
                  required
                  placeholder="Target"
                  value={customTarget}
                  onChange={(e) => setCustomTarget(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                />
                <input
                  type="number"
                  step="0.1"
                  required
                  placeholder="Weight"
                  value={customWeight}
                  onChange={(e) => setCustomWeight(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <select
                  value={customYear ?? ""}
                  onChange={(e) => setCustomYear(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                >
                  {yearOptions.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
                <select
                  value={customPeriod ?? ""}
                  onChange={(e) => setCustomPeriod(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                >
                  {data.months.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="submit"
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-violet-700 transition"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                Add Custom KPI
              </button>
            </form>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
