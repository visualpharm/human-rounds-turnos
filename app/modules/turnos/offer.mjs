/* Waitlist-offer controller. Mounted by the server-rendered offer bootstrap;
   an email-link GET never changes an appointment on its own. */

export function mount(options) {
  options = options || {};
  var doc = options.document || document;
  var token = options.token || "";
  var accept = options.accept || doc.getElementById("oferta-aceptar");
  var reject = options.reject || doc.getElementById("oferta-rechazar");
  var controls = [accept, reject].filter(Boolean);
  var msg = options.message || doc.getElementById("oferta-msg");
  var destroyed = false;
  var busy = false;
  var I18n = null;
  var readinessTimer = null;
  var finishReadiness = null;
  var redirectTimer = null;
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
    : Promise.resolve();

  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  function setMsg(text, isError) {
    if (!msg) return;
    msg.textContent = text;
    msg.style.color = isError ? "#a3352f" : "var(--text-2)";
  }
  function lock() {
    if (accept) { accept.disabled = true; accept.style.opacity = "0.6"; }
    if (reject) { reject.style.pointerEvents = "none"; reject.style.opacity = "0.6"; }
  }
  function unlock() {
    if (accept) { accept.disabled = false; accept.style.opacity = ""; }
    if (reject) { reject.style.pointerEvents = ""; reject.style.opacity = ""; }
  }
  function after(delay, callback) {
    redirectTimer = setTimeout(function () { if (!destroyed) callback(); }, delay);
  }
  function retryError() {
    if (destroyed) return;
    busy = false;
    setMsg(T("wizard.offer.error", null, "No pudimos procesar tu respuesta. Probá de nuevo."), true);
    unlock();
  }
  function resolve(action) {
    if (destroyed || busy || !token || (action !== "accept" && action !== "reject")) return Promise.resolve();
    busy = true;
    lock();
    setMsg(T("wizard.offer.wait", null, "Un momento…"));
    return i18nReady.then(function () {
      if (destroyed) return;
      return fetch("/api/offer/" + encodeURIComponent(token) + "/" + action, {
        method: "POST", credentials: "same-origin"
      }).then(function (response) {
        return response.json().then(function (json) {
          return { response: response, json: json };
        });
      }).then(function (result) {
        if (destroyed) return;
        var response = result.response;
        var json = result.json;
        var knownUnavailable = response.status === 410;
        if (json && json.outcome === "unknown" && json.detalle) {
          // The database broke under the save: the answer may have landed, so
          // no «probá de nuevo» and the buttons stay off (a second accept
          // would read «ya no está disponible») — the way to check is «Mis turnos».
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
          return;
        }
        if (response.status >= 500 || (!response.ok && !knownUnavailable) ||
            (response.ok && (!json || typeof json !== "object" || Array.isArray(json) ||
              typeof json.ok !== "boolean"))) {
          retryError();
          return;
        }
        var ok = response.ok && json.ok === true;
        if (ok) {
          setMsg(action === "accept"
            ? T("wizard.offer.accepted", null, "¡Listo! Tu turno quedó confirmado. Te llevamos a Mis turnos…")
            : T("wizard.offer.rejected", null, "Avisamos a la siguiente persona de la lista. Gracias por responder."));
          after(1400, function () { location.href = "/my-requests"; });
        } else if (response.status === 410) {
          setMsg(T("wizard.offer.gone", null, "Este turno ya no está disponible."), true);
          after(1400, action === "accept"
            ? function () { location.reload(); }
            : function () { location.href = "/my-requests"; });
        } else if (action === "reject") {
          setMsg(T("wizard.offer.gone", null, "Este turno ya no está disponible."), true);
          after(1400, function () { location.href = "/my-requests"; });
        } else {
          setMsg(T("wizard.offer.gone", null, "Este turno ya no está disponible."), true);
          after(1400, function () { location.reload(); });
        }
      });
    }).catch(retryError);
  }
  if (accept) accept.addEventListener("click", function (event) { event.preventDefault(); resolve("accept"); });
  if (reject) reject.addEventListener("click", function (event) { event.preventDefault(); resolve("reject"); });
  if (options.initialAction === "accept" || options.initialAction === "reject") resolve(options.initialAction);
  return {
    resolve: resolve,
    destroy: function () {
      destroyed = true;
      clearTimeout(redirectTimer);
      if (finishReadiness) finishReadiness(null);
    }
  };
}
