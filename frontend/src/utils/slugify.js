// Shared filename slugger (review finding m22): ASCII-only, with a
// fallback so a non-Latin name (e.g. Burmese) never produces an empty
// slug — pass "department" for a department file, "employee" for a
// per-person file.
export function slugify(value, fallback = "file") {
  const slug = String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return slug || fallback;
}
