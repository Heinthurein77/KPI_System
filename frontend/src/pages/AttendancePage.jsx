import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import AppShell from "../components/layout/AppShell";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { getErrorMessage } from "../api/errors";
import {
  checkIn,
  checkOut,
  getAttendanceReport,
  getAttendanceSettings,
  getAttendanceSummary,
  getAttendanceToday,
  markLeave,
  updateAttendanceSettings,
} from "../api/attendance";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STATUS_LABEL = {
  present: "Present · လာတယ်",
  half: "Half day · နေ့တစ်ဝက်",
  leave: "Leave · ခွင့်",
};

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  );
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
  const [leaveDate, setLeaveDate] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const isAdmin = user?.role === "tenant_admin";
  const isDeptAdmin = user?.role === "dept_admin";
  const canCheckIn = user?.role === "employee" || isDeptAdmin;

  const load = useCallback(async () => {
    setError("");
    try {
      const [today, sum] = await Promise.all([
        getAttendanceToday(),
        getAttendanceSummary(year, month),
      ]);
      setTodayData(today);
      setSummary(sum);
      if (isAdmin || isDeptAdmin) {
        setReport(await getAttendanceReport(year, month));
      }
      if (isAdmin) {
        const s = await getAttendanceSettings();
        setSettings(s);
        setSettingsForm(s);
      }
    } catch (e) {
      setError(getErrorMessage(e));
    }
  }, [year, month, isAdmin, isDeptAdmin]);

  useEffect(() => {
    load();
  }, [load]);

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

  async function handleCheckIn() {
    setError("");
    setNotice("");
    setBusy(true);
    try {
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

  // Summary of the pending settings, shown in the save confirm dialog
  // so the admin sees exactly what will be saved before confirming.
  function pendingSettingsSummary() {
    if (!settingsForm) return "";
    const days = settingsForm.work_days.map((d) => WEEKDAYS[d]).join(", ");
    const toMin = (t) => {
      const [h, m] = t.split(":").map(Number);
      return h * 60 + m;
    };
    const mid = (toMin(settingsForm.start_time) + toMin(settingsForm.end_time)) / 2;
    const midday = `${String(Math.floor(mid / 60)).padStart(2, "0")}:${String(mid % 60).padStart(2, "0")}`;
    const ips = settingsForm.office_ips.length;
    const holidays = settingsForm.holidays.length;
    return [
      `Working days: ${days}`,
      `Office hours: ${settingsForm.start_time}–${settingsForm.end_time} (midday ${midday})`,
      ips > 0
        ? `Office WiFi IPs: ${settingsForm.office_ips.join(", ")} (${ips} configured — check-in/out enforced on office WiFi only)`
        : "Office WiFi IPs: none — check-in/out not enforced yet",
      `Holidays: ${holidays} date${holidays === 1 ? "" : "s"}`,
    ].join("\n");
  }

  async function handleSaveSettings() {
    setError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      const payload = {
        work_days: settingsForm.work_days,
        start_time: settingsForm.start_time,
        end_time: settingsForm.end_time,
        office_ips: settingsForm.office_ips,
        holidays: settingsForm.holidays,
      };
      const saved = await updateAttendanceSettings(payload);
      setSettings(saved);
      setSettingsForm(saved);
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
      setError(getErrorMessage(e, "Settings could not be saved. Please try again."));
      setShowSaveConfirm(false);
    } finally {
      setSettingsSaving(false);
    }
  }

  const record = todayData?.record;
  const monthName = new Date(year, month - 1, 1).toLocaleString("en-US", { month: "long" });

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-4 py-6">
        <h1 className="text-2xl font-bold text-slate-900">Attendance · တက်ရောက်မှု</h1>
        <p className="mt-1 text-sm text-slate-500">
          Office WiFi only · ရုံး WiFi နဲ့မှ check-in/check-out လုပ်လို့ရပါတယ်။ Late never
          deducts — late time is shown for info only.
        </p>

        {error && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">
            {notice}
          </div>
        )}

        {/* Today */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-lg font-semibold text-slate-900">Today · ဒီနေ့</h2>
          {todayData && (
            <>
              <p className="mt-1 text-sm text-slate-600">
                {todayData.today} · Office {todayData.start_time}–{todayData.end_time} ·
                Midday {todayData.midday} · {todayData.is_working_day ? "Working day" : "Not a working day"}
              </p>
              {todayData.wifi_enforced ? (
                <p className={`mt-2 text-sm ${todayData.ip_allowed ? "text-emerald-700" : "text-red-700"}`}>
                  {todayData.ip_allowed
                    ? "✓ You are on office WiFi."
                    : "✕ You are NOT on office WiFi — check-in/out is disabled."}
                </p>
              ) : (
                <p className="mt-2 text-sm text-amber-700">
                  ⚠ No office WiFi IP is configured yet — check-in is allowed for testing.
                  Ask the tenant admin to add the office public IP in Settings below.
                </p>
              )}
              {record && (
                <p className="mt-2 text-sm text-slate-700">
                  Check-in {record.check_in || "—"}
                  {record.check_out ? ` · Check-out ${record.check_out}` : " · Not checked out yet"}
                  {" · "}
                  {STATUS_LABEL[record.status] || record.status}
                  {record.late_minutes > 0 && ` · Late ${record.late_minutes} min`}
                </p>
              )}
              {canCheckIn && (
                <div className="mt-4 flex gap-3">
                  <button
                    type="button"
                    onClick={handleCheckIn}
                    disabled={busy || !todayData.is_working_day || !todayData.ip_allowed || !!record}
                    className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
                  >
                    Check-in
                  </button>
                  <button
                    type="button"
                    onClick={handleCheckOut}
                    disabled={busy || !todayData.ip_allowed || !record || !!record.check_out || record.status === "leave"}
                    className="rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-700 disabled:opacity-40"
                  >
                    Check-out
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Month navigation + summary */}
        <div className="mt-6 flex items-center gap-3">
          <button type="button" onClick={() => shiftMonth(-1)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">‹ Prev</button>
          <h2 className="text-lg font-semibold text-slate-900">{monthName} {year}</h2>
          <button type="button" onClick={() => shiftMonth(1)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">Next ›</button>
        </div>

        {summary && (
          <>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Attendance" value={`${summary.attendance_pct}%`} sub={`${summary.present + summary.half * 0.5} / ${summary.working_days} days`} />
              <Stat label="Present" value={summary.present} />
              <Stat label="Half day" value={summary.half} />
              <Stat label="Leave" value={summary.leave} sub="counts as absent" />
              <Stat label="Absent" value={summary.absent} sub="no record" />
              <Stat label="Late" value={`${summary.late_days} days`} sub={`${summary.total_late_minutes} min total`} />
            </div>

            <div className="mt-6 overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Date</th>
                    <th className="px-4 py-2">Check-in</th>
                    <th className="px-4 py-2">Check-out</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Late</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.records.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-4 text-slate-400">No attendance records this month yet.</td></tr>
                  )}
                  {summary.records.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100">
                      <td className="px-4 py-2">{r.record_date}</td>
                      <td className="px-4 py-2">{r.check_in || "—"}</td>
                      <td className="px-4 py-2">{r.check_out || "—"}</td>
                      <td className="px-4 py-2">{STATUS_LABEL[r.status] || r.status}</td>
                      <td className="px-4 py-2">{r.late_minutes > 0 ? `${r.late_minutes} min` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mark leave */}
            <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5">
              <h3 className="font-semibold text-slate-900">Mark a leave day · ခွင့်ရက်မှတ်ရန်</h3>
              <p className="mt-1 text-sm text-slate-500">Approved leave counts as absent in the attendance %.</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <input
                  type="date"
                  value={leaveDate}
                  onChange={(e) => setLeaveDate(e.target.value)}
                  className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <button
                  type="button"
                  onClick={handleLeave}
                  disabled={busy || !leaveDate}
                  className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
                >
                  Mark leave
                </button>
              </div>
            </div>
          </>
        )}

        {/* Admin report */}
        {report && (
          <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="font-semibold text-slate-900">Monthly report · လချုပ်အစီရင်ခံစာ</h3>
            <p className="mt-1 text-sm text-slate-500">
              Attendance % is display-only — it is never written to KPI.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-4 py-2">Employee</th>
                    <th className="px-4 py-2">Attendance</th>
                    <th className="px-4 py-2">Present</th>
                    <th className="px-4 py-2">Half</th>
                    <th className="px-4 py-2">Leave</th>
                    <th className="px-4 py-2">Absent</th>
                    <th className="px-4 py-2">Late</th>
                  </tr>
                </thead>
                <tbody>
                  {report.map((row) => (
                    <tr key={row.user_id} className="border-t border-slate-100">
                      <td className="px-4 py-2">{row.employee_name}</td>
                      <td className="px-4 py-2 font-semibold">{row.attendance_pct}%</td>
                      <td className="px-4 py-2">{row.present}</td>
                      <td className="px-4 py-2">{row.half}</td>
                      <td className="px-4 py-2">{row.leave}</td>
                      <td className="px-4 py-2">{row.absent}</td>
                      <td className="px-4 py-2">{row.late_days} days · {row.total_late_minutes} min</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tenant admin settings */}
        {isAdmin && settingsForm && (
          <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5">
            <h3 className="font-semibold text-slate-900">Attendance settings · ဆက်တင် (your organization only)</h3>
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
                  value={settingsForm.office_ips.join("\n")}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      office_ips: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder={"203.0.113.10\n198.51.100.23"}
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
                <span className="text-xs text-slate-400">Empty = not enforced yet (testing). Once set, check-in/out works only from these IPs.</span>
              </label>
              <label className="block">
                <span className="text-sm font-medium text-slate-700">Holidays (one date per line, YYYY-MM-DD)</span>
                <textarea
                  rows={3}
                  value={settingsForm.holidays.join("\n")}
                  onChange={(e) =>
                    setSettingsForm({
                      ...settingsForm,
                      holidays: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder={"2026-11-15"}
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                />
              </label>
              <button
                type="button"
                onClick={() => setShowSaveConfirm(true)}
                disabled={busy || settingsSaving}
                className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
              >
                Save settings
              </button>
            </div>
          </div>
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
    </AppShell>
  );
}
