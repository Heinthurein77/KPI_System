import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// M3 (2026-11-12 PM review): bake the release identity into the bundle so
// the UI footer can answer "what version is live?". The Docker production
// build has no .git at runtime, so the commit comes from the environment
// first (Dockerfile ARG VITE_APP_COMMIT, passed at deploy time), then from
// git when a checkout is present (local dev, CI), and falls back to "dev"
// — the build must never fail because git is absent. The version defaults
// to package.json; override with VITE_APP_VERSION when releases are cut.
const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8"));

function shortCommit() {
  if (process.env.VITE_APP_COMMIT) return process.env.VITE_APP_COMMIT;
  try {
    return execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
  } catch {
    return "dev";
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __APP_VERSION__: JSON.stringify(process.env.VITE_APP_VERSION || pkg.version || "0.0.0"),
    __APP_COMMIT__: JSON.stringify(shortCommit()),
  },
  server: {
    port: 5173,
  },
})
