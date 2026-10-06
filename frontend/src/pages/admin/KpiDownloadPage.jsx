import { useEffect, useMemo, useState } from "react";
import AppShell from "../../components/layout/AppShell";
import EmployeeCombobox from "../../components/ui/EmployeeCombobox";
import PageLoading from "../../components/ui/PageLoading";
import { downloadEmployeeAnnualKpiReport, downloadEmployeeKpiReport, listUsers } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function safeFilePart(value) {
  return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

export default function KpiDownloadPage() {
  const { user: currentUser } = useAuth();
  const toast = useToast();
  const now = new Date();
  const [roster, setRoster] = useState(null);
  const [departmentId, setDepartmentId] = useState("");
  const [employeeId, setEmployeeId] = useState("");
  const [reportType, setReportType] = useState("monthly");
  const [month, setMonth] = useState(MONTHS[now.getMonth()]);
  const [year, setYear] = useState(now.getFullYear());
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    listUsers()
      .then((data) => {
        // A Department Admin is scoped to employees in their own team; a Tenant
        // Admin can also export a Department Admin's self-assessment report.
        const eligibleRoles = currentUser.role === "dept_admin" ? ["employee"] : ["employee", "dept_admin"];
        const eligible = data.users
          .filter((person) => eligibleRoles.includes(person.role))
          .sort((a, b) => a.name.localeCompare(b.name));
        setRoster(eligible);
        setEmployeeId((previous) =>
          eligible.some((person) => String(person.id) === String(previous))
            ? previous
            : String(eligible[0]?.id ?? "")
        );
      })
      .catch((err) => {
        setRoster([]);
        toast.error(getErrorMessage(err, "Could not load people for the report."));
      });
    // `toast` is recreated by its provider each render; including it would re-fetch indefinitely.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const departments = useMemo(() => {
    const unique = new Map();
    roster?.forEach((person) => {
      if (person.department) unique.set(person.department.id, person.department);
    });
    return [...unique.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [roster]);
  const filteredRoster = useMemo(
    () => roster?.filter((person) => !departmentId || String(person.department?.id) === String(departmentId)) ?? [],
    [roster, departmentId]
  );
  const employee = useMemo(
    () => filteredRoster.find((person) => String(person.id) === String(employeeId)),
    [filteredRoster, employeeId]
  );
  const yearOptions = Array.from({ length: 5 }, (_, index) => now.getFullYear() - 2 + index);

  useEffect(() => {
    setEmployeeId((previous) =>
      filteredRoster.some((person) => String(person.id) === String(previous))
        ? previous
        : String(filteredRoster[0]?.id ?? "")
    );
  }, [filteredRoster]);

  async function handleDownload(event) {
    event.preventDefault();
    if (!employee || downloading) return;

    setDownloading(true);
    try {
      const file = reportType === "annual"
        ? await downloadEmployeeAnnualKpiReport(employee.id, year)
        : await downloadEmployeeKpiReport(employee.id, month, year);
      const url = URL.createObjectURL(file);
      const link = document.createElement("a");
      link.href = url;
      link.download = reportType === "annual"
        ? `kpi-report-${safeFilePart(employee.name)}-${year}-annual.xlsx`
        : `kpi-report-${safeFilePart(employee.name)}-${year}-${month.toLowerCase()}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      toast.success(`${reportType === "annual" ? "Annual" : "Monthly"} Excel report for ${employee.name} downloaded.`);
    } catch (err) {
      toast.error(getErrorMessage(err, "Could not download the Excel report. Please try again."));
    } finally {
      setDownloading(false);
    }
  }

  if (!roster) {
    return (
      <AppShell title="Excel Downloads">
        <PageLoading />
      </AppShell>
    );
  }

  const scopeText =
    currentUser.role === "dept_admin"
      ? "Choose a member of your department and the KPI period to export."
      : "Choose a team member and the KPI period to export.";

  return (
    <AppShell title="Excel Downloads">
      <div className="mx-auto max-w-5xl">
        <div className="mb-7 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-brand-700">KPI reporting</p>
            <h2 className="mt-1 text-2xl font-bold tracking-tight text-slate-900">Download KPI score report</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
              {scopeText} Download a monthly or annual report for each person individually.
            </p>
          </div>
          <div className="inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-200">
            <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V3.75m0 12.75 4.5-4.5M12 16.5l-4.5-4.5M4.5 20.25h15" />
            </svg>
            Excel .xlsx
          </div>
        </div>

        {roster.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-16 text-center">
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-white text-slate-400 shadow-sm ring-1 ring-slate-200">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-8.25A3.375 3.375 0 0 0 4.5 11.625v6.75a3.375 3.375 0 0 0 3.375 3.375h8.25a3.375 3.375 0 0 0 3.375-3.375V14.25ZM8.25 8.25V5.625A3.375 3.375 0 0 1 11.625 2.25h2.25a3.375 3.375 0 0 1 3.375 3.375V8.25" />
              </svg>
            </div>
            <h3 className="mt-4 text-sm font-semibold text-slate-800">No KPI reports are available yet</h3>
            <p className="mt-1 text-sm text-slate-500">Add an employee or Department Admin before downloading a report.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
            <form onSubmit={handleDownload} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm lg:col-span-3">
              <div className="mb-6 flex items-start gap-3 border-b border-slate-100 pb-5">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-600">
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.9">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-8.25A3.375 3.375 0 0 0 4.5 11.625v6.75a3.375 3.375 0 0 0 3.375 3.375h8.25a3.375 3.375 0 0 0 3.375-3.375V14.25ZM8.25 8.25V5.625A3.375 3.375 0 0 1 11.625 2.25h2.25a3.375 3.375 0 0 1 3.375 3.375V8.25" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-semibold text-slate-900">Report details</h3>
                  <p className="mt-1 text-sm text-slate-500">Select the report recipient and period.</p>
                </div>
              </div>

              <div className="space-y-5">
                <fieldset>
                  <legend className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Report type</legend>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className={`cursor-pointer rounded-xl border p-3.5 transition ${reportType === "monthly" ? "border-brand-500 bg-brand-50/60 ring-1 ring-brand-500" : "border-slate-200 hover:border-slate-300"}`}>
                      <input type="radio" name="report-type" value="monthly" checked={reportType === "monthly"} onChange={() => setReportType("monthly")} className="sr-only" />
                      <span className="block text-sm font-semibold text-slate-800">Monthly report</span>
                      <span className="mt-1 block text-xs leading-5 text-slate-500">One selected month with metric details.</span>
                    </label>
                    <label className={`cursor-pointer rounded-xl border p-3.5 transition ${reportType === "annual" ? "border-brand-500 bg-brand-50/60 ring-1 ring-brand-500" : "border-slate-200 hover:border-slate-300"}`}>
                      <input type="radio" name="report-type" value="annual" checked={reportType === "annual"} onChange={() => setReportType("annual")} className="sr-only" />
                      <span className="block text-sm font-semibold text-slate-800">Annual report</span>
                      <span className="mt-1 block text-xs leading-5 text-slate-500">One workbook for the full selected year.</span>
                    </label>
                  </div>
                </fieldset>
                {departments.length > 1 && (
                  <div>
                    <label htmlFor="report-department" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Department</label>
                    <select
                      id="report-department"
                      value={departmentId}
                      onChange={(event) => setDepartmentId(event.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
                    >
                      <option value="">All departments</option>
                      {departments.map((department) => <option key={department.id} value={department.id}>{department.name}</option>)}
                    </select>
                  </div>
                )}
                <div>
                  <label className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Employee</label>
                  <EmployeeCombobox roster={filteredRoster} value={employeeId} onChange={setEmployeeId} />
                  {employee?.department && <p className="mt-2 text-xs text-slate-500">{employee.department.name} Department</p>}
                </div>

                <div className={`grid grid-cols-1 gap-4 ${reportType === "monthly" ? "sm:grid-cols-2" : "max-w-xs"}`}>
                  {reportType === "monthly" && (
                    <div>
                    <label htmlFor="report-month" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Month</label>
                    <select
                      id="report-month"
                      value={month}
                      onChange={(event) => setMonth(event.target.value)}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
                    >
                      {MONTHS.map((name) => <option key={name} value={name}>{name}</option>)}
                    </select>
                    </div>
                  )}
                  <div>
                    <label htmlFor="report-year" className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500">Year</label>
                    <select
                      id="report-year"
                      value={year}
                      onChange={(event) => setYear(Number(event.target.value))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 transition focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
                    >
                      {yearOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                    </select>
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={!employee || downloading}
                className="mt-7 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {downloading ? (
                  <><span className="h-4 w-4 animate-spin rounded-full border-2 border-white/35 border-t-white" /> Preparing Excel file…</>
                ) : (
                  <><svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.2"><path strokeLinecap="round" strokeLinejoin="round" d="M12 3v12m0 0 4-4m-4 4-4-4m-3 8.25v.75a2.25 2.25 0 0 0 2.25 2.25h9.5A2.25 2.25 0 0 0 19.5 19.5v-.75" /></svg> Download {reportType === "annual" ? "annual" : "monthly"} Excel report</>
                )}
              </button>
            </form>

            <aside className="rounded-2xl border border-slate-200 bg-slate-50/70 p-6 lg:col-span-2">
              <h3 className="text-sm font-semibold text-slate-900">Included in the report</h3>
              <ul className="mt-5 space-y-4 text-sm text-slate-600">
                {[
                  "Employee and department details",
                  reportType === "annual" ? "Yearly KPI summary" : "KPI targets and metric weights",
                  reportType === "annual" ? "A detail sheet for every month" : "Self, department, and final scores",
                  reportType === "annual" ? "Finalized monthly attainment and status" : "Combined KPI attainment and status",
                ].map((item) => (
                  <li key={item} className="flex gap-3">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700"><svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5"><path strokeLinecap="round" strokeLinejoin="round" d="m5 12 4 4L19 7" /></svg></span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-7 rounded-xl border border-brand-100 bg-white p-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-brand-700">Selected report</p>
                <p className="mt-1 text-sm font-semibold text-slate-800">{employee?.name} · {reportType === "annual" ? `Annual ${year}` : `${month} ${year}`}</p>
                <p className="mt-1 text-xs leading-5 text-slate-500">The report opens in Excel or another spreadsheet application after download.</p>
              </div>
            </aside>
          </div>
        )}
      </div>
    </AppShell>
  );
}
