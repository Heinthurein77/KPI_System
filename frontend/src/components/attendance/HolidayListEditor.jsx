import { useState } from "react";
import { MONTHS } from "../../utils/months";
import { datesInRange, monthBounds, normalizeHolidays } from "../../utils/holidays";

// Holiday list editor for Attendance settings, one month at a time.
//
// The parent owns the full holidays list (all months) and passes only
// the selected month's rows as `value`; every onChange returns the new
// rows for that month, and the parent merges them back — rows for other
// months are never touched, so switching months can never lose a
// holiday. The name is what the banner and the bell notification show;
// leave it blank for an unnamed date.
//
// Rows are keyed by index (not by date) so typing a name never remounts
// the row and drops focus. The list is sorted when rows are added via
// the range (and the backend sorts + dedupes again on save); per-keystroke
// edits update in place and never reorder the rows.
//
// The date pickers are clamped to the selected month (min/max), and the
// range add must stay inside it — a holiday that belongs to another
// month is added by switching to that month first. A row whose date is
// edited to another month disappears from this month's view but stays
// in the full list; the hint below says so.
export default function HolidayListEditor({ value, month, onChange, onError }) {
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const { first, last } = monthBounds(month.year, month.month);
  const monthLabel = `${MONTHS[month.month - 1]} ${month.year}`;

  function updateRow(idx, patch) {
    onChange(value.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
  }

  function removeRow(idx) {
    onChange(value.filter((_, i) => i !== idx));
  }

  // A new row starts on the 1st of the selected month, so it is always
  // a real date in this month — the user only changes the day.
  function addRow() {
    onChange([...value, { date: first, name: "" }]);
  }

  // Append every day in the picked range (deduped, sorted), all inside
  // the selected month. Rows that are still being typed (no date yet)
  // are kept at the end.
  function handleAddRange() {
    onError("");
    if (!rangeFrom || !rangeTo) {
      onError("Pick both a start and an end date for the range.");
      return;
    }
    if (rangeFrom < first || rangeTo > last) {
      onError(`That range leaves ${monthLabel} — pick a range inside ${monthLabel}, or switch months first.`);
      return;
    }
    const days = datesInRange(rangeFrom, rangeTo);
    if (days.length === 0) {
      onError("The range end must be on or after the start date.");
      return;
    }
    if (days.length > 366) {
      onError(`That range is ${days.length} days — add at most one year (366 days) at a time.`);
      return;
    }
    const merged = normalizeHolidays([
      ...value.filter((row) => row.date),
      ...days.map((date) => ({ date, name: "" })),
    ]);
    onChange([...merged, ...value.filter((row) => !row.date)]);
    setRangeFrom("");
    setRangeTo("");
  }

  return (
    <div>
      <p className="text-sm font-medium text-slate-700">
        Holidays in {monthLabel} ({value.length} date{value.length === 1 ? "" : "s"}) — give each holiday a name for the announcement
      </p>
      <div className="mt-2 space-y-2">
        {value.map((row, idx) => (
          <div key={idx} className="flex flex-wrap items-center gap-2">
            <input
              type="date"
              value={row.date}
              min={first}
              max={last}
              aria-label={`Holiday ${idx + 1} date`}
              onChange={(e) => updateRow(idx, { date: e.target.value })}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
            />
            <input
              type="text"
              value={row.name}
              placeholder="Holiday name, e.g. Thingyan"
              maxLength={80}
              aria-label={`Holiday ${idx + 1} name`}
              onChange={(e) => updateRow(idx, { name: e.target.value })}
              className="min-w-[160px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              type="button"
              onClick={() => removeRow(idx)}
              aria-label={`Remove holiday ${idx + 1}`}
              className="rounded-lg border border-slate-200 px-2.5 py-2 text-sm text-slate-500 hover:bg-slate-50"
            >
              ✕
            </button>
          </div>
        ))}
        {value.length === 0 && (
          <p className="text-sm text-slate-400">No holidays in {monthLabel} yet.</p>
        )}
      </div>
      <button
        type="button"
        onClick={addRow}
        className="mt-3 rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
      >
        + Add holiday
      </button>
      <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <p className="text-sm font-medium text-slate-700">Add a date range in {monthLabel} · ရက်အပိုင်းအခြား ထည့်ရန်</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={rangeFrom}
            min={first}
            max={last}
            onChange={(e) => setRangeFrom(e.target.value)}
            aria-label="Range start date"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
          />
          <span className="text-sm text-slate-500">to</span>
          <input
            type="date"
            value={rangeTo}
            min={first}
            max={last}
            onChange={(e) => setRangeTo(e.target.value)}
            aria-label="Range end date"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
          />
          <button
            type="button"
            onClick={handleAddRange}
            className="rounded-lg bg-slate-800 px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700"
          >
            Add range
          </button>
        </div>
        <p className="mt-1.5 text-xs text-slate-400">
          Adds every day in the range (e.g. a multi-day holiday) — duplicates are skipped, the list stays sorted.
        </p>
      </div>
      <p className="mt-2 text-xs text-slate-400">
        A date moved to another month is kept — switch months above to see it.
      </p>
    </div>
  );
}
