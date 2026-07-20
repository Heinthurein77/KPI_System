const CHIP_STYLES = {
  good: "bg-emerald-50 text-emerald-700",
  warning: "bg-amber-50 text-amber-700",
  critical: "bg-red-50 text-red-700",
};
const ACCENT = { good: "bg-emerald-500", warning: "bg-amber-500", critical: "bg-red-500" };
const LABELS = { good: "On Target", warning: "Near Target", critical: "Below Target" };

const ICONS = {
  good: <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />,
  warning: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L14.71 3.86a2 2 0 0 0-3.42 0Z"
    />
  ),
  critical: <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />,
};

export default function CombinedScoreCard({ combined }) {
  if (!combined) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-5 py-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-400">
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l2.5 2.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
        </div>
        <div>
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">Combined Final KPI Score</p>
          <p className="text-sm text-slate-400">Appears once at least one metric is final-approved</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex items-center gap-5 rounded-xl border border-slate-200 bg-white shadow-sm pl-5 pr-5 py-4 overflow-hidden">
      <span className={`absolute inset-y-0 left-0 w-1 ${ACCENT[combined.status]}`}></span>
      <div className="min-w-0">
        <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Combined Final KPI Score</p>
        <p className="mt-0.5 text-4xl font-bold text-slate-900 leading-none">{combined.attainment.toFixed(0)}</p>
        <p className="mt-1.5 text-xs text-slate-400">
          {combined.scored_count} of {combined.total_count} metrics finalized
        </p>
      </div>
      <div className="ml-auto shrink-0">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ${CHIP_STYLES[combined.status]}`}
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.2">
            {ICONS[combined.status]}
          </svg>
          {LABELS[combined.status]}
        </span>
      </div>
    </div>
  );
}
