(function () {
  "use strict";

  const storageKey = "tuxcmdb-agent-install-status";
  let currentPage = null;
  let statusUrl = null;
  let pollTimer = null;
  let pollingPage = null;
  const find = id => currentPage?.querySelector("#" + id);

  function open(id) {
    const modal = find(id);
    modal.hidden = false;
    modal.querySelector("button, input")?.focus();
  }

  function feedback(message) {
    find("agent-feedback-message").textContent = message;
    open("agent-feedback-modal");
  }

  function remember(url) {
    statusUrl = url;
    try {
      if (url) sessionStorage.setItem(storageKey, url);
      else sessionStorage.removeItem(storageKey);
    } catch (failure) {
      feedback("Browser storage is unavailable. Progress cannot resume automatically after a refresh: " + failure.message);
    }
  }

  async function request(url, options = {}) {
    const response = await fetch(url, {
      cache: "no-store", ...options,
      headers: {"Accept": "application/json"},
    });
    if (response.redirected || !response.headers.get("content-type")?.includes("application/json")) {
      throw new Error("Request failed (HTTP " + response.status +
        "). Your login may have expired or the security token may be invalid. Sign in again or refresh this page.");
    }
    const data = await response.json();
    if (!response.ok) {
      const fields = Object.entries(data.errors || {}).map(([name, errors]) =>
        name + ": " + errors.map(value => value.message).join("; "));
      throw new Error(data.error || fields.join("\n") || "Request failed (HTTP " + response.status + ").");
    }
    return data;
  }

  function render(data) {
    const progress = find("agent-install-progress");
    progress.replaceChildren();
    const heading = document.createElement("h5");
    heading.textContent = "Installation: " + data.state;
    progress.append(heading);
    for (const host of data.hosts) {
      const title = document.createElement("h6");
      title.textContent = host.host + " — " + host.state;
      progress.append(title);
      const list = document.createElement("ul");
      for (const step of host.steps) {
        const item = document.createElement("li");
        item.textContent = step.label + ": " + step.state + (step.message ? " — " + step.message : "");
        if (["failed", "interrupted"].includes(step.state)) item.className = "text-danger";
        else if (step.state === "success") item.className = "text-success";
        list.append(item);
      }
      progress.append(list);
    }
  }

  async function poll() {
    if (pollingPage === currentPage || !statusUrl || !find("agent-install-submit")) return;
    const page = currentPage;
    pollingPage = page;
    const url = statusUrl;
    clearTimeout(pollTimer);
    find("agent-install-error").hidden = true;
    find("agent-install-retry").hidden = true;
    find("agent-install-submit").disabled = true;
    try {
      const data = await request(url);
      if (page !== currentPage || !page.isConnected) return;
      render(data);
      if (data.state === "running") {
        pollTimer = setTimeout(poll, 2000);
      } else {
        find("agent-install-submit").disabled = false;
        remember(null);
      }
    } catch (failure) {
      if (page !== currentPage || !page.isConnected) return;
      find("agent-install-error").textContent = failure.message +
        " Installation may still be running; retry status before starting another batch.";
      find("agent-install-error").hidden = false;
      find("agent-install-retry").hidden = false;
      open("agent-install-modal");
    } finally {
      if (pollingPage === page) pollingPage = null;
    }
  }

  async function settings() {
    const button = find("agent-settings-open");
    button.disabled = true;
    try {
      const data = await request(button.dataset.url);
      find("agent-versions-to-keep").value = data.versions_to_keep;
      find("agent-settings-error").hidden = true;
      open("agent-settings-modal");
    } catch (failure) {
      feedback(failure.message);
    } finally {
      button.disabled = false;
    }
  }

  async function buildStatus(button) {
    button.disabled = true;
    try {
      const {build_status: status} = await request(button.dataset.url);
      find("agent-build-state").textContent = "Status: " + status.state + (status.job ? " (" + status.job + ")" : "");
      find("agent-build-status-error").textContent = status.error;
      find("agent-build-status-error").hidden = !status.error;
      open("agent-build-status-modal");
    } catch (failure) {
      feedback(failure.message);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("submit", async event => {
    const form = event.target;
    if (!form.matches("#agent-install-form, #agent-build-form, #agent-settings-form")) return;
    event.preventDefault();
    event.stopPropagation();
    const button = form.querySelector("[type=submit]");
    if (button.disabled) return;
    button.disabled = true;
    const body = new FormData(form);
    let started = false;
    try {
      const data = await request(form.action, {method: "POST", body});
      if (!form.isConnected) return;
      if (form.id === "agent-install-form") {
        started = true;
        form.elements.password.value = "";
        body.delete("password");
        find("agent-install-error").hidden = true;
        find("agent-install-progress").textContent = "Installation started. Reading host progress…";
        find("agent-install-show").hidden = false;
        remember(data.status_url);
        open("agent-install-modal");
        poll();
      } else {
        if (form.id === "agent-settings-form") find("agent-settings-modal").hidden = true;
        feedback(data.message);
      }
    } catch (failure) {
      if (!form.isConnected) return;
      if (form.id === "agent-settings-form") {
        find("agent-settings-error").textContent = failure.message;
        find("agent-settings-error").hidden = false;
      } else {
        feedback(failure.message);
      }
    } finally {
      body.delete("password");
      if (!started) button.disabled = false;
    }
  }, true);

  document.addEventListener("click", async event => {
    const button = event.target.closest("#agent-settings-open, #agent-build-status-open, #agent-install-retry, #agents-page a[download]");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    if (button.id === "agent-settings-open") return settings();
    if (button.id === "agent-build-status-open") return buildStatus(button);
    if (button.id === "agent-install-retry") return poll();
    if (button.getAttribute("aria-disabled") === "true") return;
    button.setAttribute("aria-disabled", "true");
    try {
      const response = await fetch(button.href);
      if (!response.ok || response.headers.get("content-type") !== "application/octet-stream") {
        throw new Error("Download failed (HTTP " + response.status +
          "). The package may have been retired. Refresh the downloads list and try again.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = button.download;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (failure) {
      feedback(failure.message);
    } finally {
      button.removeAttribute("aria-disabled");
    }
  }, true);

  function initialize() {
    const page = document.getElementById("agents-page");
    if (page === currentPage) return;
    currentPage = page;
    clearTimeout(pollTimer);
    if (!page) return;
    const error = find("agent-build-error");
    if (error) feedback(error.textContent);
    if (find("agent-build-status-error") && !find("agent-build-status-error").hidden) open("agent-build-status-modal");
    if (new URLSearchParams(location.search).has("agent-settings") && find("agent-settings-open")) settings();
    if (!find("agent-install-submit")) return;
    try {
      const saved = statusUrl || sessionStorage.getItem(storageKey);
      if (saved && /^\/agents\/install\/[a-f0-9]{32}\/$/.test(saved)) {
        statusUrl = saved;
        find("agent-install-show").hidden = false;
        open("agent-install-modal");
        poll();
      }
    } catch (failure) {
      feedback("Cannot restore installation progress: " + failure.message);
    }
  }

  initialize();
  new MutationObserver(initialize).observe(document.getElementById("live-root"), {childList: true});
})();
