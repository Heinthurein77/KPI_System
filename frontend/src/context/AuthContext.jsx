import { createContext, useContext, useEffect, useState } from "react";
import * as authApi from "../api/auth";
import { getErrorMessage } from "../api/errors";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Session lives in the httpOnly cookie now; if it's missing/invalid the
    // server 401s and we land logged-out.
    authApi
      .me()
      .then(setUser)
      .catch(() => {
        localStorage.removeItem("tenant_slug");
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  async function login(orgSlug, email, password) {
    try {
      const data = await authApi.login(orgSlug, email, password);
      // The server sets the session cookie; the token never touches JS.
      setUser(data.user);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: getErrorMessage(err, "Invalid organization, email, or password.") };
    }
  }

  async function platformLogin(email, password) {
    try {
      const data = await authApi.platformLogin(email, password);
      setUser(data.user);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: getErrorMessage(err, "Invalid email or password.") };
    }
  }

  async function logout() {
    try {
      await authApi.logout();
    } catch {
      // Cookie already gone/expired — still clear local state.
    }
    localStorage.removeItem("tenant_slug");
    setUser(null);
  }

  // Refresh the user object from the server (e.g. after a password change
  // clears the must_change_password flag).
  async function refreshUser() {
    try {
      const me = await authApi.me();
      setUser(me);
      return me;
    } catch {
      return null;
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, platformLogin, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
