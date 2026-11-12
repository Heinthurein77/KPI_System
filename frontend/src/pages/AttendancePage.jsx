// MAINTAINABILITY RULE (PM review 2026-11-12, finding m3): this file is
// a god file (1,209 lines at HEAD a2acb06). Do NOT grow it further —
// split on next touch: the next feature that belongs here goes in a new
// module/component imported from this page instead of another block
// inline. Behaviour must stay identical when that split happens.
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";
import AppShell from "../components/layout/AppShell";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import Modal from "../components/ui/Modal";
import { getBlobErrorMessage, getErrorMessage } from "../api/errors";
import { MONTHS } from "../utils/months";
import { slugify } from "../utils/slugify";
import { middayLabel } from "../utils/time";
import {
  checkIn,
  checkOut,
  deleteAttendanceRecord,
  downloadAttendancePersonExcel,
  downloadAttendanceReportExcel,
  getAttendanceReport,
  getAttendanceSettings,
  getAttendanceSummary,
  getAttendanceToday,
  markLeave,
  updateAttendanceRecord,
  updateAttendanceSettings,
} from "../api/attendance";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STATUS_LABEL = {
  present: "Present · လာတယ်",
  half: "Half day · နေ့တစ်ဝက်",
  leave: "Leave · ခွင့်",
  absent: "Absent · ပျက်ကွက်",
};
// Restrained status pills, same shape as the shared StatusBadge.
const STATUS_PILL = {
  present: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-600/20",
  half: "bg-amber-50 text-amber-700 ring-1 ring-amber-600/20",
  leave: "bg-sky-50 text-sky-700 ring-1 ring-sky-600/20",
  absent: "bg-rose-50 text-rose-700 ring-1 ring-rose-600/20",
};

function StatusPill({ status }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
        STATUS_PILL[status] || "bg-slate-100 text-slate-600"
      }`}
    >
      {STATUS_LABEL[status] || status}
    </span>
  );
}

function Stat({ label, value, sub, accent }) {
  return (
    <div
      className={`rounded-2xl border bg-white px-4 py-3 shadow-sm ${
        accent ? "border-brand-200" : "border-slate-200"
      }`}
    >
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${accent ? "text-brand-700" : "text-slate-900"}`}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

// Holidays textarea parsing — the textarea keeps its RAW text while the
// admin types (no per-keystroke normalising: rewriting the value on every
// keystroke yanks the caret to the end whenever Enter creates a transient
// empty line, and typed dates get glued onto existing ones on a phone).
// Parsing happens only when the count is displayed, when a range is added
// and when saving. `dates` are real YYYY-MM-DD calendar dates, sorted and
// deduped; `invalid` holds lines that are not real dates (bad format or an
// impossible day such as 2026-02-30).
function parseHolidayLines(text) {
  const dates = [];
  const invalid = [];
  for (const raw of String(text ?? "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(line)) {
      invalid.push(line);
      continue;
    }
    const [y, m, d] = line.split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) {
      invalid.push(line);
      continue;
    }
    dates.push(line);
  }
  return { dates: [...new Set(dates)].sort(), invalid };
}

function parseLines(text) {
  return String(text ?? "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

// Every date from `from` to `to` inclusive, as YYYY-MM-DD strings.
// Returns [] when either end is not a real date or the end is before the start.
function datesInRange(from, to) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from || "") || !/^\d{4}-\d{2}-\d{2}$/.test(to || "")) return [];
  const [fy, fm, fd] = from.split("-").map(Number);
  const [ty, tm, td] = to.split("-").map(Number);
  const start = Date.UTC(fy, fm - 1, fd);
  const end = Date.UTC(ty, tm - 1, td);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return [];
  const out = [];
  for (let t = start; t <= end; t += 86400000) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

export default function AttendancePage() {
  const { user } = useAuth();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [todayData, setTodayData] = useState(null);
  const [summary, setSummary] = useState(null);
  const [report, setReport] = useState(null);
  const [settings, setSettings] = useState(null);
  const [settingsForm, setSettingsForm] = useState(null);
  const [showSaveConfirm, setShowSaveConfirm] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  // Raw textarea text for the two one-per-line settings lists. The
  // textareas keep exactly what the admin typed (see parseHolidayLines);
  // the parsed lists are derived only for display, range-add and saving.
  const [holidaysText, setHolidaysText] = useState("");
  const [officeIpsText, setOfficeIpsText] = useState("");
  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  // Settings-only error, shown under the Save button where the admin is
  // working (the page-top banner is a long scroll away from the form).
  const [settingsError, setSettingsError] = useState("");
  const [leaveDate, setLeaveDate] = useState("");
  const [editingLeave, setEditingLeave] = useState(null);
  const [editLeaveDate, setEditLeaveDate] = useState("");
  const [editError, setEditError] = useState("");
  const [deletingLeave, setDeletingLeave] = useState(null);
  const [recordBusy, setRecordBusy] = useState(false);
  const [deptFilter, setDeptFilter] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportingUserId, setExportingUserId] = useState(null);
  const [viewingPerson, setViewingPerson] = useState(null);
  const [personSummary, setPersonSummary] = useState(null);
  const [personLoading, setPersonLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const isAdmin = user?.role === "tenant_admin";
  const isDeptAdmin = user?.role === "dept_admin";
  const canCheckIn = user?.role === "employee" || isDeptAdmin;

  // m21: a request sequence number — month Prev/Next + dropdowns each
  // fire load(), and a slow earlier response must not overwrite a newer
  // month's data. m14: the default month comes from the server's /today
  // date (Asia/Yangon), not the browser's clock — synced once, the first
  // time /today loads, so a device in another timezone opens the right
  // month. The sync re-runs load() for the server month; the guard keeps
  // the extra fetch from looping.
  const loadSeq = useRef(0);
  const monthSyncedRef = useRef(false);
  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setError("");
    try {
      const [today, sum] = await Promise.all([
        getAttendanceToday(),
        getAttendanceSummary(year, month),
      ]);
      if (seq !== loadSeq.current) return;
      if (!monthSyncedRef.current && today.today) {
        monthSyncedRef.current = true;
        const [y, m] = today.today.split("-").map(Number);
        if (y !== year || m !== month) {
          setYear(y);
          setMonth(m);
          return;
        }
      }
      setTodayData(today);
      setSummary(sum);
      if (isAdmin || isDeptAdmin) {
        const rep = await getAttendanceReport(year, month);
        if (seq !== loadSeq.current) return;
        setReport(rep);
      }
      if (isAdmin) {
        const s = await getAttendanceSettings();
        if (seq !== loadSeq.current) return;
        setSettings(s);
        setSettingsForm(s);
        setHolidaysText(s.holidays.join("\n"));
        setOfficeIpsText(s.office_ips.join("\n"));
      }
    } catch (e) {
      if (seq !== loadSeq.current) return;
      setError(getErrorMessage(e));
    }
  }, [year, month, isAdmin, isDeptAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  // M17: the page used to load once and go stale (WiFi chip, Today date
  // and check-in button state froze if the page sat open). Re-check
  // /today every 60s and on window focus, and reload at local midnight
  // — same cadence as the notification bell's poll. The /today refresh
  // is silent and never wipes the summary/report; the midnight refresh
  // is a full load (new day, new month) and then re-arms itself.
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    let midnightTimer;
    async function refreshToday() {
      if (document.hidden) return;
      try {
        setTodayData(await getAttendanceToday());
      } catch {
        // Keep the stale chip; the next tick retries.
      }
    }
    function scheduleMidnight() {
      const next = new Date();
      next.setHours(24, 0, 0, 0);
      midnightTimer = setTimeout(() => {
        loadRef.current();
        scheduleMidnight();
      }, next.getTime() - Date.now());
    }
    const interval = setInterval(refreshToday, 60 * 1000);
    const onFocus = () => refreshToday();
    window.addEventListener("focus", onFocus);
    scheduleMidnight();
    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      clearTimeout(midnightTimer);
    };
  }, []);

  // Per-person records modal (opened from a report row): fetches that
  // person's summary — including the records array — for the selected
  // month, and refetches when the month changes while it is open. The
  // report only ever holds rows the viewer may see, so a dept admin can
  // only ever open their own department's people here.
  const refreshPersonSummary = useCallback(async (row) => {
    if (!row) return;
    setPersonLoading(true);
    try {
      setPersonSummary(await getAttendanceSummary(year, month, row.user_id));
    } catch (e) {
      setError(getErrorMessage(e));
      setPersonSummary(null);
    } finally {
      setPersonLoading(false);
    }
  }, [year, month]);

  useEffect(() => {
    if (viewingPerson) refreshPersonSummary(viewingPerson);
  }, [viewingPerson, refreshPersonSummary]);

  // Don't close the person modal while the edit dialog or delete confirm
  // is stacked on top of it (Escape/backdrop would otherwise close both).
  function closePersonModal() {
    if (editingLeave || deletingLeave) return;
    setViewingPerson(null);
    setPersonSummary(null);
  }

  function shiftMonth(delta) {
    let m = month + delta;
    let y = year;
    if (m < 1) {
      m = 12;
      y -= 1;
    }
    if (m > 12) {
      m = 1;
      y += 1;
    }
    setYear(y);
    setMonth(m);
  }

  // Re-check the office-WiFi gate immediately before submitting, so a page
  // loaded at the office cannot submit from home. Returns true when the
  // fresh /today answer allows check-in/out. The server enforces the same
  // gate per request and stays the arbiter: when the re-check itself fails
  // we proceed and let the server decide (its 403 is surfaced as before).
  async function recheckWifiBeforeSubmit() {
    try {
      const fresh = await getAttendanceToday();
      setTodayData(fresh);
      return fresh.ip_allowed;
    } catch {
      return true;
    }
  }

  async function handleCheckIn() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      // M12: a failed re-check must say so — silently doing nothing
      // looks like a broken button.
      if (!(await recheckWifiBeforeSubmit())) {
        setError("You are not on office WiFi — check-in/out is disabled.");
        return;
      }
      await checkIn();
      setNotice("Checked in. · Check-in လုပ်ပြီးပြီ။");
      await load();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleCheckOut() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      if (!(await recheckWifiBeforeSubmit())) {
        setError("You are not on office WiFi — check-in/out is disabled.");
        return;
      }
      await checkOut();
      setNotice("Checked out. · Check-out လုပ်ပြီးပြီ။");
      await load();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleLeave() {
    if (!leaveDate) return;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await markLeave(leaveDate);
      setNotice(`Leave recorded for ${leaveDate}. · ခွင့်မှတ်ပြီးပြီ။`);
      setLeaveDate("");
      await load();
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function openEditLeave(record) {
    setEditError("");
    setEditLeaveDate(record.record_date);
    setEditingLeave(record);
  }

  async function handleSaveEditLeave() {
    if (!editingLeave || !editLeaveDate) return;
    setEditError("");
    setError("");
    setNotice("");
    setRecordBusy(true);
    try {
      await updateAttendanceRecord(editingLeave.id, editLeaveDate);
      setNotice(`Leave day moved to ${editLeaveDate}. · ခွင့်ရက်ပြင်ပြီးပြီ။`);
      setEditingLeave(null);
      await load();
      if (viewingPerson) await refreshPersonSummary(viewingPerson);
    } catch (e) {
      // Keep the dialog open so another date can be picked.
      setEditError(getErrorMessage(e));
    } finally {
      setRecordBusy(false);
    }
  }

  async function handleDeleteLeave() {
    if (!deletingLeave) return;
    setError("");
    setNotice("");
    setRecordBusy(true);
    try {
      await deleteAttendanceRecord(deletingLeave.id);
      setNotice(`Leave day ${deletingLeave.record_date} deleted. · ခွင့်ရက်ဖျက်ပြီးပြီ။`);
      setDeletingLeave(null);
      await load();
      if (viewingPerson) await refreshPersonSummary(viewingPerson);
    } catch (e) {
      setError(getErrorMessage(e));
      setDeletingLeave(null);
    } finally {
      setRecordBusy(false);
    }
  }

  // Edit/Delete buttons for a leave row, shared by the own-records table
  // and the per-person records modal. Delete is tenant-admin-only on the
  // backend, so the button is hidden for everyone else.
  function leaveActions(r) {
    return (
      <span className="inline-flex gap-1.5">
        <button
          type="button"
          onClick={() => openEditLeave(r)}
          title={`Edit leave day ${r.record_date}`}
          aria-label={`Edit leave day ${r.record_date}`}
          className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 shadow-sm transition hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700"
        >
          Edit
        </button>
        {isAdmin && (
          <button
            type="button"
            onClick={() => setDeletingLeave(r)}
            title={`Delete leave day ${r.record_date}`}
            aria-label={`Delete leave day ${r.record_date}`}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-red-600 shadow-sm transition hover:border-red-200 hover:bg-red-50"
          >
            Delete
          </button>
        )}
      </span>
    );
  }

  // Departments present in the loaded report rows (sorted by name).
  // The filter is client-side: the report already holds exactly the rows
  // the viewer may see, so a dept admin only ever sees their own dept.
  const reportDepartments = (report || [])
    .reduce((acc, row) => {
      if (row.department_id != null && !acc.some((d) => d.id === row.department_id)) {
        acc.push({ id: row.department_id, name: row.department_name || "—" });
      }
      return acc;
    }, [])
    .sort((a, b) => a.name.localeCompare(b.name));
  // If the selected department is gone from the new month's rows, fall
  // back to All instead of showing an empty table.
  const effectiveDeptFilter =
    deptFilter && reportDepartments.some((d) => String(d.id) === deptFilter) ? deptFilter : "";
  const filteredReport = (report || []).filter(
    (row) => !effectiveDeptFilter || String(row.department_id) === effectiveDeptFilter,
  );

  async function handleExport() {
    if (exporting) return;
    setError("");
    setNotice("");
    setExporting(true);
    try {
      const { blob, filename } = await downloadAttendanceReportExcel(
        year,
        month,
        effectiveDeptFilter || undefined,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const dept = reportDepartments.find((d) => String(d.id) === effectiveDeptFilter);
      const slug = dept ? `-${slugify(dept.name, "department")}` : "";
      // Prefer the server's filename; rebuild locally only as a fallback.
      link.download =
        filename || `attendance-${year}-${String(month).padStart(2, "0")}${slug}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice("Attendance Excel downloaded. · Excel ထုတ်ပြီးပြီ။");
    } catch (e) {
      setError(await getBlobErrorMessage(e, "Could not download the Excel. Please try again."));
    } finally {
      setExporting(false);
    }
  }

  async function handleExportPerson(row) {
    if (exportingUserId) return;
    setError("");
    setNotice("");
    setExportingUserId(row.user_id);
    try {
      const { blob, filename } = await downloadAttendancePersonExcel(year, month, row.user_id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download =
        filename ||
        `attendance-${year}-${String(month).padStart(2, "0")}-${slugify(row.employee_name, "employee")}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice(`${row.employee_name}'s attendance Excel downloaded.`);
    } catch (e) {
      setError(await getBlobErrorMessage(e, "Could not download the Excel. Please try again."));
    } finally {
      setExportingUserId(null);
    }
  }

  // Summary of the pending settings, shown in the save confirm dialog
  // so the admin sees exactly what will be saved before confirming.
  // Counts come from the parsed textarea text (the live lists), not the
  // last-saved form — the dialog must show what is about to be saved.
  function pendingSettingsSummary() {
    if (!settingsForm) return "";
    const days = settingsForm.work_days.map((d) => WEEKDAYS[d]).join(", ");
    // m17: reuse the backend-computed midday when the pending hours are
    // the saved ones; only compute client-side for pending (unsaved)
    // hours, via the shared helper.
    const midday =
      settings &&
      settingsForm.start_time === settings.start_time &&
      settingsForm.end_time === settings.end_time
        ? settings.midday
        : middayLabel(settingsForm.start_time, settingsForm.end_time);
    const ips = parseLines(officeIpsText).length;
    const holidays = parseHolidayLines(holidaysText).dates.length;
    return [
      `Working days: ${days}`,
      `Office hours: ${settingsForm.start_time}–${settingsForm.end_time} (midday ${midday})`,
      ips > 0
        ? `Office WiFi IPs: ${parseLines(officeIpsText).join(", ")} (${ips} configured — check-in/out enforced on office WiFi only)`
        : "Office WiFi IPs: none — check-in/out not enforced yet",
      `Holidays: ${holidays} date${holidays === 1 ? "" : "s"}`,
    ].join("\n");
  }

  // Validate the holidays text BEFORE opening the save confirm dialog:
  // an invalid line must block the save with a clear message next to the
  // form, not a failed PATCH whose banner sits at the top of the page.
  function openSaveConfirm() {
    setSettingsError("");
    const { invalid } = parseHolidayLines(holidaysText);
    if (invalid.length > 0) {
      setSettingsError(
        `Invalid holiday date${invalid.length === 1 ? "" : "s"}: ${invalid.slice(0, 3).join(", ")}${invalid.length > 3 ? ` (+${invalid.length - 3} more)` : ""} — use YYYY-MM-DD, e.g. 2026-12-25. Nothing was saved; fix the line${invalid.length === 1 ? "" : "s"} and try again.`,
      );
      return;
    }
    setShowSaveConfirm(true);
  }

  // Append every day in the picked range to the holidays list (deduped,
  // sorted). One range covers a multi-day holiday such as Thingyan
  // without typing each day.
  function handleAddRange() {
    setSettingsError("");
    if (!rangeFrom || !rangeTo) {
      setSettingsError("Pick both a start and an end date for the range.");
      return;
    }
    const days = datesInRange(rangeFrom, rangeTo);
    if (days.length === 0) {
      setSettingsError("The range end must be on or after the start date.");
      return;
    }
    if (days.length > 366) {
      setSettingsError(`That range is ${days.length} days — add at most one year (366 days) at a time.`);
      return;
    }
    const merged = [...new Set([...parseHolidayLines(holidaysText).dates, ...days])].sort();
    setHolidaysText(merged.join("\n"));
    if (settingsForm) setSettingsForm({ ...settingsForm, holidays: merged });
  }

  async function handleSaveSettings() {
    setError("");
    setSettingsError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      // Parse the raw textarea text at save time (see parseHolidayLines):
      // the backend re-validates, but a bad line must never reach it.
      const { dates: holidays, invalid } = parseHolidayLines(holidaysText);
      if (invalid.length > 0) {
        setSettingsError(
          `Invalid holiday date${invalid.length === 1 ? "" : "s"}: ${invalid.slice(0, 3).join(", ")} — use YYYY-MM-DD. Nothing was saved.`,
        );
        setShowSaveConfirm(false);
        return;
      }
      const payload = {
        work_days: settingsForm.work_days,
        start_time: settingsForm.start_time,
        end_time: settingsForm.end_time,
        office_ips: [...new Set(parseLines(officeIpsText))],
        holidays,
      };
      const saved = await updateAttendanceSettings(payload);
      setSettings(saved);
      setSettingsForm(saved);
      setHolidaysText(saved.holidays.join("\n"));
      setOfficeIpsText(saved.office_ips.join("\n"));
      const days = saved.work_days.map((d) => WEEKDAYS[d]).join(", ");
      const ips = saved.office_ips.length;
      setNotice(
        `✓ Settings saved — Office ${saved.start_time}–${saved.end_time} (${days}), ` +
          (ips > 0
            ? `WiFi enforced with ${ips} office IP${ips === 1 ? "" : "s"} (${saved.office_ips.join(", ")}).`
            : "WiFi not enforced (no office IP yet)."),
      );
      setShowSaveConfirm(false);
      await load();
    } catch (e) {
      const message = getErrorMessage(e, "Settings could not be saved. Please try again.");
      setError(message);
      // Show it next to the form too — the page-top banner is a long
      // scroll away from the settings section on a phone.
      setSettingsError(message);
      setShowSaveConfirm(false);
    } finally {
      setSettingsSaving(false);
    }
  }

  // Live parse of the holidays textarea (display only — the raw text is
  // never rewritten while typing, so the caret stays where the admin put it).
  const { dates: holidayDates, invalid: holidayInvalid } = parseHolidayLines(holidaysText);

  const record = todayData?.record;
  const monthName = new Date(year, month - 1, 1).toLocaleString("en-US", { month: "long" });

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-4 py-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900">Attendance · တက်ရောက်မှု</h1>
            <p className="mt-1 text-sm text-slate-500">
              Office WiFi only · ရုံး WiFi နဲ့မှ check-in/check-out လုပ်လို့ရပါတယ်။
            </p>
          </div>
          {todayData && (
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold ${
                !todayData.wifi_enforced
                  ? "bg-amber-50 text-amber-700 ring-1 ring-amber-600/20"
                  : todayData.ip_allowed
                    ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-600/20"
                    : "bg-rose-50 text-rose-700 ring-1 ring-rose-600/20"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  !todayData.wifi_enforced
                    ? "bg-amber-500"
                    : todayData.ip_allowed
                      ? "bg-emerald-500"
                      : "bg-rose-500"
                }`}
              />
              {!todayData.wifi_enforced
                ? "WiFi not enforced yet"
                : todayData.ip_allowed
                  ? "On office WiFi"
                  : "Not on office WiFi"}
            </span>
          )}
        </div>

        {error && (
          <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-sm">
            {error}
          </div>
        )}
        {notice && (
          <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800 shadow-sm">
            {notice}
          </div>
        )}

        {/* Today — personal dashboard: only employees and dept admins check in,
            so tenant admins never see their own (meaningless) Today card. */}
        {canCheckIn && (
        <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-base font-semibold text-slate-900">Today · ဒီနေ့</h2>
            {todayData && (
              <p className="text-sm text-slate-500">
                {todayData.today} · Office {todayData.start_time}–{todayData.end_time} ·
                Midday {todayData.midday} · {todayData.is_working_day ? "Working day" : "Not a working day"}
              </p>
            )}
          </div>
          {todayData && (
            <>
              {todayData.wifi_enforced ? (
                <p className={`mt-3 text-sm font-medium ${todayData.ip_allowed ? "text-emerald-700" : "text-red-700"}`}>
                  {todayData.ip_allowed
                    ? "✓ You are on office WiFi."
                    : "✕ You are NOT on office WiFi — check-in/out is disabled."}
                </p>
              ) : (
                <p className="mt-3 text-sm font-medium text-amber-700">
                  ⚠ No office WiFi IP is configured yet — check-in is allowed for testing.
                  Ask the tenant admin to add the office public IP in Settings below.
                </p>
              )}
              {record && (
                <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-slate-100 pt-4 text-sm">
                  <span className="text-slate-600">
                    Check-in <span className="font-semibold tabular-nums text-slate-900">{record.check_in || "—"}</span>
                  </span>
                  <span className="text-slate-600">
                    Check-out <span className="font-semibold tabular-nums text-slate-900">{record.check_out || "—"}</span>
                  </span>
                  <StatusPill status={record.status} />
                  {!record.check_out && record.status !== "leave" && (
                    <span className="text-xs text-slate-400">Not checked out yet</span>
                  )}
                </div>
              )}
              {canCheckIn && (
                <div className="mt-5 flex gap-3">
                  <button
                    type="button"
                    onClick={handleCheckIn}
                    disabled={busy || !todayData.is_working_day || !todayData.ip_allowed || !!record}
                    className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-40"
                  >
                    Check-in
                  </button>
                  <button
                    type="button"
                    onClick={handleCheckOut}
                    disabled={busy || !todayData.ip_allowed || !record || !!record.check_out || record.status === "leave"}
                    className="rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-40"
                  >
                    Check-out
                  </button>
                </div>
              )}
            </>
          )}
        </section>
        )}

        {/* Month navigation + summary — personal dashboard, same gate as Today. */}
        {canCheckIn && (
        <>
        <div className="mt-8 flex items-center gap-3">
          <button
            type="button"
            onClick={() => shiftMonth(-1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
            aria-label="Previous month"
          >
            ‹ Prev
          </button>
          <h2 className="text-lg font-semibold tracking-tight text-slate-900">{monthName} {year}</h2>
          <button
            type="button"
            onClick={() => shiftMonth(1)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50"
            aria-label="Next month"
          >
            Next ›
          </button>
        </div>

        {summary && (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              <Stat accent label="Attendance" value={`${summary.attendance_pct}%`} sub={`${summary.present + summary.half * 0.5} / ${summary.working_days} days`} />
              <Stat label="Present" value={summary.present} />
              <Stat label="Half day" value={summary.half} />
              <Stat label="Leave" value={summary.leave} sub="counts as absent" />
              <Stat label="Absent" value={summary.absent} sub="no record" />
            </div>

            <section className="mt-6 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Check-in</th>
                    <th className="px-4 py-3">Check-out</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3" aria-label="Actions"></th>
                  </tr>
                </thead>
                <tbody>
                  {summary.records.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-4 text-slate-400">No attendance records this month yet.</td></tr>
                  )}
                  {summary.records.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100 transition hover:bg-slate-50">
                      <td className="px-4 py-2.5 tabular-nums text-slate-900">{r.record_date}</td>
                      <td className="px-4 py-2.5 tabular-nums text-slate-700">{r.check_in || "—"}</td>
                      <td className="px-4 py-2.5 tabular-nums text-slate-700">{r.check_out || "—"}</td>
                      <td className="px-4 py-2.5"><StatusPill status={r.status} /></td>
                      <td className="px-4 py-2.5 text-right">
                        {r.status === "leave" && leaveActions(r)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* Mark leave */}
            <section className="mt-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <h3 className="text-base font-semibold text-slate-900">Mark a leave day · ခွင့်ရက်မှတ်ရန်</h3>
              <p className="mt-1 text-sm text-slate-500">Approved leave counts as absent in the attendance %.</p>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <input
                  type="date"
                  value={leaveDate}
                  onChange={(e) => setLeaveDate(e.target.value)}
                  aria-label="Leave date"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
                />
                <button
                  type="button"
                  onClick={handleLeave}
                  disabled={busy || !leaveDate}
                  className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-40"
                >
                  Mark leave
                </button>
              </div>
            </section>
          </>
        )}
        </>
        )}

        {/* Admin report */}
        {report && (
          <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-base font-semibold text-slate-900">Monthly report · လချုပ်အစီရင်ခံစာ</h3>
              <p className="text-xs text-slate-400">{filteredReport.length} {filteredReport.length === 1 ? "person" : "people"}</p>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              Attendance % is display-only — it is never written to KPI.
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <label className="text-sm font-medium text-slate-700">
                <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Month</span>
                <select
                  value={month}
                  onChange={(e) => setMonth(Number(e.target.value))}
                  aria-label="Month"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
                >
                  {MONTHS.map((name, i) => (
                    <option key={i + 1} value={i + 1}>{name}</option>
                  ))}
                </select>
              </label>
              <label className="text-sm font-medium text-slate-700">
                <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Year</span>
                <select
                  value={year}
                  onChange={(e) => setYear(Number(e.target.value))}
                  aria-label="Year"
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
                >
                  {[now.getFullYear() - 2, now.getFullYear() - 1, now.getFullYear(), now.getFullYear() + 1, now.getFullYear() + 2].map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              </label>
              <label className="text-sm font-medium text-slate-700">
                <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">Department</span>
                <select
                  value={effectiveDeptFilter}
                  onChange={(e) => setDeptFilter(e.target.value)}
                  className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
                >
                  <option value="">All departments</option>
                  {reportDepartments.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                onClick={handleExport}
                disabled={exporting}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-40"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4" />
                </svg>
                {exporting ? "Exporting…" : "Export Excel"}
              </button>
            </div>
            <div className="mt-4 overflow-x-auto">
              {/* Narrower than the phone on purpose: the full 8-column
                  table is 664px wide, and a table wider than the viewport
                  expands the mobile layout viewport, which pushes the
                  fixed confirm dialog (and its Save button) partly
                  off-screen. Department / Half / Leave stay visible on
                  tablet and desktop (md+) and are hidden only on phones —
                  the headline numbers (Attendance, Present, Absent) and
                  the View button are always shown. */}
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Employee</th>
                    <th className="hidden px-4 py-3 md:table-cell">Department</th>
                    <th className="px-4 py-3 text-right">Attendance</th>
                    <th className="px-4 py-3 text-right">Present</th>
                    <th className="hidden px-4 py-3 text-right md:table-cell">Half</th>
                    <th className="hidden px-4 py-3 text-right md:table-cell">Leave</th>
                    <th className="px-4 py-3 text-right">Absent</th>
                    <th className="px-4 py-3" aria-label="Actions"></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredReport.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-4 text-slate-400">No attendance records for this department yet.</td></tr>
                  )}
                  {filteredReport.map((row) => (
                    <tr key={row.user_id} className="border-t border-slate-100 transition hover:bg-slate-50">
                      <td className="px-4 py-2.5 font-medium text-slate-900">{row.employee_name}</td>
                      <td className="hidden px-4 py-2.5 text-slate-700 md:table-cell">{row.department_name || "—"}</td>
                      <td className="px-4 py-2.5 text-right font-semibold tabular-nums text-slate-900">{row.attendance_pct}%</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-700">{row.present}</td>
                      <td className="hidden px-4 py-2.5 text-right tabular-nums text-slate-700 md:table-cell">{row.half}</td>
                      <td className="hidden px-4 py-2.5 text-right tabular-nums text-slate-700 md:table-cell">{row.leave}</td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-slate-700">{row.absent}</td>
                      <td className="px-4 py-2.5 text-right">
                        <span className="inline-flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => setViewingPerson(row)}
                            title={`View ${row.employee_name}'s records`}
                            aria-label={`View ${row.employee_name}'s records`}
                            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 shadow-sm transition hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700"
                          >
                            View
                          </button>
                          <button
                            type="button"
                            onClick={() => handleExportPerson(row)}
                          disabled={exportingUserId !== null}
                          title={`Download ${row.employee_name}'s Excel`}
                          aria-label={`Download ${row.employee_name}'s attendance Excel`}
                          className="inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 shadow-sm transition hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700 disabled:opacity-40"
                        >
                          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4" />
                          </svg>
                        </button>
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Per-person records (opened from a report row) */}
        <Modal
          open={viewingPerson !== null}
          title={viewingPerson ? `${viewingPerson.employee_name} · ${monthName} ${year}` : ""}
          onClose={closePersonModal}
          wide
        >
          {personLoading && <p className="text-sm text-slate-500">Loading…</p>}
          {!personLoading && personSummary && (
            <>
              <p className="text-sm text-slate-500">
                {viewingPerson.department_name || "—"} · {personSummary.attendance_pct}% attendance ·
                Present {personSummary.present} · Half {personSummary.half} ·
                Leave {personSummary.leave} · Absent {personSummary.absent}
              </p>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-4 py-3">Date</th>
                      <th className="px-4 py-3">Check-in</th>
                      <th className="px-4 py-3">Check-out</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3" aria-label="Actions"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {personSummary.records.length === 0 && (
                      <tr><td colSpan={5} className="px-4 py-4 text-slate-400">No attendance records this month yet.</td></tr>
                    )}
                    {personSummary.records.map((r) => (
                      <tr key={r.id} className="border-t border-slate-100 transition hover:bg-slate-50">
                        <td className="px-4 py-2.5 tabular-nums text-slate-900">{r.record_date}</td>
                        <td className="px-4 py-2.5 tabular-nums text-slate-700">{r.check_in || "—"}</td>
                        <td className="px-4 py-2.5 tabular-nums text-slate-700">{r.check_out || "—"}</td>
                        <td className="px-4 py-2.5"><StatusPill status={r.status} /></td>
                        <td className="px-4 py-2.5 text-right">
                          {r.status === "leave" && leaveActions(r)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {!personLoading && !personSummary && (
            <p className="text-sm text-slate-500">Could not load this person&apos;s records.</p>
          )}
        </Modal>

        {/* Tenant admin settings */}
        {isAdmin && settingsForm && (
          <section className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="text-base font-semibold text-slate-900">Attendance settings · ဆက်တင် (your organization only)</h3>
            <div className="mt-4 space-y-4">
              <div>
                <p className="text-sm font-medium text-slate-700">Working days</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {WEEKDAYS.map((label, idx) => (
                    <label key={label} className="flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-sm">
                      <input
                        type="checkbox"
                        checked={settingsForm.work_days.includes(idx)}
                        onChange={(e) => {
                          const days = e.target.checked
                            ? [...settingsForm.work_days, idx].sort()
                            : settingsForm.work_days.filter((d) => d !== idx);
                          setSettingsForm({ ...settingsForm, work_days: days });
                        }}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap gap-4">
                <label className="block">
                  <span className="text-sm font-medium text-slate-700">Office start</span>
                  <input
                    type="time"
                    value={settingsForm.start_time}
                    onChange={(e) => setSettingsForm({ ...settingsForm, start_time: e.target.value })}
                    className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
                <label className="block">
                  <span className="text-sm font-medium text-slate-700">Office end</span>
                  <input
                    type="time"
                    value={settingsForm.end_time}
                    onChange={(e) => setSettingsForm({ ...settingsForm, end_time: e.target.value })}
                    className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
                <span className="self-end pb-2 text-sm text-slate-500">Midday: {settings?.midday}</span>
              </div>
              <label className="block">
                <span className="text-sm font-medium text-slate-700">Office WiFi public IPs (one per line)</span>
                <textarea
                  rows={3}
                  value={officeIpsText}
                  onChange={(e) => setOfficeIpsText(e.target.value)}
                  placeholder={"203.0.113.10\n198.51.100.23"}
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <span className="text-xs text-slate-400">Empty = not enforced yet (testing). Once set, check-in/out works only from these IPs.</span>
              </label>
              <div>
                <label className="block">
                  <span className="text-sm font-medium text-slate-700">
                    Holidays ({holidayDates.length} date{holidayDates.length === 1 ? "" : "s"}) — one date per line, YYYY-MM-DD
                  </span>
                  <textarea
                    rows={8}
                    value={holidaysText}
                    onChange={(e) => {
                      setHolidaysText(e.target.value);
                      setSettingsError("");
                    }}
                    placeholder={"2026-12-25\n2026-12-31\n2027-01-01"}
                    className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  />
                </label>
                {holidayInvalid.length > 0 && (
                  <p className="mt-1.5 text-xs font-medium text-amber-700">
                    {holidayInvalid.length} line{holidayInvalid.length === 1 ? "" : "s"} {holidayInvalid.length === 1 ? "looks" : "look"} invalid ({holidayInvalid.slice(0, 2).join(", ")}) — fix {holidayInvalid.length === 1 ? "it" : "them"} before saving.
                  </p>
                )}
                <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <p className="text-sm font-medium text-slate-700">Add a date range · ရက်အပိုင်းအခြား ထည့်ရန်</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <input
                      type="date"
                      value={rangeFrom}
                      onChange={(e) => setRangeFrom(e.target.value)}
                      aria-label="Range start date"
                      className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
                    />
                    <span className="text-sm text-slate-500">to</span>
                    <input
                      type="date"
                      value={rangeTo}
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
              </div>
              {settingsError && (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{settingsError}</p>
              )}
              <button
                type="button"
                onClick={openSaveConfirm}
                disabled={busy || settingsSaving}
                className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-40"
              >
                Save settings
              </button>
            </div>
          </section>
        )}
      </div>
      <ConfirmDialog
        open={showSaveConfirm}
        title="Save attendance settings?"
        message={pendingSettingsSummary()}
        confirmLabel="Save"
        tone="brand"
        submitting={settingsSaving}
        onConfirm={handleSaveSettings}
        onCancel={() => setShowSaveConfirm(false)}
      />
      <Modal
        open={editingLeave !== null}
        title="Edit leave day · ခွင့်ရက်ပြင်ရန်"
        onClose={() => setEditingLeave(null)}
      >
        <p className="text-sm text-slate-500">
          Move the leave day{editingLeave ? ` from ${editingLeave.record_date}` : ""} to another date.
        </p>
        <div className="mt-4">
          <input
            type="date"
            value={editLeaveDate}
            onChange={(e) => setEditLeaveDate(e.target.value)}
            aria-label="New leave date"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm"
          />
        </div>
        {editError && (
          <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{editError}</p>
        )}
        <div className="mt-5 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setEditingLeave(null)}
            className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] font-semibold text-slate-600 hover:bg-slate-50 transition"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSaveEditLeave}
            disabled={recordBusy || !editLeaveDate || (editingLeave && editLeaveDate === editingLeave.record_date)}
            className="flex-1 rounded-lg bg-brand-600 px-3 py-2 text-[13px] font-bold text-white shadow-sm transition hover:bg-brand-700 disabled:opacity-60"
          >
            {recordBusy ? "Wait…" : "Save"}
          </button>
        </div>
      </Modal>
      <ConfirmDialog
        open={deletingLeave !== null}
        title="Delete this leave day?"
        message={deletingLeave ? `Remove the leave record for ${deletingLeave.record_date}? The day will count as absent (no record) in the attendance %.` : ""}
        confirmLabel="Delete"
        tone="danger"
        submitting={recordBusy}
        onConfirm={handleDeleteLeave}
        onCancel={() => setDeletingLeave(null)}
      />
    </AppShell>
  );
}
