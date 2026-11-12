import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import PageLoading from "../../components/ui/PageLoading";
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import Modal from "../../components/ui/Modal";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { createCustomTemplateWithRebalance, deleteTemplate, listTemplates, renameTemplate } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";
import { MONTH_NAMES_EN } from "../../utils/months";

// The only period the M6 admin-creation gate allows right now: during the
// grace week (days 1..SUBMISSION_GRACE_DAYS, default 7) the previous month;
// from day 8 on, the current month. Mirrors BulkImportPage. The backend
// stays the authority and rejects anything else with a clear message
// (shown via getErrorMessage in the create toast error path).
function allowedTemplatePeriod(now = new Date()) {
  let year = now.getFullYear();
  let month = now.getMonth() + 1;
  const graceWeek = now.getDate() <= 7;
  if (graceWeek) {
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
  }
  const period = MONTH_NAMES_EN[month - 1];
  return { year, period, label: `${period} ${year}`, graceWeek };
}

// Shown when the initial data load fails (instead of an endless spinner).
// Lets the user retry the failed request.
function LoadError({ message, onRetry }) {
  return (
    <div className="rounded-2xl bg-white shadow-sm border border-slate-200 px-8 py-10 max-w-md w-full mx-auto text-center">
      <p className="text-sm font-semibold text-red-600">Couldn't load data</p>
      <p className="mt-2 text-sm text-slate-500">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-6 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
      >
        Try again
      </button>
    </div>
  );
}

// Metric name that clamps to 2 lines with an expand affordance, but only
// when the name actually overflows (measured, not guessed from length, so
// it behaves the same at desktop and mobile widths). The full name is
// always one click away — never silently truncated.
function ExpandableMetricName({ name }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef(null);
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    // NOTE: scrollHeight does NOT reveal overflow on a line-clamped
    // element in Chromium — it reports the clamped height. So measure a
    // hidden unclamped clone at the same width instead.
    const check = () => {
      if (expandedRef.current) return;
      const clone = el.cloneNode(true);
      clone.style.position = "absolute";
      clone.style.visibility = "hidden";
      clone.style.display = "block";
      clone.style.overflow = "visible";
      clone.style.width = `${el.clientWidth}px`;
      clone.style.webkitLineClamp = "unset";
      el.parentNode.appendChild(clone);
      const overflows = clone.scrollHeight > el.clientHeight + 1;
      clone.remove();
      setOverflows(overflows);
    };
    check();
    window.addEventListener("resize", check);
    if (document.fonts?.ready) document.fonts.ready.then(check).catch(() => {});
    return () => window.removeEventListener("resize", check);
  }, [name]);
  const toggleable = overflows || expanded;
  return (
    <div>
      <span
        ref={ref}
        title={name}
        onClick={() => toggleable && setExpanded((v) => !v)}
        className={`text-sm font-medium text-slate-800 ${expanded ? "block" : "line-clamp-2"} ${toggleable ? "cursor-pointer" : ""}`}
      >
        {name}
      </span>
      {overflows && !expanded && (
        <button type="button" onClick={() => setExpanded(true)} className="mt-0.5 text-xs font-medium text-brand-600 hover:text-brand-700">
          အပြည့်ကြည့်ရန်
        </button>
      )}
      {expanded && (
        <button type="button" onClick={() => setExpanded(false)} className="mt-0.5 text-xs font-medium text-brand-600 hover:text-brand-700">
          ချုံ့ရန်
        </button>
      )}
    </div>
  );
}

// Scope badge, stacked so long person names wrap at word boundaries
// instead of splitting awkwardly mid-pill, with "recurring" on its own line.
function ScopePill({ t }) {
  if (!t.is_custom) {
    return <span className="text-slate-600">{t.department ? t.department.name : "All Departments"}</span>;
  }
  return (
    <span className="inline-flex flex-col items-start gap-0.5 rounded-xl bg-violet-50 px-2.5 py-1.5 ring-1 ring-inset ring-violet-200">
      <span className="flex items-center gap-1.5 text-xs font-semibold text-violet-700 leading-snug">
        <svg className="h-3 w-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
          />
        </svg>
        <span className="break-words">{t.employee?.name}</span>
      </span>
      {t.is_recurring && <span className="text-[11px] text-violet-400 font-normal">recurring</span>}
    </span>
  );
}

export default function TemplatesPage() {
  const { user } = useAuth();
  const toast = useToast();
  const isDeptAdmin = user.role === "dept_admin";

  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);

  // ── New Custom KPI form state ──
  const [customEmployeeId, setCustomEmployeeId] = useState("");
  const [customMetricName, setCustomMetricName] = useState("");
  const [customTarget, setCustomTarget] = useState("");
  const [customWeight, setCustomWeight] = useState("");
  const [customRecurring, setCustomRecurring] = useState(true);
  // Target period for the new KPI — NOT the backend default (current month),
  // which the M6 gate rejects during days 1–7. During grace week this is the
  // previous month; from day 8 it is the current month.
  const [customPeriod, setCustomPeriod] = useState(() => allowedTemplatePeriod());
  const [weightError, setWeightError] = useState(null);
  // Backend/submit errors other than the weight total (shown inline above
  // Submit, alongside the toast) so a failed submit says which field failed
  // and keeps the typed values.
  const [formError, setFormError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // ── Inline rebalancer: tracks per-template weight overrides ──
  // { [templateId: number]: string }  — only populated when admin edits
  const [adjustedWeights, setAdjustedWeights] = useState({});

  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [showAddCustom, setShowAddCustom] = useState(false);

  // ── List filters: by person (custom KPIs of that person) and by
  // department (department templates of that department + custom KPIs
  // whose owner belongs to it — custom templates carry the owner's
  // department_id). Both set = intersection. Dept admins see only their
  // own department's data, so they get the person filter only.
  const [filterPerson, setFilterPerson] = useState("");
  const [filterDept, setFilterDept] = useState("");

  // ── Inline rename: one row at a time, in the Metric cell ──
  const [renamingId, setRenamingId] = useState(null);
  const [renamingName, setRenamingName] = useState("");
  const [renamingBusy, setRenamingBusy] = useState(false);

  function startRename(t) {
    setRenamingId(t.id);
    setRenamingName(t.metric_name);
  }

  async function saveRename() {
    const name = renamingName.trim();
    if (!name) {
      toast.error("Metric name cannot be empty.");
      return;
    }
    setRenamingBusy(true);
    try {
      const updated = await renameTemplate(renamingId, name);
      setData((d) =>
        d
          ? { ...d, kpi_templates: d.kpi_templates.map((t) => (t.id === updated.id ? updated : t)) }
          : d
      );
      setRenamingId(null);
      toast.success("Metric renamed.");
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setRenamingBusy(false);
    }
  }

  function load() {
    listTemplates()
      .then((d) => {
        setData(d);
        setLoadError(null);
      })
      .catch((err) => {
        const message = getErrorMessage(err);
        setLoadError(message);
        toast.error(message);
      });
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

  const filteredTemplates = useMemo(() => {
    if (!data) return [];
    return data.kpi_templates.filter((t) => {
      if (filterPerson && !(t.is_custom && t.employee_id === Number(filterPerson))) return false;
      if (filterDept && t.department_id !== Number(filterDept)) return false;
      return true;
    });
  }, [data, filterPerson, filterDept]);

  const hasActiveFilters = filterPerson !== "" || filterDept !== "";

  function clearFilters() {
    setFilterPerson("");
    setFilterDept("");
  }

  // Create-time budget (what the backend enforces): the employee's custom
  // template weights AS SAVED — never the in-form rebalancer adjustments.
  // The backend guard sums the target period's submission snapshots, and a
  // template-weight edit only affects FUTURE submissions, so an adjustment
  // never frees room for THIS create. Crediting adjustments here previously
  // showed 60 + 30 = 90 while the backend enforced 90 + 30 = 120 and
  // rejected the create — and the error text sent the user to the
  // rebalancer, which cannot help this period.
  const existingSum = useMemo(() => {
    return existingEmployeeKpis.reduce((sum, t) => sum + Number(t.weight), 0);
  }, [existingEmployeeKpis]);

  // Future-months sum after the in-form rebalancer edits (rebalancer
  // footer only — the rebalancer changes future months, not this create).
  const adjustedExistingSum = useMemo(() => {
    return existingEmployeeKpis.reduce((sum, t) => {
      const adj = adjustedWeights[t.id];
      return sum + (adj !== undefined ? Number(adj) || 0 : Number(t.weight));
    }, 0);
  }, [existingEmployeeKpis, adjustedWeights]);

  // Live projected total = existing (as saved) + new KPI weight.
  const projectedTotal = existingSum + (Number(customWeight) || 0);

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
    setCustomRecurring(true);
    setWeightError(null);
    setFormError(null);
    setAdjustedWeights({});
  }

  async function handleCreateCustom(e) {
    e.preventDefault();

    // Block submission only if total would EXCEED 100% — under-allocation is valid.
    if (projectedTotal > 100) {
      // The rebalancer below only changes FUTURE months, so it cannot free
      // room for this create — the only fix here is a smaller new weight.
      // (The "Use X%" button fills the exact remaining budget.)
      const remaining = 100 - existingSum;
      setWeightError(
        remaining > 0
          ? `Weight total would be ${projectedTotal.toFixed(1)}% — the combined total must not exceed 100%. ` +
            `Lower the new weight to ${remaining.toFixed(1)}% or less.`
          : `Weight total would be ${projectedTotal.toFixed(1)}% — this person is already at 100% for this period, so no new KPI fits this month. ` +
            `Use the rebalancer below to free room for future months.`
      );
      return;
    }
    setWeightError(null);
    setFormError(null);
    setSubmitting(true);

    try {
      // Single atomic request: weight rebalancing + custom KPI create commit
      // together on the backend. A failed create can no longer leave the
      // weight changes silently persisted (they roll back as one unit).
      await createCustomTemplateWithRebalance({
        employee_id: Number(customEmployeeId),
        metric_name: customMetricName.trim(),
        target: Number(customTarget),
        weight: Number(customWeight),
        year: customPeriod.year,
        period: customPeriod.period,
        is_recurring: customRecurring,
        weight_adjustments: changedTemplates.map((t) => ({
          template_id: t.id,
          weight: Number(adjustedWeights[t.id]),
        })),
      });

      const rebalanceNote =
        changedTemplates.length > 0
          ? ` Rebalanced ${changedTemplates.length} existing KPI weight${changedTemplates.length > 1 ? "s" : ""} (future months only).`
          : "";
      toast.success(
        `Custom KPI "${customMetricName.trim()}" added — ${
          customRecurring ? "will auto-carry to future months." : "one-off for this month only."
        }${rebalanceNote}`
      );
      resetForm();
      setShowAddCustom(false);
      load();
    } catch (err) {
      setFormError(getErrorMessage(err));
      toast.error(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Fill the new weight with the exact remaining budget for this create
  // (100 - existing sum, as saved). The rebalancer cannot change this
  // number — it only affects future months.
  function suggestRemainingWeight() {
    const needed = 100 - existingSum;
    if (needed > 0) {
      setCustomWeight(String(Math.round(needed * 10) / 10));
      setWeightError(null);
    }
  }


  // Update a single existing template's in-form weight override. The raw
  // text is kept while typing — clamping happens on blur (clampWeight),
  // never mid-keystroke, so typing "150" is not rewritten to "100" under
  // the user's fingers.
  function handleAdjustWeight(templateId, value) {
    setAdjustedWeights((prev) => ({ ...prev, [templateId]: value }));
    setWeightError(null);
  }

  // Weights can never exceed 100%. Clamp ONLY on blur (or when a suggested
  // value is applied) — never while typing: clamping at input time rewrote
  // "150" to "100" mid-keystroke, and "100.5" lost its ".5". Empty stays
  // empty so clearing the field still works.
  function clampWeight(value) {
    if (value === "" || value === null || value === undefined) return value;
    return Number.isNaN(Number(value)) || Number(value) <= 100 ? value : "100";
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

  if (!data) {
    return (
      <AppShell title="KPI Metrics">
        {loadError ? <LoadError message={loadError} onRetry={load} /> : <PageLoading />}
      </AppShell>
    );
  }

  return (
    <AppShell title={isDeptAdmin ? "My Department Metrics" : "KPI Metric Templates"}>
      <div className="flex justify-end mb-4">
        <button
          type="button"
          onClick={() => setShowAddCustom(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          Add Custom KPI
        </button>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex flex-col sm:flex-row gap-3">
            <select
              value={filterPerson}
              onChange={(e) => setFilterPerson(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            >
              <option value="">All people</option>
              {data.team.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {!isDeptAdmin && (
              <select
                value={filterDept}
                onChange={(e) => setFilterDept(e.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              >
                <option value="">All departments</option>
                {data.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            )}
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition shrink-0"
              >
                Clear
              </button>
            )}
          </div>
          <div className="hidden lg:block overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Metric</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Target</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Weight</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Scope</th>
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
                ) : filteredTemplates.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-6 py-10 text-center text-sm text-slate-400">
                      No metrics match your filters.{" "}
                      <button type="button" onClick={clearFilters} className="font-medium text-brand-600 hover:text-brand-700">
                        Clear filters
                      </button>
                    </td>
                  </tr>
                ) : (
                  filteredTemplates.map((t) => (
                    <tr key={t.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4 text-sm font-medium text-slate-800">
                        {renamingId === t.id ? (
                          <input
                            autoFocus
                            value={renamingName}
                            disabled={renamingBusy}
                            onChange={(e) => setRenamingName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") saveRename();
                              if (e.key === "Escape") setRenamingId(null);
                            }}
                            className="w-full rounded-lg border border-indigo-300 bg-white px-2.5 py-1.5 text-sm font-medium text-slate-800 outline-none focus:ring-2 focus:ring-indigo-200"
                          />
                        ) : (
                          <ExpandableMetricName name={t.metric_name} />
                        )}
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-500 tabular-nums">{t.target}</td>
                      <td className="px-6 py-4 text-sm text-slate-500 tabular-nums">{t.weight}</td>
                      <td className="px-6 py-4 text-sm">
                        <ScopePill t={t} />
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {renamingId === t.id ? (
                            <>
                              <button
                                type="button"
                                disabled={renamingBusy}
                                onClick={saveRename}
                                className="inline-flex items-center rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 transition disabled:opacity-60"
                              >
                                Save
                              </button>
                              <button
                                type="button"
                                disabled={renamingBusy}
                                onClick={() => setRenamingId(null)}
                                className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition disabled:opacity-60"
                              >
                                Cancel
                              </button>
                            </>
                          ) : (
                            <>
                              {(!isDeptAdmin || t.department_id === user.department_id) &&
                              !(isDeptAdmin && t.employee_id === user.id) && (
                                <button
                                  type="button"
                                  onClick={() => startRename(t)}
                                  title="Rename this metric (scores and history are unchanged)"
                                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 transition"
                                >
                                  Rename
                                </button>
                              )}
                              {(!isDeptAdmin || t.department_id === user.department_id) &&
                              !(isDeptAdmin && t.employee_id === user.id) ? (
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
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="lg:hidden divide-y divide-slate-100">
            {filteredTemplates.length === 0 ? (
              <p className="px-6 py-10 text-center text-sm text-slate-400">
                {data.kpi_templates.length === 0 ? (
                  "No KPI metrics configured yet."
                ) : (
                  <>
                    No metrics match your filters.{" "}
                    <button type="button" onClick={clearFilters} className="font-medium text-brand-600 hover:text-brand-700">
                      Clear filters
                    </button>
                  </>
                )}
              </p>
            ) : (
              filteredTemplates.map((t) => (
                <div key={t.id} className="px-6 py-4 space-y-2">
                  {renamingId === t.id ? (
                    <input
                      autoFocus
                      value={renamingName}
                      disabled={renamingBusy}
                      onChange={(e) => setRenamingName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveRename();
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      className="w-full rounded-lg border border-indigo-300 bg-white px-2.5 py-1.5 text-sm font-medium text-slate-800 outline-none focus:ring-2 focus:ring-indigo-200"
                    />
                  ) : (
                    <ExpandableMetricName name={t.metric_name} />
                  )}
                  <p className="text-sm text-slate-500 tabular-nums">
                    Target {t.target} · Weight {t.weight}
                  </p>
                  <ScopePill t={t} />
                  <div className="flex items-center gap-2 pt-1">
                    {renamingId === t.id ? (
                      <>
                        <button
                          type="button"
                          disabled={renamingBusy}
                          onClick={saveRename}
                          className="inline-flex items-center rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 transition disabled:opacity-60"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => setRenamingId(null)}
                          className="inline-flex items-center rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50 transition"
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => startRename(t)}
                        className="inline-flex items-center rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50 transition"
                      >
                        Rename
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setPendingDelete(t)}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 hover:border-red-300 transition"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

      <Modal
        open={showAddCustom}
        title="Add Custom KPI"
        onClose={() => setShowAddCustom(false)}
      >
            <p className="text-sm text-slate-500 mb-4">
              Assigns a KPI to a specific {isDeptAdmin ? "employee" : "person"} starting from{" "}
              <span className="font-medium text-slate-700">
                {customPeriod.label}
              </span>
              .
              {!isDeptAdmin && " A Dept Admin's own KPI skips department review and comes straight to you for final approval."}
            </p>

            <form onSubmit={handleCreateCustom} className="space-y-3">
              {/* Employee selector */}
              <div>
                <label htmlFor="custom-person" className="mb-1 block text-xs font-semibold text-slate-600">
                  {isDeptAdmin ? "Employee" : "Person"} <span className="text-red-500">*</span>
                </label>
                <select
                  id="custom-person"
                  required
                  value={customEmployeeId}
                  onChange={(e) => {
                    setCustomEmployeeId(e.target.value);
                    setWeightError(null);
                    setFormError(null);
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
              </div>

              {/* Target period — only the period the M6 gate allows is offered:
                  previous month during days 1–7 (grace week), current month
                  from day 8. Anything else the backend rejects with a clear
                  message, shown in the error toast. */}
              <div>
                <label htmlFor="custom-period" className="mb-1 block text-xs font-semibold text-slate-600">
                  Period
                </label>
                <div className="flex flex-wrap items-center gap-3">
                  <select
                    id="custom-period"
                    value={`${customPeriod.year}|${customPeriod.period}`}
                    onChange={(e) => {
                      const [y, p] = e.target.value.split("|");
                      setCustomPeriod({ year: Number(y), period: p, label: `${p} ${y}`, graceWeek: customPeriod.graceWeek });
                    }}
                    className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                  >
                    <option value={`${customPeriod.year}|${customPeriod.period}`}>{customPeriod.label}</option>
                  </select>
                  <span className="text-xs text-slate-500">
                    {customPeriod.graceWeek
                      ? "Days 1–7 (grace week): new KPIs target the previous month. The current month opens on day 8."
                      : "New KPIs target the current month. The previous month's grace window has closed."}
                  </span>
                </div>
              </div>

              {/* New KPI name — a textarea, not a single-line input: metric
                  names are long by the user's own rule, and a one-line field
                  hid the typed name behind horizontal scrolling. */}
              <div>
                <label htmlFor="custom-metric-name" className="mb-1 block text-xs font-semibold text-slate-600">
                  Metric name <span className="text-red-500">*</span>
                </label>
                <textarea
                  id="custom-metric-name"
                  rows={3}
                  required
                  placeholder="e.g. Q3 Product Launch Readiness"
                  value={customMetricName}
                  onChange={(e) => setCustomMetricName(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition resize-y"
                />
                <p className="mt-1 text-[11px] text-slate-400">Long names are fine — enter the full metric name as-is.</p>
              </div>

              {/* Target + Weight */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label htmlFor="custom-target" className="mb-1 block text-xs font-semibold text-slate-600">
                    Target <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="custom-target"
                    type="number"
                    step="0.1"
                    inputMode="decimal"
                    required
                    placeholder="Target"
                    value={customTarget}
                    onChange={(e) => setCustomTarget(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                  />
                </div>
                <div>
                  <label htmlFor="custom-weight" className="mb-1 block text-xs font-semibold text-slate-600">
                    Weight % <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="custom-weight"
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="100"
                    inputMode="decimal"
                    required
                    placeholder="Weight %"
                    value={customWeight}
                    onChange={(e) => { setCustomWeight(e.target.value); setWeightError(null); }}
                    onBlur={(e) => setCustomWeight(clampWeight(e.target.value))}
                    aria-invalid={!!weightError}
                    className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 transition ${
                      weightError
                        ? "border-red-400 focus:ring-red-400"
                        : "border-slate-300 focus:ring-brand-500 focus:border-brand-500"
                    }`}
                  />
                </div>
              </div>

              {/* ── Weight sum indicator ── */}
              {customEmployeeId && (
                <div className={`rounded-lg px-3 py-2 text-xs flex items-center justify-between gap-2 ${
                  projectedTotal > 100
                    ? "bg-red-50 text-red-700 border border-red-200"
                    : "bg-emerald-50 text-emerald-700 border border-emerald-200"
                }`}>
                  <span>
                    Existing: <strong>{existingSum.toFixed(1)}%</strong>
                    {customWeight ? (
                      <>
                        {" + New: "}
                        <strong>{(Number(customWeight) || 0).toFixed(1)}%</strong>
                        {" = "}
                        <strong>{projectedTotal.toFixed(1)}% / 100%</strong>
                      </>
                    ) : (
                      <> · <strong>{(100 - existingSum).toFixed(1)}%</strong> remaining</>
                    )}
                  </span>
                  {Math.round(projectedTotal * 10) !== 1000 && (100 - existingSum) > 0 && (
                    <button
                      type="button"
                      onClick={suggestRemainingWeight}
                      className="shrink-0 underline underline-offset-2 text-xs font-semibold hover:opacity-75 transition"
                    >
                      Use {(100 - existingSum).toFixed(1)}%
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
                    <span className="text-xs text-slate-400 flex items-center gap-1">
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

                          {/* Weight input — raw while typing, clamped on blur */}
                          <input
                            type="number"
                            step="0.1"
                            min="0.1"
                            max="100"
                            inputMode="decimal"
                            value={currentVal}
                            onChange={(e) => handleAdjustWeight(t.id, e.target.value)}
                            onBlur={(e) => handleAdjustWeight(t.id, clampWeight(e.target.value))}
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
                  <div className={`px-3 py-1.5 border-t flex items-center justify-between text-xs font-medium ${
                    Math.round(existingSum * 10) + Math.round((Number(customWeight) || 0) * 10) === 1000
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : "bg-slate-50 text-slate-500 border-slate-200"
                  }`}>
                    <span>
                      Existing subtotal: <strong>{adjustedExistingSum.toFixed(1)}%</strong>
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

              {/* Submit error (e.g. backend validation) */}
              {formError && (
                <p role="alert" className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
                  {formError}
                </p>
              )}

              {/* Submit */}
              <label className="flex items-center gap-2.5 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={customRecurring}
                  onChange={(e) => setCustomRecurring(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
                />
                <span className="text-xs text-slate-600">
                  <span className="font-semibold text-slate-700">Repeat every month</span>
                  <span className="block text-xs text-slate-400">
                    {customRecurring
                      ? "Auto-carries forward each month."
                      : "One-off — only for the selected month."}
                  </span>
                </span>
              </label>
              <button
                type="submit"
                disabled={submitting || !!weightError}
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
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
                <p className="text-xs text-slate-400 text-center leading-relaxed">
                  Weight changes apply to <strong>future months only</strong>.<br />
                  Current &amp; past approved records remain immutable.
                </p>
              )}
            </form>
      </Modal>
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
