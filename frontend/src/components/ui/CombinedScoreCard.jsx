const TIER = {
  good: {
    ring: "#10b981", // emerald-500
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    glow: "bg-emerald-400/25",
    wash: "from-emerald-50/80",
    label: "On Target",
    icon: <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />,
  },
  warning: {
    ring: "#f59e0b", // amber-500
    chip: "bg-amber-50 text-amber-700 ring-amber-200",
    glow: "bg-amber-400/25",
    wash: "from-amber-50/80",
    label: "Near Target",
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L14.71 3.86a2 2 0 0 0-3.42 0Z"
      />
    ),
  },
  critical: {
    ring: "#ef4444", // red-500
    chip: "bg-red-50 text-red-700 ring-red-200",
    glow: "bg-red-400/25",
    wash: "from-red-50/80",
    label: "Below Target",
    icon: <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />,
  },
};

const RADIUS = 32;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

function ScoreRing({ attainment, color }) {
  const pct = Math.min(Math.max(attainment, 0), 100);
  const offset = CIRCUMFERENCE - (CIRCUMFERENCE * pct) / 100;

  return (
    <div className="relative h-20 w-20 shrink-0">
      <svg viewBox="0 0 80 80" className="h-20 w-20 -rotate-90">
        <circle cx="40" cy="40" r={RADIUS} fill="none" stroke="currentColor" strokeWidth="8" className="text-slate-100" />
        <circle
          cx="40"
          cy="40"
          r={RADIUS}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          className="transition-[stroke-dashoffset] duration-700 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold text-slate-900 leading-none tabular-nums">{attainment.toFixed(0)}</span>
        <span className="text-[9px] font-semibold text-slate-400 mt-0.5">%</span>
      </div>
    </div>
  );
}

export default function CombinedScoreCard({ combined }) {
  if (!combined) {
    return (
      <div className="flex items-center gap-4 rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-5 py-4">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-2 border-dashed border-slate-200 text-slate-300">
          <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l2.5 2.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
          </svg>
        </div>
        <div>
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide">Combined Final KPI Score</p>
          <p className="text-sm text-slate-400 mt-0.5">Appears once at least one metric is final-approved</p>
        </div>
      </div>
    );
  }

  const tier = TIER[combined.status];

  return (
    <div
      className={`relative flex items-center gap-5 overflow-hidden rounded-2xl border border-slate-200 bg-gradient-to-br ${tier.wash} via-white to-white px-5 py-4 shadow-sm`}
    >
      <div className={`pointer-events-none absolute -top-10 -right-10 h-32 w-32 rounded-full blur-3xl ${tier.glow}`}></div>

      <ScoreRing attainment={combined.attainment} color={tier.ring} />

      <div className="relative min-w-0 flex-1">
        <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">Combined Final KPI Score</p>
        <p className="mt-1 text-2xl font-bold text-slate-900 leading-none tabular-nums">
          {combined.attainment.toFixed(0)}
          <span className="text-sm font-semibold text-slate-400">%</span>
        </p>
        <p className="mt-1.5 text-xs text-slate-500">
          {combined.scored_count} of {combined.total_count} metrics finalized
        </p>
      </div>

      <span
        className={`relative ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ring-1 ring-inset ${tier.chip}`}
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.2">
          {tier.icon}
        </svg>
        {tier.label}
      </span>
    </div>
  );
}
