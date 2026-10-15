import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import AppShell from "../../components/layout/AppShell";
import { getAnalyticsOverview, getAnalyticsTrend } from "../../api/analytics";
import { getErrorMessage } from "../../api/errors";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const BUCKET_COLORS = {
  "Need to improve": "#f87171",
  Normal: "#fbbf24",
  Performance: "#34d399",
};

function StatCard({ label, value, detail }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-bold text-slate-900">{value}</p>
      {detail && <p className="mt-1 text-sm text-slate-500">{detail}</p>}
    </div>
  );
}

function Card({ title, children }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-4 text-base font-semibold text-slate-900">{title}</h3>
      {children}
    </div>
  );
}

export default function AnalyticsPage() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [period, setPeriod] = useState(MONTHS[now.getMonth()]);
  const [overview, setOverview] = useState(null);
  const [trend, setTrend] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([getAnalyticsOverview(year, period), getAnalyticsTrend(6)])
      .then(([ov, tr]) => {
        if (cancelled) return;
        setOverview(ov);
        setTrend(tr);
      })
      .catch((e) => {
        if (!cancelled) setError(getErrorMessage(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [year, period]);

  const bucketData = useMemo(() => {
    if (!overview) return [];
    return Object.entries(overview.buckets).map(([name, value]) => ({ name, value }));
  }, [overview]);

  const trendData = useMemo(
    () =>
      trend.map((p) => ({
        label: `${p.period.slice(0, 3)} ${p.year}`,
        avg: p.avg_score,
        submitted: p.submitted_count,
      })),
    [trend]
  );

  const years = useMemo(() => {
    const y = now.getFullYear();
    return [y - 2, y - 1, y, y + 1];
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold text-slate-900">Analytics</h1>
          <div className="flex gap-2">
            <select
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              {MONTHS.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <select
              value={year}
              onChange={(e) => setYear(Number(e.target.value))}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              {years.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-slate-500">Loading analytics…</p>
        ) : (
          overview && (
            <>
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <StatCard
                  label="Avg Score"
                  value={overview.avg_score ?? "—"}
                  detail={`${overview.approved_count ?? 0} approved`}
                />
                <StatCard
                  label="Submission Rate"
                  value={`${overview.submission_rate}%`}
                  detail={`${overview.submitted_count} of ${overview.employee_count} people`}
                />
                <StatCard
                  label="Pending"
                  value={overview.pending_count}
                  detail="people not yet submitted"
                />
                <StatCard
                  label="Period"
                  value={overview.period.slice(0, 3)}
                  detail={String(overview.year)}
                />
              </div>

              <div className="grid gap-6 lg:grid-cols-2">
                <Card title="Average Score by Department">
                  {overview.by_department.length === 0 ? (
                    <p className="text-sm text-slate-500">No departments yet.</p>
                  ) : (
                    <ResponsiveContainer width="100%" height={280}>
                      <BarChart data={overview.by_department} margin={{ top: 8, right: 8, left: -12, bottom: 8 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="department_name" tick={{ fontSize: 12 }} interval={0} angle={-18} dy={10} height={60} />
                        <YAxis domain={[0, 120]} tick={{ fontSize: 12 }} />
                        <Tooltip />
                        <Bar dataKey="avg_score" name="Avg score" fill="#6366f1" radius={[6, 6, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </Card>

                <Card title="Score Distribution">
                  {bucketData.every((b) => b.value === 0) ? (
                    <p className="text-sm text-slate-500">No submitted scores this period.</p>
                  ) : (
                    <ResponsiveContainer width="100%" height={280}>
                      <PieChart>
                        <Pie data={bucketData} dataKey="value" nameKey="name" innerRadius={60} outerRadius={100} paddingAngle={3} label>
                          {bucketData.map((entry) => (
                            <Cell key={entry.name} fill={BUCKET_COLORS[entry.name] || "#94a3b8"} />
                          ))}
                        </Pie>
                        <Tooltip />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                </Card>
              </div>

              <Card title="Average Score Trend (last 6 months)">
                <ResponsiveContainer width="100%" height={280}>
                  <LineChart data={trendData} margin={{ top: 8, right: 8, left: -12, bottom: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                    <YAxis yAxisId="score" domain={[0, 120]} tick={{ fontSize: 12 }} />
                    <YAxis yAxisId="count" orientation="right" tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip />
                    <Legend />
                    <Line yAxisId="score" type="monotone" dataKey="avg" name="Avg score" stroke="#6366f1" strokeWidth={2.5} dot={{ r: 4 }} connectNulls />
                    <Line yAxisId="count" type="monotone" dataKey="submitted" name="Submitted" stroke="#34d399" strokeWidth={2} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </Card>

              <Card title="Department Detail">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-slate-200 text-xs uppercase tracking-wider text-slate-500">
                        <th className="py-2 pr-4">Department</th>
                        <th className="py-2 pr-4">People</th>
                        <th className="py-2 pr-4">Submitted</th>
                        <th className="py-2">Avg Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {overview.by_department.map((d) => (
                        <tr key={d.department_id} className="border-b border-slate-100 last:border-0">
                          <td className="py-2 pr-4 font-medium text-slate-900">{d.department_name}</td>
                          <td className="py-2 pr-4 text-slate-600">{d.employee_count}</td>
                          <td className="py-2 pr-4 text-slate-600">{d.submitted_count}</td>
                          <td className="py-2 text-slate-600">{d.avg_score ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </>
          )
        )}
      </div>
    </AppShell>
  );
}
