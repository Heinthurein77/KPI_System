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
import {
  createDailyTask,
  dailyEvidenceUrl,
  deleteDailyTask,
  exportDailyDiary,
  getMonthlyReview,
  giveDailyFeedback,
  listDailyTasks,
  monthlyDailySummary,
  saveMonthlyReview,
  updateDailyTask,
  uploadDailyEvidence,
} from "../api/dailyTasks";

const STATUS_OPTIONS = [
  { value: "not_started", label: "Not Started" },
  { value: "in_progress", label: "In Progress" },
  { value: "completed", label: "Completed" },
  { value: "pending", label: "Pending" },
];

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

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none";

export default function DailyTasksPage() {
  const { user } = useAuth();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [viewUserId, setViewUserId] = useState("");
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
  const [notice, setNotice] = useState("");

  const isSupervisor = user.role === "dept_admin" || user.role === "tenant_admin";
  const reviewUserId = useMemo(() => {
    if (viewUserId) return Number(viewUserId);
    return user.id;
  }, [viewUserId, user.id]);

  const load = useCallback(async () => {
    const [list, sum] = await Promise.all([
      listDailyTasks(year, month, viewUserId || undefined),
      monthlyDailySummary(year, month),
    ]);
    setEntries(list);
    setSummary(sum);
  }, [year, month, viewUserId]);

  useEffect(() => {
    load().catch(() => setNotice("Failed to load diary entries."));
  }, [load]);

  useEffect(() => {
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

  const submitForm = async (e) => {
    e.preventDefault();
    if (!form.work_done.trim()) return;
    try {
      let saved;
      if (editingId) {
        saved = await updateDailyTask(editingId, form);
      } else {
        saved = await createDailyTask(form);
      }
      if (evidenceFile) {
        saved = await uploadDailyEvidence(saved.id, evidenceFile);
        setEvidenceFile(null);
      }
      setEditingId(null);
      setForm(EMPTY_FORM);
      setNotice("Diary entry saved.");
      load();
    } catch {
      setNotice("Could not save the entry.");
    }
  };

  const startEdit = (entry) => {
    setEditingId(entry.id);
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
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const chartData = (summary?.users || []).map((u) => ({
    name: u.author_name,
    Completed: u.completed,
    "In Progress / Pending": u.in_progress_or_pending,
  }));

  const ownSummary = summary?.users.find((u) => u.user_id === user.id);

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold text-slate-800">Daily Task Diary</h1>
        <div className="flex items-center gap-2">
          <button className="rounded-lg border px-3 py-1 text-sm" onClick={() => shiftMonth(-1)}>‹ Prev</button>
          <span className="text-sm font-medium">{year}-{String(month).padStart(2, "0")}</span>
          <button className="rounded-lg border px-3 py-1 text-sm" onClick={() => shiftMonth(1)}>Next ›</button>
          <button
            className="rounded-lg bg-indigo-600 px-3 py-1 text-sm text-white"
            onClick={() => exportDailyDiary(year, month, viewUserId || undefined)}
          >
            Export Excel
          </button>
        </div>
      </div>

      {notice && <p className="rounded-lg bg-indigo-50 px-3 py-2 text-sm text-indigo-800">{notice}</p>}

      {isSupervisor && summary && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-600">View diary of:</span>
          <select
            className="rounded-lg border px-2 py-1 text-sm"
            value={viewUserId}
            onChange={(e) => setViewUserId(e.target.value)}
          >
            <option value="">Everyone in scope</option>
            {summary.users.map((u) => (
              <option key={u.user_id} value={u.user_id}>{u.author_name}</option>
            ))}
          </select>
        </div>
      )}

      {/* Monthly summary + chart */}
      {summary && (
        <section className="rounded-2xl bg-white p-4 shadow-sm">
          <h2 className="font-semibold text-slate-800">Monthly Summary</h2>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {(ownSummary ? [ownSummary] : summary.users).map((u) => (
              <div key={u.user_id} className="rounded-xl bg-slate-50 p-3">
                <p className="truncate text-xs text-slate-500">{u.author_name}</p>
                <p className="mt-1 text-sm">Working days: <b>{u.working_days}</b></p>
                <p className="text-sm">Completed: <b>{u.completed}</b></p>
                <p className="text-sm">In progress / pending: <b>{u.in_progress_or_pending}</b></p>
              </div>
            ))}
          </div>
          {chartData.length > 1 && (
            <div className="mt-4 h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData}>
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

      {/* Entry form — diary authors only (employee + dept admin).
          Tenant admins never fill a diary; they view and give feedback. */}
      {user.role !== "tenant_admin" && (
      <section className="rounded-2xl bg-white p-4 shadow-sm">
        <h2 className="font-semibold text-slate-800">{editingId ? "Edit entry" : "New diary entry"}</h2>
        <form onSubmit={submitForm} className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Date">
            <input type="date" className={inputCls} value={form.task_date}
              onChange={(e) => setForm({ ...form, task_date: e.target.value })} required />
          </Field>
          <Field label="Plan Linkage">
            <input className={inputCls} value={form.plan_linkage} placeholder="e.g. November Plan — Monthly Report"
              onChange={(e) => setForm({ ...form, plan_linkage: e.target.value })} />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Work Done *">
              <textarea rows={3} className={inputCls} value={form.work_done}
                onChange={(e) => setForm({ ...form, work_done: e.target.value })} required />
            </Field>
          </div>
          <Field label="Status">
            <select className={inputCls} value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
          <Field label="Figure / Output">
            <input className={inputCls} value={form.figure_output} placeholder="e.g. Report 1"
              onChange={(e) => setForm({ ...form, figure_output: e.target.value })} />
          </Field>
          <Field label="Evidence (link or report name)">
            <input className={inputCls} value={form.evidence_link}
              onChange={(e) => setForm({ ...form, evidence_link: e.target.value })} />
          </Field>
          <Field label="Evidence file (max 10 MB)">
            <input type="file" className="text-sm" onChange={(e) => setEvidenceFile(e.target.files[0] || null)} />
          </Field>
          <Field label="Difficulties & Pending">
            <textarea rows={2} className={inputCls} value={form.difficulties}
              onChange={(e) => setForm({ ...form, difficulties: e.target.value })} />
          </Field>
          <Field label="Support Needed">
            <textarea rows={2} className={inputCls} value={form.support_needed}
              onChange={(e) => setForm({ ...form, support_needed: e.target.value })} />
          </Field>
          <Field label="Self-Reflection">
            <textarea rows={2} className={inputCls} value={form.self_reflection}
              onChange={(e) => setForm({ ...form, self_reflection: e.target.value })} />
          </Field>
          <Field label="Tomorrow Plan 1">
            <input className={inputCls} value={form.tomorrow_plan_1}
              onChange={(e) => setForm({ ...form, tomorrow_plan_1: e.target.value })} />
          </Field>
          <Field label="Tomorrow Plan 2">
            <input className={inputCls} value={form.tomorrow_plan_2}
              onChange={(e) => setForm({ ...form, tomorrow_plan_2: e.target.value })} />
          </Field>
          <Field label="Tomorrow Plan 3">
            <input className={inputCls} value={form.tomorrow_plan_3}
              onChange={(e) => setForm({ ...form, tomorrow_plan_3: e.target.value })} />
          </Field>
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white">
              {editingId ? "Save changes" : "Add entry"}
            </button>
            {editingId && (
              <button type="button" className="rounded-lg border px-4 py-2 text-sm"
                onClick={() => { setEditingId(null); setForm(EMPTY_FORM); }}>
                Cancel
              </button>
            )}
          </div>
        </form>
      </section>
      )}

      {/* Entries */}
      <section className="space-y-3">
        {entries.map((entry) => (
          <article key={entry.id} className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold">{entry.task_date} · {entry.author_name}
                {entry.department_name ? ` · ${entry.department_name}` : ""}
              </p>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs">{entry.status.replace("_", " ")}</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">{entry.work_done}</p>
            {entry.plan_linkage && <p className="mt-1 text-xs text-slate-500">Plan: {entry.plan_linkage}</p>}
            {entry.figure_output && <p className="text-xs text-slate-500">Output: {entry.figure_output}</p>}
            {entry.evidence_link && <p className="text-xs text-slate-500">Evidence: {entry.evidence_link}</p>}
            {entry.has_evidence_file && (
              <a className="text-xs text-indigo-600 underline" href={dailyEvidenceUrl(entry.id)} target="_blank" rel="noreferrer">
                Download attached evidence
              </a>
            )}
            {entry.difficulties && <p className="mt-1 text-xs text-slate-500">Difficulties: {entry.difficulties}</p>}
            {entry.support_needed && <p className="text-xs text-slate-500">Support needed: {entry.support_needed}</p>}
            {entry.self_reflection && <p className="text-xs text-slate-500">Reflection: {entry.self_reflection}</p>}
            {(entry.tomorrow_plan_1 || entry.tomorrow_plan_2 || entry.tomorrow_plan_3) && (
              <p className="text-xs text-slate-500">
                Tomorrow: {[entry.tomorrow_plan_1, entry.tomorrow_plan_2, entry.tomorrow_plan_3].filter(Boolean).join(" · ")}
              </p>
            )}
            {entry.supervisor_feedback && (
              <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Supervisor: {entry.supervisor_feedback}
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              {entry.user_id === user.id && (
                <>
                  <button className="rounded-lg border px-3 py-1 text-xs" onClick={() => startEdit(entry)}>Edit</button>
                  <button className="rounded-lg border px-3 py-1 text-xs text-red-600"
                    onClick={async () => { await deleteDailyTask(entry.id); load(); }}>
                    Delete
                  </button>
                </>
              )}
            </div>
            {isSupervisor && entry.user_id !== user.id && (
              <div className="mt-3 flex gap-2">
                <input className={inputCls} placeholder="Supervisor feedback…"
                  value={feedbackDraft[entry.id] || ""}
                  onChange={(e) => setFeedbackDraft({ ...feedbackDraft, [entry.id]: e.target.value })} />
                <button className="shrink-0 rounded-lg bg-indigo-600 px-3 py-1 text-xs text-white"
                  onClick={async () => {
                    await giveDailyFeedback(entry.id, feedbackDraft[entry.id] || "");
                    setNotice("Feedback sent — the employee is notified.");
                    load();
                  }}>
                  Send
                </button>
              </div>
            )}
          </article>
        ))}
        {entries.length === 0 && (
          <p className="rounded-2xl bg-white p-4 text-center text-sm text-slate-500">No diary entries this month yet.</p>
        )}
      </section>

      {/* Month-end review — only for diary authors. A tenant admin with no
          user selected has no diary of their own, so nothing is shown. */}
      {(user.role !== "tenant_admin" || viewUserId !== "") && (
      <section className="rounded-2xl bg-white p-4 shadow-sm">
        <h2 className="font-semibold text-slate-800">Month-End Review</h2>
        <div className="mt-3 grid gap-3">
          <Field label="Major Achievements">
            <textarea rows={2} className={inputCls} value={reviewDraft.achievements}
              onChange={(e) => setReviewDraft({ ...reviewDraft, achievements: e.target.value })} />
          </Field>
          <Field label="Key Challenges & Solutions">
            <textarea rows={2} className={inputCls} value={reviewDraft.challenges_solutions}
              onChange={(e) => setReviewDraft({ ...reviewDraft, challenges_solutions: e.target.value })} />
          </Field>
          <Field label="Areas for Improvement / Next Month Focus">
            <textarea rows={2} className={inputCls} value={reviewDraft.improvements_next_focus}
              onChange={(e) => setReviewDraft({ ...reviewDraft, improvements_next_focus: e.target.value })} />
          </Field>
          {isSupervisor && reviewUserId !== user.id && (
            <Field label="HOD Evaluation & Recommendations">
              <textarea rows={2} className={inputCls} value={reviewDraft.hod_evaluation}
                onChange={(e) => setReviewDraft({ ...reviewDraft, hod_evaluation: e.target.value })} />
            </Field>
          )}
          {review?.hod_evaluation && (!isSupervisor || reviewUserId === user.id) && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">HOD Evaluation: {review.hod_evaluation}</p>
          )}
          <button className="w-fit rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white"
            onClick={async () => {
              await saveMonthlyReview(year, month, reviewUserId, reviewDraft);
              setNotice("Month-end review saved.");
            }}>
            Save review
          </button>
        </div>
      </section>
      )}
    </div>
  );
}
