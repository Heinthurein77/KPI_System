import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import AppShell from "../../components/layout/AppShell";
import PageLoading from "../../components/ui/PageLoading";
import ConfirmDialog from "../../components/ui/ConfirmDialog";
import Modal from "../../components/ui/Modal";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { createUser, deleteUser, listUsers, resetUserPassword, toggleUserActive } from "../../api/admin";
import { getErrorMessage } from "../../api/errors";

// Shown when the initial data load fails (instead of an endless spinner).
// Lets the user retry the failed request.
function LoadError({ message, onRetry }) {
  return (
    <div className="rounded-2xl bg-white shadow-sm border border-slate-200 px-8 py-10 max-w-md w-full mx-auto text-center">
      <p className="text-sm font-semibold text-red-600">Couldn't load data</p>
      <p className="mt-2 text-sm text-slate-500">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-6 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
      >
        Try again
      </button>
    </div>
  );
}

function titleCase(role) {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const toast = useToast();
  const isDeptAdmin = currentUser.role === "dept_admin";

  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [role, setRole] = useState("employee");
  const [departmentId, setDepartmentId] = useState("");
  const [position, setPosition] = useState("");
  // Inline form errors (shown next to the field / above Submit, alongside
  // the toast): a failed submit must not leave the user guessing which
  // field is wrong, and typed values are kept so nothing has to be retyped.
  const [formError, setFormError] = useState(null);
  const [deptError, setDeptError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [pendingToggle, setPendingToggle] = useState(null);
  const [toggling, setToggling] = useState(false);
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
      .then((d) => {
        setData(d);
        setLoadError(null);
      })
      .catch((err) => {
        const message = getErrorMessage(err);
        setLoadError(message);
        toast.error(message);
      });
  }

  // `toast` is a plain object re-created every render (not memoized by
  // ToastContext), so listing it here would refetch on every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  // Close the Add dialog and clear its transient state (errors, password
  // visibility) so a reopened form starts clean — typed values are kept
  // only across a failed submit, never across closes.
  function closeAdd() {
    setShowAdd(false);
    setFormError(null);
    setDeptError(null);
    setShowPassword(false);
  }

  async function handleCreate(e) {
    e.preventDefault();
    setFormError(null);
    setDeptError(null);
    // Client-side pre-check for the backend rule (dept admins must belong
    // to a department): fail inline, without a round trip, next to the
    // department field.
    if (!isDeptAdmin && role === "dept_admin" && !departmentId) {
      setDeptError("Department Admins must belong to a department.");
      return;
    }
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
      setShowPassword(false);
      toast.success(isDeptAdmin ? "Employee added." : "User created.");
      closeAdd();
      load();
    } catch (err) {
      setFormError(getErrorMessage(err));
      toast.error(getErrorMessage(err));
    }
  }

  async function confirmToggle() {
    if (!pendingToggle) return;
    const u = pendingToggle;
    setToggling(true);
    try {
      await toggleUserActive(u.id);
      toast.success(u.is_active ? `${u.name}'s account disabled.` : `${u.name}'s account re-enabled.`);
      setPendingToggle(null);
      load();
    } catch (err) {
      toast.error(getErrorMessage(err));
    } finally {
      setToggling(false);
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

  if (!data) {
    return (
      <AppShell title={isDeptAdmin ? "My Team" : "Users"}>
        {loadError ? <LoadError message={loadError} onRetry={load} /> : <PageLoading />}
      </AppShell>
    );
  }

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
      <div className="flex justify-end mb-4">
        <button
          type="button"
          onClick={() => setShowAdd(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 transition"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          {isDeptAdmin ? "Add Employee" : "Add User"}
        </button>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
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
          <div className="hidden lg:block overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100">
              <thead>
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Name</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Email</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Role</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Position</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Department</th>
                  <th className="px-6 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">Status</th>
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

                          {(!isDeptAdmin || u.role === "employee") && (
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
                              onClick={() => setPendingToggle(u)}
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
          <div className="lg:hidden divide-y divide-slate-100">
            {filteredUsers.length === 0 ? (
              <p className="px-6 py-10 text-center text-sm text-slate-400">
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
              </p>
            ) : (
              filteredUsers.map((u) => (
                <div key={u.id} className="px-6 py-4 space-y-1.5">
                  <div className="flex items-center gap-3">
                    <div className="h-8 w-8 shrink-0 rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white flex items-center justify-center text-xs font-semibold">
                      {u.name[0]?.toUpperCase()}
                    </div>
                    <span className="font-medium text-slate-800 text-sm">{u.name}</span>
                    {u.is_active ? (
                      <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-inset ring-emerald-200">
                        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                        Active
                      </span>
                    ) : (
                      <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500 ring-1 ring-inset ring-slate-200">
                        <span className="h-1.5 w-1.5 rounded-full bg-slate-400"></span>
                        Disabled
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-slate-500">{u.email}</p>
                  <p className="text-sm text-slate-600">
                    {titleCase(u.role)}
                    {u.position ? ` · ${u.position}` : ""}
                    {u.department ? ` · ${u.department.name}` : ""}
                  </p>
                  <div className="flex items-center gap-2 pt-1">
                    {!isDeptAdmin && (
                      <Link
                        to={`/admin/users/${u.id}/edit`}
                        className="inline-flex items-center rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50 transition"
                      >
                        Edit
                      </Link>
                    )}
                    {(!isDeptAdmin || u.role === "employee") && (
                      <button
                        type="button"
                        onClick={() => openResetPassword(u)}
                        className="inline-flex items-center rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-50 transition"
                      >
                        Reset password
                      </button>
                    )}
                    {(!isDeptAdmin || u.role === "employee") && u.id !== currentUser.id && (
                      <button
                        type="button"
                        onClick={() => setPendingToggle(u)}
                        className={
                          u.is_active
                            ? "inline-flex items-center rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 transition"
                            : "inline-flex items-center rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100 transition"
                        }
                      >
                        {u.is_active ? "Disable" : "Enable"}
                      </button>
                    )}
                    {!isDeptAdmin && u.id !== currentUser.id && (
                      <button
                        type="button"
                        onClick={() => setPendingDelete(u)}
                        className="inline-flex items-center rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100 transition"
                      >
                        Delete
                      </button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

      <Modal
        open={showAdd}
        title={isDeptAdmin ? "Add Employee" : "Add User"}
        onClose={closeAdd}
      >
          <form onSubmit={handleCreate} className="space-y-3">
            <div>
              <label htmlFor="add-user-name" className="mb-1 block text-xs font-semibold text-slate-600">
                Full name <span className="text-red-500">*</span>
              </label>
              <input
                id="add-user-name"
                type="text"
                required
                autoComplete="name"
                autoCapitalize="words"
                placeholder="e.g. Aung Khant"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>
            <div>
              <label htmlFor="add-user-email" className="mb-1 block text-xs font-semibold text-slate-600">
                Email <span className="text-red-500">*</span>
              </label>
              <input
                id="add-user-email"
                type="email"
                required
                autoComplete="email"
                autoCapitalize="none"
                inputMode="email"
                spellCheck={false}
                placeholder="e.g. aung@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>
            <div>
              <label htmlFor="add-user-password" className="mb-1 block text-xs font-semibold text-slate-600">
                Temporary password <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <input
                  id="add-user-password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="new-password"
                  placeholder="At least 8 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 pr-11 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  title={showPassword ? "Hide password" : "Show password"}
                  className="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-slate-600 transition"
                >
                  {showPassword ? (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
                    </svg>
                  ) : (
                    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                    </svg>
                  )}
                </button>
              </div>
            </div>
            <div>
              <label htmlFor="add-user-position" className="mb-1 block text-xs font-semibold text-slate-600">
                Position <span className="font-normal text-slate-400">(optional)</span>
              </label>
              <input
                id="add-user-position"
                type="text"
                autoComplete="organization-title"
                autoCapitalize="words"
                placeholder="e.g. Accountant, HR Manager"
                value={position}
                onChange={(e) => setPosition(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
              />
            </div>

            {isDeptAdmin ? (
              <p className="text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
                Role: <span className="font-medium text-slate-700">Employee</span> · Department:{" "}
                <span className="font-medium text-slate-700">{currentUser.department?.name}</span>
              </p>
            ) : (
              <>
                <div>
                  <label htmlFor="add-user-role" className="mb-1 block text-xs font-semibold text-slate-600">
                    Role
                  </label>
                  <select
                    id="add-user-role"
                    value={role}
                    onChange={(e) => { setRole(e.target.value); setDeptError(null); }}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-brand-500 transition"
                  >
                    {data.roles.map((r) => (
                      <option key={r} value={r}>
                        {titleCase(r)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="add-user-department" className="mb-1 block text-xs font-semibold text-slate-600">
                    Department {role === "dept_admin" && <span className="text-red-500">*</span>}
                  </label>
                  <select
                    id="add-user-department"
                    value={departmentId}
                    onChange={(e) => { setDepartmentId(e.target.value); setDeptError(null); }}
                    title={role === "dept_admin" ? "Department Admins must belong to a department" : undefined}
                    aria-invalid={!!deptError}
                    aria-describedby={deptError ? "add-user-department-error" : undefined}
                    className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none focus:ring-2 transition ${deptError ? "border-red-400 focus:ring-red-400" : "border-slate-300 focus:ring-brand-500 focus:border-brand-500"}`}
                  >
                    <option value="">No department (Tenant Admin)</option>
                    {data.departments.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                  {deptError && (
                    <p id="add-user-department-error" className="mt-1 text-xs text-red-600">{deptError}</p>
                  )}
                </div>
              </>
            )}

            {formError && (
              <p role="alert" className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
                {formError}
              </p>
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
      </Modal>
      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete ${pendingDelete?.name}?`}
        message="This also permanently removes all of their KPI submission history. This cannot be undone."
        submitting={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
      <ConfirmDialog
        open={pendingToggle !== null}
        title={pendingToggle?.is_active ? `Disable ${pendingToggle?.name}'s account?` : `Re-enable ${pendingToggle?.name}'s account?`}
        message={
          pendingToggle?.is_active
            ? "They will be signed out and unable to log in until re-enabled."
            : "They will be able to log in again."
        }
        danger={pendingToggle?.is_active}
        confirmLabel={pendingToggle?.is_active ? "Disable" : "Re-enable"}
        submitting={toggling}
        onConfirm={confirmToggle}
        onCancel={() => setPendingToggle(null)}
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
