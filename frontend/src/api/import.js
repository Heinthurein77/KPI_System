import client from "./client";

export function downloadImportTemplate() {
  return client
    .get("/api/admin/import/template", { responseType: "blob" })
    .then((r) => r.data);
}

export function uploadImport(file, dryRun = true, year = null, month = null) {
  const form = new FormData();
  form.append("file", file);
  const params = { dry_run: dryRun };
  if (year !== null && month !== null) {
    params.year = year;
    params.month = month;
  }
  return client
    .post("/api/admin/import/upload", form, {
      params,
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
}
