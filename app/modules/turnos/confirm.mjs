/* Confirmation-page controller.  It is intentionally mounted by the
   server-rendered bootstrap: email-link scanners only GET this page.
   Automatic changes require affirmative intent; captured patient clicks may
   explicitly confirm or decline. */

export function mount(options) {
  options = options || {};
  var doc = options.document || document;
  var token = options.token || "";
  var controls = options.controls || Array.prototype.slice.call(doc.querySelectorAll("[data-confirm-action]"));
  var msg = options.message || doc.getElementById("confirm-msg");
  var destroyed = false;
  var busy = false;
  var I18n = null;
  var readinessTimer = null;
  var finishReadiness = null;
  var expiryTimer = null;
  var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
    ? new Promise(function (settle) {
      var settled = false;
      finishReadiness = function (module) {
        if (settled) return;
        settled = true;
        clearTimeout(readinessTimer);
        if (!destroyed) I18n = module;
        settle();
      };
      readinessTimer = setTimeout(function () { finishReadiness(null); }, 8000);
      import(window.SANA_STATIC["platform/i18n.mjs"]).then(finishReadiness, function () { finishReadiness(null); });
    })
    : Promise.resolve(null);

  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  function setMsg(text, isError) {
    if (!msg) return;
    msg.textContent = text;
    msg.style.color = isError ? "#a3352f" : "var(--text-2)";
  }
  function lock() {
    controls.forEach(function (el) { el.disabled = true; el.style.pointerEvents = "none"; el.style.opacity = "0.6"; });
  }
  function unlock() {
    controls.forEach(function (el) { el.disabled = false; el.style.pointerEvents = ""; el.style.opacity = ""; });
  }
  function resolve(action) {
    if (destroyed || busy || !token) return Promise.resolve();
    busy = true;
    lock();
    setMsg(T("wizard.confirm.wait", null, "Un momento…"));
    return i18nReady.then(function () {
      if (destroyed) return;
      return fetch("/api/confirm/" + encodeURIComponent(token) + "/" + action, {
      method: "POST", credentials: "same-origin"
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (json) {
        if (destroyed) return;
        if (response.ok && json.ok) {
          setMsg(json.mensaje || T("wizard.confirm.saved", null, "Listo, gracias por avisar."));
          controls.forEach(function (el) { el.style.display = "none"; });
        } else if (json && json.outcome === "unknown" && json.detalle) {
          // The database broke under the save: the answer may have landed, so
          // no «probá de nuevo» and the buttons stay off — «Mis turnos» shows it.
          setMsg(json.detalle, true);
          var salida = json.accion;
          if (msg && salida && salida.texto && /^\/(?!\/)/.test(String(salida.url || ""))) {
            var link = doc.createElement("a");
            link.className = "stz-link";
            link.href = salida.url;
            link.textContent = salida.texto;
            msg.textContent = json.detalle + " ";
            msg.appendChild(link);
          }
        } else if (response.status === 410) {
          setMsg(T("wizard.confirm.expired", null, "Ese turno ya no se puede confirmar."), true);
          expiryTimer = setTimeout(function () { if (!destroyed) location.reload(); }, 1600);
        } else {
          busy = false;
          setMsg(T("wizard.confirm.error", null, "No pudimos guardar tu respuesta. Probá de nuevo."), true);
          unlock();
        }
      });
    });
    }).catch(function () {
      if (destroyed) return;
      busy = false;
      setMsg(T("wizard.confirm.error", null, "No pudimos guardar tu respuesta. Probá de nuevo."), true);
      unlock();
    });
  }
  controls.forEach(function (el) {
    el.addEventListener("click", function (event) {
      event.preventDefault();
      var action = el.getAttribute("data-confirm-action") === "no" ? "no" : "si";
      resolve(action);
    });
  });
  // A real queued click and a URL's auto flag are different authorities.
  if (options.initialAction === "si" || options.initialAction === "no") resolve(options.initialAction);
  else if (options.autoAction === "si") resolve("si");
  return {
    resolve: function (action) { return resolve(action === "no" ? "no" : "si"); },
    destroy: function () {
      destroyed = true;
      clearTimeout(expiryTimer);
      if (finishReadiness) finishReadiness(null);
    }
  };
}
