import { useEffect } from "react";

// tone: "danger" | "success" | "brand" | "info"
// Kept backward compatible: the old `danger` boolean still works.
const TONES = {
  danger: {
    gradient: "from-red-500 to-rose-600",
    icon: "M6 18 18 6M6 6l12 12",
    button: "from-red-600 to-rose-500 hover:from-red-500 hover:to-rose-400 shadow-red-500/30",
  },
  success: {
    gradient: "from-emerald-500 to-green-600",
    icon: "m5 13 4 4L19 7",
    button: "from-emerald-600 to-green-500 hover:from-emerald-500 hover:to-green-400 shadow-emerald-500/30",
  },
  brand: {
    gradient: "from-brand-500 to-brand-700",
    icon: "m5 13 4 4L19 7",
    button: "from-brand-600 to-brand-500 hover:from-brand-500 hover:to-brand-400 shadow-brand-500/30",
  },
  info: {
    gradient: "from-indigo-500 to-violet-600",
    icon: "M13 10V3L4 14h7v7l9-11h-7z",
    button: "from-indigo-600 to-violet-500 hover:from-indigo-500 hover:to-violet-400 shadow-indigo-500/30",
  },
};

export default function ConfirmDialog({
  open,
  title,
  message,
  details,
  confirmLabel = "Delete",
  danger = true,
  tone,
  submitting = false,
  onConfirm,
  onCancel,
}) {
  useEffect(() => {
    if (!open) return;
    function handleKey(e) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onCancel]);

  if (!open) return null;

  const t = TONES[tone ?? (danger ? "danger" : "success")] ?? TONES.danger;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm px-4 animate-fade-in"
      onClick={onCancel}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        className="w-full max-w-sm overflow-hidden rounded-xl bg-white shadow-2xl animate-dialog-in"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`h-1 bg-gradient-to-r ${t.gradient}`} />
        <div className="p-5">
          <div className="flex items-center gap-3">
            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${t.gradient} shadow-md`}>
              <svg
                className="h-5 w-5 text-white"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth="2.4"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d={t.icon} />
              </svg>
            </div>
            <h3 className="text-[15px] font-bold text-slate-900 tracking-tight leading-snug">{title}</h3>
          </div>
          {message && <p className="mt-2.5 text-[13px] text-slate-500 whitespace-pre-wrap leading-relaxed">{message}</p>}
          {details && <div className="mt-3">{details}</div>}
          <div className="mt-5 flex items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] font-semibold text-slate-600 hover:bg-slate-50 transition"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={submitting}
              className={`flex-1 inline-flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r ${t.button} px-3 py-2 text-[13px] font-bold text-white shadow-md transition hover:shadow-lg active:scale-[0.98] disabled:opacity-60`}
            >
              {submitting ? (
                <>
                  <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
                  </svg>
                  Wait…
                </>
              ) : (
                <>
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.8">
                    <path strokeLinecap="round" strokeLinejoin="round" d={t.icon} />
                  </svg>
                  {confirmLabel}
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
