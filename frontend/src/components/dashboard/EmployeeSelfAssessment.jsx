import { useEffect, useState } from "react";
import PeriodSelector from "../ui/PeriodSelector";
import CombinedScoreCard from "../ui/CombinedScoreCard";
import KpiCard from "../ui/KpiCard";
import EmptyState from "../ui/EmptyState";
import { employeeSaveScores, employeeSubmit } from "../../api/kpi";
import { getErrorMessage } from "../../api/errors";

export default function EmployeeSelfAssessment({ fetcher, emptyTitle = "No KPI records" }) {
  const [data, setData] = useState(null);
  const [scores, setScores] = useState({});
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [params, setParams] = useState({});

  function load(nextParams) {
    fetcher(nextParams).then((d) => {
      setData(d);
      const initial = {};
      d.submissions.forEach((s) => {
        initial[s.id] = s.self_score ?? "";
      });
      setScores(initial);
    });
  }

  useEffect(() => {
    load(params);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) return null;

  const isEditable = data.is_current_period && data.submissions[0]?.status === "draft";

  function handleApply(nextParams) {
    setParams(nextParams);
    load(nextParams);
  }

  function handleSelfScoreChange(id, value) {
    setScores((prev) => ({ ...prev, [id]: value }));
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

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await employeeSaveScores({ year: data.active_year, period: data.active_period, scores: buildScoresPayload() });
      load(params);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit() {
    setSaving(true);
    setError(null);
    try {
      await employeeSubmit({ year: data.active_year, period: data.active_period, scores: buildScoresPayload() });
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

      {data.submissions.length === 0 ? (
        <EmptyState title={emptyTitle} message={`Nothing found for ${data.active_period} ${data.active_year}.`} />
      ) : (
        <>
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

          {data.submissions[0]?.remarks && (
            <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm px-6 py-5">
              <h3 className="text-sm font-semibold text-slate-800 mb-1.5">Reviewer Remarks</h3>
              <p className="text-sm text-slate-600 leading-relaxed">{data.submissions[0].remarks}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
