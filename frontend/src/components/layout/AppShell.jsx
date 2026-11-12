import { useState } from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { APP_VERSION, APP_COMMIT } from "../../version";

export default function AppShell({ title, children }) {
  const [navOpen, setNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <Topbar title={title} onMenuClick={() => setNavOpen(true)} />
        <main className="flex-1 min-h-0 overflow-y-auto px-4 md:px-8 py-7">
          <div className="max-w-[1400px] w-full mx-auto">{children}</div>
        </main>
        {/* M3: release identity — answers "what version is live?" without
            asking in chat. The commit is baked in at build time (see
            vite.config.js); "dev" means the build had no git checkout and
            no VITE_APP_COMMIT build arg. */}
        <footer className="shrink-0 border-t border-slate-200 bg-white px-4 py-1.5 text-center text-[11px] text-slate-400">
          KPI Approval System · v{APP_VERSION} · {APP_COMMIT}
        </footer>
      </div>
    </div>
  );
}
