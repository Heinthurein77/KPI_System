const TIER_COLOR = {
  info: { text: "text-brand-600", bg: "bg-brand-50", glow: "bg-brand-400/25", bar: "bg-brand-500" },
  good: { text: "text-emerald-600", bg: "bg-emerald-50", glow: "bg-emerald-400/25", bar: "bg-emerald-500" },
  warning: { text: "text-amber-600", bg: "bg-amber-50", glow: "bg-amber-400/25", bar: "bg-amber-500" },
  critical: { text: "text-red-600", bg: "bg-red-50", glow: "bg-red-400/25", bar: "bg-red-500" },
};

// Thresholds match dashboard.py: >75 = "Performance", >50 = "Normal", ≤50 = "Need to improve"
function scoreTier(attainment) {
  if (attainment > 75) return "good";
  if (attainment > 50) return "warning";
  return "critical";
}

const ICON = {
  people: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M17 20h5v-2a4 4 0 0 0-3-3.87M9 20H4v-2a4 4 0 0 1 3-3.87m5-8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm6 3a4 4 0 1 1-2.6-3.75"
    />
  ),
  score: (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
    />
  ),
  pending: <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l4 2m6-2a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z" />,
  approved: <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />,
};

function StatCard({ label, value, detail, accent, icon }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className={`pointer-events-none absolute -top-8 -right-8 h-24 w-24 rounded-full blur-2xl ${accent.glow}`}></div>
      <div className="relative flex items-start gap-3">
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${accent.bg}`}>
          <svg className={`h-4.5 w-4.5 ${accent.text}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            {icon}
          </svg>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">{label}</p>
          <p className="mt-1 text-2xl font-bold text-slate-900 leading-none tabular-nums truncate">{value}</p>
          <p className="mt-1.5 text-xs text-slate-500 truncate">{detail}</p>
        </div>
      </div>
    </div>
  );
}

export default function TeamOverview({ data, pendingStatuses, pendingLabel }) {
  const combinedEntries = Object.entries(data.employee_combined || {});
  const scored = combinedEntries.filter(([, c]) => c);
  const peopleCount = combinedEntries.length;
  const avgAttainment = scored.length ? scored.reduce((sum, [, c]) => sum + c.attainment, 0) / scored.length : null;
  const pendingCount = data.submissions.filter((s) => pendingStatuses.includes(s.status)).length;
  const approvedCount = data.submissions.filter((s) => s.status === "approved").length;

  const avgTier = avgAttainment !== null ? TIER_COLOR[scoreTier(avgAttainment)] : TIER_COLOR.warning;

  return (
    <div className="mb-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="People"
          value={peopleCount}
          detail={`${data.active_period} ${data.active_year}`}
          accent={TIER_COLOR.info}
          icon={ICON.people}
        />
        <StatCard
          label="Average Score"
          value={avgAttainment !== null ? `${avgAttainment.toFixed(0)}%` : "—"}
          detail={scored.length ? `Across ${scored.length} finalized ${scored.length === 1 ? "person" : "people"}` : "No finalized scores yet"}
          accent={avgTier}
          icon={ICON.score}
        />
        <StatCard label="Pending Review" value={pendingCount} detail={pendingLabel} accent={TIER_COLOR.warning} icon={ICON.pending} />
        <StatCard
          label="Approved"
          value={approvedCount}
          detail={`${data.active_period} ${data.active_year}`}
          accent={TIER_COLOR.good}
          icon={ICON.approved}
        />
      </div>
    </div>
  );
}
