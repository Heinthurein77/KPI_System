import axios from "axios";

// Vite only exposes env vars prefixed VITE_ (via import.meta.env) — anything named
// REACT_APP_* (a Create React App convention) or set only as a runtime/non-build
// variable is invisible here. This value is baked into the JS bundle at `npm run
// build` time; changing it on the host afterward does nothing without a rebuild.
//
// An explicit empty string is a deliberate "same origin" setting (the single-service
// deploy, where this app is served by the same host as the API) — only an actually
// *unset* var (undefined) should fall back to the local dev default. Using `??`
// instead of `||` preserves that distinction; `||` would treat "" as unset too.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

if (import.meta.env.VITE_API_BASE_URL === undefined && import.meta.env.PROD) {
  // Falling back to localhost in a production build almost always means the
  // request never reaches the real API — surface it loudly instead of failing silently.
  console.error(
    "VITE_API_BASE_URL was not set at build time — API requests will target " +
      API_BASE_URL +
      ", which is wrong in production. Set it as a build variable and rebuild/redeploy, " +
      "or leave it as an empty string for a same-origin deploy."
  );
}

const client = axios.create({ baseURL: API_BASE_URL, withCredentials: true });

client.interceptors.request.use((config) => {
  // Session auth travels in the httpOnly cookie (set by the server on login),
  // so no Authorization header is needed — and JS can never read the token.
  // Identifies which tenant a request belongs to (see app/core/tenant.py).
  // Unset for platform-admin sessions (/platform/*), which aren't tenant-scoped.
  const tenantSlug = localStorage.getItem("tenant_slug");
  if (tenantSlug) {
    config.headers["X-Tenant-Slug"] = tenantSlug;
  }
  return config;
});

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!error.response) {
      // Request never got a response at all: wrong base URL, CORS block, DNS
      // failure, or the server is down — distinct from a 4xx/5xx the API returned.
      error.message = `Could not reach the API at ${API_BASE_URL}. Check the base URL and CORS configuration.`;
    } else if (error.response.status === 401) {
      // Session expired, token revoked, or account disabled mid-session: drop
      // the tenant context and send the user back to sign in. The session
      // cookie itself is httpOnly, so only the server can clear it — a fresh
      // login replaces it. Skip when the failing request is itself a login
      // attempt or we're already on a login page, so failed logins still
      // show their error message. Platform admins go to their own login —
      // org credentials don't work there and vice versa.
      const url = error.config?.url || "";
      const pathname = window.location.pathname;
      const onLoginPage = /\/login\/?$/.test(pathname);
      const isLoginRequest = /\/auth\/login\/?$/.test(url);
      const isPlatform = pathname.startsWith("/platform") || !localStorage.getItem("tenant_slug");
      if (!onLoginPage && !isLoginRequest) {
        localStorage.removeItem("tenant_slug");
        window.location.href = isPlatform ? "/platform/login" : "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default client;
