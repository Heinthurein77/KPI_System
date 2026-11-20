// Shared blob download (F-M1, senior review 2026-11-20). The same
// createObjectURL / anchor-click / revoke snippet was copy-pasted in
// BulkImportPage, AttendancePage (x2) and KpiDownloadPage.
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
