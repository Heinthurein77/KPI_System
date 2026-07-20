import { createContext, useContext, useEffect, useState } from "react";
import * as authApi from "../api/auth";
import { getErrorMessage } from "../api/errors";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      setLoading(false);
      return;
    }
    authApi
      .me()
      .then(setUser)
      .catch(() => {
        localStorage.removeItem("access_token");
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  async function login(email, password) {
    try {
      const data = await authApi.login(email, password);
      localStorage.setItem("access_token", data.access_token);
      setUser(data.user);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: getErrorMessage(err, "Invalid email or password.") };
    }
  }

  function logout() {
    localStorage.removeItem("access_token");
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
