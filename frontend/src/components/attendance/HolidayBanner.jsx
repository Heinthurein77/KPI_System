import { useEffect, useState } from "react";
import { formatHoliday } from "../../utils/holidays";

// Upcoming-holiday announcement, shown above the Today card to every
// role (tenant admins see it too — their personal dashboard is hidden,
// but holidays concern the whole organisation). Dismissal lasts only for
// this page load; when the holiday list changes, the banner shows again.
export default function HolidayBanner({ upcoming = [], today }) {
  const [dismissed, setDismissed] = useState(false);
  const signature = upcoming.map((h) => `${h.date}|${h.name}`).join(";");
  useEffect(() => {
    setDismissed(false);
  }, [signature]);

  if (dismissed || upcoming.length === 0) return null;

  const todayHoliday = upcoming.find((h) => h.date === today);
  const future = upcoming.filter((h) => h.date !== today);

  return (
    <div className="mt-5 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          {todayHoliday && (
            <p className="font-semibold">
              Today is a holiday{todayHoliday.name ? `: ${todayHoliday.name}` : ""} — no check-in needed.
            </p>
          )}
          {future.length === 1 && (
            <p className={todayHoliday ? "mt-1" : "font-semibold"}>
              {todayHoliday ? "Coming up: " : "Upcoming holiday: "}
              {formatHoliday(future[0].date, future[0].name)}
            </p>
          )}
          {future.length > 1 && (
            <>
              <p className={todayHoliday ? "mt-1 font-semibold" : "font-semibold"}>
                {todayHoliday ? "Coming up:" : "Upcoming holidays:"}
              </p>
              <ul className="mt-1 space-y-0.5">
                {future.map((h) => (
                  <li key={h.date}>• {formatHoliday(h.date, h.name)}</li>
                ))}
              </ul>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss holiday announcement"
          className="shrink-0 rounded-lg px-2 py-1 text-sky-500 hover:bg-sky-100"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
