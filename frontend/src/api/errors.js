export function getErrorMessage(err, fallback = "Something went wrong. Please try again.") {
  // No response at all (network/CORS/base-URL failure) — client.js's response
  // interceptor already rewrote err.message into something actionable.
  if (!err?.response) return err?.message || fallback;
  return err.response.data?.detail || fallback;
}
