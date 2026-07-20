const STYLES = {
  good: "bg-emerald-50 text-emerald-700",
  warning: "bg-amber-50 text-amber-700",
  critical: "bg-red-50 text-red-700",
};

export default function ScoreBadge({ value, target, size = "sm" }) {
  if (value === null || value === undefined) {
    return <span className="text-sm text-slate-300">—</span>;
  }
  const attainment = target ? (value / target) * 100 : 0;
  const tier = attainment >= 100 ? "good" : attainment >= 85 ? "warning" : "critical";

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full px-2.5 ${
        size === "sm" ? "py-1 text-xs" : "py-1.5 text-sm"
      } font-bold tabular-nums ${STYLES[tier]}`}
      title={`${attainment.toFixed(0)}% of target (${target})`}
    >
      {value.toFixed(1)}
    </span>
  );
}
