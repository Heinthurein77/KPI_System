import { useRef, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import { downloadImportTemplate, uploadImport } from "../../api/import";
import { getErrorMessage } from "../../api/errors";

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function BulkImportPage() {
  const fileRef = useRef(null);
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null); // dry-run result
  const [result, setResult] = useState(null); // final import result

  async function handleTemplate() {
    setError("");
    try {
      const blob = await downloadImportTemplate();
      downloadBlob(blob, "bulk-import-template.xlsx");
    } catch (e) {
      setError(getErrorMessage(e));
    }
  }

  function handleFileChange(e) {
    const f = e.target.files?.[0];
    setFileName(f ? f.name : "");
    setPreview(null);
    setResult(null);
    setError("");
  }

  async function handleValidate() {
    const f = fileRef.current?.files?.[0];
    if (!f) {
      setError("Please choose an Excel (.xlsx) file first.");
      return;
    }
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const data = await uploadImport(f, true);
      setPreview(data);
    } catch (e) {
      setError(getErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleImport() {
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    if (!window.confirm(`Import ${preview.users_found} users and ${preview.kpis_found} KPIs?`)) return;
    setBusy(true);
    setError("");
    try {
      const data = await uploadImport(f, false);
      setResult(data);
      setPreview(null);
      if (fileRef.current) fileRef.current.value = "";
      setFileName("");
    } catch (e) {
      // 422 carries row-level errors in detail.errors
      const detail = e?.response?.data?.detail;
      if (detail?.errors) {
        setPreview({ dry_run: false, ok: false, errors: detail.errors });
      } else {
        setError(getErrorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  const errors = preview && !preview.ok ? preview.errors : [];

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl space-y-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Bulk Import</h1>
            <p className="mt-1 text-sm text-slate-500">
              Add many employees and their KPIs at once from an Excel file. Everyone is added to
              your department as an Employee.
            </p>
          </div>
          <button
            onClick={handleTemplate}
            className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Download Template
          </button>
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-base font-semibold text-slate-900">1. Choose file</h3>
          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx"
              onChange={handleFileChange}
              className="text-sm text-slate-600 file:mr-3 file:rounded-lg file:border file:border-slate-300 file:bg-white file:px-4 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-50"
            />
            {fileName && <span className="text-sm text-slate-500">{fileName}</span>}
          </div>

          <h3 className="mb-3 mt-6 text-base font-semibold text-slate-900">2. Validate</h3>
          <button
            onClick={handleValidate}
            disabled={busy || !fileName}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "Working…" : "Validate File"}
          </button>
          <p className="mt-2 text-xs text-slate-500">
            Validation checks every row first — nothing is imported until you confirm.
          </p>
        </div>

        {preview && preview.ok && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
            <h3 className="text-base font-semibold text-emerald-900">Ready to import</h3>
            <p className="mt-1 text-sm text-emerald-800">
              {preview.users_found} users and {preview.kpis_found} KPIs found, no errors.
            </p>
            <p className="mt-2 text-sm text-emerald-800">
              Every new user starts with the initial password <code className="rounded bg-emerald-100 px-1 font-mono">password</code> —
              ask them to change it on first login.
            </p>
            <button
              onClick={handleImport}
              disabled={busy}
              className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {busy ? "Importing…" : "Confirm Import"}
            </button>
          </div>
        )}

        {errors.length > 0 && (
          <div className="rounded-2xl border border-red-200 bg-white p-5 shadow-sm">
            <h3 className="mb-3 text-base font-semibold text-red-900">
              {errors.length} problem{errors.length === 1 ? "" : "s"} found — nothing was imported
            </h3>
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-200 text-xs uppercase tracking-wider text-slate-500">
                    <th className="py-2 pr-4">Sheet</th>
                    <th className="py-2 pr-4">Row</th>
                    <th className="py-2">Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.map((e, i) => (
                    <tr key={i} className="border-b border-slate-100 last:border-0">
                      <td className="py-2 pr-4 text-slate-600">{e.sheet}</td>
                      <td className="py-2 pr-4 text-slate-600">{e.row || "—"}</td>
                      <td className="py-2 text-slate-800">{e.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-sm text-slate-500">
              Fix the rows in Excel and validate again.
            </p>
          </div>
        )}

        {result && result.ok && (
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
            <h3 className="text-base font-semibold text-emerald-900">Import complete</h3>
            <p className="mt-1 text-sm text-emerald-800">
              {result.users_created} users and {result.kpis_created} KPIs created for {result.period} {result.year}.
              KPIs are recurring, so they will carry forward to next months automatically.
            </p>
            <p className="mt-2 text-sm text-emerald-800">
              Initial password for everyone: <code className="rounded bg-emerald-100 px-1 font-mono">password</code> —
              remind them to change it on first login.
            </p>
          </div>
        )}
      </div>
    </AppShell>
  );
}
