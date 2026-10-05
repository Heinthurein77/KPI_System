const ICONS = {
  success: (
    <svg className="h-5 w-5 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
    </svg>
  ),
  error: (
    <svg className="h-5 w-5 text-red-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"
      />
    </svg>
  ),
};

const ICON_BG = {
  success: "bg-emerald-50",
  error: "bg-red-50",
};

export default function ToastViewport({ toasts, onDismiss }) {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2.5">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="animate-toast-in flex items-start gap-3 rounded-xl bg-white px-4 py-3.5 shadow-lg ring-1 ring-slate-900/5 border border-slate-100"
        >
          <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${ICON_BG[t.type]}`}>
            {ICONS[t.type]}
          </div>
          <p className="flex-1 pt-0.5 text-sm font-medium text-slate-700 leading-snug">{t.message}</p>
          <button
            type="button"
            onClick={() => onDismiss(t.id)}
            className="shrink-0 rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
