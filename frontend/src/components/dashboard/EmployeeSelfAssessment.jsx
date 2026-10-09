import { useEffect, useRef, useState } from "react";
import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import EmptyState from "../ui/EmptyState";
import PageLoading from "../ui/PageLoading";
import { STYLES as STATUS_STYLES, LABELS as STATUS_LABELS } from "../ui/statusMeta";
import { employeeSaveScores, employeeSubmit } from "../../api/kpi";
import { getErrorMessage } from "../../api/errors";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../context/AuthContext";

const TARGET_EXCEEDED_MESSAGE = "Target ထက်ကျော်နေပါ၍ပြန်ထည့်ရန်";
const WEIGHT_TOTAL_MESSAGE = "Weight စုစုပေါင်းသည် 100% ဖြစ်ရပါမည်။ (100 ထက် ကျော်လွန်နေပါသည် သို့မဟုတ် 100 မပြည့်သေးပါ)";

export default function EmployeeSelfAssessment({ fetcher, emptyTitle = "No KPI records" }) {
  const toast = useToast();
  const { user } = useAuth();
  const isDeptAdmin = user?.role === "dept_admin";
  const [data, setData] = useState(null);
  const [scores, setScores] = useState({});
  const [error, setError] = useState(null);
  const [invalidScoreId, setInvalidScoreId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [params, setParams] = useState({});
  const loadIdRef = useRef(0);

  function load(nextParams) {
    // Guard against out-of-order responses: switching the period while a
    // previous request is still in flight must not let a stale response
    // overwrite the newer one.
    const requestId = ++loadIdRef.current;
    fetcher(nextParams)
      .then((d) => {
        if (loadIdRef.current !== requestId) return;
        setData(d);
        const initial = {};
        d.submissions.forEach((s) => {
          initial[s.id] = s.self_score ?? "";
        });
        setScores(initial);
      })
      .catch((err) => {
        if (loadIdRef.current !== requestId) return;
        toast.error(getErrorMessage(err));
      });
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) return <PageLoading />;

  // Editable only when: this is the current month AND we're in the last-3-days
  // window (month-end lock). Past periods and early-in-month views are read-only.
  const isEditable =
    data.is_current_period &&
    data.is_month_end &&
    data.submissions[0]?.status === "draft";

  // Show a "not yet open" banner if we're in the current month but before month-end.
  const isEarlyInMonth =
    data.is_current_period && !data.is_month_end && data.submissions[0]?.status === "draft";


  function handleApply(nextParams) {
    setParams(nextParams);
    load(nextParams);
  }

  function validateScores(nextScores) {
    const exceeded = data.submissions.find((submission) => {
      const value = nextScores[submission.id];
      return value !== "" && value !== null && value !== undefined
        && Number(value) > Number(submission.kpi_template.target);
    });

    if (exceeded) {
      setInvalidScoreId(exceeded.id);
      setError(TARGET_EXCEEDED_MESSAGE);
      requestAnimationFrame(() => document.getElementById(`self-score-${exceeded.id}`)?.focus());
      return false;
    }

    // An emptied field is not a corrected value. Keep this validation message
    // visible until the previously invalid value is replaced with one at or
    // below its target.
    if (invalidScoreId !== null) {
      const previousInvalidValue = nextScores[invalidScoreId];
      const previousInvalidSubmission = data.submissions.find((s) => s.id === invalidScoreId);
      if (
        previousInvalidSubmission
        && previousInvalidValue !== ""
        && previousInvalidValue !== null
        && previousInvalidValue !== undefined
        && Number(previousInvalidValue) <= Number(previousInvalidSubmission.kpi_template.target)
      ) {
        setInvalidScoreId(null);
        if (error === TARGET_EXCEEDED_MESSAGE) setError(null);
      }
    }
    return error !== TARGET_EXCEEDED_MESSAGE;
  }

  function handleSelfScoreChange(id, value) {
    const nextScores = { ...scores, [id]: value };
    setScores(nextScores);
    validateScores(nextScores);
  }

  function buildScoresPayload() {
    const payload = {};
    for (const [id, value] of Object.entries(scores)) {
      if (value !== "" && value !== null && value !== undefined) {
        payload[id] = Number(value);
      }
    }
    return payload;
  }

  function validateTotalWeight() {
    const totalWeight = data.submissions.reduce(
      (sum, submission) => sum + Number(submission.kpi_template.weight),
      0
    );
    if (totalWeight !== 100) {
      setError(WEIGHT_TOTAL_MESSAGE);
      toast.error(WEIGHT_TOTAL_MESSAGE);
      return false;
    }
    return true;
  }

  async function handleSave() {
    if (!validateScores(scores)) return;
    if (!validateTotalWeight()) return;
    setSaving(true);
    setError(null);
    try {
      await employeeSaveScores({ year: data.active_year, period: data.active_period, scores: buildScoresPayload() });
      toast.success("Draft saved.");
      load(params);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit() {
    if (!validateScores(scores)) return;
    if (!validateTotalWeight()) return;
    setSaving(true);
    setError(null);
    try {
      await employeeSubmit({ year: data.active_year, period: data.active_period, scores: buildScoresPayload() });
      toast.success("Submitted for department approval.");
      load(params);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PeriodSelector
        activeYear={data.active_year}
        activePeriod={data.active_period}
        months={data.months}
        onApply={handleApply}
      />

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {/* Month-end lock: current month but entry window not open yet */}
      {isEarlyInMonth && (
        <div className="mb-4 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 flex items-start gap-3">
          <svg className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
          <div>
            <p className="text-sm font-semibold text-amber-800">Score entry opens at month-end</p>
            <p className="text-xs text-amber-700 mt-0.5">
              KPI scores for <span className="font-medium">{data.active_period} {data.active_year}</span> can only be
              entered during the last 3 days of the month. Check back then to record your actual scores.
            </p>
          </div>
        </div>
      )}

      {data.submissions.length === 0 ? (
        <EmptyState title={emptyTitle} message={`Nothing found for ${data.active_period} ${data.active_year}.`} />
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide mr-1">
              {data.submissions.length} metric{data.submissions.length !== 1 ? "s" : ""}
            </span>
            {Object.entries(
              data.submissions.reduce((acc, s) => {
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

          <div className="mb-6">
            <CombinedScoreCard combined={data.combined_score} />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {data.submissions.map((s) => (
              <KpiCard
                key={s.id}
                submission={s}
                editableSelf={isEditable}
                selfScoreValue={scores[s.id]}
                selfScoreInvalid={invalidScoreId === s.id}
                onSelfScoreChange={handleSelfScoreChange}
              />
            ))}
          </div>

          {isEditable ? (
            <div className="mt-6 flex items-center gap-3">
              <button
                type="button"
                disabled={saving}
                onClick={handleSave}
                className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 transition disabled:opacity-60"
              >
                Save Draft
              </button>
              <button
                type="button"
                disabled={saving}
                onClick={handleSubmit}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
              >
                Submit for Department Approval
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
                </svg>
              </button>
            </div>
          ) : isEarlyInMonth ? (
            <p className="mt-5 flex items-center gap-1.5 text-sm text-slate-500">
              <svg className="h-4 w-4 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
              </svg>
              Score entry opens during the last 3 days of {data.active_period}.
            </p>
          ) : (
            <p className="mt-5 flex items-center gap-1.5 text-sm text-slate-500">
              <svg className="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 9.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z"
                />
              </svg>
              This KPI has been submitted and is now read-only. Track its status above.
            </p>
          )}

        </>
      )}
    </div>
  );
}
