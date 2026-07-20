import { Link } from "react-router-dom";

const STEPS = [
  {
    to: "/admin/departments",
    title: "1. Create a department",
    desc: "Engineering, Sales, Support — however your org is structured.",
    cta: "Go to Departments",
    icon: <path strokeLinecap="round" strokeLinejoin="round" d="M3 21V7l9-4 9 4v14M9 21v-6h6v6" />,
  },
  {
    to: "/admin/users",
    title: "2. Add users",
    desc: "Create Dept Admins and Employees, assigned to their department.",
    cta: "Go to Users",
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M17 20h5v-2a4 4 0 0 0-3-3.87M9 20H4v-2a4 4 0 0 1 3-3.87m5-8a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm6 3a4 4 0 1 1-2.6-3.75"
      />
    ),
  },
  {
    to: "/admin/templates",
    title: "3. Define KPI metrics",
    desc: "Set the metrics, targets, and weights each team is scored on.",
    cta: "Go to KPI Metrics",
    icon: (
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M9 12h6m-6 3.75h6M9 8.25h1.5M8.25 6h7.5A2.25 2.25 0 0 1 18 8.25v10.5A2.25 2.25 0 0 1 15.75 21H8.25A2.25 2.25 0 0 1 6 18.75V8.25A2.25 2.25 0 0 1 8.25 6Z"
      />
    ),
  },
];

export default function FreshInstallWelcome({ userName }) {
  return (
    <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-900 to-brand-900 px-8 py-12 sm:px-12">
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage: "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
          backgroundSize: "26px 26px",
        }}
      ></div>
      <div className="absolute -top-20 -right-20 h-72 w-72 rounded-full bg-brand-500/20 blur-3xl"></div>

      <div className="relative max-w-2xl">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-brand-200 uppercase tracking-wide">
          Welcome, {userName}
        </span>
        <h2 className="mt-4 text-2xl font-bold text-white tracking-tight">Let's set up your organization</h2>
        <p className="mt-2 text-slate-300 text-[15px] leading-relaxed">
          Your workspace is empty — no departments, teams, or KPI metrics yet. Set these up in order and you'll be
          ready to start collecting KPI submissions.
        </p>
      </div>

      <div className="relative mt-8 grid grid-cols-1 sm:grid-cols-3 gap-4">
        {STEPS.map((step) => (
          <Link
            key={step.to}
            to={step.to}
            className="group rounded-xl bg-white/5 border border-white/10 p-5 hover:bg-white/10 hover:border-white/20 transition"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-500/20 text-brand-300">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                {step.icon}
              </svg>
            </div>
            <p className="mt-3 text-sm font-semibold text-white">{step.title}</p>
            <p className="mt-1 text-xs text-slate-400">{step.desc}</p>
            <span className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-brand-300 group-hover:gap-1.5 transition-all">
              {step.cta}
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 8l4 4m0 0-4 4m4-4H3" />
              </svg>
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
