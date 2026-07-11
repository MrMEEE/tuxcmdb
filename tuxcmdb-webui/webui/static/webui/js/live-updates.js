(function () {
  "use strict";

  const rootSelector = "#live-root";
  let isRefreshing = false;

  function parseRootFromHtml(htmlText) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(htmlText, "text/html");
    return {
      root: doc.querySelector(rootSelector),
      title: doc.title,
    };
  }

  async function refreshCurrentView(nextUrl) {
    if (isRefreshing) {
      return;
    }
    isRefreshing = true;
    try {
      const response = await fetch(nextUrl || window.location.href, {
        method: "GET",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });

      const htmlText = await response.text();
      const parsed = parseRootFromHtml(htmlText);
      const root = document.querySelector(rootSelector);
      if (!root || !parsed.root) {
        window.location.href = response.url || window.location.href;
        return;
      }

      root.innerHTML = parsed.root.innerHTML;
      if (parsed.title) {
        document.title = parsed.title;
      }
      if (response.url && response.url !== window.location.href) {
        window.history.replaceState({}, "", response.url);
      }
      bindLiveForms();
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
    const root = document.querySelector(rootSelector);
    if (!root || !parsed.root) {
      window.location.href = response.url || window.location.href;
      return;
    }

    root.innerHTML = parsed.root.innerHTML;
    if (parsed.title) {
      document.title = parsed.title;
    }
    if (response.url && response.url !== window.location.href) {
      window.history.pushState({}, "", response.url);
    }
    bindLiveForms();
  }

  function bindLiveForms() {
    const forms = document.querySelectorAll("form[method='post']:not([data-no-live])");
    forms.forEach((form) => {
      if (form.dataset.liveBound === "1") {
        return;
      }
      form.dataset.liveBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitFormAjax(form);
      });
    });
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

  window.addEventListener("popstate", function () {
    refreshCurrentView(window.location.href);
  });

  bindLiveForms();
  connectWebSocket();
})();
