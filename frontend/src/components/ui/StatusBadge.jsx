import { STYLES, LABELS } from "./statusMeta";

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
