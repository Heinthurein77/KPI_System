/**
 * Circular attainment indicator for a KPI card. value is 0-120 (the backend
 * caps attainment at 120); null means no score yet. Colors follow the app's
 * score tiers (dashboard.py / ScoreBadge): >75 Performance (emerald),
 * >50 Normal (amber), otherwise Need to improve (red).
 */
export default function AttainmentRing({ value, label, size = 60 }) {
  const pct = value == null ? 0 : Math.min(120, Math.max(0, value));
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(pct, 100) / 100) * c;

  const tier = value == null ? null : pct > 75 ? "good" : pct > 50 ? "warning" : "critical";
  const ringColor =
    tier === "good" ? "text-emerald-500" : tier === "warning" ? "text-amber-500" : tier === "critical" ? "text-red-400" : "text-slate-200";
  const textColor =
    tier === "good" ? "text-emerald-600" : tier === "warning" ? "text-amber-600" : tier === "critical" ? "text-red-500" : "text-slate-300";

  return (
    <div className="flex shrink-0 flex-col items-center gap-1" title={label ? `Attainment (${label} score)` : "Attainment"}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth="6" className="text-slate-100" stroke="currentColor" />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${filled} ${c}`}
            className={`${ringColor} transition-all`}
            stroke="currentColor"
          />
        </svg>
        <span className={`absolute inset-0 flex items-center justify-center text-sm font-bold tabular-nums ${textColor}`}>
          {value == null ? "—" : `${Math.round(pct)}%`}
        </span>
      </div>
      {label && <span className="text-[10px] font-medium text-slate-400 uppercase tracking-wide">{label}</span>}
    </div>
  );
}
