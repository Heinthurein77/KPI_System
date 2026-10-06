const STYLES = {
  good: "bg-emerald-50 text-emerald-700",
  warning: "bg-amber-50 text-amber-700",
  critical: "bg-red-50 text-red-700",
};

const RATING_LABELS = {
  good: "Performance",
  warning: "Normal",
  critical: "Need to improve",
};

export default function ScoreBadge({ value, target, size = "sm" }) {
  if (value === null || value === undefined) {
    return <span className="text-sm text-slate-300">—</span>;
  }
  const attainment = target ? (value / target) * 100 : 0;
  // Thresholds match dashboard.py calculate_kpi: >75 = "Performance", >50 = "Normal", ≤50 = "Need to improve"
  const tier = attainment > 75 ? "good" : attainment > 50 ? "warning" : "critical";

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full px-2.5 ${
        size === "sm" ? "py-1 text-xs" : "py-1.5 text-sm"
      } font-bold tabular-nums ${STYLES[tier]}`}
      title={`${attainment.toFixed(0)}% of target (${target}) · ${RATING_LABELS[tier]}`}
    >
      {value.toFixed(1)}
    </span>
  );
}
