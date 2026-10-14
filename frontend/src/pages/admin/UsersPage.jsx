import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import AppShell from "../../components/layout/AppShell";
import PageLoading from "../../components/ui/PageLoading";
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { createUser, deleteUser, listUsers, resetUserPassword, toggleUserActive } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

function titleCase(role) {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const toast = useToast();
  const isDeptAdmin = currentUser.role === "dept_admin";

  const [data, setData] = useState(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("employee");
  const [departmentId, setDepartmentId] = useState("");
  const [position, setPosition] = useState("");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [resetTarget, setResetTarget] = useState(null);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [resetting, setResetting] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterDept, setFilterDept] = useState("");
  const [filterRole, setFilterRole] = useState("");

  function load() {
    listUsers()
      .then(setData)
      .catch((err) => toast.error(getErrorMessage(err)));
  }

  // `toast` is a plain object re-created every render (not memoized by
  // ToastContext), so listing it here would refetch on every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  async function handleCreate(e) {
    e.preventDefault();
    try {
      await createUser({
        name: name.trim(),
        email: email.trim(),
        password,
        role: isDeptAdmin ? "employee" : role,
        department_id: isDeptAdmin ? undefined : departmentId || undefined,
        position: position.trim() || undefined,
      });
      setName("");
      setEmail("");
      setPassword("");
      setPosition("");
      toast.success(isDeptAdmin ? "Employee added." : "User created.");
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }

  async function handleToggle(u) {
    try {
      await toggleUserActive(u.id);
      toast.success(u.is_active ? `${u.name}'s account disabled.` : `${u.name}'s account re-enabled.`);
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      await deleteUser(pendingDelete.id);
      toast.success(`${pendingDelete.name} deleted.`);
      setPendingDelete(null);
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setDeleting(false);
    }
  }

  function openResetPassword(u) {
    setResetTarget(u);
    setNewPassword("");
    setConfirmPassword("");
  }

  function closeResetPassword() {
    setResetTarget(null);
    setNewPassword("");
    setConfirmPassword("");
  }

  useEffect(() => {
    if (resetTarget === null) return;
    function handleKey(e) {
      if (e.key === "Escape") closeResetPassword();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [resetTarget]);

  async function confirmResetPassword() {
    if (!resetTarget) return;
    if (newPassword.length < 8) {
      toast.error("Password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("Passwords do not match.");
      return;
    }
    setResetting(true);
    try {
      await resetUserPassword(resetTarget.id, newPassword);
      toast.success(`Password reset for ${resetTarget.name}.`);
      closeResetPassword();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setResetting(false);
    }
  }

  if (!data) return <AppShell title={isDeptAdmin ? "My Team" : "Users"}><PageLoading /></AppShell>;

  const q = searchQuery.trim().toLowerCase();
  const filteredUsers = data.users.filter((u) => {
    if (q && !u.name.toLowerCase().includes(q) && !u.email.toLowerCase().includes(q)) return false;
    if (filterDept && String(u.department?.id ?? "") !== filterDept) return false;
    if (filterRole && u.role !== filterRole) return false;
    return true;
  });
  const hasActiveFilters = q !== "" || filterDept !== "" || filterRole !== "";

  function clearFilters() {
    setSearchQuery("");
    setFilterDept("");
    setFilterRole("");
  }

  return (
    <AppShell title={isDeptAdmin ? "My Team" : "Users"}>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex flex-col sm:flex-row gap-3">
            <div className="relative flex-1">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
              </svg>
              <input
                type="text"
                placeholder="Search name or email…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-lg border border-slate-300 pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>
            {!isDeptAdmin && (
              <select
                value={filterDept}
                onChange={(e) => setFilterDept(e.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              >
                <option value="">All departments</option>
                {data.departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            )}
            <select
              value={filterRole}
              onChange={(e) => setFilterRole(e.target.value)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            >
              <option value="">All roles</option>
              {data.roles.map((r) => (
                <option key={r} value={r}>
                  {titleCase(r)}
                </option>
              ))}
            </select>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={clearFilters}
                className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-700 transition shrink-0"
              >
                Clear
              </button>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Name</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Email</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Role</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Position</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Department</th>
                  <th className="px-6 py-3 text-left text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-10 text-center text-sm text-slate-400">
                      {data.users.length === 0 ? (
                        "No users yet."
                      ) : (
                        <>
                          No users match your filters.{" "}
                          <button type="button" onClick={clearFilters} className="font-medium text-brand-600 hover:text-brand-700">
                            Clear filters
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((u) => (
                    <tr key={u.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4 text-sm">
                        <div className="flex items-center gap-3">
                          <div className="h-8 w-8 shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center text-xs font-semibold">
                            {u.name[0]?.toUpperCase()}
                          </div>
                          <span className="font-medium text-slate-800">{u.name}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-500">{u.email}</td>
                      <td className="px-6 py-4 text-sm text-slate-600">{titleCase(u.role)}</td>
                      <td className="px-6 py-4 text-sm text-slate-600">{u.position || "—"}</td>
                      <td className="px-6 py-4 text-sm text-slate-600">{u.department ? u.department.name : "—"}</td>
                      <td className="px-6 py-4 text-sm">
                        {u.is_active ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-200">
                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                            Active
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 ring-1 ring-inset ring-slate-200">
                            <span className="h-1.5 w-1.5 rounded-full bg-slate-400"></span>
                            Disabled
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center justify-end gap-2">
                          {!isDeptAdmin && (
                            <Link
                              to={`/admin/users/${u.id}/edit`}
                              title={`Edit ${u.name}`}
                              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-700 hover:border-slate-300 transition"
                            >
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                                />
                              </svg>
                            </Link>
                          )}

                          {!isDeptAdmin && (
                            <button
                              type="button"
                              onClick={() => openResetPassword(u)}
                              title={`Reset ${u.name}'s password`}
                              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-slate-700 hover:border-slate-300 transition"
                            >
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M15.75 5.25a3 3 0 0 1 3 3m3 0a6 6 0 0 1-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1 1 21.75 8.25Z"
                                />
                              </svg>
                            </button>
                          )}

                          {(!isDeptAdmin || u.role === "employee") && u.id !== currentUser.id && (
                            <button
                              type="button"
                              onClick={() => handleToggle(u)}
                              title={u.is_active ? `Disable ${u.name}'s account` : `Re-enable ${u.name}'s account`}
                              className={
                                u.is_active
                                  ? "inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 hover:border-red-300 transition"
                                  : "inline-flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100 hover:border-emerald-300 transition"
                              }
                            >
                              {u.is_active ? (
                                <>
                                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M18.364 18.364A9 9 0 1 0 5.636 5.636a9 9 0 0 0 12.728 12.728ZM5.636 5.636l12.728 12.728"
                                    />
                                  </svg>
                                  Disable
                                </>
                              ) : (
                                <>
                                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.4">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
                                  </svg>
                                  Enable
                                </>
                              )}
                            </button>
                          )}

                          {!isDeptAdmin && u.id !== currentUser.id && (
                            <button
                              type="button"
                              onClick={() => setPendingDelete(u)}
                              title={`Delete ${u.name} (also removes their KPI history)`}
                              className="inline-flex items-center justify-center h-8 w-8 rounded-lg border border-slate-200 text-slate-400 hover:bg-red-50 hover:text-red-600 hover:border-red-200 transition"
                            >
                              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="m14.74 9-.346 9m-4.788 0L9.26 9M19.228 5.79c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                                />
                              </svg>
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 h-fit">
          <h2 className="text-sm font-semibold text-slate-900 mb-4">{isDeptAdmin ? "Add Employee" : "Add User"}</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <input
              type="text"
              required
              placeholder="Full name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            />
            <input
              type="email"
              required
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            />
            <input
              type="password"
              required
              placeholder="Temporary password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            />
            <input
              type="text"
              placeholder="Position (e.g. Accountant, HR Manager)"
              value={position}
              onChange={(e) => setPosition(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
            />

            {isDeptAdmin ? (
              <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
                Role: <span className="font-medium text-slate-700">Employee</span> · Department:{" "}
                <span className="font-medium text-slate-700">{currentUser.department?.name}</span>
              </p>
            ) : (
              <>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                >
                  {data.roles.map((r) => (
                    <option key={r} value={r}>
                      {titleCase(r)}
                    </option>
                  ))}
                </select>
                <select
                  value={departmentId}
                  onChange={(e) => setDepartmentId(e.target.value)}
                  required={role === "dept_admin"}
                  title={role === "dept_admin" ? "Department Admins must belong to a department" : undefined}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                >
                  <option value="">No department (Tenant Admin)</option>
                  {data.departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </>
            )}

            <button
              type="submit"
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              {isDeptAdmin ? "Add Employee" : "Create User"}
            </button>
          </form>
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete ${pendingDelete?.name}?`}
        message="This also permanently removes all of their KPI submission history. This cannot be undone."
        submitting={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      {resetTarget !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm px-4"
          onClick={closeResetPassword}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="w-full max-w-sm rounded-2xl bg-white shadow-xl p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-slate-900">
              Reset password for {resetTarget.name}
            </h3>
            <p className="mt-1.5 text-sm text-slate-500">
              They will sign in with this new password next time.
            </p>
            <div className="mt-4 space-y-3">
              <input
                type="password"
                placeholder="New password (min 8 characters)"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
              <input
                type="password"
                placeholder="Confirm new password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>
            <div className="mt-6 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={closeResetPassword}
                className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmResetPassword}
                disabled={resetting || newPassword.length < 8 || confirmPassword === ""}
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition disabled:opacity-60"
              >
                {resetting ? "Please wait…" : "Reset password"}
              </button>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}
