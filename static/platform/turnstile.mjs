// platform/turnstile.mjs — el widget de Cloudflare Turnstile, un solo widget por página.
//
// Módulo ES canónico: antes era el UMD turnstile.js con global de navegador
// (window.SanaTurnstile); ese archivo se borró y éste es el dueño único del
// widget. Los consumidores (service.js, upload-order.js) lo importan por su
// namespace desde window.SANA_STATIC — no publica ningún contrato global.
//
// Invariante (docs/solutions/ai/turnstile-mata-el-analisis-de-orden.md): si un
// endpoint está detrás de abuso.py (/api/analyze-order, /api/preparacion) o de
// accounts.py (/api/account/login), el cliente que lo llama TIENE que montar el
// widget y mandar el token. Un endpoint gateado + un cliente que no manda token
// = feature muerta en producción que ningún test ve.
//
//   mount(box)   -> Promise<bool>  renderiza el widget en `box` (lo esconde y
//                                   resuelve false si no hay gate: sin secret, o
//                                   host de dev — el server sirve
//                                   turnstile_sitekey:null en GET /api/account/config)
//   token(ms)    -> Promise<string> token del widget; espera hasta `ms`
//                                   (default 8000) a que resuelva. "" si no hay gate.
//   activo()     -> bool  ¿hay gate en esta página?
//   reset()      el token es de un solo uso: resetear entre intentos.
//   config()     -> Promise<object> GET /api/account/config, cacheada.
//
// Un solo widget por página (estos flujos nunca muestran dos a la vez).
// Cero acceso a `window`/`document` a nivel de módulo — sólo dentro de las
// funciones — así el módulo se puede importar y testear en Node sin más.

var cfgPromise = null;
var sitekey = null;   // null = sin gate (el server no lo sirve)
var mountedBox = null;
var mountReady = Promise.resolve(false);
var mountGeneration = 0;
var widget = null;    // id del render explícito

export function config() {
  if (!cfgPromise) {
    cfgPromise = fetch("/api/account/config", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : {}; })
      .catch(function () { return {}; });
  }
  return cfgPromise;
}

var PROVIDER_LOAD_TIMEOUT_MS = 8000;

function loadScript() {
  if (window.turnstile && window.turnstile.render) return Promise.resolve(true);
  return new Promise(function (resolve) {
    var el = document.getElementById("cf-turnstile-script");
    if (el && el.getAttribute("data-load-failed") === "1") {
      if (el.parentNode) el.parentNode.removeChild(el);
      el = null;
    }
    var created = false;
    if (!el) {
      el = document.createElement("script");
      el.id = "cf-turnstile-script";
      el.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      el.async = true;
      el.defer = true;
      created = true;
    }
    var settled = false;
    var pollTimer = null;
    var timeoutTimer = null;
    function finish(ok) {
      if (settled) return;
      settled = true;
      if (pollTimer !== null) clearTimeout(pollTimer);
      if (timeoutTimer !== null) clearTimeout(timeoutTimer);
      el.removeEventListener("error", failed);
      if (!ok) el.setAttribute("data-load-failed", "1");
      resolve(ok);
    }
    function failed() { finish(false); }
    function poll() {
      if (settled) return;
      if (window.turnstile && window.turnstile.render) { finish(true); return; }
      pollTimer = setTimeout(poll, 200);
    }
    el.addEventListener("error", failed, { once: true });
    timeoutTimer = setTimeout(failed, PROVIDER_LOAD_TIMEOUT_MS);
    if (created) document.head.appendChild(el);
    poll();
  });
}

export function mount(box, suppliedConfig) {
  var generation = ++mountGeneration;
  mountReady = Promise.resolve(suppliedConfig || config()).then(function (cfg) {
    if (generation !== mountGeneration) return false;
    sitekey = (cfg && cfg.turnstile_sitekey) || null;
    if (!box) return false;
    if (!sitekey) {
      box.style.display = "none";
      if (widget !== null && window.turnstile && window.turnstile.remove) window.turnstile.remove(widget);
      widget = null; mountedBox = null;
      return false;
    }
    box.style.display = "";
    return loadScript().then(function (loaded) {
      if (!loaded || !box.isConnected || generation !== mountGeneration) return false;  // proveedor caído o paso re-renderizado
      try {
        /* interaction-only: invisible salvo que Cloudflare exija interacción
           real (responsable del producto, 2026-07-21 — nada de "Success!" a la vista). */
        if (widget !== null && mountedBox === box) return true;
        if (widget !== null && window.turnstile.remove) window.turnstile.remove(widget);
        mountedBox = box;
        widget = window.turnstile.render(box, { sitekey: sitekey, theme: "auto", appearance: "interaction-only" });
        return true;
      } catch (e) { return false; }
    });
  });
  return mountReady;
}

function raw() {
  if (!sitekey || !window.turnstile || widget === null) return "";
  try { return window.turnstile.getResponse(widget) || ""; } catch (e) { return ""; }
}

/* El widget tarda ~1s en resolver: si la persona sube el archivo apenas carga
   la pantalla, el token todavía no está. Esperarlo es mejor que mandar "" y
   comerse un 403. */
export function token(ms) {
  if (!sitekey) return Promise.resolve("");
  var limite = (typeof ms === "number" ? ms : 8000);
  var t0 = Date.now();
  return new Promise(function (resolve) {
    (function poll() {
      var t = raw();
      if (t) { resolve(t); return; }
      if (Date.now() - t0 >= limite) { resolve(""); return; }
      setTimeout(poll, 200);
    })();
  });
}

export function reset() {
  if (widget !== null && window.turnstile) {
    try { window.turnstile.reset(widget); } catch (e) {}
  }
}

export function activo() { return !!sitekey; }

// Submit callers share the same bounded wait and never send an empty gated
// token. The provider owns visibility, including interactive escalation.
export async function requireToken(options) {
  options = options || {};
  var timer = options.onWait ? setTimeout(options.onWait, 600) : null;
  try {
    await mountReady;
    var value = await token(options.timeout);
    if (activo() && !value) throw new Error("turnstile");
    return value;
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}
