// Release identity shown in the UI footer (M3, 2026-11-12 PM review).
// __APP_VERSION__ / __APP_COMMIT__ are replaced at build time by the Vite
// `define` block in vite.config.js: commit from VITE_APP_COMMIT (Docker
// build arg) or `git rev-parse --short HEAD`, falling back to "dev" when
// neither is available. They are globals only inside the bundle — the
// /* global */ comment below tells oxlint they are intentional.

/* global __APP_VERSION__, __APP_COMMIT__ */

export const APP_VERSION =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "dev";
export const APP_COMMIT =
  typeof __APP_COMMIT__ !== "undefined" ? __APP_COMMIT__ : "dev";
