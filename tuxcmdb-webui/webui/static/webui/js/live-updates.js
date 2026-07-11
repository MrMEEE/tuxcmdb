(function () {
  "use strict";

  const rootSelector = "#live-root";
  const pageTitleSelector = ".page-title";
  let isRefreshing = false;
  let autoSubmitTimer = null;

  function captureFocusState(root) {
    const active = document.activeElement;
    if (!active || !root || !root.contains(active)) {
      return null;
    }

    const tagName = (active.tagName || "").toLowerCase();
    if (!["input", "textarea", "select"].includes(tagName)) {
      return null;
    }

    return {
      id: active.id || "",
      name: active.getAttribute("name") || "",
      type: active.getAttribute("type") || "",
      value: "value" in active ? active.value : "",
      selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
      selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null,
    };
  }

  function restoreFocusState(root, focusState) {
    if (!root || !focusState) {
      return;
    }

    let selector = null;
    if (focusState.id) {
      selector = "#" + CSS.escape(focusState.id);
    } else if (focusState.name) {
      selector = "[name='" + CSS.escape(focusState.name) + "']";
    }
    if (!selector) {
      return;
    }

    const nextField = root.querySelector(selector);
    if (!nextField) {
      return;
    }

    if ("value" in nextField && typeof focusState.value === "string" && nextField.value !== focusState.value) {
      nextField.value = focusState.value;
    }

    nextField.focus();
    if (
      typeof nextField.setSelectionRange === "function" &&
      focusState.selectionStart !== null &&
      focusState.selectionEnd !== null
    ) {
      const nextLength = typeof nextField.value === "string" ? nextField.value.length : 0;
      const start = Math.min(focusState.selectionStart, nextLength);
      const end = Math.min(focusState.selectionEnd, nextLength);
      nextField.setSelectionRange(start, end);
    }
  }

  function parseRootFromHtml(htmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, "text/html");
    return {
      doc: doc,
      htmlText: htmlText,
      root: doc.querySelector(rootSelector),
      title: doc.title,
      pageTitle: doc.querySelector(pageTitleSelector),
    };
  }

  function replaceDocument(parsed, responseUrl, historyMode) {
    if (responseUrl && responseUrl !== window.location.href) {
      if (historyMode === "replace") {
        window.history.replaceState({}, "", responseUrl);
      } else if (historyMode === "push") {
        window.history.pushState({}, "", responseUrl);
      }
    }

    document.open();
    document.write(parsed.htmlText);
    document.close();
  }

  function updateDomFromParsed(parsed, responseUrl, historyMode) {
    const root = document.querySelector(rootSelector);
    if (!root || !parsed.root) {
      replaceDocument(parsed, responseUrl, historyMode);
      return false;
    }

    const focusState = captureFocusState(root);
    root.innerHTML = parsed.root.innerHTML;
    if (parsed.title) {
      document.title = parsed.title;
    }

    const currentPageTitle = document.querySelector(pageTitleSelector);
    if (currentPageTitle && parsed.pageTitle) {
      currentPageTitle.innerHTML = parsed.pageTitle.innerHTML;
    }

    if (responseUrl && responseUrl !== window.location.href) {
      if (historyMode === "replace") {
        window.history.replaceState({}, "", responseUrl);
      } else if (historyMode === "push") {
        window.history.pushState({}, "", responseUrl);
      }
    }

    restoreFocusState(root, focusState);

    return true;
  }

  async function loadUrl(url, historyMode) {
    const response = await fetch(url, {
      method: "GET",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
    });

    const htmlText = await response.text();
    const parsed = parseRootFromHtml(htmlText);
    const ok = updateDomFromParsed(parsed, response.url || url, historyMode);
    if (!ok) {
      return;
    }
    bindLiveForms();
  }

  async function refreshCurrentView(nextUrl) {
    if (isRefreshing) {
      return;
    }
    isRefreshing = true;
    try {
      await loadUrl(nextUrl || window.location.href, "replace");
    } catch (_err) {
      window.location.reload();
    } finally {
      isRefreshing = false;
    }
  }

  async function submitFormAjax(form) {
    const formAction = form.getAttribute("action") || window.location.href;
    const formMethod = (form.getAttribute("method") || "POST").toUpperCase();

    const response = await fetch(formAction, {
      method: formMethod,
      body: new FormData(form),
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
    });

    const htmlText = await response.text();
    const parsed = parseRootFromHtml(htmlText);
    const ok = updateDomFromParsed(parsed, response.url || formAction, "push");
    if (!ok) {
      return;
    }
    bindLiveForms();
  }

  function toAbsoluteUrl(url) {
    return new URL(url, window.location.href);
  }

  function shouldHandleLink(anchor, event) {
    if (!anchor) {
      return false;
    }
    if (event.defaultPrevented) {
      return false;
    }
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return false;
    }
    if (anchor.hasAttribute("download")) {
      return false;
    }
    if ((anchor.getAttribute("target") || "").toLowerCase() === "_blank") {
      return false;
    }

    const href = anchor.getAttribute("href");
    if (!href || href.startsWith("#")) {
      return false;
    }

    const url = toAbsoluteUrl(href);
    if (url.origin !== window.location.origin) {
      return false;
    }
    return true;
  }

  async function submitGetFormAjax(form) {
    const action = form.getAttribute("action") || window.location.href;
    const methodUrl = toAbsoluteUrl(action);
    const params = new URLSearchParams(new FormData(form));
    methodUrl.search = params.toString();
    await loadUrl(methodUrl.toString(), "push");
  }

  function renderHistoryRows(historyBody, rows) {
    historyBody.innerHTML = "";
    if (!rows.length) {
      historyBody.innerHTML = '<tr><td colspan="3">No history entries found.</td></tr>';
      return;
    }

    rows.forEach(function (entry) {
      const tr = document.createElement("tr");

      const whenTd = document.createElement("td");
      whenTd.textContent = entry.assigned_at || "-";
      tr.appendChild(whenTd);

      const valueTd = document.createElement("td");
      valueTd.textContent = entry.value === null ? "-" : String(entry.value);
      tr.appendChild(valueTd);

      const stateTd = document.createElement("td");
      stateTd.textContent = entry.assigned ? "Assigned" : "Removed";
      tr.appendChild(stateTd);

      historyBody.appendChild(tr);
    });
  }

  function showHistoryError(historyBody, message) {
    historyBody.innerHTML = "";
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 3;
    td.textContent = message;
    tr.appendChild(td);
    historyBody.appendChild(tr);
  }

  async function loadAssetAttributeHistory(button) {
    const card = document.getElementById("historyCard");
    const title = document.getElementById("historyTitle");
    const body = document.getElementById("historyBody");
    if (!card || !title || !body) {
      return;
    }

    const assetRef = button.getAttribute("data-asset-ref");
    const attributeRef = button.getAttribute("data-attribute-ref");
    if (!assetRef || !attributeRef) {
      return;
    }

    const url = "/assets/" + encodeURIComponent(assetRef) + "/attributes/" + encodeURIComponent(attributeRef) + "/history/";
    card.hidden = false;
    title.textContent = "History · " + attributeRef;
    body.innerHTML = '<tr><td colspan="3">Loading history...</td></tr>';

    try {
      const response = await fetch(url, {
        method: "GET",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error("Failed to load history");
      }
      const payload = await response.json();
      renderHistoryRows(body, Array.isArray(payload.history) ? payload.history : []);
    } catch (_err) {
      showHistoryError(body, "Unable to load history");
    }
  }

  function bindLiveForms() {
    const forms = document.querySelectorAll("form:not([data-no-live])");
    forms.forEach((form) => {
      if (form.dataset.liveBound === "1") {
        return;
      }
      form.dataset.liveBound = "1";
      form.addEventListener("submit", async (event) => {
        if (form.dataset.modalId) {
          return;
        }

        const method = (form.getAttribute("method") || "GET").toUpperCase();
        event.preventDefault();
        if (method === "GET") {
          await submitGetFormAjax(form);
          return;
        }
        await submitFormAjax(form);
      });
    });
  }

  function scheduleAutoSubmit(form) {
    if (!form) {
      return;
    }
    window.clearTimeout(autoSubmitTimer);
    autoSubmitTimer = window.setTimeout(function () {
      if (!form.isConnected) {
        return;
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      }
    }, 250);
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${protocol}://${window.location.host}/ws/updates/`;
    let socket;

    function establish() {
      socket = new WebSocket(wsUrl);
      socket.onmessage = function () {
        refreshCurrentView();
      };
      socket.onclose = function () {
        window.setTimeout(establish, 1200);
      };
    }

    establish();
  }

  document.addEventListener("click", async function (event) {
    const sortButton = event.target.closest(".js-sort-trigger");
    if (sortButton) {
      event.preventDefault();
      const formId = sortButton.getAttribute("data-sort-form");
      const sortField = sortButton.getAttribute("data-sort-field");
      const sortDir = sortButton.getAttribute("data-sort-dir");
      const form = formId ? document.getElementById(formId) : null;
      if (!form) {
        return;
      }
      const sortByInput = form.querySelector("input[name='sort_by']");
      const sortDirInput = form.querySelector("input[name='sort_dir']");
      if (sortByInput) {
        sortByInput.value = sortField || "";
      }
      if (sortDirInput) {
        sortDirInput.value = sortDir || "desc";
      }
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      }
      return;
    }

    const historyButton = event.target.closest(".js-history-btn");
    if (historyButton) {
      event.preventDefault();
      await loadAssetAttributeHistory(historyButton);
      return;
    }

    const historyClose = event.target.closest("#historyClose");
    if (historyClose) {
      const card = document.getElementById("historyCard");
      const body = document.getElementById("historyBody");
      if (card) {
        card.hidden = true;
      }
      if (body) {
        body.innerHTML = "";
      }
      return;
    }

    const anchor = event.target.closest("a[href]");
    if (!shouldHandleLink(anchor, event)) {
      return;
    }

    event.preventDefault();
    try {
      await loadUrl(toAbsoluteUrl(anchor.getAttribute("href")).toString(), "push");
    } catch (_err) {
      window.location.href = anchor.href;
    }
  });

  document.addEventListener("input", function (event) {
    const target = event.target;
    const form = target && target.form;
    if (!form || !form.hasAttribute("data-auto-submit")) {
      return;
    }
    if (target.matches("input[type='text'], input[type='search'], input[type='number'], textarea")) {
      scheduleAutoSubmit(form);
    }
  });

  document.addEventListener("change", function (event) {
    const target = event.target;
    const form = target && target.form;
    if (!form || !form.hasAttribute("data-auto-submit")) {
      return;
    }
    scheduleAutoSubmit(form);
  });

  window.addEventListener("popstate", function () {
    refreshCurrentView(window.location.href);
  });

  bindLiveForms();
  connectWebSocket();
})();
