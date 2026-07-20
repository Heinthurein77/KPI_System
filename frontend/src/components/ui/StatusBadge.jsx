const STYLES = {
  draft: "bg-slate-100 text-slate-600",
  pending_dept_approval: "bg-amber-100 text-amber-700",
  pending_final_approval: "bg-blue-100 text-blue-700",
  approved: "bg-emerald-100 text-emerald-700",
  rejected: "bg-red-100 text-red-700",
};

const LABELS = {
  draft: "Draft",
  pending_dept_approval: "Pending Dept Approval",
  pending_final_approval: "Pending Final Approval",
  approved: "Approved",
  rejected: "Rejected",
};

export default function StatusBadge({ status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
        STYLES[status] || "bg-slate-100 text-slate-600"
      }`}
    >
      {LABELS[status] || status}
    </span>
  );
}
