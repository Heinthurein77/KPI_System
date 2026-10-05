export function getErrorMessage(err, fallback = "Something went wrong. Please try again.") {
  // No response at all (network/CORS/base-URL failure) — client.js's response
  // interceptor already rewrote err.message into something actionable.
  if (!err?.response) return err?.message || fallback;

  const detail = err.response.data?.detail;
  // Most handlers raise HTTPException with a plain string `detail`, but FastAPI's
  // own request-validation errors (422) return `detail` as a list of {loc, msg, type}
  // objects instead. Passing that object through unchanged would either render as
  // "[object Object]" or crash the caller (React refuses to render a raw object/array
  // of objects as a child) — and it leaks internal field/type names for no benefit.
  // Only trust `detail` as a message when it's actually a string; otherwise fall back.
  return typeof detail === "string" && detail ? detail : fallback;
}
