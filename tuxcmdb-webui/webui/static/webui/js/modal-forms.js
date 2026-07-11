(function () {
  "use strict";

  // ── Open / close ──────────────────────────────────────────────────────────

  function openModal(id) {
    var el = document.getElementById(id);
    if (el) el.removeAttribute("hidden");
  }

  function closeModal(id) {
    var el = document.getElementById(id);
    if (el) {
      el.setAttribute("hidden", "");
      // Reset any error message inside the modal
      el.querySelectorAll(".modal-form-error").forEach(function (e) {
        e.textContent = "";
        e.hidden = true;
      });
    }
  }

  // Trigger buttons: <button data-open-modal="myModalId">
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-open-modal]");
    if (btn) openModal(btn.dataset.openModal);
  });

  // Close buttons: <button data-close-modal="myModalId"> and clicking the backdrop
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-close-modal]");
    if (btn) {
      closeModal(btn.dataset.closeModal);
      return;
    }
    // Clicking the overlay backdrop itself
    if (e.target.classList.contains("tuxcmdb-modal-overlay")) {
      closeModal(e.target.id);
    }
  });

  // Escape key closes any open modal
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".tuxcmdb-modal-overlay:not([hidden])").forEach(function (m) {
        closeModal(m.id);
      });
    }
  });

  // ── Form submission ───────────────────────────────────────────────────────
  // Forms inside modals must have: data-modal-id="<overlay id>"

  document.addEventListener("submit", async function (e) {
    var form = e.target;
    var modalId = form.dataset.modalId;
    if (!modalId) return;          // not a modal form – let live-updates handle it
    e.preventDefault();
    e.stopPropagation();           // prevent live-updates from also catching it

    var errorEl = form.querySelector(".modal-form-error");

    var submitBtn = form.querySelector("[type=submit]");
    if (submitBtn) submitBtn.disabled = true;

    try {
      var formAction = form.getAttribute("action") || window.location.href;
      var formMethod = (form.getAttribute("method") || "POST").toUpperCase();

      var resp = await fetch(formAction, {
        method: formMethod,
        body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });

      var html = await resp.text();
      var doc = new DOMParser().parseFromString(html, "text/html");

      // Success detection: no .alert-danger present in the response
      var hasError = doc.querySelector(".alert-danger, .alert-error");

      if (!hasError) {
        closeModal(modalId);
        // Refresh #live-root with the returned page content
        var newRoot = doc.querySelector("#live-root");
        var root = document.querySelector("#live-root");
        if (root && newRoot) {
          root.innerHTML = newRoot.innerHTML;
        }
        // Reset the form for next use
        form.reset();
      } else {
        if (errorEl) {
          errorEl.textContent = hasError.textContent.trim();
          errorEl.hidden = false;
        }
      }
    } catch (_err) {
      if (errorEl) {
        errorEl.textContent = "Request failed. Please try again.";
        errorEl.hidden = false;
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }, true);   // capture phase so we run before live-updates.js bubble phase

})();
