// Saves a Blob the browser already has in memory — used for exports that
// must be fetched with an auth header first (a plain <a href> can't carry
// one), so the download itself is just: object URL, click, revoke.
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
