import { useState } from "react";
import StatusBadge from "./StatusBadge";
import ScoreBadge from "./ScoreBadge";

const ACCENT = {
  draft: "bg-slate-300",
  pending_dept_approval: "bg-amber-400",
  pending_final_approval: "bg-blue-400",
  approved: "bg-emerald-400",
  rejected: "bg-red-400",
};

export default function KpiCard({
  submission: s,
  editableSelf = false,
  selfScoreValue,
  onSelfScoreChange,
  onDeptSave,
  onDeptApprove,
  onFinalApprove,
  onOverride,
  onReject,
}) {
  const [deptScore, setDeptScore] = useState(s.dept_score ?? s.self_score ?? "");
  const [deptRemarks, setDeptRemarks] = useState("");
  const [finalScore, setFinalScore] = useState(s.final_score ?? s.dept_score ?? "");
  const [overrideScore, setOverrideScore] = useState("");
  const [overrideRemarks, setOverrideRemarks] = useState("");
  const [overrideOpen, setOverrideOpen] = useState(false);

  const canReject = onReject && (s.status === "pending_dept_approval" || s.status === "pending_final_approval");

  return (
    <div className="relative rounded-xl border border-slate-200 bg-white pt-4 px-4 pb-4 flex flex-col gap-3 overflow-hidden hover:shadow-md hover:border-slate-300 hover:-translate-y-0.5 transition-all">
      <span className={`absolute inset-x-0 top-0 h-1 ${ACCENT[s.status]}`}></span>

      <div>
        <h4
          className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2 min-h-[2.5rem]"
          title={s.kpi_template.metric_name}
        >
          {s.kpi_template.metric_name}
        </h4>
        <p className="text-[11px] text-slate-400 mt-0.5">
          Target {s.kpi_template.target} · Weight {s.kpi_template.weight}
        </p>
      </div>

      <StatusBadge status={s.status} />

      <div className="grid grid-cols-3 gap-1.5 rounded-lg bg-slate-50 px-1.5 py-3">
        <div className="text-center">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Self</p>
          {editableSelf ? (
            <input
              type="number"
              step="0.1"
              min="0"
              max="200"
              value={selfScoreValue ?? ""}
              onChange={(e) => onSelfScoreChange(s.id, e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-1 py-1.5 text-sm text-center tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          ) : (
            <ScoreBadge value={s.self_score} target={s.kpi_template.target} />
          )}
        </div>
        <div className="text-center border-x border-slate-200">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Dept</p>
          <ScoreBadge value={s.dept_score} target={s.kpi_template.target} />
        </div>
        <div className="text-center">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Final</p>
          <ScoreBadge value={s.final_score} target={s.kpi_template.target} size="md" />
        </div>
      </div>

      {onDeptApprove && s.status === "pending_dept_approval" && (
        <div className="pt-1 border-t border-slate-100 space-y-2 mt-auto">
          <div className="flex items-center gap-2 pt-2">
            <label className="text-[11px] font-medium text-slate-500 shrink-0">Score</label>
            <input
              type="number"
              step="0.1"
              min="0"
              max="200"
              value={deptScore}
              onChange={(e) => setDeptScore(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <input
            type="text"
            placeholder="Remarks (optional)"
            value={deptRemarks}
            onChange={(e) => setDeptRemarks(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => onDeptSave(s.id, Number(deptScore), deptRemarks || undefined)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:border-slate-400 transition"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => onDeptApprove(s.id, Number(deptScore), deptRemarks || undefined)}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-brand-700 transition"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
              </svg>
              Approve
            </button>
            {onReject && (
              <button
                type="button"
                onClick={() => onReject(s.id)}
                title="Reject"
                className="rounded-md border border-red-200 px-2.5 py-1.5 text-red-600 hover:bg-red-50 transition"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      {(onFinalApprove || onOverride || canReject) && (
        <div className="pt-1 border-t border-slate-100 space-y-2 mt-auto">
          {onFinalApprove && s.status === "pending_final_approval" && (
            <div className="space-y-2 pt-2">
              <input
                type="number"
                step="0.1"
                min="0"
                max="200"
                value={finalScore}
                onChange={(e) => setFinalScore(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <button
                type="button"
                onClick={() => onFinalApprove(s.id, Number(finalScore))}
                className="w-full inline-flex items-center justify-center gap-1 rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700 transition whitespace-nowrap"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
                </svg>
                Approve
              </button>
            </div>
          )}

          {onOverride && (
            <div>
              <button
                type="button"
                onClick={() => setOverrideOpen((v) => !v)}
                className="cursor-pointer inline-flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-700 transition"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 0 0 2.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 0 0 1.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 0 0-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 0 0-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 0 0-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 0 0-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 0 0 1.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065Z"
                  />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                </svg>
                Override score
              </button>
              {overrideOpen && (
                <div className="mt-2 space-y-2">
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="200"
                    placeholder="Score"
                    value={overrideScore}
                    onChange={(e) => setOverrideScore(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <input
                    type="text"
                    placeholder="Reason for override"
                    value={overrideRemarks}
                    onChange={(e) => setOverrideRemarks(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <button
                    type="button"
                    onClick={() => onOverride(s.id, Number(overrideScore), overrideRemarks || undefined)}
                    className="w-full rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-slate-800 transition"
                  >
                    Override &amp; Approve
                  </button>
                </div>
              )}
            </div>
          )}

          {canReject && (
            <button
              type="button"
              onClick={() => onReject(s.id)}
              className="text-xs font-medium text-red-600 hover:text-red-700 transition"
            >
              Reject
            </button>
          )}
        </div>
      )}
    </div>
  );
}
