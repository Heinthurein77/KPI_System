import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAuth } from "../context/AuthContext";
import AppShell from "../components/layout/AppShell";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { getErrorMessage } from "../api/errors";
import {
  createDailyTask,
  dailyEvidenceUrl,
  deleteDailyTask,
  exportDailyDiary,
  getDailyAuditLog,
  getMonthlyReview,
  giveDailyFeedback,
  listDailyTasks,
  monthlyDailySummary,
  saveMonthlyReview,
  updateDailyTask,
  uploadDailyEvidence,
} from "../api/dailyTasks";

const STATUS_OPTIONS = [
  { value: "not_started", label: "မစရသေး · Not Started" },
  { value: "in_progress", label: "လုပ်နေဆဲ · In Progress" },
  { value: "completed", label: "ပြီးစီး · Completed" },
  { value: "pending", label: "ဆိုင်းငံ့ · Pending" },
];

const STATUS_CHIP = {
  not_started: "bg-slate-100 text-slate-600",
  in_progress: "bg-blue-100 text-blue-700",
  completed: "bg-emerald-100 text-emerald-700",
  pending: "bg-amber-100 text-amber-700",
};

const ACTION_LABEL = {
  daily_task_created: "ဖန်တီး · Created",
  daily_task_updated: "ပြင်ဆင် · Edited",
  daily_task_deleted: "ဖျက် · Deleted",
  daily_task_feedback: "Feedback ပေး · Feedback",
  daily_task_evidence_uploaded: "Evidence တင် · Evidence",
  daily_task_review_updated: "Review ပြင်ဆင် · Review",
};

const EMPTY_FORM = {
  task_date: new Date().toISOString().slice(0, 10),
  plan_linkage: "",
  work_done: "",
  status: "in_progress",
  figure_output: "",
  evidence_link: "",
  difficulties: "",
  support_needed: "",
  self_reflection: "",
  tomorrow_plan_1: "",
  tomorrow_plan_2: "",
  tomorrow_plan_3: "",
};

function Field({ label, sub, children }) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-slate-700">{label}</span>
      {sub && <span className="ml-1 text-xs text-slate-400">{sub}</span>}
      <div className="mt-1">{children}</div>
    </label>
  );
}

const inputCls =
  "w-full rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-sm focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100";

function formatTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DailyTasksPage() {
  const { user } = useAuth();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [viewUserId, setViewUserId] = useState("");
  // Dept admins get two separate dashboards: "mine" (fill own diary) and
  // "team" (department diaries + feedback). Tenant admins only ever see
  // the team view (they never fill a diary); employees only see "mine".
  const [tab, setTab] = useState(user.role === "tenant_admin" ? "team" : "mine");
  // Mine view split: the fill form and the filled entries never share one
  // screen — "fill" shows only the form, "history" shows only filled entries.
  const [mineView, setMineView] = useState("fill");
  const effectiveUserId = tab === "mine" ? String(user.id) : (viewUserId || undefined);
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [evidenceFile, setEvidenceFile] = useState(null);
  const [feedbackDraft, setFeedbackDraft] = useState({});
  const [review, setReview] = useState(null);
  const [reviewDraft, setReviewDraft] = useState({
    achievements: "",
    challenges_solutions: "",
    improvements_next_focus: "",
    hod_evaluation: "",
  });
  const [audit, setAudit] = useState([]);
  const [notice, setNotice] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [confirmBusy, setConfirmBusy] = useState(false);
  // Success toasts disappear on their own after 4s instead of lingering;
  // error notices stay until the next action.
  useEffect(() => {
    if (!notice?.ok) return undefined;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);

  const isSupervisor = user.role === "dept_admin" || user.role === "tenant_admin";
  const reviewUserId = useMemo(() => {
    if (tab === "mine") return user.id;
    if (viewUserId) return Number(viewUserId);
    return null;
  }, [tab, viewUserId, user.id]);

  const load = useCallback(async () => {
    const [list, sum, trail] = await Promise.all([
      listDailyTasks(year, month, effectiveUserId),
      monthlyDailySummary(year, month),
      // Audit trail is tenant-admin-only; others never even request it.
      user.role === "tenant_admin"
        ? getDailyAuditLog(year, month, effectiveUserId).catch(() => [])
        : Promise.resolve([]),
    ]);
    setEntries(list);
    setSummary(sum);
    setAudit(trail);
  }, [year, month, effectiveUserId]);

  useEffect(() => {
    load().catch(() => setNotice({ text: "Diary ကို load လုပ်လို့မရပါ။", ok: false }));
  }, [load]);

  useEffect(() => {
    if (!reviewUserId) {
      setReview(null);
      setReviewDraft({
        achievements: "",
        challenges_solutions: "",
        improvements_next_focus: "",
        hod_evaluation: "",
      });
      return;
    }
    getMonthlyReview(year, month, reviewUserId)
      .then((r) => {
        setReview(r);
        setReviewDraft({
          achievements: r?.achievements || "",
          challenges_solutions: r?.challenges_solutions || "",
          improvements_next_focus: r?.improvements_next_focus || "",
          hod_evaluation: r?.hod_evaluation || "",
        });
      })
      .catch(() => setReview(null));
  }, [year, month, reviewUserId]);

  const shiftMonth = (delta) => {
    let m = month + delta;
    let y = year;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    setYear(y);
    setMonth(m);
  };

  // Submit happens in two steps: first a confirmation dialog, then the save.
  // The dialog shows what is about to be saved so a mistaken tap can't
  // silently create or overwrite an entry.
  const requestSubmit = (e) => {
    e.preventDefault();
    if (!form.work_done.trim() || saving) return;
    if (evidenceFile && evidenceFile.size > 10 * 1024 * 1024) {
      setNotice({ text: "သက်သေဖိုင်က 10 MB ထက် ကြီးနေတယ်။ ဖိုင်ကို ချုံ့ပြီး ပြန်တင်ပါ။", ok: false });
      return;
    }
    const statusLabel = STATUS_OPTIONS.find((o) => o.value === form.status)?.label || form.status;
    setConfirm({
      title: editingId ? "ပြင်ဆင်ချက်ကို သိမ်းမလား?" : "Diary entry ထည့်မလား?",
      message: `${form.task_date} · ${statusLabel}${evidenceFile ? `\n📎 ${evidenceFile.name}` : ""}`,
      details: (
        <p className="rounded-xl bg-slate-50 px-3 py-2 text-[13px] text-slate-600 whitespace-pre-wrap">
          {form.work_done.length > 200 ? form.work_done.slice(0, 197) + "…" : form.work_done}
        </p>
      ),
      confirmLabel: "သေချာတယ်၊ သိမ်းမယ်",
      tone: "success",
      onConfirm: doSubmit,
    });
  };

  const doSubmit = async () => {
    setConfirmBusy(true);
    setSaving(true);
    try {
      let saved;
      if (editingId) {
        saved = await updateDailyTask(editingId, form);
      } else {
        saved = await createDailyTask(form);
      }
      const hadEvidence = !!evidenceFile;
      let fileError = null;
      if (evidenceFile) {
        try {
          saved = await uploadDailyEvidence(saved.id, evidenceFile);
        } catch (err) {
          // The entry itself is already saved — only the file failed.
          // Reporting this as a total save failure was the old misleading
          // "သိမ်းလို့မရပါ" error; say exactly what failed instead.
          fileError = getErrorMessage(err, "ဖိုင်တင်လို့မရပါ။");
        }
        setEvidenceFile(null);
      }
      setEditingId(null);
      setForm(EMPTY_FORM);
      if (fileError) {
        setNotice({ text: `Entry သိမ်းပြီးပါပြီ၊ ဒါပေမဲ့ သက်သေဖိုင်တင်လို့မရပါ: ${fileError}`, ok: false });
      } else {
        setNotice({ text: "✓ Diary entry သိမ်းပြီးပါပြီ။", ok: true });
      }
      load();
      if (hadEvidence) setMineView("history");
    } catch (err) {
      // Show the real reason (validation, session, server) instead of a
      // generic "try again" that hides the cause.
      setNotice({ text: getErrorMessage(err, "သိမ်းလို့မရပါ။ ထပ်စမ်းကြည့်ပါ။"), ok: false });
    } finally {
      setConfirmBusy(false);
      setSaving(false);
      setConfirm(null);
    }
  };

  const requestDelete = (entry) => {
    setConfirm({
      title: "ဒီ entry ကို ဖျက်မလား?",
      message: `${entry.task_date} · ${entry.author_name}`,
      details: (
        <p className="rounded-xl bg-slate-50 px-3 py-2 text-[13px] text-slate-600 whitespace-pre-wrap">
          {entry.work_done.length > 200 ? entry.work_done.slice(0, 197) + "…" : entry.work_done}
        </p>
      ),
      confirmLabel: "ဖျက်မယ်",
      danger: true,
      onConfirm: async () => {
        setConfirmBusy(true);
        try {
          await deleteDailyTask(entry.id);
          setNotice({ text: "✓ Entry ဖျက်ပြီးပါပြီ။", ok: true });
          load();
        } catch (err) {
          setNotice({ text: getErrorMessage(err, "ဖျက်လို့မရပါ။ ထပ်စမ်းကြည့်ပါ။"), ok: false });
        } finally {
          setConfirmBusy(false);
          setConfirm(null);
        }
      },
    });
  };

  const requestSaveReview = () => {
    if (confirmBusy || !reviewUserId) return;
    const monthLabel = `${year}-${String(month).padStart(2, "0")}`;
    const ownerLabel = viewingName ? `${viewingName} ၏ review` : "ကိုယ့် review";
    const fields = [
      ["အောင်မြင်မှုများ", reviewDraft.achievements],
      ["အခက်အခဲ & ဖြေရှင်းပုံ", reviewDraft.challenges_solutions],
      ["ပိုကောင်းအောင် ပြင်ဆင်မည့်အချက်", reviewDraft.improvements_next_focus],
      ["HOD အကဲဖြတ်ချက်", reviewDraft.hod_evaluation],
    ].filter(([, v]) => v.trim());
    setConfirm({
      title: "Month-end review သိမ်းမလား?",
      message: `${monthLabel} · ${ownerLabel}`,
      details:
        fields.length > 0 ? (
          <div className="space-y-1">
            {fields.map(([label, value]) => (
              <p key={label} className="rounded-xl bg-slate-50 px-3 py-2 text-[13px] text-slate-600 whitespace-pre-wrap">
                <span className="font-medium text-slate-700">{label}:</span>{" "}
                {value.length > 100 ? value.slice(0, 97) + "…" : value}
              </p>
            ))}
          </div>
        ) : (
          <p className="rounded-xl bg-slate-50 px-3 py-2 text-[13px] text-slate-600">
            ဖြည့်ထားတာ ဘာမှမရှိပါ — အလွတ်အဖြစ် သိမ်းမည်။
          </p>
        ),
      confirmLabel: "သေချာတယ်၊ သိမ်းမယ်",
      tone: "success",
      onConfirm: doSaveReview,
    });
  };

  const doSaveReview = async () => {
    setConfirmBusy(true);
    try {
      const saved = await saveMonthlyReview(year, month, reviewUserId, reviewDraft);
      setReview(saved);
      setNotice({ text: "✓ Month-end review သိမ်းပြီးပါပြီ။", ok: true });
    } catch (err) {
      // The old button had no error handling at all — a failed save died
      // silently. Show the real reason instead.
      setNotice({ text: getErrorMessage(err, "Review သိမ်းလို့မရပါ။ ထပ်စမ်းကြည့်ပါ။"), ok: false });
    } finally {
      setConfirmBusy(false);
      setConfirm(null);
    }
  };

  const startEdit = (entry) => {
    setEditingId(entry.id);
    setMineView("fill");
    setForm({
      task_date: entry.task_date,
      plan_linkage: entry.plan_linkage || "",
      work_done: entry.work_done,
      status: entry.status,
      figure_output: entry.figure_output || "",
      evidence_link: entry.evidence_link || "",
      difficulties: entry.difficulties || "",
      support_needed: entry.support_needed || "",
      self_reflection: entry.self_reflection || "",
      tomorrow_plan_1: entry.tomorrow_plan_1 || "",
      tomorrow_plan_2: entry.tomorrow_plan_2 || "",
      tomorrow_plan_3: entry.tomorrow_plan_3 || "",
    });
    // AppShell scrolls inside <main>, not the window.
    const main = document.querySelector("main");
    if (main) main.scrollTo({ top: 0, behavior: "smooth" });
    else window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const ownSummary = summary?.users.find((u) => u.user_id === user.id);
  // Team tab shows department members only (dept admin's own row excluded);
  // mine tab shows only the author's own row.
  const displayUsers = useMemo(() => {
    if (!summary) return [];
    if (tab === "mine") return ownSummary ? [ownSummary] : [];
    // Team tab with a member selected shows ONLY that member — per-user view.
    if (viewUserId) {
      const one = summary.users.find((u) => String(u.user_id) === String(viewUserId));
      return one ? [one] : [];
    }
    if (user.role === "dept_admin") return summary.users.filter((u) => u.user_id !== user.id);
    return summary.users;
  }, [summary, tab, viewUserId, ownSummary, user]);
  const teamChartData = displayUsers.map((u) => ({
    name: u.author_name,
    Completed: u.completed,
    "In Progress / Pending": u.in_progress_or_pending,
  }));
  // Team tab with no member selected shows everyone except the admin self.
  const displayEntries =
    tab === "team" && !viewUserId
      ? entries.filter((e) => e.user_id !== user.id)
      : entries;
  const viewingName = tab === "team" && viewUserId
    ? summary?.users.find((u) => String(u.user_id) === String(viewUserId))?.author_name
    : null;

  return (
    <AppShell title="နေ့စဉ် အလုပ် မှတ်တမ်း · Daily Tasks">
    <div className="mx-auto max-w-5xl space-y-5">
      {/* Header */}
      <div className="rounded-3xl bg-gradient-to-r from-indigo-600 via-indigo-500 to-violet-500 p-5 text-white shadow">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">နေ့စဉ် အလုပ် မှတ်တမ်း</h1>
            <p className="text-sm text-indigo-100">Daily Working Diary · {user.name}</p>
          </div>
          <div className="flex items-center gap-2">
            <button className="rounded-full bg-white/20 px-3 py-1 text-sm hover:bg-white/30" onClick={() => shiftMonth(-1)}>‹ ပြီးခဲ့သောလ</button>
            <span className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-indigo-700">{year}-{String(month).padStart(2, "0")}</span>
            <button className="rounded-full bg-white/20 px-3 py-1 text-sm hover:bg-white/30" onClick={() => shiftMonth(1)}>နောက်လ ›</button>
            <button
              className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-indigo-700"
              onClick={() => exportDailyDiary(year, month, viewUserId || undefined)}
            >
              Excel ထုတ်ရန်
            </button>
          </div>
        </div>
        {isSupervisor && summary && tab === "team" && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-sm text-indigo-100">ဝန်ထမ်း၏ diary ကြည့်ရန်:</span>
            <select
              className="rounded-full border-0 px-3 py-1.5 text-sm text-slate-800"
              value={viewUserId}
              onChange={(e) => setViewUserId(e.target.value)}
            >
              <option value="">အားလုံး (ကိုယ့်ဌာန / tenant)</option>
              {summary.users.map((u) => (
                <option key={u.user_id} value={u.user_id}>{u.author_name}</option>
              ))}
            </select>
            {viewingName && (
              <button
                className="rounded-full bg-white/20 px-3 py-1 text-sm hover:bg-white/30"
                onClick={() => setViewUserId("")}
              >
                ✕ {viewingName} ကို ပိတ်ရန်
              </button>
            )}
          </div>
        )}
      </div>

      {/* Dept admin's two separate dashboards */}
      {user.role === "dept_admin" && (
        <div className="flex gap-2">
          <button
            className={`rounded-full px-5 py-2 text-sm font-medium ${tab === "mine" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 shadow-sm"}`}
            onClick={() => setTab("mine")}
          >
            📝 ကိုယ့် Diary · ဖြည့်ရန်
          </button>
          <button
            className={`rounded-full px-5 py-2 text-sm font-medium ${tab === "team" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 shadow-sm"}`}
            onClick={() => setTab("team")}
          >
            👥 ဌာန Diary · Feedback ပေးရန်
          </button>
        </div>
      )}

      {/* Mine view split: form and filled entries live on separate tabs */}
      {tab === "mine" && user.role !== "tenant_admin" && (
        <div className="flex gap-2">
          <button
            className={`rounded-full px-5 py-2 text-sm font-medium ${mineView === "fill" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 shadow-sm"}`}
            onClick={() => setMineView("fill")}
          >
            ✏️ ဖြည့်ရန် · Fill Form
          </button>
          <button
            className={`rounded-full px-5 py-2 text-sm font-medium ${mineView === "history" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 shadow-sm"}`}
            onClick={() => setMineView("history")}
          >
            📋 ဖြည့်ပြီးသား · Filled Entries
          </button>
        </div>
      )}

      {notice && (
        <p className={`rounded-2xl px-4 py-2 text-sm ${notice.ok ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-700"}`}>
          {notice.text}
        </p>
      )}

      {/* Monthly summary + chart */}
      {summary && (
        <section className="rounded-3xl bg-white p-5 shadow-sm">
          <h2 className="font-semibold text-slate-800">လစဉ် အကျဉ်းချုပ် · Monthly Summary</h2>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {displayUsers.map((u) => (
              <div key={u.user_id} className="rounded-2xl bg-slate-50 p-3">
                <p className="truncate text-xs font-medium text-slate-500">{u.author_name}</p>
                <p className="mt-1 text-sm">အလုပ်လုပ်ရက်: <b>{u.working_days}</b></p>
                <p className="text-sm">ပြီးစီး: <b className="text-emerald-600">{u.completed}</b></p>
                <p className="text-sm">မပြီးသေး: <b className="text-amber-600">{u.in_progress_or_pending}</b></p>
              </div>
            ))}
          </div>
          {teamChartData.length > 1 && (
            <div className="mt-4 h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={teamChartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="name" />
                  <YAxis allowDecimals={false} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="Completed" fill="#16a34a" />
                  <Bar dataKey="In Progress / Pending" fill="#f59e0b" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
      )}

      {/* Entry form — diary authors only, and only on the "fill" sub-tab */}
      {user.role !== "tenant_admin" && tab === "mine" && mineView === "fill" && (
      <section className="rounded-3xl bg-white p-5 shadow-sm">
        <h2 className="font-semibold text-slate-800">
          {editingId ? "Entry ပြင်ဆင်ရန် · Edit Entry" : "ဒီနေ့ အလုပ် ထည့်ရန် · New Diary Entry"}
        </h2>
        <form onSubmit={requestSubmit} className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="ရက်စွဲ" sub="Date">
              <input type="date" className={inputCls} value={form.task_date}
                onChange={(e) => setForm({ ...form, task_date: e.target.value })} required />
            </Field>
            <Field label="Plan ၏ အစိတ်အပိုင်း" sub="Plan Linkage">
              <input className={inputCls} value={form.plan_linkage} placeholder="ဥပမာ November Plan — Monthly Report"
                onChange={(e) => setForm({ ...form, plan_linkage: e.target.value })} />
            </Field>
            <Field label="အခြေအနေ" sub="Status">
              <select className={inputCls} value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </Field>
          </div>
          <Field label="ယနေ့ လုပ်ခဲ့သော / ပြီးမြောက်သော အလုပ်" sub="Work Done *">
            <textarea rows={3} className={inputCls} value={form.work_done}
              onChange={(e) => setForm({ ...form, work_done: e.target.value })} required />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="ကိန်းဂဏန်း / ရလဒ်" sub="Figure / Output">
              <input className={inputCls} value={form.figure_output} placeholder="ဥပမာ Report ၁ စောင်"
                onChange={(e) => setForm({ ...form, figure_output: e.target.value })} />
            </Field>
            <Field label="သက်သေ (Report / File / Link)" sub="Evidence">
              <input className={inputCls} value={form.evidence_link}
                onChange={(e) => setForm({ ...form, evidence_link: e.target.value })} />
            </Field>
          </div>
          <Field label="Evidence ဖိုင် တိုက်ရိုက်တင်ရန်" sub="ဖိုင်တွဲရန် · max 10 MB">
            <input type="file" className="text-sm" onChange={(e) => setEvidenceFile(e.target.files[0] || null)} />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="အခက်အခဲ / မပြီးသေးသည့် အကြောင်းရင်း" sub="Difficulties & Pending">
              <textarea rows={2} className={inputCls} value={form.difficulties}
                onChange={(e) => setForm({ ...form, difficulties: e.target.value })} />
            </Field>
            <Field label="လိုအပ်သော ပံ့ပိုးမှု" sub="Support Needed">
              <textarea rows={2} className={inputCls} value={form.support_needed}
                onChange={(e) => setForm({ ...form, support_needed: e.target.value })} />
            </Field>
          </div>
          <Field label="ကိုယ်တိုင် သုံးသပ်ချက်" sub="Self-Reflection">
            <textarea rows={2} className={inputCls} value={form.self_reflection}
              onChange={(e) => setForm({ ...form, self_reflection: e.target.value })} />
          </Field>
          <div>
            <p className="text-sm font-medium text-slate-700">မနက်ဖြန် အဓိက အလုပ် ၃ ခု <span className="text-xs text-slate-400">Tomorrow Top 3 Plan</span></p>
            <div className="mt-1 grid gap-3 sm:grid-cols-3">
              {[1, 2, 3].map((n) => (
                <input key={n} className={inputCls} placeholder={`အလုပ် ${n}`}
                  value={form[`tomorrow_plan_${n}`]}
                  onChange={(e) => setForm({ ...form, [`tomorrow_plan_${n}`]: e.target.value })} />
              ))}
            </div>
          </div>
          <div className="flex gap-2">
            <button type="submit" disabled={saving} className="rounded-full bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
              {saving ? "သိမ်းနေတယ်…" : editingId ? "ပြင်ဆင်ချက်ကို သိမ်းရန်" : "Entry ထည့်ရန်"}
            </button>
            {editingId && (
              <button type="button" className="rounded-full border px-5 py-2 text-sm"
                onClick={() => { setEditingId(null); setForm(EMPTY_FORM); }}>
                မလုပ်တော့ပါ
              </button>
            )}
          </div>
        </form>
      </section>
      )}

      {/* Entries — team tab always; mine tab only on the "history" sub-tab,
          never under the form */}
      {(tab === "team" || mineView === "history") && (
      <section className="space-y-3">
        <h2 className="px-1 font-semibold text-slate-800">
          {tab === "team" ? "ဌာန Diary Entries" : "ကိုယ့် Diary Entries"} {viewingName ? `— ${viewingName}` : ""}
        </h2>
        {displayEntries.map((entry) => (
          <article key={entry.id} id={`diary-entry-${entry.id}`} className="rounded-3xl bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-slate-800">
                {entry.task_date} · {entry.author_name}
                {entry.department_name ? ` · ${entry.department_name}` : ""}
                {entry.position ? ` · ${entry.position}` : ""}
              </p>
              <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_CHIP[entry.status] || STATUS_CHIP.not_started}`}>
                {entry.status.replace("_", " ")}
              </span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">{entry.work_done}</p>
            <div className="mt-2 space-y-0.5 text-xs text-slate-500">
              {entry.plan_linkage && <p>Plan: {entry.plan_linkage}</p>}
              {entry.figure_output && <p>ရလဒ်: {entry.figure_output}</p>}
              {entry.evidence_link && <p>သက်သေ: {entry.evidence_link}</p>}
              {entry.has_evidence_file && (
                <div className="mt-2 space-y-1">
                  {["jpg", "jpeg", "png", "webp", "gif"].includes(
                    (entry.evidence_file || "").split(".").pop().toLowerCase(),
                  ) && (
                    <a href={dailyEvidenceUrl(entry.id)} target="_blank" rel="noreferrer">
                      <img src={dailyEvidenceUrl(entry.id)} alt="Evidence" className="max-h-64 rounded-2xl border object-contain" />
                    </a>
                  )}
                  {(entry.evidence_file || "").toLowerCase().endsWith(".pdf") && (
                    <iframe src={dailyEvidenceUrl(entry.id)} title="Evidence preview" className="h-80 w-full rounded-2xl border" />
                  )}
                  <div>
                    <a className="text-indigo-600 underline" href={dailyEvidenceUrl(entry.id)} target="_blank" rel="noreferrer">
                      📎 ဖိုင်ကို အပြည့်ကြည့်ရန် / ဒေါင်းလုဒ်လုပ်ရန်
                    </a>
                  </div>
                </div>
              )}
              {entry.difficulties && <p>အခက်အခဲ: {entry.difficulties}</p>}
              {entry.support_needed && <p>လိုအပ်သော ပံ့ပိုးမှု: {entry.support_needed}</p>}
              {entry.self_reflection && <p>သုံးသပ်ချက်: {entry.self_reflection}</p>}
              {(entry.tomorrow_plan_1 || entry.tomorrow_plan_2 || entry.tomorrow_plan_3) && (
                <p>မနက်ဖြန်: {[entry.tomorrow_plan_1, entry.tomorrow_plan_2, entry.tomorrow_plan_3].filter(Boolean).join(" · ")}</p>
              )}
            </div>
            {entry.supervisor_feedback && (
              <p className="mt-3 rounded-2xl bg-amber-50 px-3 py-2 text-sm text-amber-900">
                💬 Supervisor: {entry.supervisor_feedback}
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              {entry.user_id === user.id && (
                <>
                  <button className="rounded-full border px-3 py-1 text-xs" onClick={() => startEdit(entry)}>ပြင်ဆင်ရန်</button>
                  <button className="rounded-full border px-3 py-1 text-xs text-red-600"
                    onClick={() => requestDelete(entry)}>
                    ဖျက်ရန်
                  </button>
                </>
              )}
            </div>
            {isSupervisor && entry.user_id !== user.id && (
              <div className="mt-3 flex gap-2">
                <input className={inputCls} placeholder="Supervisor feedback ရေးရန်…"
                  value={feedbackDraft[entry.id] || ""}
                  onChange={(e) => setFeedbackDraft({ ...feedbackDraft, [entry.id]: e.target.value })} />
                <button className="shrink-0 rounded-full bg-indigo-600 px-4 py-1 text-xs text-white"
                  onClick={async () => {
                    await giveDailyFeedback(entry.id, feedbackDraft[entry.id] || "");
                    setNotice({ text: "Feedback ပို့ပြီးပါပြီ — ဝန်ထမ်းဆီ notification ရောက်မယ်။", ok: true });
                    load();
                  }}>
                  ပို့ရန်
                </button>
              </div>
            )}
          </article>
        ))}
        {displayEntries.length === 0 && (
          <p className="rounded-3xl bg-white p-5 text-center text-sm text-slate-500">
            {tab === "team"
              ? "ဌာနမှာ ဒီလ diary entry မရှိသေးပါ။ ဝန်ထမ်းတစ်ဦးကို အပေါ်က dropdown နဲ့ ရွေးပြီး ကြည့်နိုင်ပါတယ်။"
              : "ဒီလမှာ diary entry မရှိသေးပါ။"}
          </p>
        )}
      </section>
      )}

      {/* Month-end review — mine history tab: own review; team tab: selected member only.
          The saved review is shown separately, read-only, like the Excel
          Monthly Dashboard — the form below is only for editing it. */}
      {((tab === "mine" && mineView === "history" && user.role !== "tenant_admin") ||
        (tab === "team" && viewUserId !== "")) && (
      <>
      {review && (review.achievements || review.challenges_solutions || review.improvements_next_focus || review.hod_evaluation) && (
      <section className="rounded-3xl bg-indigo-50/60 p-5 shadow-sm ring-1 ring-indigo-100">
        <h2 className="font-semibold text-slate-800">
          သိမ်းပြီးသော သုံးသပ်ချက် · Saved Review — {year}-{String(month).padStart(2, "0")}{viewingName ? ` · ${viewingName}` : ""}
        </h2>
        <div className="mt-3 grid gap-3">
          {[
            ["ဒီလ၏ အဓိက အောင်မြင်ပြီးစီးမှုများ", "Major Achievements", review.achievements],
            ["ကြုံတွေ့ခဲ့သော အခက်အခဲများနှင့် ဖြေရှင်းပုံ", "Key Challenges & Solutions", review.challenges_solutions],
            ["နောက်လတွင် ပိုမိုကောင်းမွန်အောင် ပြင်ဆင်မည့်အချက်များ", "Improvement / Next Month Focus", review.improvements_next_focus],
            ["HOD ၏ အကဲဖြတ်ချက်နှင့် လမ်းညွှန်ချက်", "HOD Evaluation", review.hod_evaluation],
          ].map(([label, sub, value]) => (
            <div key={sub}>
              <p className="text-xs font-medium text-slate-500">{label} · {sub}</p>
              <p className="mt-1 whitespace-pre-wrap rounded-2xl bg-white px-3 py-2 text-sm text-slate-800">{value?.trim() ? value : "—"}</p>
            </div>
          ))}
        </div>
      </section>
      )}
      <section className="rounded-3xl bg-white p-5 shadow-sm">
        <h2 className="font-semibold text-slate-800">သုံးသပ်ချက် ပြင်ဆင်ရန် · Edit Review</h2>
        <div className="mt-3 grid gap-3">
          <Field label="ဒီလ၏ အဓိက အောင်မြင်ပြီးစီးမှုများ" sub="Major Achievements">
            <textarea rows={2} className={inputCls} value={reviewDraft.achievements}
              onChange={(e) => setReviewDraft({ ...reviewDraft, achievements: e.target.value })} />
          </Field>
          <Field label="ကြုံတွေ့ခဲ့သော အခက်အခဲများနှင့် ဖြေရှင်းပုံ" sub="Key Challenges & Solutions">
            <textarea rows={2} className={inputCls} value={reviewDraft.challenges_solutions}
              onChange={(e) => setReviewDraft({ ...reviewDraft, challenges_solutions: e.target.value })} />
          </Field>
          <Field label="နောက်လတွင် ပိုမိုကောင်းမွန်အောင် ပြင်ဆင်မည့်အချက်များ" sub="Improvement / Next Month Focus">
            <textarea rows={2} className={inputCls} value={reviewDraft.improvements_next_focus}
              onChange={(e) => setReviewDraft({ ...reviewDraft, improvements_next_focus: e.target.value })} />
          </Field>
          {isSupervisor && reviewUserId !== user.id && (
            <Field label="HOD ၏ အကဲဖြတ်ချက်နှင့် လမ်းညွှန်ချက်" sub="HOD Evaluation">
              <textarea rows={2} className={inputCls} value={reviewDraft.hod_evaluation}
                onChange={(e) => setReviewDraft({ ...reviewDraft, hod_evaluation: e.target.value })} />
            </Field>
          )}
          <button className="w-fit rounded-full bg-indigo-600 px-5 py-2 text-sm text-white"
            onClick={requestSaveReview}>
            Review သိမ်းရန်
          </button>
        </div>
      </section>
      </>
      )}

      {/* Audit log — TENANT ADMIN ONLY. Dept admins and employees never see
          this section, and the API now rejects their requests too. */}
      {user.role === "tenant_admin" && (
      <section className="rounded-3xl bg-white p-5 shadow-sm">
        <h2 className="font-semibold text-slate-800">
          ပြင်ဆင်မှတ်တမ်း · Audit Log {viewingName ? `— ${viewingName}` : ""}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          ဘယ်သူက ဘယ်အချိန်မှာ ဘယ်နေရာမှာ ဘာကို ပြင်ခဲ့လဲ (အဟောင်း → အသစ်)၊ ဘာကို ဖျက်ခဲ့လဲ — အတိအကျ။
        </p>
        {audit.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">ဒီလမှာ မှတ်တမ်း မရှိသေးပါ။</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {audit.map((e) => (
              <li
                key={e.id}
                className={`flex flex-wrap items-baseline justify-between gap-2 py-2 ${e.entity_type === "daily_task" ? "cursor-pointer hover:bg-slate-50 rounded px-1" : ""}`}
                onClick={() => {
                  if (e.entity_type !== "daily_task") return;
                  document.getElementById(`diary-entry-${e.entity_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
                }}
                title={e.entity_type === "daily_task" ? "Diary entry ကို ကြည့်ရန် နှိပ်ပါ" : undefined}
              >
                <div className="text-sm">
                  <span className="font-medium text-slate-800">{e.actor_name}</span>
                  <span className="text-slate-400"> · {ACTION_LABEL[e.action] || e.action}</span>
                  <span className="text-slate-600"> — {e.summary}</span>
                </div>
                <span className="text-xs text-slate-400">{formatTime(e.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      )}
      {/* Confirmation dialog for submit, delete and review save */}
      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title}
        message={confirm?.message}
        details={confirm?.details}
        confirmLabel={confirm?.confirmLabel}
        danger={confirm?.danger}
        tone={confirm?.tone}
        submitting={confirmBusy}
        onConfirm={() => confirm?.onConfirm?.()}
        onCancel={() => { if (!confirmBusy) setConfirm(null); }}
      />
    </div>
    </AppShell>
  );
}
