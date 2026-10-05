import { useEffect, useId, useRef, useState } from "react";

const STATUS_COLOR = {
  good: "#10b981", // emerald-500
  warning: "#f59e0b", // amber-500
  critical: "#ef4444", // red-500
};
const NO_DATA_COLOR = "#cbd5e1"; // slate-300
const LINE_COLOR = "#0284c7"; // brand-600

const STATUS_LABEL = { good: "On Target", warning: "Near Target", critical: "Below Target" };

const MONTH_ABBR = {
  January: "Jan", February: "Feb", March: "Mar", April: "Apr", May: "May", June: "Jun",
  July: "Jul", August: "Aug", September: "Sep", October: "Oct", November: "Nov", December: "Dec",
};

const WIDTH = 760;
const HEIGHT = 280;
const PAD = { top: 20, right: 20, bottom: 36, left: 40 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

function periodLabel(point, showYear) {
  const abbr = MONTH_ABBR[point.month_or_quarter] ?? point.month_or_quarter;
  return showYear ? `${abbr} '${String(point.year).slice(2)}` : abbr;
}

export default function KpiTrendChart({ points, onPointClick, pointLinkLabel = "View details →" }) {
  const [hoverIdx, setHoverIdx] = useState(null);
  const gradientId = useId();

  // The tooltip's "View details" link sits outside the tiny circular hit
  // target, so moving the mouse from the point down into the tooltip briefly
  // leaves the circle — without this bridge, that mouseleave would hide the
  // tooltip (and the link inside it) before the click can land.
  const clearHoverTimer = useRef(null);
  function holdHover(i) {
    if (clearHoverTimer.current) {
      clearTimeout(clearHoverTimer.current);
      clearHoverTimer.current = null;
    }
    setHoverIdx(i);
  }
  function scheduleClearHover() {
    clearHoverTimer.current = setTimeout(() => setHoverIdx(null), 200);
  }
  function cancelClearHover() {
    if (clearHoverTimer.current) {
      clearTimeout(clearHoverTimer.current);
      clearHoverTimer.current = null;
    }
  }

  // If the chart unmounts (e.g. navigating away) while a hover-clear timer
  // is pending, it would otherwise still fire later and call setHoverIdx on
  // an unmounted component.
  useEffect(() => cancelClearHover, []);

  // The grid always spans a full year, so "empty" means no month is scored,
  // not zero points.
  if (points.length === 0 || points.every((p) => p.attainment === null)) {
    return (
      <div className="flex items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 py-16 text-sm text-slate-400">
        No finalized KPI scores for this employee in this year.
      </div>
    );
  }

  const showYear = new Set(points.map((p) => p.year)).size > 1;
  const maxAttainment = Math.max(100, ...points.map((p) => p.attainment ?? 0));
  const yMax = Math.ceil((maxAttainment * 1.1) / 10) * 10;
  const yTicks = [0, yMax / 2, yMax].map((v) => Math.round(v));

  const xFor = (i) => (points.length === 1 ? PLOT_W / 2 : (i / (points.length - 1)) * PLOT_W);
  const yFor = (attainment) => PLOT_H - (attainment / yMax) * PLOT_H;

  // Build one path per contiguous run of scored months, so a run of missing
  // months (common once the chart always spans a full Jan-Dec grid) breaks
  // the line rather than silently interpolating straight across the gap.
  const segments = [];
  let current = [];
  points.forEach((p, i) => {
    if (p.attainment !== null) {
      current.push({ ...p, i });
    } else if (current.length) {
      segments.push(current);
      current = [];
    }
  });
  if (current.length) segments.push(current);

  const pathFor = (segment) =>
    segment.map((p, idx) => `${idx === 0 ? "M" : "L"} ${xFor(p.i)} ${yFor(p.attainment)}`).join(" ");
  const areaFor = (segment) =>
    `${pathFor(segment)} L ${xFor(segment[segment.length - 1].i)} ${PLOT_H} L ${xFor(segment[0].i)} ${PLOT_H} Z`;

  const targetY = yFor(100);
  const hovered = hoverIdx !== null ? points[hoverIdx] : null;

  return (
    <div>
      <div className="relative">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label="KPI attainment trend by month">
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={LINE_COLOR} stopOpacity="0.12" />
              <stop offset="100%" stopColor={LINE_COLOR} stopOpacity="0" />
            </linearGradient>
          </defs>

          <g transform={`translate(${PAD.left},${PAD.top})`}>
            {/* Gridlines + y ticks */}
            {yTicks.map((tick) => (
              <g key={tick}>
                <line
                  x1={0}
                  x2={PLOT_W}
                  y1={yFor(tick)}
                  y2={yFor(tick)}
                  stroke="#e2e8f0"
                  strokeWidth="1"
                />
                <text x={-10} y={yFor(tick)} textAnchor="end" dominantBaseline="middle" className="fill-slate-400 text-[10px]">
                  {tick}
                </text>
              </g>
            ))}

            {/* 100% target reference line */}
            <line
              x1={0}
              x2={PLOT_W}
              y1={targetY}
              y2={targetY}
              stroke="#94a3b8"
              strokeWidth="1"
              strokeDasharray="4 3"
            />
            <text x={PLOT_W} y={targetY - 5} textAnchor="end" className="fill-slate-400 text-[10px] font-medium">
              Target
            </text>

            {/* Area wash + trend line, one per contiguous run of scored months */}
            {segments.map((segment, idx) =>
              segment.length > 1 ? (
                <g key={idx}>
                  <path d={areaFor(segment)} fill={`url(#${gradientId})`} />
                  <path
                    d={pathFor(segment)}
                    fill="none"
                    stroke={LINE_COLOR}
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </g>
              ) : null
            )}

            {/* X axis labels */}
            {points.map((p, i) => (
              <text
                key={i}
                x={xFor(i)}
                y={PLOT_H + 20}
                textAnchor="middle"
                className="fill-slate-500 text-[10px] font-medium"
              >
                {periodLabel(p, showYear)}
              </text>
            ))}

            {/* Points + hit targets */}
            {points.map((p, i) => {
              const hasScore = p.attainment !== null;
              const cy = hasScore ? yFor(p.attainment) : PLOT_H;
              const color = hasScore ? STATUS_COLOR[p.status] ?? LINE_COLOR : NO_DATA_COLOR;
              const clickable = Boolean(onPointClick) && p.total_count > 0;
              return (
                <g key={i}>
                  {hasScore && <circle cx={xFor(i)} cy={cy} r="5" fill={color} stroke="white" strokeWidth="2" />}
                  <circle
                    cx={xFor(i)}
                    cy={cy}
                    r="14"
                    fill="transparent"
                    tabIndex={0}
                    role={clickable ? "button" : undefined}
                    className={clickable ? "cursor-pointer outline-none" : "outline-none"}
                    onMouseEnter={() => holdHover(i)}
                    onMouseLeave={scheduleClearHover}
                    onFocus={() => holdHover(i)}
                    onBlur={scheduleClearHover}
                    onClick={() => clickable && onPointClick(p)}
                  />
                  {hoverIdx === i && (
                    <line x1={xFor(i)} x2={xFor(i)} y1={0} y2={PLOT_H} stroke="#94a3b8" strokeWidth="1" pointerEvents="none" />
                  )}
                </g>
              );
            })}
          </g>
        </svg>

        {hovered && (
          <div
            className={`absolute top-2 -translate-x-1/2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg ${
              onPointClick && hovered.total_count > 0 ? "" : "pointer-events-none"
            }`}
            style={{
              left: `${((PAD.left + xFor(hoverIdx)) / WIDTH) * 100}%`,
            }}
            onMouseEnter={cancelClearHover}
            onMouseLeave={scheduleClearHover}
          >
            <p className="font-semibold text-slate-800">
              {hovered.month_or_quarter} {hovered.year}
            </p>
            {hovered.attainment !== null ? (
              <>
                <p className="mt-0.5 text-sm font-bold tabular-nums text-slate-900">{hovered.attainment.toFixed(0)}%</p>
                <p className="text-slate-500">{STATUS_LABEL[hovered.status]}</p>
              </>
            ) : (
              <p className="mt-0.5 text-slate-500">Not yet scored</p>
            )}
            <p className="mt-0.5 text-slate-400">
              {hovered.scored_count} of {hovered.total_count} metrics finalized
            </p>
            {onPointClick && hovered.total_count > 0 && (
              <button
                type="button"
                onClick={() => onPointClick(hovered)}
                className="mt-1.5 font-semibold text-brand-600 hover:text-brand-700 hover:underline"
              >
                {pointLinkLabel}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {Object.entries(STATUS_LABEL).map(([key, label]) => (
          <span key={key} className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: STATUS_COLOR[key] }}></span>
            {label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NO_DATA_COLOR }}></span>
          No score yet
        </span>
      </div>
    </div>
  );
}
