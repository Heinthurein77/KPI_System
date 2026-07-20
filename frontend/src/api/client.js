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

const client = axios.create({ baseURL: API_BASE_URL });

client.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
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
    }
    return Promise.reject(error);
  }
);

export default client;
