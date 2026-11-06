(function () {
  "use strict";
  let currentTable = null;
  let controller = null;

  async function load(table) {
    controller?.abort();
    controller = new AbortController();
    const root = document.getElementById("live-root");
    const status = root.querySelector("#asset-vm-status");
    const retry = root.querySelector("#asset-vm-retry");
    retry.hidden = true;
    status.textContent = "Loading VM mappings in the background…";
    try {
      const response = await fetch(table.dataset.mappingsUrl, {
        signal: controller.signal, cache: "no-store", headers: {"Accept": "application/json"},
      });
      if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
        throw new Error("Cannot load VM mappings. Check your login and refresh the page.");
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Cannot load VM mappings.");
      if (!table.isConnected) return;
      table.querySelectorAll("[data-vm-asset-id]").forEach(cell => {
        cell.replaceChildren();
        if (data.mapped_assets[cell.dataset.vmAssetId]) {
          const badge = document.createElement("span");
          badge.className = "badge bg-info-subtle text-info-emphasis";
          badge.textContent = "Mapped to VM";
          cell.append(badge);
        }
      });
      status.textContent = "VM mappings loaded (matches cached for one hour; no match for 60 seconds).";
    } catch (failure) {
      if (failure.name === "AbortError" || !table.isConnected) return;
      table.querySelectorAll("[data-vm-asset-id]").forEach(cell => { cell.textContent = "Unavailable"; });
      status.textContent = failure.message;
      retry.hidden = false;
    }
  }

  function initialize() {
    const table = document.getElementById("asset-vm-table");
    if (table === currentTable) return;
    currentTable = table;
    if (table && table.querySelector("[data-vm-asset-id]")) load(table);
    else controller?.abort();
  }

  document.addEventListener("click", event => {
    if (event.target.closest("#asset-vm-retry") && currentTable) load(currentTable);
  });
  initialize();
  new MutationObserver(initialize).observe(document.getElementById("live-root"), {childList: true});
})();
