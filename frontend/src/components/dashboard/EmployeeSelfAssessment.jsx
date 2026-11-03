import { useEffect, useRef, useState } from "react";
import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import ConfirmDialog from "../ui/ConfirmDialog";
import EmptyState from "../ui/EmptyState";
import PageLoading from "../ui/PageLoading";
import { STYLES as STATUS_STYLES, LABELS as STATUS_LABELS } from "../ui/statusMeta";
import { employeeSaveScores, employeeSubmit } from "../../api/kpi";
import { getErrorMessage } from "../../api/errors";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../context/AuthContext";

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

const TARGET_EXCEEDED_MESSAGE = "Target ထက်ကျော်နေပါ၍ပြန်ထည့်ရန်";
const WEIGHT_TOTAL_MESSAGE = "Weight စုစုပေါင်းသည် 100% ဖြစ်ရပါမည်။ (100 ထက် ကျော်လွန်နေပါသည် သို့မဟုတ် 100 မပြည့်သေးပါ)";

export default function EmployeeSelfAssessment({ fetcher, emptyTitle = "No KPI records" }) {
  const toast = useToast();
  const { user } = useAuth();
  const isDeptAdmin = user?.role === "dept_admin";
  const [data, setData] = useState(null);
  const [scores, setScores] = useState({});
  const [comments, setComments] = useState({});
  const initialCommentsRef = useRef({});
  const [error, setError] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [invalidScoreId, setInvalidScoreId] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmSubmit, setConfirmSubmit] = useState(false);
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
        setLoadError(null);
        const initial = {};
        const initialComments = {};
        d.submissions.forEach((s) => {
          initial[s.id] = s.self_score ?? "";
          initialComments[s.id] = s.self_score_comment ?? "";
        });
        setScores(initial);
        setComments(initialComments);
        initialCommentsRef.current = initialComments;
      })
      .catch((err) => {
        if (loadIdRef.current !== requestId) return;
        const message = getErrorMessage(err);
        setLoadError(message);
        toast.error(message);
      });
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) {
    if (loadError) return <LoadError message={loadError} onRetry={() => load(params)} />;
    return <PageLoading />;
  }

  // Editability is PER ROW, not per period. An admin may add a new DRAFT row
  // to a period where other rows are already submitted (e.g. a grace-week
  // October KPI added after the employee submitted the rest). The old
  // whole-form gate (first row's status) locked the entire form in that
  // case — and the save payload still carried the submitted rows' scores,
  // which the backend rejects — so the newly added row could never be
  // filled at all. A row is editable when its period is fillable and the
  // row itself is still a draft or was rejected.
  const isRowEditable = (s) =>
    data.is_current_period &&
    data.is_month_end &&
    (s.status === "draft" || s.status === "rejected");
  const isEditable = data.submissions.some(isRowEditable);
  const isResubmit = data.submissions.some((s) => s.status === "rejected" && isRowEditable(s));

  // Future months are locked — employees cannot enter scores in advance.
  const isFutureLocked = !!data.is_future_period;

  // Grace-week shortcut: when the current month's set has not opened yet
  // (rollover banner above), the previous month is still fillable until
  // day 7 — but nothing on this empty view says so. Offer a one-click jump
  // to it instead of stranding the employee on the empty current month.
  // data.months comes from the backend (full English month names).
  const MONTH_FALLBACK = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const monthList = data.months && data.months.length > 0 ? data.months : MONTH_FALLBACK;
  const activeIdx = monthList.indexOf(data.active_period);
  const prevIdx = activeIdx > 0 ? activeIdx - 1 : 11;
  const gracePrevYear = activeIdx === 0 ? data.active_year - 1 : data.active_year;
  const gracePrevPeriod = monthList[prevIdx];
  const showGraceShortcut =
    !!data.rollover_on && !isFutureLocked && activeIdx >= 0 && new Date().getDate() <= 7;

  function handleApply(nextParams) {
    setParams(nextParams);
    load(nextParams);
  }

  function validateScores(nextScores) {
    const exceeded = data.submissions.find((submission) => {
      // Only editable rows are validated: already-submitted rows carry
      // stored scores (including legacy over-target ones) that the user
      // cannot change, and they must not block saving the editable rows.
      if (!isRowEditable(submission)) return false;
      const value = nextScores[submission.id];
      return value !== "" && value !== null && value !== undefined
        && Number(value) > Number(submission.target_snapshot ?? submission.kpi_template.target);
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
    //
    // Validity is computed LOCALLY (never read back from `error` state after
    // setError): React state updates are async, so reading `error` here
    // returned the previous render's value — the first Save after
    // correcting an over-target score was silently swallowed.
    let valid = true;
    if (invalidScoreId !== null) {
      const previousInvalidValue = nextScores[invalidScoreId];
      const previousInvalidSubmission = data.submissions.find((s) => s.id === invalidScoreId);
      const corrected =
        previousInvalidSubmission
        && previousInvalidValue !== ""
        && previousInvalidValue !== null
        && previousInvalidValue !== undefined
        && Number(previousInvalidValue) <= Number(previousInvalidSubmission.target_snapshot ?? previousInvalidSubmission.kpi_template.target);
      if (corrected) {
        setInvalidScoreId(null);
        if (error === TARGET_EXCEEDED_MESSAGE) setError(null);
      } else if (error === TARGET_EXCEEDED_MESSAGE) {
        valid = false;
      }
    }
    return valid;
  }

  function handleSelfScoreChange(id, value) {
    const nextScores = { ...scores, [id]: value };
    setScores(nextScores);
    validateScores(nextScores);
  }

  function handleSelfCommentChange(id, value) {
    setComments({ ...comments, [id]: value });
  }

  function buildScoresPayload() {
    const payload = {};
    for (const [id, value] of Object.entries(scores)) {
      // Only editable rows may be sent: the backend rejects the whole save
      // if any submitted (PENDING/APPROVED) row's id is included, and their
      // scores are already stored anyway.
      const row = data.submissions.find((s) => String(s.id) === String(id));
      if (row && !isRowEditable(row)) continue;
      if (value !== "" && value !== null && value !== undefined) {
        payload[id] = Number(value);
      }
    }
    return payload;
  }

  // Only send comments the user actually changed (trimmed); an emptied field
  // clears the stored comment. Untouched comments are left alone server-side.
  // Comments on non-editable rows are never sent (backend rejects them).
  function buildCommentsPayload() {
    const payload = {};
    const initial = initialCommentsRef.current;
    for (const [id, value] of Object.entries(comments)) {
      const row = data.submissions.find((s) => String(s.id) === String(id));
      if (row && !isRowEditable(row)) continue;
      const trimmed = (value ?? "").trim();
      if (trimmed !== (initial[id] ?? "").trim()) {
        payload[id] = trimmed;
      }
    }
    return payload;
  }

  function validateTotalWeight() {
    const totalWeight = data.submissions.reduce(
      (sum, submission) => sum + Number(submission.weight_snapshot ?? submission.kpi_template.weight),
      0
    );
    if (totalWeight > 100) {
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
      await employeeSaveScores({ year: data.active_year, period: data.active_period, scores: buildScoresPayload(), comments: buildCommentsPayload() });
      toast.success("Draft saved.");
      load(params);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit() {
    setConfirmSubmit(false);
    setSaving(true);
    setError(null);
    try {
      await employeeSubmit({ year: data.active_year, period: data.active_period, scores: buildScoresPayload(), comments: buildCommentsPayload() });
      toast.success("Submitted for department approval.");
      load(params);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  // Validates first; the actual submit happens from the confirm dialog.
  function requestSubmit() {
    if (!validateScores(scores)) return;
    if (!validateTotalWeight()) return;
    // The backend refuses to submit while any submittable row is unscored —
    // say so here instead of after the confirm dialog.
    const unscored = data.submissions.find(
      (s) => isRowEditable(s) && (scores[s.id] === "" || scores[s.id] === null || scores[s.id] === undefined)
    );
    if (unscored) {
      setError("Please score every metric before submitting.");
      return;
    }
    setError(null);
    setConfirmSubmit(true);
  }

  // Rows for the submit confirmation summary: only rows that will actually
  // be (re)submitted — already-submitted rows stay untouched.
  function submitSummaryRows() {
    return data.submissions.filter(isRowEditable).map((s) => {
      const value = scores[s.id];
      const filled = value !== "" && value !== null && value !== undefined;
      const target = s.target_snapshot ?? s.kpi_template.target;
      return (
        <div key={s.id} className="flex items-center justify-between gap-3 px-3 py-2">
          <span className="min-w-0 truncate text-xs font-medium text-slate-700" title={s.kpi_template.metric_name}>
            {s.kpi_template.metric_name}
          </span>
          {filled ? (
            <span className="shrink-0 rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-semibold text-brand-700 tabular-nums">
              {Number(value)} <span className="font-normal text-brand-500">/ {target}</span>
            </span>
          ) : (
            <span className="shrink-0 rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-medium text-slate-400">
              no score
            </span>
          )}
        </div>
      );
    });
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

      {/* Info banner: this month's KPI set starts on rollover day */}
      {data.rollover_on && !isFutureLocked && (
        <div className="mb-4 rounded-lg bg-sky-50 border border-sky-200 px-4 py-3 flex items-start gap-3">
          <svg className="h-5 w-5 text-sky-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
          <div className="space-y-2">
            <p className="text-sm text-sky-800">
              KPIs for{" "}
              <span className="font-semibold">{data.active_period} {data.active_year}</span>{" "}
              start on{" "}
              <span className="font-semibold">
                {new Date(`${data.rollover_on}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}
              </span>{" "}
              — they will appear here automatically from that day.
            </p>
            {showGraceShortcut && (
              <button
                type="button"
                onClick={() => handleApply({ year: gracePrevYear, period: gracePrevPeriod })}
                className="inline-flex items-center gap-1.5 rounded-full bg-sky-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-sky-700 transition"
              >
                → {gracePrevPeriod} {gracePrevYear} KPIs ကို ဖြည့်ရန် (grace week အတွင်း ဖြည့်လို့ရသေးတယ်)
              </button>
            )}
          </div>
        </div>
      )}

      {/* Info banner: grace month — past month still open after month-end */}
      {data.is_grace_period && !isFutureLocked && (
        <div className="mb-4 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 flex items-start gap-3">
          <svg className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
          <p className="text-sm text-amber-800">
            Grace period — you can still update scores for{" "}
            <span className="font-semibold">{data.active_period} {data.active_year}</span>{" "}
            until{" "}
            <span className="font-semibold">
              {data.grace_ends_on
                ? new Date(`${data.grace_ends_on}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })
                : "the end of the grace period"}
            </span>{" "}
            (7 days after month-end). After that, this month closes.
          </p>
        </div>
      )}

      {/* Info banner: current month — scores open all month */}
      {data.is_current_period && !data.is_grace_period && !isFutureLocked && (
        <div className="mb-4 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 flex items-start gap-3">
          <svg className="h-5 w-5 text-amber-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z" />
          </svg>
          <p className="text-sm text-amber-800">
            You can update actual scores for{" "}
            <span className="font-semibold">{data.active_period} {data.active_year}</span>{" "}
            at any time during this month. Future months remain locked until they begin.
          </p>
        </div>
      )}

      {/* Locked banner: future month navigation */}
      {isFutureLocked && (
        <div className="mb-4 rounded-lg bg-slate-50 border border-slate-200 px-4 py-3 flex items-start gap-3">
          <svg className="h-5 w-5 text-slate-400 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 9.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
          </svg>
          <div>
            <p className="text-sm font-semibold text-slate-600">Future month — score entry locked</p>
            <p className="text-xs text-slate-500 mt-0.5">
              Scores for <span className="font-medium">{data.active_period} {data.active_year}</span> will
              unlock automatically when that month begins.
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

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.submissions.map((s) => (
              <KpiCard
                key={s.id}
                submission={s}
                editableSelf={isRowEditable(s)}
                selfScoreValue={scores[s.id]}
                selfScoreInvalid={invalidScoreId === s.id}
                onSelfScoreChange={handleSelfScoreChange}
                selfCommentValue={comments[s.id]}
                onSelfCommentChange={handleSelfCommentChange}
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
                onClick={requestSubmit}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
              >
                {isResubmit ? "Resubmit for Department Approval" : "Submit for Department Approval"}
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
                </svg>
              </button>
            </div>
          ) : isFutureLocked ? (
            <p className="mt-5 flex items-center gap-1.5 text-sm text-slate-500">
              <svg className="h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 9.75h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
              </svg>
              Future month — score entry unlocks when {data.active_period} begins.
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

      <ConfirmDialog
        open={confirmSubmit}
        title={isResubmit ? "Resubmit for department approval?" : "Submit for department approval?"}
        message={`Your scores for ${data.active_period} ${data.active_year} will be sent to your department admin for review. After submitting you won't be able to edit them unless they are rejected.`}
        details={
          <div className="rounded-xl border border-slate-200 bg-slate-50 divide-y divide-slate-100 max-h-56 overflow-y-auto">
            {submitSummaryRows()}
          </div>
        }
        confirmLabel={isResubmit ? "Resubmit" : "Submit"}
        tone="brand"
        submitting={saving}
        onConfirm={handleSubmit}
        onCancel={() => setConfirmSubmit(false)}
      />
    </div>
  );
}
