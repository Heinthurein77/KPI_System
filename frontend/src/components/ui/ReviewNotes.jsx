import { useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { addNote, listNotes } from "../../api/reviewNotes";
import { getErrorMessage } from "../../api/errors";

const ROLE_LABELS = {
  super_admin: "Super Admin",
  tenant_admin: "Tenant Admin",
  dept_admin: "Dept Admin",
  employee: "Employee",
};

function timeAgo(iso) {
  const seconds = Math.max(1, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function ReviewNotes({ submissionId, ownerId }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const canPost =
    !!user &&
    (user.id === ownerId ||
      ["dept_admin", "tenant_admin", "super_admin"].includes(user.role));

  async function load() {
    try {
      setNotes(await listNotes(submissionId));
    } catch (err) {
      setError(getErrorMessage(err, "Could not load notes."));
    }
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && notes === null) load();
  }

  async function handleSend() {
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true);
    setError("");
    try {
      const created = await addNote(submissionId, body);
      setNotes((prev) => [...(prev ?? []), created]);
      setDraft("");
    } catch (err) {
      setError(getErrorMessage(err, "Could not send note."));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
          Review notes{notes !== null && ` (${notes.length})`}
        </span>
        <svg
          className={`h-4 w-4 text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          {notes === null ? (
            <p className="text-xs text-slate-400">Loading…</p>
          ) : notes.length === 0 ? (
            <p className="text-xs text-slate-400">No notes yet. Start the discussion.</p>
          ) : (
            notes.map((n) => (
              <div key={n.id} className="rounded-lg bg-white border border-slate-100 px-2.5 py-2">
                <p className="text-xs text-slate-800 leading-relaxed whitespace-pre-wrap">{n.body}</p>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <span className="text-[11px] font-medium text-slate-600">{n.author_name}</span>
                  <span className="text-[10px] text-slate-400">
                    · {ROLE_LABELS[n.author_role] || n.author_role}
                  </span>
                  <span className="text-[10px] text-slate-400 ml-auto tabular-nums">
                    {timeAgo(n.created_at)}
                  </span>
                </div>
              </div>
            ))
          )}

          {canPost && (
            <div className="flex gap-2 pt-1">
              <input
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSend();
                }}
                placeholder="Write a note…"
                maxLength={2000}
                className="flex-1 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={sending || !draft.trim()}
                className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-50"
              >
                {sending ? "Sending…" : "Send"}
              </button>
            </div>
          )}

          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
      )}
    </div>
  );
}
