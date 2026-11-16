/* admin-shell.js — the panel's transient toast provider, extracted 2026-08-31
   from admin-app.js so the implementation lives ONCE for BOTH admin shells:
   the legacy /admin and every server-routed /admin/<screen> rendered off
   templates/admin/base.html. Loads right after i18n.js in both.

   The node is created lazily on first use (the legacy shell used to carry a
   static one; the direct screens never did — their toast calls silently did
   nothing). Same attributes the static node carried: role=status +
   aria-live=polite + aria-atomic=true — the toast is the only confirmation
   most panel actions give, and without role=status a screen reader never
   announced it (the hospital's appointments clerk is blind, reunión
   2026-07-15). One deliberate fix over the extraction: a second toast within
   2,6 s no longer gets cut short by the first one's timer. */
(function () {
  "use strict";
  var hideTimer = null;

  function moduleError(name, error) {
    var host = document.querySelector(".adm-content-wrap") ||
      document.querySelector("main") || document.body;
    var node = document.getElementById("admin-module-load-error");
    if (!node) {
      node = document.createElement("p");
      node.id = "admin-module-load-error";
      node.className = "adm-empty";
      node.setAttribute("role", "alert");
      host.appendChild(node);
    }
    node.textContent = "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.";
    if (typeof console !== "undefined" && console.error) {
      console.error("[admin-module] " + name, error);
    }
  }

  window.sanaImportStatic = function (name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    if (!url) {
      var missing = new Error(name + " is not present in window.SANA_STATIC");
      moduleError(name, missing);
      return Promise.reject(missing);
    }
    return import(url).catch(function (error) {
      // The portal gateway ended the session: the page is going to the portal
      // sign-in and back, so neither an error line nor a rejection (core/static_recover.js).
      var recover = window.sanaRecoverStatic ? window.sanaRecoverStatic() : Promise.resolve(false);
      return recover.then(function (leaving) {
        if (leaving) return new Promise(function () {});
        moduleError(name, error);
        throw error;
      });
    });
  };

  function nodo() {
    var t = document.getElementById("toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "toast";
      t.id = "toast";
      t.setAttribute("role", "status");
      t.setAttribute("aria-live", "polite");
      t.setAttribute("aria-atomic", "true");
      document.body.appendChild(t);
    }
    return t;
  }

  window.toast = function (msg) {
    var t = nodo();
    t.textContent = msg;
    t.classList.add("on");
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { t.classList.remove("on"); }, 2600);
  };
})();
