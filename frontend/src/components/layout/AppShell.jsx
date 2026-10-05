import { useState } from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

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
      </div>
    </div>
  );
}
