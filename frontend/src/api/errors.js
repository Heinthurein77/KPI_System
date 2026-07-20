export function getErrorMessage(err, fallback = "Something went wrong. Please try again.") {
  return err?.response?.data?.detail || fallback;
}
