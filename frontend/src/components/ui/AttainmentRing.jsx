/**
 * Circular attainment indicator for a KPI card. value is 0-120 (the backend
 * caps attainment at 120); null means no score yet. Colors follow the app's
 * score tiers: emerald at/over target, amber above 50, red below.
 */
export default function AttainmentRing({ value, label, size = 60 }) {
  const pct = value == null ? 0 : Math.min(120, Math.max(0, value));
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const filled = (Math.min(pct, 100) / 100) * c;

  const ringColor =
    value == null ? "text-slate-200" : pct >= 100 ? "text-emerald-500" : pct > 50 ? "text-amber-500" : "text-red-400";
  const textColor =
    value == null ? "text-slate-300" : pct >= 100 ? "text-emerald-600" : pct > 50 ? "text-amber-600" : "text-red-500";

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
