import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100";

// One restrained design system for the whole page: a single card style, a
// single primary button, and one segmented-tab style. No per-section
// gradients or competing accents — indigo-600 is the only accent color.
const cardCls = "rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100";
const sectionTitleCls = "text-base font-semibold text-slate-900";
const sectionSubCls = "mt-0.5 text-xs text-slate-500";
const btnPrimaryCls =
  "rounded-full bg-indigo-600 px-5 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50";
const btnGhostCls =
  "rounded-full border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-50";
const tabBarCls = "flex flex-wrap gap-1 rounded-full bg-slate-100 p-1 w-fit";
const tabBtnCls = (active) =>
  `rounded-full px-4 py-2 text-sm font-medium ${active ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`;
const smallBtnCls = "rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50";
const smallDangerBtnCls = "rounded-full border border-slate-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50";

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
  // F-M5: diary drafts survive a 401 hard redirect (session expiry) via
  // sessionStorage — the form (plus which entry is being edited) and the
  // review draft are restored on mount. The attached evidence File cannot
  // be persisted (browser security), so a file selection is still lost
  // on redirect; everything typed is kept.
  const formStorageKey = `kpi-daily-form-${user.id}`;
  const reviewStorageKey = (y, m, uid) => `kpi-daily-review-${user.id}-${y}-${m}-${uid ?? "none"}`;
  const loadStoredForm = () => {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(formStorageKey) || "null");
      if (!parsed || typeof parsed !== "object") return { form: EMPTY_FORM, editingId: null };
      const { form: storedForm, editingId: storedEditingId } = parsed;
      if (!storedForm || typeof storedForm !== "object") return { form: EMPTY_FORM, editingId: null };
      // A restored draft is always for today — a stale date would otherwise
      // submit yesterday's half-written entry under yesterday's date.
      return {
        form: { ...EMPTY_FORM, ...storedForm, task_date: EMPTY_FORM.task_date },
        editingId: storedEditingId || null,
      };
    } catch {
      return { form: EMPTY_FORM, editingId: null };
    }
  };
  const [initialForm] = useState(loadStoredForm);
  const [form, setForm] = useState(initialForm.form);
  const [editingId, setEditingId] = useState(initialForm.editingId);
  const [evidenceFile, setEvidenceFile] = useState(null);
  const [feedbackDraft, setFeedbackDraft] = useState({});
  const [feedbackSending, setFeedbackSending] = useState({});
  const reviewHydratedRef = useRef(false);
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

  // F-M1: a dept admin viewing a MEMBER's review may only write the HOD
  // evaluation — the backend silently drops the other three sections for
  // them (tenant admins, by contrast, may write all four). So the author
  // sections are disabled with a note, the save-confirm lists only HOD,
  // and the save payload carries only HOD. Nothing the UI shows will be
  // dropped anymore.
  const isDeptAdminViewingMember = user.role === "dept_admin"
    && tab === "team"
    && reviewUserId !== null
    && reviewUserId !== user.id;

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
      reviewHydratedRef.current = true;
      return;
    }
    getMonthlyReview(year, month, reviewUserId)
      .then((r) => {
        setReview(r);
        // F-M5: a stored unsaved draft for this exact review wins over
        // the server values — it is the user's newer, not-yet-saved work.
        let stored = null;
        try {
          stored = JSON.parse(sessionStorage.getItem(reviewStorageKey(year, month, reviewUserId)) || "null");
        } catch {
          stored = null;
        }
        setReviewDraft(stored && typeof stored === "object" ? {
          achievements: stored.achievements || "",
          challenges_solutions: stored.challenges_solutions || "",
          improvements_next_focus: stored.improvements_next_focus || "",
          hod_evaluation: stored.hod_evaluation || "",
        } : {
          achievements: r?.achievements || "",
          challenges_solutions: r?.challenges_solutions || "",
          improvements_next_focus: r?.improvements_next_focus || "",
          hod_evaluation: r?.hod_evaluation || "",
        });
        reviewHydratedRef.current = true;
      })
      .catch(() => {
        setReview(null);
        reviewHydratedRef.current = true;
      });
  }, [year, month, reviewUserId]);

  // F-M5 persistence: form + review draft are written on every change.
  // The review write is skipped until the server load hydrates the draft,
  // so the initial empty draft never clobbers a stored one on mount.
  useEffect(() => {
    try {
      sessionStorage.setItem(formStorageKey, JSON.stringify({ form, editingId }));
    } catch {
      // Private-mode quota errors must never break the diary itself.
    }
  }, [form, editingId]);
  useEffect(() => {
    if (!reviewHydratedRef.current) return;
    try {
      sessionStorage.setItem(reviewStorageKey(year, month, reviewUserId), JSON.stringify(reviewDraft));
    } catch {
      // See above.
    }
  }, [reviewDraft, year, month, reviewUserId]);

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
    const allFields = [
      ["အောင်မြင်မှုများ", reviewDraft.achievements],
      ["အခက်အခဲ & ဖြေရှင်းပုံ", reviewDraft.challenges_solutions],
      ["ပိုကောင်းအောင် ပြင်ဆင်မည့်အချက်", reviewDraft.improvements_next_focus],
      ["HOD အကဲဖြတ်ချက်", reviewDraft.hod_evaluation],
    ];
    // A dept admin viewing a member's review can only save HOD — never
    // list the author sections in the confirm dialog (they would be dropped).
    const fields = (isDeptAdminViewingMember ? allFields.slice(3) : allFields)
      .filter(([, v]) => v.trim());
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
      // Dept admins viewing a member send HOD only — the author sections
      // would be dropped server-side, so they are not sent at all.
      const payload = isDeptAdminViewingMember
        ? { hod_evaluation: reviewDraft.hod_evaluation }
        : reviewDraft;
      const saved = await saveMonthlyReview(year, month, reviewUserId, payload);
      setReview(saved);
      // F-M5: the saved review supersedes the stored draft for this key.
      try {
        sessionStorage.removeItem(reviewStorageKey(year, month, reviewUserId));
      } catch {
        // See above.
      }
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

  // F-M3: feedback send with error handling, a sending-disabled state,
  // and empty feedback skipped — previously a failed send died silently
  // and empty feedback could be sent.
  const sendFeedback = async (entry) => {
    const text = (feedbackDraft[entry.id] || "").trim();
    if (!text || feedbackSending[entry.id]) return;
    setFeedbackSending((prev) => ({ ...prev, [entry.id]: true }));
    try {
      await giveDailyFeedback(entry.id, text);
      setNotice({ text: "Feedback ပို့ပြီးပါပြီ — ဝန်ထမ်းဆီ notification ရောက်မယ်။", ok: true });
      setFeedbackDraft((prev) => ({ ...prev, [entry.id]: "" }));
      load();
    } catch (err) {
      setNotice({ text: getErrorMessage(err, "Feedback ပို့လို့မရပါ။ ထပ်စမ်းကြည့်ပါ။"), ok: false });
    } finally {
      setFeedbackSending((prev) => ({ ...prev, [entry.id]: false }));
    }
  };

  const startEdit = (entry) => {
    setEditingId(entry.id);
    // F-M4: a file picked for another entry must never leak into this one.
    setEvidenceFile(null);
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
    <div className="mx-auto max-w-5xl space-y-6">
      {/* Header: calm white card. Title on top; one control bar below with
          month navigation, export, and (for supervisors on the team tab)
          the member picker — instead of controls scattered across sections. */}
      <div className={cardCls}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-900">နေ့စဉ် အလုပ် မှတ်တမ်း</h1>
            <p className="mt-0.5 text-sm text-slate-500">Daily Working Diary · {user.name}</p>
          </div>
          <button
            className={btnGhostCls}
            onClick={() => exportDailyDiary(year, month, viewUserId || undefined)}
          >
            Excel ထုတ်ရန်
          </button>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
          <div className="flex items-center gap-1 rounded-full bg-slate-100 p-1">
            <button className="rounded-full px-3 py-1.5 text-sm text-slate-600 hover:bg-white" onClick={() => shiftMonth(-1)}>‹ ပြီးခဲ့သောလ</button>
            <span className="rounded-full bg-white px-3 py-1.5 text-sm font-semibold text-slate-900 shadow-sm">{year}-{String(month).padStart(2, "0")}</span>
            <button className="rounded-full px-3 py-1.5 text-sm text-slate-600 hover:bg-white" onClick={() => shiftMonth(1)}>နောက်လ ›</button>
          </div>
          {isSupervisor && summary && tab === "team" && (
            <>
              <span className="text-sm text-slate-500">ဝန်ထမ်း၏ diary ကြည့်ရန်:</span>
              <select
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
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
                  className="rounded-full border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
                  onClick={() => setViewUserId("")}
                >
                  ✕ {viewingName} ကို ပိတ်ရန်
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Dept admin's two separate dashboards */}
      {user.role === "dept_admin" && (
        <div className={tabBarCls}>
          <button
            className={tabBtnCls(tab === "mine")}
            onClick={() => setTab("mine")}
          >
            📝 ကိုယ့် Diary · ဖြည့်ရန်
          </button>
          <button
            className={tabBtnCls(tab === "team")}
            onClick={() => setTab("team")}
          >
            👥 ဌာန Diary · Feedback ပေးရန်
          </button>
        </div>
      )}

      {/* Mine view split: form and filled entries live on separate tabs */}
      {tab === "mine" && user.role !== "tenant_admin" && (
        <div className={tabBarCls}>
          <button
            className={tabBtnCls(mineView === "fill")}
            onClick={() => setMineView("fill")}
          >
            ✏️ ဖြည့်ရန် · Fill Form
          </button>
          <button
            className={tabBtnCls(mineView === "history")}
            onClick={() => setMineView("history")}
          >
            📋 ဖြည့်ပြီးသား · Filled Entries
          </button>
        </div>
      )}

      {notice && (
        <p className={`rounded-xl px-4 py-2.5 text-sm ${notice.ok ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-700"}`}>
          {notice.text}
        </p>
      )}

      {/* Monthly summary + chart */}
      {summary && (
        <section className={cardCls}>
          <h2 className={sectionTitleCls}>လစဉ် အကျဉ်းချုပ် · Monthly Summary</h2>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {displayUsers.map((u) => (
              <div key={u.user_id} className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100">
                <p className="truncate text-sm font-medium text-slate-800">{u.author_name}</p>
                <dl className="mt-2 space-y-1 text-sm text-slate-500">
                  <div className="flex justify-between gap-2"><dt>အလုပ်လုပ်ရက်</dt><dd className="font-semibold text-slate-800">{u.working_days}</dd></div>
                  <div className="flex justify-between gap-2"><dt>ပြီးစီး</dt><dd className="font-semibold text-emerald-600">{u.completed}</dd></div>
                  <div className="flex justify-between gap-2"><dt>မပြီးသေး</dt><dd className="font-semibold text-amber-600">{u.in_progress_or_pending}</dd></div>
                </dl>
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
      <section className={cardCls}>
        <h2 className={sectionTitleCls}>
          {editingId ? "Entry ပြင်ဆင်ရန် · Edit Entry" : "ဒီနေ့ အလုပ် ထည့်ရန် · New Diary Entry"}
        </h2>
        <form onSubmit={requestSubmit} className="mt-5 space-y-5">
          <div className="grid gap-4 sm:grid-cols-3">
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
          <div className="grid gap-4 sm:grid-cols-2">
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
            {/* Keyed by the entry being edited so switching entries (or
                cancelling) remounts the input and clears the picked file —
                otherwise the DOM value survives setEvidenceFile(null). */}
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-3 py-2.5">
              <input key={editingId ?? "new"} type="file" className="w-full text-sm text-slate-600" onChange={(e) => setEvidenceFile(e.target.files[0] || null)} />
              {evidenceFile && <p className="mt-1 text-xs text-slate-500">📎 {evidenceFile.name}</p>}
            </div>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
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
            <div className="mt-1 grid gap-4 sm:grid-cols-3">
              {[1, 2, 3].map((n) => (
                <input key={n} className={inputCls} placeholder={`အလုပ် ${n}`}
                  value={form[`tomorrow_plan_${n}`]}
                  onChange={(e) => setForm({ ...form, [`tomorrow_plan_${n}`]: e.target.value })} />
              ))}
            </div>
          </div>
          <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-4">
            <button type="submit" disabled={saving} className={btnPrimaryCls}>
              {saving ? "သိမ်းနေတယ်…" : editingId ? "ပြင်ဆင်ချက်ကို သိမ်းရန်" : "Entry ထည့်ရန်"}
            </button>
            {editingId && (
              <button type="button" className={btnGhostCls}
                onClick={() => { setEditingId(null); setForm(EMPTY_FORM); setEvidenceFile(null); }}>
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
      <section className="space-y-4">
        <h2 className={`${sectionTitleCls} px-1`}>
          {tab === "team" ? "ဌာန Diary Entries" : "ကိုယ့် Diary Entries"} {viewingName ? `— ${viewingName}` : ""}
        </h2>
        {displayEntries.map((entry) => (
          <article key={entry.id} id={`diary-entry-${entry.id}`} className={cardCls}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-slate-900">{entry.task_date}</p>
                <p className="mt-0.5 text-xs text-slate-500">
                  {entry.author_name}
                  {entry.department_name ? ` · ${entry.department_name}` : ""}
                  {entry.position ? ` · ${entry.position}` : ""}
                </p>
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_CHIP[entry.status] || STATUS_CHIP.not_started}`}>
                {entry.status.replace("_", " ")}
              </span>
            </div>
            <p className="mt-3 whitespace-pre-wrap text-[15px] leading-relaxed text-slate-800">{entry.work_done}</p>
            {(entry.plan_linkage || entry.figure_output || entry.evidence_link || entry.difficulties || entry.support_needed || entry.self_reflection || entry.tomorrow_plan_1 || entry.tomorrow_plan_2 || entry.tomorrow_plan_3) && (
            <dl className="mt-3 grid gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-sm sm:grid-cols-2">
              {entry.plan_linkage && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">Plan</dt><dd className="text-slate-600">{entry.plan_linkage}</dd></div>)}
              {entry.figure_output && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">ရလဒ်</dt><dd className="text-slate-600">{entry.figure_output}</dd></div>)}
              {entry.evidence_link && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">သက်သေ</dt><dd className="break-all text-slate-600">{entry.evidence_link}</dd></div>)}
              {entry.difficulties && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">အခက်အခဲ</dt><dd className="text-slate-600">{entry.difficulties}</dd></div>)}
              {entry.support_needed && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">ပံ့ပိုးမှု</dt><dd className="text-slate-600">{entry.support_needed}</dd></div>)}
              {entry.self_reflection && (<div className="flex gap-2"><dt className="shrink-0 text-slate-400">သုံးသပ်ချက်</dt><dd className="text-slate-600">{entry.self_reflection}</dd></div>)}
              {(entry.tomorrow_plan_1 || entry.tomorrow_plan_2 || entry.tomorrow_plan_3) && (
                <div className="flex gap-2 sm:col-span-2"><dt className="shrink-0 text-slate-400">မနက်ဖြန်</dt><dd className="text-slate-600">{[entry.tomorrow_plan_1, entry.tomorrow_plan_2, entry.tomorrow_plan_3].filter(Boolean).join(" · ")}</dd></div>
              )}
            </dl>
            )}
            {entry.has_evidence_file && (
              <div className="mt-3 space-y-2">
                {["jpg", "jpeg", "png", "webp", "gif"].includes(
                  (entry.evidence_file || "").split(".").pop().toLowerCase(),
                ) && (
                  <a href={dailyEvidenceUrl(entry.id)} target="_blank" rel="noreferrer">
                    <img src={dailyEvidenceUrl(entry.id)} alt="Evidence" className="max-h-64 rounded-xl border border-slate-100 object-contain" />
                  </a>
                )}
                {(entry.evidence_file || "").toLowerCase().endsWith(".pdf") && (
                  <iframe src={dailyEvidenceUrl(entry.id)} title="Evidence preview" className="h-80 w-full rounded-xl border border-slate-100" />
                )}
                <div>
                  <a className="text-sm text-indigo-600 underline" href={dailyEvidenceUrl(entry.id)} target="_blank" rel="noreferrer">
                    📎 ဖိုင်ကို အပြည့်ကြည့်ရန် / ဒေါင်းလုဒ်လုပ်ရန်
                  </a>
                </div>
              </div>
            )}
            {entry.supervisor_feedback && (
              <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-900">
                💬 Supervisor: {entry.supervisor_feedback}
              </p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              {entry.user_id === user.id && (
                <>
                  <button className={smallBtnCls} onClick={() => startEdit(entry)}>ပြင်ဆင်ရန်</button>
                  <button className={smallDangerBtnCls}
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
                <button className="shrink-0 rounded-full bg-indigo-600 px-4 py-2 text-xs text-white disabled:opacity-50"
                  disabled={feedbackSending[entry.id] || !(feedbackDraft[entry.id] || "").trim()}
                  onClick={() => sendFeedback(entry)}>
                  {feedbackSending[entry.id] ? "ပို့နေတယ်…" : "ပို့ရန်"}
                </button>
              </div>
            )}
          </article>
        ))}
        {displayEntries.length === 0 && (
          <p className={`${cardCls} text-center text-sm text-slate-500`}>
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
      <section className={cardCls}>
        <h2 className={sectionTitleCls}>
          သိမ်းပြီးသော သုံးသပ်ချက် · Saved Review — {year}-{String(month).padStart(2, "0")}{viewingName ? ` · ${viewingName}` : ""}
        </h2>
        <div className="mt-4 grid gap-4">
          {[
            ["ဒီလ၏ အဓိက အောင်မြင်ပြီးစီးမှုများ", "Major Achievements", review.achievements],
            ["ကြုံတွေ့ခဲ့သော အခက်အခဲများနှင့် ဖြေရှင်းပုံ", "Key Challenges & Solutions", review.challenges_solutions],
            ["နောက်လတွင် ပိုမိုကောင်းမွန်အောင် ပြင်ဆင်မည့်အချက်များ", "Improvement / Next Month Focus", review.improvements_next_focus],
            ["HOD ၏ အကဲဖြတ်ချက်နှင့် လမ်းညွှန်ချက်", "HOD Evaluation", review.hod_evaluation],
          ].map(([label, sub, value]) => (
            <div key={sub}>
              <p className="text-xs font-medium text-slate-500">{label} · {sub}</p>
              <p className="mt-1 whitespace-pre-wrap rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-800 ring-1 ring-slate-100">{value?.trim() ? value : "—"}</p>
            </div>
          ))}
        </div>
      </section>
      )}
      <section className={cardCls}>
        <h2 className={sectionTitleCls}>သုံးသပ်ချက် ပြင်ဆင်ရန် · Edit Review</h2>
        {isDeptAdminViewingMember && (
          <p className="mt-2 rounded-xl bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
            ဒီ review က အဖွဲ့ဝင်ရဲ့ ကိုယ်ပိုင်သုံးသပ်ချက်ပါ — dept admin အနေနဲ့ HOD အကဲဖြတ်ချက်ကိုသာ ဖြည့်နိုင်ပါတယ်။ အောက်ပါ ၃ ချက်ကို ပြင်လို့မရပါ။
          </p>
        )}
        <div className="mt-4 grid gap-4">
          <Field label="ဒီလ၏ အဓိက အောင်မြင်ပြီးစီးမှုများ" sub="Major Achievements">
            <textarea rows={2} className={inputCls} value={reviewDraft.achievements} disabled={isDeptAdminViewingMember}
              onChange={(e) => setReviewDraft({ ...reviewDraft, achievements: e.target.value })} />
          </Field>
          <Field label="ကြုံတွေ့ခဲ့သော အခက်အခဲများနှင့် ဖြေရှင်းပုံ" sub="Key Challenges & Solutions">
            <textarea rows={2} className={inputCls} value={reviewDraft.challenges_solutions} disabled={isDeptAdminViewingMember}
              onChange={(e) => setReviewDraft({ ...reviewDraft, challenges_solutions: e.target.value })} />
          </Field>
          <Field label="နောက်လတွင် ပိုမိုကောင်းမွန်အောင် ပြင်ဆင်မည့်အချက်များ" sub="Improvement / Next Month Focus">
            <textarea rows={2} className={inputCls} value={reviewDraft.improvements_next_focus} disabled={isDeptAdminViewingMember}
              onChange={(e) => setReviewDraft({ ...reviewDraft, improvements_next_focus: e.target.value })} />
          </Field>
          {isSupervisor && reviewUserId !== user.id && (
            <Field label="HOD ၏ အကဲဖြတ်ချက်နှင့် လမ်းညွှန်ချက်" sub="HOD Evaluation">
              <textarea rows={2} className={inputCls} value={reviewDraft.hod_evaluation}
                onChange={(e) => setReviewDraft({ ...reviewDraft, hod_evaluation: e.target.value })} />
            </Field>
          )}
          <button className={btnPrimaryCls}
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
      <section className={cardCls}>
        <h2 className={sectionTitleCls}>
          ပြင်ဆင်မှတ်တမ်း · Audit Log {viewingName ? `— ${viewingName}` : ""}
        </h2>
        <p className={`${sectionSubCls}`}>
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
