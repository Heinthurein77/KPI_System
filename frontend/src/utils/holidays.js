// Named-holiday helpers for the Attendance module.
//
// Holidays are {date: "YYYY-MM-DD", name: ""} objects; older payloads
// carry plain date strings, which normalise to unnamed holidays here.
// The name is what the announcement banner and the bell notification
// show, so an unnamed holiday still works — it just shows its date.

export function isRealDateString(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
  const [y, m, d] = value.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

export function normalizeHoliday(entry) {
  if (typeof entry === "string") {
    const date = entry.trim();
    return isRealDateString(date) ? { date, name: "" } : null;
  }
  if (entry && typeof entry === "object") {
    const date = typeof entry.date === "string" ? entry.date.trim() : "";
    const name = typeof entry.name === "string" ? entry.name.trim() : "";
    return isRealDateString(date) ? { date, name: name.slice(0, 80) } : null;
  }
  return null;
}

// Sorted, deduped by date (a duplicate keeps the entry that has a name,
// else the first). Bad entries are dropped — a corrupt row must never
// break the settings form. The backend sorts + dedupes again on save.
export function normalizeHolidays(list) {
  if (!Array.isArray(list)) return [];
  const byDate = {};
  for (const entry of list) {
    const item = normalizeHoliday(entry);
    if (!item) continue;
    const prev = byDate[item.date];
    if (!prev || (!prev.name && item.name)) byDate[item.date] = item;
  }
  return Object.keys(byDate).sort().map((d) => byDate[d]);
}

// --- Month-by-month editing -------------------------------------------
// Holidays are stored as one flat list (the date carries the month), but
// the settings UI edits one month at a time: the admin picks a month,
// works on that month's holidays, and at month end moves on to the next
// month. Past months stay in the list — switching months never deletes
// anything. A row with no date yet (still being typed) belongs to the
// month being edited, so it never vanishes from the editor.

// First and last day of a month, as YYYY-MM-DD strings.
export function monthBounds(year, month) {
  const first = `${year}-${String(month).padStart(2, "0")}-01`;
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const last = `${year}-${String(month).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;
  return { first, last };
}

// True when `dateIso` falls in the given month — or when it is blank /
// not a real date, so an in-progress row stays visible (in every month)
// instead of vanishing while it is being typed; the save validation
// still blocks it until a date is picked or the row is removed.
export function isInHolidayMonth(dateIso, year, month) {
  if (!isRealDateString(dateIso || "")) return true;
  const [y, m] = dateIso.split("-").map(Number);
  return y === year && m === month;
}

export function holidaysInMonth(list, year, month) {
  if (!Array.isArray(list)) return [];
  return list.filter((row) => isInHolidayMonth(row?.date, year, month));
}
export function formatHolidayDate(dateIso) {
  const [y, m, d] = dateIso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  return dt.toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// "Thingyan — Monday, 23 Nov 2026" (or just the date when unnamed).
export function formatHoliday(dateIso, name) {
  return name ? `${name} — ${formatHolidayDate(dateIso)}` : formatHolidayDate(dateIso);
}

// Problems that must block a save: a row with no date, or a date that is
// not a real calendar date. Returns [] when every row is saveable.
export function validateHolidayRows(rows) {
  const problems = [];
  rows.forEach((row, idx) => {
    if (!row.date) problems.push(`Holiday ${idx + 1}: pick a date or remove the row.`);
    else if (!isRealDateString(row.date)) {
      problems.push(`Holiday ${idx + 1}: ${row.date} is not a real date.`);
    }
  });
  return problems;
}

// Every date from `from` to `to` inclusive, as YYYY-MM-DD strings.
// Returns [] when either end is not a real date or the end is before the start.
export function datesInRange(from, to) {
  if (!isRealDateString(from || "") || !isRealDateString(to || "")) return [];
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  const start = Date.UTC(fy, fm - 1, fd);
  const end = Date.UTC(ty, tm - 1, td);
  if (end < start) return [];
  const out = [];
  for (let t = start; t <= end; t += 86400000) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}
