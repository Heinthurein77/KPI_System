import { useEffect, useMemo, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageLoading from "../../components/ui/PageLoading";
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { createCustomTemplate, deleteTemplate, listTemplates, updateTemplateWeight } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

export default function TemplatesPage() {
  const { user } = useAuth();
  const toast = useToast();
  const isDeptAdmin = user.role === "dept_admin";

  const [data, setData] = useState(null);

  // ── New Custom KPI form state ──
  const [customEmployeeId, setCustomEmployeeId] = useState("");
  const [customMetricName, setCustomMetricName] = useState("");
  const [customTarget, setCustomTarget] = useState("");
  const [customWeight, setCustomWeight] = useState("");
  const [weightError, setWeightError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // ── Inline rebalancer: tracks per-template weight overrides ──
  // { [templateId: number]: string }  — only populated when admin edits
  const [adjustedWeights, setAdjustedWeights] = useState({});

  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  function load() {
    listTemplates()
      .then((d) => {
        setData(d);
      })
      .catch((err) => toast.error(getErrorMessage(err)));
  }

  // `toast` is a plain object re-created every render (not memoized by
  // ToastContext), so listing it here would refetch on every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  // All existing recurring custom KPIs for the selected employee.
  const existingEmployeeKpis = useMemo(() => {
    if (!data || !customEmployeeId) return [];
    return data.kpi_templates.filter(
      (t) => t.is_custom && t.employee_id === Number(customEmployeeId)
    );
  }, [data, customEmployeeId]);

  // Sum of weights using admin's in-form adjustments (falls back to template.weight).
  const effectiveExistingSum = useMemo(() => {
    return existingEmployeeKpis.reduce((sum, t) => {
      const adj = adjustedWeights[t.id];
      return sum + (adj !== undefined ? Number(adj) || 0 : Number(t.weight));
    }, 0);
  }, [existingEmployeeKpis, adjustedWeights]);

  // Live projected total = existing (maybe adjusted) + new KPI weight.
  const projectedTotal = effectiveExistingSum + (Number(customWeight) || 0);

  // Which existing templates have been changed from their current weight?
  const changedTemplates = useMemo(() => {
    return existingEmployeeKpis.filter((t) => {
      const adj = adjustedWeights[t.id];
      return adj !== undefined && Math.abs(Number(adj) - Number(t.weight)) > 0.001;
    });
  }, [existingEmployeeKpis, adjustedWeights]);

  function resetForm() {
    setCustomEmployeeId("");
    setCustomMetricName("");
    setCustomTarget("");
    setCustomWeight("");
    setWeightError(null);
    setAdjustedWeights({});
  }

  async function handleCreateCustom(e) {
    e.preventDefault();

    // Block submission only if total would EXCEED 100% — under-allocation is valid.
    if (projectedTotal > 100) {
      setWeightError(
        `Weight total would be ${projectedTotal.toFixed(1)}% — the combined total must not exceed 100%. ` +
        `Use the rebalancer below to reduce existing KPI weights.`
      );
      return;
    }
    setWeightError(null);
    setSubmitting(true);

    try {
      // 1. PATCH changed template weights (template-only — no submission rows touched).
      //    Approved/historical records remain immutable; new weight is for future months.
      for (const t of changedTemplates) {
        await updateTemplateWeight(t.id, { weight: Number(adjustedWeights[t.id]) });
      }

      // 2. Create the new recurring custom KPI starting from the current month.
      await createCustomTemplate({
        employee_id: Number(customEmployeeId),
        metric_name: customMetricName.trim(),
        target: Number(customTarget),
        weight: Number(customWeight),
        year: data.default_year,
        period: data.default_period,
        is_recurring: true,
      });

      const rebalanceNote =
        changedTemplates.length > 0
          ? ` Rebalanced ${changedTemplates.length} existing KPI weight${changedTemplates.length > 1 ? "s" : ""} (future months only).`
          : "";
      toast.success(
        `Custom KPI "${customMetricName.trim()}" added — will auto-carry to future months.${rebalanceNote}`
      );
      resetForm();
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Suggest the exact weight needed to fill remaining budget (100 - existing sum).
  function suggestRebalancedWeight() {
    const needed = 100 - effectiveExistingSum;
    if (needed > 0) {
      setCustomWeight(String(Math.round(needed * 10) / 10));
      setWeightError(null);
    }
  }


  // Update a single existing template's in-form weight override.
  function handleAdjustWeight(templateId, value) {
    setAdjustedWeights((prev) => ({ ...prev, [templateId]: value }));
    setWeightError(null);
  }

  // Reset a single template's weight override back to its saved value.
  function resetAdjustedWeight(templateId) {
    setAdjustedWeights((prev) => {
      const next = { ...prev };
      delete next[templateId];
      return next;
    });
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteTemplate(pendingDelete.id);
      toast.success("Metric deleted.");
      setPendingDelete(null);
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setDeleting(false);
    }
  }

  if (!data) return <AppShell title="KPI Metrics"><PageLoading /></AppShell>;

  return (
    <AppShell title={isDeptAdmin ? "My Department Metrics" : "KPI Metric Templates"}>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* ── Template list ── */}
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
                            {t.employee?.name}
                            {t.is_recurring && (
                              <span className="ml-1 text-violet-400 font-normal">· recurring</span>
                            )}
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
                              onClick={() => setPendingDelete(t)}
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

        {/* ── Add Custom KPI panel ── */}
        <div className="space-y-6">
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
              Assigns a recurring monthly KPI to a specific {isDeptAdmin ? "employee" : "person"} starting from{" "}
              <span className="font-medium text-slate-700">
                {data.default_period} {data.default_year}
              </span>{" "}
              — it auto-carries forward each month.
              {!isDeptAdmin && " A Dept Admin's own KPI skips department review and goes straight to you for final approval."}
            </p>

            <form onSubmit={handleCreateCustom} className="space-y-3">
              {/* Employee selector */}
              <select
                required
                value={customEmployeeId}
                onChange={(e) => {
                  setCustomEmployeeId(e.target.value);
                  setWeightError(null);
                  setAdjustedWeights({});
                }}
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

              {/* New KPI name */}
              <input
                type="text"
                required
                placeholder="e.g. Q3 Product Launch Readiness"
                value={customMetricName}
                onChange={(e) => setCustomMetricName(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />

              {/* Target + Weight */}
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
                  min="0.1"
                  max="100"
                  required
                  placeholder="Weight %"
                  value={customWeight}
                  onChange={(e) => { setCustomWeight(e.target.value); setWeightError(null); }}
                  className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 transition ${
                    weightError
                      ? "border-red-400 focus:ring-red-400"
                      : "border-slate-300 focus:ring-brand-500 focus:border-brand-500"
                  }`}
                />
              </div>

              {/* ── Weight sum indicator ── */}
              {customEmployeeId && (
                <div className={`rounded-lg px-3 py-2 text-xs flex items-center justify-between gap-2 ${
                  projectedTotal > 100
                    ? "bg-red-50 text-red-700 border border-red-200"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                }`}>
                  <span>
                    Existing: <strong>{effectiveExistingSum.toFixed(1)}%</strong>
                    {customWeight ? (
                      <>
                        {" + New: "}
                        <strong>{(Number(customWeight) || 0).toFixed(1)}%</strong>
                        {" = "}
                        <strong>{projectedTotal.toFixed(1)}% / 100%</strong>
                      </>
                    ) : (
                      <> · <strong>{(100 - effectiveExistingSum).toFixed(1)}%</strong> remaining</>
                    )}
                  </span>
                  {Math.round(projectedTotal * 10) !== 1000 && (100 - effectiveExistingSum) > 0 && (
                    <button
                      type="button"
                      onClick={suggestRebalancedWeight}
                      className="shrink-0 underline underline-offset-2 text-xs font-semibold hover:opacity-75 transition"
                    >
                      Use {(100 - effectiveExistingSum).toFixed(1)}%
                    </button>
                  )}
                </div>
              )}

              {/* ── Inline Weight Rebalancer ──
                  Shows whenever the selected employee has existing KPIs.
                  All edits here ONLY update KPITemplate.weight (future months).
                  Approved submission records are strictly immutable.          */}
              {existingEmployeeKpis.length > 0 && (
                <div className="rounded-lg border border-slate-200 overflow-hidden">
                  {/* Header */}
                  <div className="bg-slate-50 px-3 py-2 flex items-center justify-between gap-2 border-b border-slate-200">
                    <div className="flex items-center gap-1.5">
                      <svg className="h-3.5 w-3.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75" />
                      </svg>
                      <span className="text-xs font-semibold text-slate-600">Rebalance Existing KPIs</span>
                    </div>
                    <span className="text-[10px] text-slate-400 flex items-center gap-1">
                      <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75m-3-7.036A11.959 11.959 0 0 1 3.598 6 11.99 11.99 0 0 0 3 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285Z" />
                      </svg>
                      Future months only · approved records untouched
                    </span>
                  </div>

                  {/* Existing KPI rows with editable weights */}
                  <div className="divide-y divide-slate-100">
                    {existingEmployeeKpis.map((t) => {
                      const currentVal = adjustedWeights[t.id] !== undefined
                        ? adjustedWeights[t.id]
                        : t.weight;
                      const isEdited = adjustedWeights[t.id] !== undefined
                        && Math.abs(Number(adjustedWeights[t.id]) - Number(t.weight)) > 0.001;

                      return (
                        <div key={t.id} className="px-3 py-2 flex items-center gap-2 bg-white hover:bg-slate-50/60 transition-colors">
                          {/* KPI name */}
                          <span className="flex-1 min-w-0 text-xs text-slate-700 truncate" title={t.metric_name}>
                            {t.metric_name}
                          </span>

                          {/* Edited badge */}
                          {isEdited && (
                            <span className="shrink-0 inline-flex items-center rounded-full bg-blue-50 px-1.5 py-0.5 text-[10px] font-semibold text-blue-600 ring-1 ring-inset ring-blue-200">
                              edited
                            </span>
                          )}

                          {/* Weight input */}
                          <input
                            type="number"
                            step="0.1"
                            min="0.1"
                            max="100"
                            value={currentVal}
                            onChange={(e) => handleAdjustWeight(t.id, e.target.value)}
                            className={`w-16 rounded border px-2 py-1 text-xs text-right tabular-nums focus:outline-none focus:ring-1 transition ${
                              isEdited
                                ? "border-blue-300 focus:ring-blue-400 bg-blue-50"
                                : "border-slate-300 focus:ring-brand-500"
                            }`}
                          />
                          <span className="shrink-0 text-xs text-slate-400">%</span>

                          {/* Reset button — only if edited */}
                          {isEdited ? (
                            <button
                              type="button"
                              onClick={() => resetAdjustedWeight(t.id)}
                              title="Reset to saved weight"
                              className="shrink-0 text-slate-400 hover:text-slate-600 transition"
                            >
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
                              </svg>
                            </button>
                          ) : (
                            <span className="w-3.5" /> /* spacer to keep alignment */
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Running total bar inside the rebalancer */}
                  <div className={`px-3 py-1.5 border-t flex items-center justify-between text-[10px] font-medium ${
                    Math.round(effectiveExistingSum * 10) + Math.round((Number(customWeight) || 0) * 10) === 1000
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : "bg-slate-50 text-slate-500 border-slate-200"
                  }`}>
                    <span>
                      Existing subtotal: <strong>{effectiveExistingSum.toFixed(1)}%</strong>
                      {changedTemplates.length > 0 && (
                        <span className="ml-1 text-blue-500">({changedTemplates.length} pending save)</span>
                      )}
                    </span>
                    {changedTemplates.length > 0 && (
                      <button
                        type="button"
                        onClick={() => setAdjustedWeights({})}
                        className="text-slate-400 hover:text-slate-600 underline underline-offset-1 transition"
                      >
                        Reset all
                      </button>
                    )}
                  </div>
                </div>
              )}

              {/* Weight validation error */}
              {weightError && (
                <p className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
                  {weightError}
                </p>
              )}

              {/* Submit */}
              <button
                type="submit"
                disabled={submitting || !!weightError}
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-violet-700 transition disabled:opacity-60"
              >
                {submitting ? (
                  <>
                    <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                    </svg>
                    Saving…
                  </>
                ) : (
                  <>
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                    </svg>
                    {changedTemplates.length > 0
                      ? `Save Weights & Add KPI`
                      : `Add Custom KPI`}
                  </>
                )}
              </button>

              {/* Contextual hint when rebalance changes are pending */}
              {changedTemplates.length > 0 && (
                <p className="text-[10px] text-slate-400 text-center leading-relaxed">
                  Weight changes apply to <strong>future months only</strong>.<br />
                  Current &amp; past approved records remain immutable.
                </p>
              )}
            </form>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={isDeptAdmin ? "Delete this metric?" : `Delete "${pendingDelete?.metric_name}"?`}
        message={
          isDeptAdmin ? null : "This also permanently removes all KPI submissions recorded against it. This cannot be undone."
        }
        submitting={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </AppShell>
  );
}
