import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

export default function AppShell({ title, children }) {
  return (
    <div className="flex h-full min-h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Topbar title={title} />
        <main className="flex-1 px-4 md:px-8 py-7 max-w-[1400px] w-full mx-auto">{children}</main>
      </div>
    </div>
  );
}
