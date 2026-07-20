import { Link } from "react-router-dom";

export default function ErrorCard({ statusCode, heading, message }) {
  return (
    <div className="rounded-2xl bg-white shadow-sm border border-slate-200 px-8 py-10 max-w-md w-full text-center">
      <p className="text-sm font-semibold text-brand-600">Error {statusCode}</p>
      <h1 className="mt-2 text-xl font-semibold text-slate-900">{heading}</h1>
      <p className="mt-2 text-sm text-slate-500">{message}</p>
      <Link
        to="/dashboard"
        className="mt-6 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 transition"
      >
        Back to Dashboard
      </Link>
    </div>
  );
}
