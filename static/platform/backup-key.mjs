/* Recovery keys stay in the current DOM only. No storage, URL or analytics. */
export function wire(panel, { key, fingerprint, copyLabel, downloadLabel } = {}) {
  if (!panel) return;
  if (key) {
    panel.replaceChildren();
    const label = document.createElement("label");
    label.textContent = fingerprint;
    const value = document.createElement("textarea");
    value.readOnly = true;
    value.value = key;
    value.setAttribute("aria-label", fingerprint);
    value.dataset.recoveryKey = "";
    value.style.cssText = "width:100%;box-sizing:border-box;font:inherit;overflow-wrap:anywhere";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "set-mini";
    copy.dataset.recoveryCopy = "";
    copy.textContent = copyLabel;
    const download = document.createElement("button");
    download.type = "button";
    download.className = "set-mini";
    download.dataset.recoveryDownload = "";
    download.textContent = downloadLabel;
    panel.append(label, value, copy, download);
  }
  const value = panel.querySelector("[data-recovery-key]");
  if (!value || panel.dataset.recoveryWired) return;
  panel.dataset.recoveryWired = "1";
  panel.querySelector("[data-recovery-copy]")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(value.value); }
    catch { value.focus(); value.select(); }
  });
  panel.querySelector("[data-recovery-download]")?.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([value.value + "\n"], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "human-rounds-recovery-key.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

if (typeof document !== "undefined") {
  document.querySelectorAll("[data-recovery-panel]").forEach(panel => wire(panel));
}
