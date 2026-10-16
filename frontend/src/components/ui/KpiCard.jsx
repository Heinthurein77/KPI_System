import { useState } from "react";
import StatusBadge from "./StatusBadge";
import ScoreBadge from "./ScoreBadge";
import ConfirmDialog from "./ConfirmDialog";
import AttainmentRing from "./AttainmentRing";
import { useToast } from "../../context/ToastContext";

const TARGET_EXCEEDED_MESSAGE = "Target ထက်ကျော်နေပါ၍ပြန်ထည့်ရန်";

const ACCENT = {
  draft: "bg-slate-300",
  pending_dept_approval: "bg-amber-400",
  pending_final_approval: "bg-blue-400",
  approved: "bg-emerald-400",
  rejected: "bg-red-400",
};

const ROLE_LABELS = {
  super_admin: "Super Admin",
  tenant_admin: "Tenant Admin",
  dept_admin: "Dept Admin",
  employee: "Employee",
};

// `Number("")` is 0, not NaN, so an emptied or never-filled score field would
// otherwise submit silently as a real 0 score/approval. Also enforces the
// same 0–200 range the inputs advertise via min/max, which the browser never
// validates here since these are plain buttons outside a <form>.
function isValidScore(value, target) {
  if (value === "" || value === null || value === undefined) return false;
  const n = Number(value);
  return !Number.isNaN(n) && n >= 0 && n <= 200 && n <= Number(target);
}

function remarkAuthor(s) {
  const deptAt = s.dept_reviewed_at ? new Date(s.dept_reviewed_at) : null;
  const finalAt = s.final_reviewed_at ? new Date(s.final_reviewed_at) : null;
  if (finalAt && (!deptAt || finalAt >= deptAt)) {
    return s.final_reviewer ? { user: s.final_reviewer, at: finalAt } : null;
  }
  return s.dept_reviewer ? { user: s.dept_reviewer, at: deptAt } : null;
}

// Attainment for the card's hero ring: best available score vs target,
// capped at 120 like the backend. Prefers the most-finalized score.
function attainmentFor(s, target) {
  const t = Number(target);
  const score = s.final_score ?? s.dept_score ?? s.self_score;
  if (score == null || !(t > 0)) return { value: null, label: null };
  const label = s.final_score != null ? "Final" : s.dept_score != null ? "Dept" : "Self";
  return { value: Math.min(120, (Number(score) / t) * 100), label };
}

export default function KpiCard({
  submission: s,
  editableSelf = false,
  selfScoreValue,
  selfScoreInvalid = false,
  onSelfScoreChange,
  selfCommentValue,
  onSelfCommentChange,
  onDeptSave,
  onDeptApprove,
  onFinalApprove,
  onOverride,
  onReject,
}) {
  const toast = useToast();
  const [deptScore, setDeptScore] = useState(s.dept_score ?? s.self_score ?? "");
  const [deptRemarks, setDeptRemarks] = useState("");
  const [finalScore, setFinalScore] = useState(s.final_score ?? s.dept_score ?? "");
  const [finalRemarks, setFinalRemarks] = useState("");
  const [overrideScore, setOverrideScore] = useState("");
  const [overrideRemarks, setOverrideRemarks] = useState("");
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [invalidScoreField, setInvalidScoreField] = useState(null);
  // Pending destructive/confirmatory action: { kind: "deptApprove" | "finalApprove" | "reject" }
  const [confirm, setConfirm] = useState(null);

  // Target/weight in force when this submission was created (frozen snapshot
  // with fallback to the live template for pre-migration rows).
  const snapTarget = s.target_snapshot ?? s.kpi_template.target;

  const attainment = attainmentFor(s, snapTarget);

  const canReject = onReject && (s.status === "pending_dept_approval" || s.status === "pending_final_approval");
  const author = remarkAuthor(s);
  // A Dept Admin's own submission skips the dept-review display: the middle
  // DEPT column is redundant for them (self approval), so show only SELF/FINAL.
  const hideDeptColumn = s.employee?.role === "dept_admin";

  function handleConfirmAction() {
    if (!confirm) return;
    if (confirm.kind === "deptApprove") {
      onDeptApprove(s.id, Number(deptScore), deptRemarks || undefined);
    } else if (confirm.kind === "finalApprove") {
      onFinalApprove(s.id, Number(finalScore));
    } else if (confirm.kind === "override") {
      onOverride(s.id, Number(overrideScore), overrideRemarks || undefined);
    } else if (confirm.kind === "reject") {
      // The remarks field doubles as the rejection reason — deptRemarks on the
      // department path, finalRemarks on the final-approval path.
      const reason = s.status === "pending_final_approval" ? finalRemarks : deptRemarks;
      onReject(s.id, reason || undefined);
    }
    setConfirm(null);
  }

  function confirmTitle() {
    if (confirm?.kind === "reject") return "Reject this submission?";
    if (confirm?.kind === "override") return "Override and approve?";
    return "Approve this submission?";
  }

  function confirmMessage() {
    const metric = s.kpi_template.metric_name;
    if (confirm?.kind === "reject") {
      return `The employee will need to revise and resubmit "${metric}".`;
    }
    if (confirm?.kind === "override") {
      return `Set the final score for "${metric}" and approve it?`;
    }
    return `Approve "${metric}" and move it to the next stage?`;
  }

  // Rich summary shown inside the approval confirmation dialog: the score
  // being approved, the employee's own justification, and any typed note.
  function approvalDetails() {
    if (!confirm) return null;
    if (confirm.kind === "reject") {
      const reason = (s.status === "pending_final_approval" ? finalRemarks : deptRemarks)?.trim();
      if (!reason) return null;
      return (
        <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2.5">
          <p className="text-[10px] font-semibold text-red-600 uppercase tracking-wide mb-1">Rejection reason</p>
          <p className="text-xs text-red-900 whitespace-pre-wrap leading-relaxed">{reason}</p>
        </div>
      );
    }
    const approving =
      confirm.kind === "deptApprove"
        ? { label: "Dept", value: deptScore }
        : confirm.kind === "finalApprove"
          ? { label: "Final", value: finalScore }
          : { label: "Override", value: overrideScore };
    const remarksText = (
      confirm.kind === "deptApprove" ? deptRemarks : confirm.kind === "finalApprove" ? finalRemarks : overrideRemarks
    )?.trim();
    return (
      <div className="space-y-2">
        {s.employee?.name && (
          <div className="flex items-center gap-1.5">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-bold text-slate-600">
              {s.employee.name[0]?.toUpperCase()}
            </div>
            <span className="truncate text-[11px] font-semibold text-slate-600">{s.employee.name}</span>
          </div>
        )}
        <div className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <span className="text-[11px] text-slate-500">
            Self <span className="font-bold text-slate-700 tabular-nums">{s.self_score ?? "—"}</span>
          </span>
          <svg className="h-3.5 w-3.5 shrink-0 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
            <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
          </svg>
          <span className="rounded-full bg-gradient-to-r from-brand-600 to-brand-500 px-2.5 py-1 text-[11px] font-bold text-white tabular-nums shadow-sm whitespace-nowrap">
            {approving.label} {approving.value}
          </span>
        </div>
        {s.self_score_comment && (
          <p className="text-[11px] italic leading-relaxed text-slate-500 line-clamp-2">“{s.self_score_comment}”</p>
        )}
        {remarksText && (
          <p className="text-[11px] text-slate-600 leading-relaxed line-clamp-2">
            <span className="font-semibold text-slate-700">Note:</span> {remarksText}
          </p>
        )}
      </div>
    );
  }

  function confirmTone() {
    if (confirm?.kind === "reject") return "danger";
    if (confirm?.kind === "override") return "info";
    if (confirm?.kind === "finalApprove") return "success";
    return "brand";
  }

  function handleScoreChange(setScore, field, value) {
    setScore(value);
    if (value !== "" && Number(value) > Number(snapTarget)) {
      setInvalidScoreField(field);
      toast.error(TARGET_EXCEEDED_MESSAGE);
    } else if (invalidScoreField === field && value !== "" && Number(value) <= Number(snapTarget)) {
      setInvalidScoreField(null);
    }
  }

  return (
    <div className="relative rounded-xl border border-slate-200 bg-white pt-4 px-4 pb-4 flex flex-col gap-3 overflow-hidden hover:shadow-md hover:border-slate-300 hover:-translate-y-0.5 transition-all">
      <span className={`absolute inset-x-0 top-0 h-1 ${ACCENT[s.status]}`}></span>

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4
            className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2 min-h-[2.5rem]"
            title={s.kpi_template.metric_name}
          >
            {s.kpi_template.metric_name}
          </h4>
          <p className="text-[11px] text-slate-400 mt-0.5">
            Target {snapTarget} · Weight {s.weight_snapshot ?? s.kpi_template.weight}
          </p>
        </div>
        <AttainmentRing value={attainment.value} label={attainment.label} />
      </div>

      <StatusBadge status={s.status} />

      <div className={`grid ${hideDeptColumn ? "grid-cols-2" : "grid-cols-3"} gap-1.5 rounded-lg bg-slate-50 px-1.5 py-3`}>
        <div className="text-center">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Self</p>
          {editableSelf ? (
            <input
              id={`self-score-${s.id}`}
              type="number"
              step="0.1"
              min="0"
              max="200"
              value={selfScoreValue ?? ""}
              onChange={(e) => onSelfScoreChange(s.id, e.target.value)}
              aria-invalid={selfScoreInvalid}
              className={`w-full rounded-lg border px-1 py-1.5 text-sm text-center tabular-nums focus:outline-none focus:ring-2 ${selfScoreInvalid ? "border-red-500 focus:ring-red-500" : "border-slate-300 focus:ring-brand-500"}`}
            />
          ) : (
            <ScoreBadge value={s.self_score} target={snapTarget} />
          )}
        </div>
        {!hideDeptColumn && (
          <div className="text-center border-x border-slate-200">
            <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Dept</p>
            <ScoreBadge value={s.dept_score} target={snapTarget} />
          </div>
        )}
        <div className="text-center">
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-1.5">Final</p>
          <ScoreBadge value={s.final_score} target={snapTarget} size="md" />
        </div>
      </div>

      {editableSelf && (
        <div>
          <label
            htmlFor={`self-comment-${s.id}`}
            className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide"
          >
            Why this score? <span className="normal-case font-normal">(optional)</span>
          </label>
          <textarea
            id={`self-comment-${s.id}`}
            rows={2}
            maxLength={2000}
            placeholder="e.g. Closed 14 tickets, 2 above target…"
            value={selfCommentValue ?? ""}
            onChange={(e) => onSelfCommentChange(s.id, e.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500 resize-y"
          />
        </div>
      )}

      {s.self_score_comment && !editableSelf && (
        <div className="rounded-lg bg-sky-50 border border-sky-100 px-3 py-2.5">
          <p className="text-[10px] font-semibold text-sky-700 uppercase tracking-wide mb-1">
            Why this score
          </p>
          <p className="text-xs text-sky-900 leading-relaxed whitespace-pre-wrap">{s.self_score_comment}</p>
        </div>
      )}

      {s.remarks && (
        <div className="rounded-lg bg-amber-50 border border-amber-100 px-3 py-2.5">
          <div className="flex items-center justify-between gap-2 mb-1">
            <p className="text-[10px] font-semibold text-amber-700 uppercase tracking-wide">Remarks</p>
            {author?.at && (
              <span className="text-[10px] text-amber-600/80 tabular-nums shrink-0">
                {author.at.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
              </span>
            )}
          </div>
          <p className="text-xs text-amber-900 leading-relaxed whitespace-pre-wrap">{s.remarks}</p>
          {author?.user && (
            <div className="flex items-center gap-1.5 mt-2 pt-2 border-t border-amber-200/70">
              <div className="h-5 w-5 shrink-0 rounded-full bg-amber-200 text-amber-800 flex items-center justify-center text-[10px] font-semibold">
                {author.user.name[0]?.toUpperCase()}
              </div>
              <span className="text-[11px] font-medium text-amber-900 truncate">{author.user.name}</span>
              <span className="text-[10px] text-amber-600 shrink-0">
                · {ROLE_LABELS[author.user.role] || author.user.role}
              </span>
            </div>
          )}
        </div>
      )}

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
              onChange={(e) => handleScoreChange(setDeptScore, "dept", e.target.value)}
              aria-invalid={invalidScoreField === "dept"}
              className={`w-full rounded-lg border px-2 py-1.5 text-sm focus:outline-none focus:ring-2 ${invalidScoreField === "dept" ? "border-red-500 focus:ring-red-500" : "border-slate-300 focus:ring-brand-500"}`}
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
              disabled={!isValidScore(deptScore, snapTarget)}
              onClick={() => onDeptSave(s.id, Number(deptScore), deptRemarks || undefined)}
              className="rounded-md border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:border-slate-400 transition disabled:opacity-60"
            >
              Save
            </button>
            <button
              type="button"
              disabled={!isValidScore(deptScore, snapTarget)}
              onClick={() => setConfirm({ kind: "deptApprove" })}
              className="flex-1 inline-flex items-center justify-center gap-1 rounded-xl bg-gradient-to-r from-brand-600 to-brand-500 px-3 py-2 text-xs font-bold text-white shadow-md shadow-brand-500/25 hover:shadow-lg hover:-translate-y-px active:translate-y-0 transition disabled:opacity-60 disabled:hover:translate-y-0"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
              </svg>
              Approve
            </button>
            {onReject && (
              <button
                type="button"
                onClick={() => setConfirm({ kind: "reject" })}
                title="Reject"
                aria-label="Reject submission"
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
              <div className="flex items-center gap-2">
                <label className="text-[11px] font-medium text-slate-500 shrink-0">Score</label>
                <input
                  type="number"
                  step="0.1"
                  min="0"
                  max="200"
                  value={finalScore}
                  onChange={(e) => handleScoreChange(setFinalScore, "final", e.target.value)}
                  aria-invalid={invalidScoreField === "final"}
                  className={`w-full rounded-lg border px-2 py-1.5 text-sm focus:outline-none focus:ring-2 ${invalidScoreField === "final" ? "border-red-500 focus:ring-red-500" : "border-slate-300 focus:ring-brand-500"}`}
                />
              </div>
              <input
                type="text"
                placeholder="Remarks (optional — used as the reason if rejected)"
                value={finalRemarks}
                onChange={(e) => setFinalRemarks(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <button
                type="button"
                disabled={!isValidScore(finalScore, snapTarget)}
                onClick={() => setConfirm({ kind: "finalApprove" })}
                className="w-full inline-flex items-center justify-center gap-1 rounded-xl bg-gradient-to-r from-emerald-600 to-green-500 px-3 py-2 text-xs font-bold text-white shadow-md shadow-emerald-500/25 hover:shadow-lg hover:-translate-y-px active:translate-y-0 transition whitespace-nowrap disabled:opacity-60 disabled:hover:translate-y-0"
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
                    onChange={(e) => handleScoreChange(setOverrideScore, "override", e.target.value)}
                    aria-invalid={invalidScoreField === "override"}
                    className={`w-full rounded-lg border px-2 py-1.5 text-xs focus:outline-none focus:ring-2 ${invalidScoreField === "override" ? "border-red-500 focus:ring-red-500" : "border-slate-300 focus:ring-brand-500"}`}
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
                    disabled={!isValidScore(overrideScore, snapTarget)}
                    onClick={() => setConfirm({ kind: "override" })}
                    className="w-full rounded-xl bg-gradient-to-r from-indigo-600 to-violet-500 px-3 py-2 text-xs font-bold text-white shadow-md shadow-indigo-500/25 hover:shadow-lg hover:-translate-y-px active:translate-y-0 transition disabled:opacity-60 disabled:hover:translate-y-0"
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
              onClick={() => setConfirm({ kind: "reject" })}
              className="text-xs font-medium text-red-600 hover:text-red-700 transition"
            >
              Reject
            </button>
          )}
        </div>
      )}
      <ConfirmDialog
        open={!!confirm}
        title={confirmTitle()}
        message={confirmMessage()}
        details={approvalDetails()}
        confirmLabel={confirm?.kind === "reject" ? "Reject" : confirm?.kind === "override" ? "Override" : "Approve"}
        tone={confirmTone()}
        onConfirm={handleConfirmAction}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}
