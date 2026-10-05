export default function PageLoading() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-slate-400">
      <svg className="h-7 w-7 animate-spin text-brand-500" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
        <path
          className="opacity-75"
          fill="currentColor"
          d="M4 12a8 8 0 0 1 8-8V0C5.373 0 0 5.373 0 12h4Z"
        ></path>
      </svg>
      <p className="mt-3 text-sm font-medium">Loading…</p>
    </div>
  );
}
