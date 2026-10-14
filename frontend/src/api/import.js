import client from "./client";

export function downloadImportTemplate() {
  return client
    .get("/api/admin/import/template", { responseType: "blob" })
    .then((r) => r.data);
}

export function uploadImport(file, dryRun = true) {
  const form = new FormData();
  form.append("file", file);
  return client
    .post("/api/admin/import/upload", form, {
      params: { dry_run: dryRun },
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
}
