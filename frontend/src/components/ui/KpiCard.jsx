import { useState } from "react";
import StatusBadge from "./StatusBadge";
import ScoreBadge from "./ScoreBadge";
import ConfirmDialog from "./ConfirmDialog";
import AttainmentRing from "./AttainmentRing";
import { useToast } from "../../context/ToastContext";

const TARGET_EXCEEDED_MESSAGE = "Target ထက်ကျော်နေပါ၍ပြန်ထည့်ရန်";

// Gradient version of the status accent for the card's top bar.
const ACCENT_GRAD = {
  draft: "from-slate-300 to-slate-200",
  pending_dept_approval: "from-amber-400 to-orange-300",
  pending_final_approval: "from-blue-400 to-sky-300",
  approved: "from-emerald-400 to-green-300",
  rejected: "from-red-400 to-rose-300",
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

// One score tile (Self / Dept / Final): tier-colored badge plus a mini
// attainment bar against the target. Purely presentational.
function ScoreTile({ label, value, target, children }) {
  const attainment =
    value === null || value === undefined || !(Number(target) > 0)
      ? null
      : Math.min(100, (Number(value) / Number(target)) * 100);
  const bar =
    attainment === null
      ? null
      : attainment > 75
        ? "bg-emerald-500"
        : attainment > 50
          ? "bg-amber-500"
          : "bg-red-500";
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/70 px-2 py-2 text-center">
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">{label}</p>
      {children ?? <ScoreBadge value={value} target={target} />}
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-200/70">
        {bar && <div className={`h-full rounded-full ${bar} transition-all`} style={{ width: `${attainment}%` }} />}
      </div>
      <p className="mt-1 h-3 text-[10px] font-semibold tabular-nums text-slate-400">
        {attainment !== null ? `${Math.round((Number(value) / Number(target)) * 100)}%` : ""}
      </p>
    </div>
  );
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
  onReviewScoreChange,
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

  // Minimal summary inside the confirmation dialog: just who + the score
  // being approved. Everything else is already visible on the card behind.
  function approvalDetails() {
    if (!confirm) return null;
    if (confirm.kind === "reject") {
      const reason = (s.status === "pending_final_approval" ? finalRemarks : deptRemarks)?.trim();
      if (!reason) return null;
      return (
        <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2">
          <p className="text-[11px] text-red-900 whitespace-pre-wrap leading-relaxed">
            <span className="font-semibold">Reason:</span> {reason}
          </p>
        </div>
      );
    }
    const approving =
      confirm.kind === "deptApprove"
        ? { label: "Dept", value: deptScore }
        : confirm.kind === "finalApprove"
          ? { label: "Final", value: finalScore }
          : { label: "Override", value: overrideScore };
    return (
      <div className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
        {s.employee?.name && (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-200 text-[10px] font-bold text-slate-600">
              {s.employee.name[0]?.toUpperCase()}
            </span>
            <span className="truncate text-xs font-semibold text-slate-700">{s.employee.name}</span>
          </span>
        )}
        <span className="shrink-0 whitespace-nowrap rounded-lg bg-gradient-to-r from-brand-600 to-brand-500 px-2.5 py-1 text-[11px] font-bold text-white tabular-nums shadow-sm">
          {approving.label} {approving.value}
        </span>
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
    // Lets the review board's Approve-all collect each card's current
    // reviewer input (dept/final paths only — self scores and overrides
    // are never bulk-approved).
    if (onReviewScoreChange && (field === "dept" || field === "final")) {
      onReviewScoreChange(s.id, field, value);
    }
    if (value !== "" && Number(value) > Number(snapTarget)) {
      setInvalidScoreField(field);
      toast.error(TARGET_EXCEEDED_MESSAGE);
    } else if (invalidScoreField === field && value !== "" && Number(value) <= Number(snapTarget)) {
      setInvalidScoreField(null);
    }
  }

  return (
    <div className="relative rounded-2xl border border-slate-200/80 bg-white px-4 py-3 flex flex-col gap-2.5 overflow-hidden shadow-sm hover:shadow-lg hover:border-slate-300 hover:-translate-y-0.5 transition-all">
      <span className={`absolute inset-x-0 top-0 h-1.5 bg-gradient-to-r ${ACCENT_GRAD[s.status]}`}></span>

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4
            className="text-sm font-bold text-slate-900 leading-snug break-words"
            title={s.kpi_template.metric_name}
          >
            {s.kpi_template.metric_name}
          </h4>
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 tabular-nums">
              Target {snapTarget}
            </span>
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 tabular-nums">
              Weight {s.weight_snapshot ?? s.kpi_template.weight}
            </span>
          </div>
        </div>
        <AttainmentRing value={attainment.value} label={attainment.label} />
      </div>

      <StatusBadge status={s.status} />

      <div className={`grid ${hideDeptColumn ? "grid-cols-2" : "grid-cols-3"} gap-2`}>
        <ScoreTile label="Self" value={s.self_score} target={snapTarget}>
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
              className={`w-full rounded-lg border px-2 py-2 text-base font-bold text-center tabular-nums focus:outline-none focus:ring-2 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none ${selfScoreInvalid ? "border-red-500 focus:ring-red-500" : "border-slate-300 focus:ring-brand-500"}`}
            />
          ) : undefined}
        </ScoreTile>
        {!hideDeptColumn && (
          <ScoreTile label="Dept" value={s.dept_score} target={snapTarget} />
        )}
        <ScoreTile label="Final" value={s.final_score} target={snapTarget} />
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
        <div className="rounded-lg bg-sky-50 border border-sky-100 px-2.5 py-2">
          <p className="text-[10px] font-semibold text-sky-700 uppercase tracking-wide mb-0.5">
            Why this score
          </p>
          <p className="text-xs text-sky-900 leading-relaxed whitespace-pre-wrap line-clamp-3" title={s.self_score_comment}>{s.self_score_comment}</p>
        </div>
      )}

      {s.remarks && (
        <div className="rounded-lg bg-amber-50 border border-amber-100 px-2.5 py-2">
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
            <div className="flex items-center gap-1.5 mt-1.5 pt-1.5 border-t border-amber-200/70">
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
        message={confirm?.kind === "reject" ? confirmMessage() : undefined}
        details={approvalDetails()}
        confirmLabel={confirm?.kind === "reject" ? "Reject" : confirm?.kind === "override" ? "Override" : "Approve"}
        tone={confirmTone()}
        onConfirm={handleConfirmAction}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}
