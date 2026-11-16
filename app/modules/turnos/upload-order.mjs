/* modules/turnos/upload-order.mjs — "Subí tu orden".
   Canonical ES module owned by modules/turnos. One named idempotent boot().
   No window.WzUpload, no classic twin. Direct native upload imports.
   El asistente completo (ampliado 2026-07-16): subís la orden y usamos el
   MISMO endpoint que el wizard de turnos (POST /api/analyze-order,
   booking_requests.py — service.js:analyze() es el contrato de referencia) en vez
   del viejo /api/preparacion (muerto). El análisis guarda un `token` server-
   side; lo llevamos en sessionStorage (st_order_pending) para que el wizard
   de turnos arranque con la orden YA cargada (?order=<token>) — nunca hay que
   volver a subirla.

   Un paso real (wizard-design): subir la orden. El resultado REEMPLAZA la
   pantalla (nunca se apila). En esta herramienta de preparación, las
   indicaciones concretas van primero y los turnos después; si no hay una
   preparación reconocida, el resultado empieza por el turno. La tabla usa
   listing.js con el MISMO row-builder que la home (Search.servRow).
   Mismo control de subida que
   "Orden de derivación" (wz-upload.js
   — wizard-design regla 6: un solo control, un solo look). */
import { css as uploadCss, zoneHtml, wire as wireUploadControl, fileToOrden, analyzeHeaders } from "./wz-upload.mjs";

var booted = false;
var pendingUploadFile = null;
export function boot(opts) {
  if (booted) return;
  booted = true;
  opts = opts || {};
  pendingUploadFile = (opts && opts.pendingFile) || null;
  function takePendingUploadFile() {
    var queued = typeof opts.takePendingFile === "function"
      ? opts.takePendingFile()
      : pendingUploadFile;
    pendingUploadFile = null;
    return queued;
  }
  function uploadUnavailable() {
    pendingUploadFile = null;
    if (typeof opts.uploadUnavailable === "function") opts.uploadUnavailable();
  }
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.resolve(null);
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function staticUrl(name) {
    var m = (typeof window !== "undefined") && window.SANA_STATIC;
    var u = m && m[name];
    if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
    return u || null;
  }
  function importStatic(name) {
    var u = staticUrl(name);
    return u ? import(u) : Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
  }
  var Prep = null;
  var prepReady = importStatic("platform/prep.mjs").then(function (m) { Prep = m; });
  var Search = null;
  var searchReady = importStatic("platform/search.mjs").then(function (m) { Search = m; })
    .catch(function () { Search = null; return null; });
  var Listing = null;
  var listingReady = importStatic("platform/listing.mjs").then(function (m) { Listing = m; })
    .catch(function () { Listing = null; return null; });
  /* Cloudflare Turnstile — widget compartido (turnstile.mjs, canónico desde
     el cutover físico). Un endpoint gateado por abuso.py sin cliente que
     monte el widget = 403 silencioso en TODA subida
     (docs/solutions/ai/turnstile-mata-el-analisis-de-orden.md): invariante,
     no relitigar. La promesa arranca con la evaluación de este script y el
     primer paso la espera; si el import falla, TS queda null y el POST viaja
     sin token — el camino fail-open que ya tomaba el script defer. */
  var TS = null;
  var tsLista = importStatic("platform/turnstile.mjs").then(function (m) { TS = m; })
    .catch(function () { /* fail-open: sin widget no hay token */ });

  var raw = $("#prep-data");
  if (!raw) { uploadUnavailable(); return; }
  var data = {};
  try { data = JSON.parse(raw.textContent); } catch (e) { uploadUnavailable(); return; }
  var servicioId = data.servicio_id || null;
  var host = $("#prep-host");
  if (!host) { uploadUnavailable(); return; }
  var titleEl = $("#prep-title");
  var subEl = $("#prep-sub");
  var initialTitle = titleEl ? titleEl.textContent : T("page.wizard.upload_order", null, "Subí tu orden");
  var initialSub = subEl ? subEl.textContent : "";
  var pageBrand = document.documentElement.getAttribute("data-theme") === "pinamar"
    ? "Turnos de salud pública en Pinamar" : "Human Rounds";
  function setPage(title, sub) {
    if (titleEl) titleEl.textContent = title || "";
    if (subEl) {
      subEl.textContent = sub || "";
      subEl.hidden = !sub;
    }
    document.title = (title || pageBrand) + " | " + pageBrand;
  }

  /* ------------------------------------------------------------------ *
   *  Estilos (mismo lenguaje visual que servicio.js: wz-cta-lg/wz-spin/ *
   *  wz-warn/wz-err — así "Orden de derivación" y esto se ven iguales) *
   * ------------------------------------------------------------------ */
  var CSS_ID = "prep-style";
  function injectStyle() {
    if (document.getElementById(CSS_ID)) return;
    var css =
      uploadCss +
      ".wz-lead{font-size:19px;line-height:1.5;color:var(--text);margin:0 0 20px;}" +
      ".wz-btnstack{display:flex;flex-direction:column;gap:12px;margin-top:24px;}" +
      ".wz-cta-lg{display:flex;align-items:center;justify-content:center;width:100%;min-height:62px;" +
        "padding:16px 20px;border:0;border-radius:var(--btn-radius,15px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);" +
        "font:700 20px var(--font);letter-spacing:-.01em;cursor:pointer;text-decoration:none;" +
        "transition:filter .12s,transform .1s,background .12s;}" +
      ".wz-cta-lg:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);} .wz-cta-lg:active{transform:translateY(1px);}" +
      ".wz-cta-sec{display:flex;align-items:center;justify-content:center;width:100%;min-height:56px;" +
        "padding:14px 20px;border:1px solid var(--btn2-bd,var(--border-strong));border-radius:var(--btn-radius,15px);background:var(--btn2-bg,var(--panel));" +
        "color:var(--btn2-fg,var(--text));font:700 18px var(--font);cursor:pointer;text-decoration:none;" +
        "transition:background .12s;}" +
      ".wz-cta-sec:hover{background:var(--btn2-bg-hover,var(--hover-bg));}" +
      ".wz-warn{background:#f7f0df;border:1px solid #ecdfbd;border-radius:12px;padding:14px 16px;margin:0 0 14px;font-size:16px;line-height:1.5;color:#7a5a12;}" +
      ".wz-warn b{color:#5f4708;}" +
      ".wz-soft{background:var(--bg);border:1px solid var(--border);border-radius:11px;padding:12px 14px;margin:14px 0 0;font-size:16px;line-height:1.5;color:var(--text-2);}" +
      ".wz-err{display:grid;gap:16px;margin-top:4px;}" +
      ".wz-err-img-btn{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;justify-self:start;}" +
      ".wz-err-img{display:block;width:100%;max-width:100%;max-height:400px;object-fit:contain;" +
        "border-radius:12px;border:1px solid var(--border);background:var(--bg-2);}" +
      "@media(min-width:640px){.wz-err{grid-template-columns:minmax(0,1fr) 300px;align-items:start;}" +
        ".wz-err-img-btn{grid-column:2;grid-row:1/span 2;}.wz-err-img{max-height:520px;}" +
        ".wz-err .wz-warn,.wz-err-act{grid-column:1;}}" +
      ".wz-spin{display:flex;align-items:center;gap:11px;color:var(--text-2);font-size:14px;padding:22px 4px;}" +
      ".wz-spin i{width:18px;height:18px;border:2.5px solid var(--border-strong);border-top-color:var(--accent);border-radius:50%;display:inline-block;animation:wzspin .8s linear infinite;}" +
      "@keyframes wzspin{to{transform:rotate(360deg)}}" +
      ".prep-reading{display:flex;flex-direction:column;align-items:center;text-align:center;padding:34px 18px 38px;}" +
      ".prep-reading-animation{width:112px;height:112px;}" +
      ".prep-reading-title{margin-top:8px;font-size:24px;font-weight:750;letter-spacing:-.02em;color:var(--text);}" +
      ".prep-reading-sub{max-width:390px;margin-top:7px;font-size:16px;line-height:1.45;color:var(--text-2);}" +
      ".wz-override{display:inline-block;margin-top:16px;padding:4px 0;border:0;background:none;color:var(--text-3);" +
        "font:500 14px/1.4 var(--font);cursor:pointer;}" +
      ".wz-override:hover{color:var(--text-2);}" +
      /* --- secciones del resultado: "Tu orden pide" / "Dónde sacar el turno"
         y preparación sólo cuando hay indicaciones reales del catálogo. --- */
      ".prep-sec{margin:0 0 26px;}" +
      ".prep-sec-h{font-size:19px;font-weight:700;color:var(--text);margin:0 0 12px;letter-spacing:-.01em;}" +
      /* --- resultado: una tarjeta por estudio matcheado --- */
      ".prep-cards{display:flex;flex-direction:column;gap:16px;}" +
      ".prep-card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px 20px 20px;}" +
      ".prep-h{font-size:19px;font-weight:700;color:var(--text);margin:0 0 12px;letter-spacing:-.01em;}" +
      ".prep-ayuno{font-size:16px;line-height:1.5;font-weight:600;border-radius:10px;padding:11px 14px;margin:0 0 12px;}" +
      ".prep-ayuno--si{color:#7a5410;background:#f7f0df;border:1px solid #ecdfbd;}" +
      ".prep-ayuno--no{color:var(--text-2);background:var(--bg);border:1px solid var(--border);}" +
      ".prep-resumen{font-size:17px;line-height:1.5;color:var(--text);margin:0 0 12px;}" +
      ".prep-nota{font-size:16px;line-height:1.5;color:var(--text);margin:0 0 12px;}" +
      ".prep-block{margin:24px 0 0;}" +
      ".prep-sub{font-size:18px;line-height:1.3;font-weight:700;color:var(--text);margin:0 0 9px;letter-spacing:-.01em;}" +
      ".prep-single{font-size:16px;line-height:1.5;color:var(--text);margin:0;}" +
      ".prep-single--no{color:var(--text-2);}" +
      ".prep-bullets{margin:0;padding:0;list-style:none;}" +
      ".prep-bullets li{font-size:16px;line-height:1.5;color:var(--text);margin:0 0 7px;padding-left:17px;position:relative;}" +
      ".prep-bullets li:last-child{margin-bottom:0;}" +
      ".prep-bullets li:before{content:'';position:absolute;left:3px;top:9px;width:6px;height:6px;border-radius:50%;background:var(--accent);}" +
      /* "No hace falta" son cosas que NO hay que hacer: raya en vez de punto y
         texto más callado, para que no se lean como una instrucción más. */
      ".prep-bullets--no li{color:var(--text-2);}" +
      ".prep-bullets--no li:before{top:12px;left:1px;width:10px;height:2px;border-radius:1px;background:var(--text-3);}" +
      ".prep-bullets--aviso li:before{background:#b07d18;}" +
      ".prep-fuentes{margin-top:14px;color:var(--text-3);}" +
      ".prep-fuentes summary{width:max-content;max-width:100%;font-size:14px;font-weight:650;color:var(--text-3);cursor:pointer;}" +
      ".prep-fuentes[open] summary{margin-bottom:8px;}" +
      ".prep-fuentes a{display:block;font-size:13px;line-height:1.45;color:var(--text-3);margin:0 0 5px;}" +
      ".prep-fuentes a:hover{color:var(--text-2);}" +
      ".prep-noreconocido{font-size:16px;line-height:1.5;color:var(--text-2);margin:0 0 10px;}" +
      ".prep-noreconocido:last-child{margin-bottom:0;}" +
      ".prep-links{margin-top:18px;display:flex;flex-direction:column;gap:8px;}" +
      ".prep-links a{font-size:15px;color:var(--link,var(--accent));}" +
      ".prep-links a:hover{color:var(--accent-hover);}";
    var el = document.createElement("style");
    el.id = CSS_ID;
    el.textContent = css;
    document.head.appendChild(el);
  }

  /* ------------------------------------------------------------------ *
   *  Catálogo de servicios/centros — para armar "Dónde sacar el turno" *
   *  con el MISMO row-builder que la home (Search.servRow). Se pide *
   *  en paralelo a la subida (no bloquea): para cuando el análisis      *
   *  vuelve, casi siempre ya está.                                     *
   * ------------------------------------------------------------------ */
  var SERV = null, CENT = {}, catalogoPromise = null;
  function loadCatalogo() {
    if (catalogoPromise) return catalogoPromise;
    catalogoPromise = Promise.all([
      fetch("/api/services").then(function (r) { return r.json(); }).catch(function () { return {}; }),
      fetch("/api/centers").then(function (r) { return r.json(); }).catch(function () { return {}; })
    ]).then(function (res) {
      SERV = (res[0] && res[0].servicios) || [];
      (((res[1] && res[1].centros) || [])).forEach(function (c) { CENT[c.slug] = c; });
      return SERV;
    }).catch(function () { SERV = []; return SERV; });
    return catalogoPromise;
  }

  /* ------------------------------------------------------------------ *
   *  Paso · Subir la orden                                              *
   * ------------------------------------------------------------------ */
  var state = { ordenPreview: null, ordenFilename: null, token: null, ai: null };
  var readingAnim = null;
  function stopReading() {
    if (readingAnim && typeof readingAnim.destroy === "function") readingAnim.destroy();
    readingAnim = null;
  }
  function showReading() {
    stopReading();
    host.innerHTML = '<div class="sec"><div class="prep-reading">' +
      '<div class="prep-reading-animation" id="prep-reading-animation" aria-hidden="true"></div>' +
      '<div class="prep-reading-title">' + esc(T("upload.reading.title", null, "Leyendo tu orden…")) + '</div>' +
      '<div class="prep-reading-sub">' + esc(T("upload.reading.sub", null, "Estamos identificando qué atención necesitás.")) + '</div></div></div>';
    var box = $("#prep-reading-animation", host);
    if (box && window.lottie && typeof window.lottie.loadAnimation === "function") {
      readingAnim = window.lottie.loadAnimation({
        container: box,
        renderer: "svg",
        loop: true,
        autoplay: true,
        path: "/static/animations/rescan-document.json?v=1"
      });
    } else if (box) {
      box.innerHTML = '<span class="wz-spin"><i></i></span>';
    }
  }

  // Toda pantalla que puede volver a mandar un archivo (subida inicial,
  // "no pudimos leerla", "¿es la orden correcta?") monta el widget DE NUEVO
  // — el widget vive en el DOM que acabamos de reemplazar, así que un token
  // viejo ya no sirve. Tragarse esto deja el análisis muerto en el reintento
  // (misma raíz que docs/solutions/ai/turnstile-mata-el-analisis-de-orden.md).
  var TS_BOX = '<div id="prep-ts" style="margin-top:16px;display:none;"></div>';
  function wireUploadZone() {
    var ts = $("#prep-ts", host);
    // onFile replaces host.innerHTML immediately. Mount first and hold both a
    // bootstrap-captured file and a very-fast native chooser result until the
    // configured widget is connected, so neither path bypasses its token.
    var uploadStep = host.firstElementChild;
    var mountFailed = false;
    var mountPending = false;
    var pendingAtMount = null;
    function uploadStepIsCurrent() {
      return !!(uploadStep && host.contains(uploadStep) && ts &&
        ts.isConnected && uploadStep.contains(ts));
    }
    function mountTurnstile() {
      if (!ts || !TS) return Promise.resolve(false);
      try { return Promise.resolve(TS.mount(ts)); }
      catch (e) { return Promise.resolve(false); }
    }
    var turnstileReady = mountTurnstile();
    function onFileWhenReady(file) {
      if (!file || !uploadStepIsCurrent()) return;
      pendingAtMount = file;
      if (mountPending) return;
      mountPending = true;
      if (mountFailed) {
        mountFailed = false;
        turnstileReady = mountTurnstile();
      }
      turnstileReady.then(async function (mounted) {
        mountPending = false;
        var currentFile = pendingAtMount;
        pendingAtMount = null;
        if (!currentFile || !uploadStepIsCurrent()) return;
        if (TS && TS.activo() && !mounted) {
          mountFailed = true;
          var result = $("#prep-result", host);
          if (result) result.innerHTML = '<div class="wz-warn">' + esc(T(
            "upload.error.turnstile", null,
            "No pudimos verificar que sos una persona. Volvé a subir la orden."
          )) + "</div>";
          return;
        }
        mountPending = true;
        try {
          var tsToken = TS ? await TS.requireToken({onWait:function() {
            var result = $("#prep-result", host);
            if (result && uploadStepIsCurrent()) result.textContent = T("account.form.verifying", null, "Verificando…");
          }}) : "";
          if (uploadStepIsCurrent()) onFile(pendingAtMount || currentFile, tsToken);
        } catch(e) {
          var result = $("#prep-result", host);
          if (result && uploadStepIsCurrent()) result.textContent = T("upload.error.turnstile", null, "No pudimos verificar que sos una persona. Volvé a subir la orden.");
          if (TS) TS.reset();
        } finally { mountPending = false; pendingAtMount = null; }
      });
    }
    wireUploadControl(host, onFileWhenReady);
    var queued = takePendingUploadFile();
    if (queued) onFileWhenReady(queued);
  }

  function stepUpload(msg) {
    stopReading();
    setPage(initialTitle, initialSub);
    host.innerHTML =
      '<div class="sec">' +
      (msg ? '<div class="wz-warn">' + esc(msg) + "</div>" : "") +
      // La consigna vive ahora en el subtítulo de la página (server-render),
      // no se repite acá (responsable del producto, 2026-07-17).
      zoneHtml() + TS_BOX +
      '<div id="prep-result"></div></div>';
    wireUploadZone();
    loadCatalogo(); // en paralelo, no bloquea la subida
  }

  function onFile(file, tsToken) {
    showReading();
    fileToOrden(file).then(function (orden) {
      if (!orden) { stepUpload(T("upload.error.cant_read_file", null, "No pudimos leer el archivo. Probá con otra foto.")); return null; }
      state.ordenFilename = orden.filename;
      state.ordenPreview = /^image\//.test(orden.mime) ? ("data:" + orden.mime + ";base64," + orden.data_b64) : null;
      return Promise.resolve(tsToken).then(function (tsToken) {
        // Timeout duro: un POST grande puede quedar colgado sin que el server
        // conteste nunca (visto en prod con un PDF pesado). Sin esto la persona
        // se queda mirando "Leyendo tu orden…" para siempre — un spinner eterno
        // es peor que cualquier mensaje de error (responsable del producto, 2026-07-20).
        var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
        if (ctrl) setTimeout(function () { try { ctrl.abort(); } catch (e) {} }, ANALISIS_TIMEOUT_MS);
        return fetch("/api/analyze-order", {
          method: "POST", headers: analyzeHeaders(),
          body: JSON.stringify({ servicio_id: servicioId, orden: orden, turnstile_token: tsToken }),
          signal: ctrl ? ctrl.signal : undefined
        });
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
      });
    }).then(function (res) {
      if (!res) return;
      // Errores del GATE (no de la IA): se muestran y reintentan en el MISMO
      // control — tragárselos deja el análisis muerto sin que nadie lo vea
      // (docs/solutions/ai/turnstile-mata-el-analisis-de-orden.md).
      if (res.status === 403 && res.j && res.j.error === "turnstile") {
        if (TS) TS.reset();
        stepUpload(T("upload.error.turnstile", null, "No pudimos verificar que sos una persona. Volvé a subir la orden."));
        return;
      }
      if (res.status === 429) {
        stepUpload((res.j && res.j.mensaje) ||
          T("upload.error.rate_limited", null, "Hiciste varias consultas seguidas. Esperá un rato y volvé a intentar."));
        return;
      }
      if (TS) TS.reset(); // el token es de un solo uso, aunque haya andado
      if (!res.ok || !res.j || !res.j.ok) { stepFallo(fallaDe(res)); return; }
      state.token = res.j.token || null;
      state.ai = res.j.ai || null;
      var doc = (state.ai && state.ai.documento) || {};
      if (doc.es_orden === false) { stepDocDudoso(); return; }
      // Acá SÍ es la foto: el modelo respondió y dijo que no la pudo leer.
      // Único lugar del flujo donde pedir otra foto es la verdad.
      if (state.ai && state.ai.verdict === "ilegible") { stepIlegible(); return; }
      continuar();
    }).catch(function () { stepFallo("servicio"); });
  }

  /* ¿De quién fue la culpa? El motivo que se le muestra a la persona tiene que
     ser el REAL. Decirle "sacá una foto con más luz" cuando lo que pasó es que
     el lector automático no respondió es mentirle y mandarla a repetir una foto
     que estaba perfecta (responsable del producto, 2026-07-20). */
  /* Cuánto esperamos al análisis antes de cortar. Una lectura normal tarda
     4-15 s; 90 s es "esto ya no vuelve", no "está tardando". */
  var ANALISIS_TIMEOUT_MS = 90000;

  function fallaDe(res) {
    var st = res.status || 0;
    if (st === 413) return "grande";      // el archivo no entró
    if (st === 400) return "archivo";     // no pudimos abrirlo
    return "servicio";                    // 5xx, 0, cualquier otra: es nuestro
  }

  /* ---- Falló la subida: mensaje + evidencia (lo que subió, si la tenemos) +
     reintentar con el MISMO control + salida hacia el costado (wizard-design
     regla 5). El texto depende de QUÉ falló: pedirle otra foto sólo tiene
     sentido si el problema es la foto. ---- */
  function FALLAS() {
    // Lo nuestro no anduvo (el lector automático no respondió, o se cayó).
    // No la mandamos a repetir la foto: no es su foto, y el turno se puede
    // sacar igual llevando la orden en papel.
    return {
      servicio: T("upload.error.service_html", null,
        "<b>No pudimos analizar la orden en este momento.</b><br>" +
        "No es por tu foto: el lector automático no está disponible. " +
        "Probá de nuevo en un rato, o sacá el turno y llevá la orden al centro."),
      grande: T("upload.error.too_big_html", null,
        "<b>El archivo es muy pesado y no lo pudimos subir.</b><br>" +
        "Si es un PDF de varias páginas, subí sólo la hoja de la orden, o sacale una foto."),
      archivo: T("upload.error.bad_file_html", null,
        "<b>No pudimos abrir el archivo.</b><br>" +
        "Probá con una foto (JPG) o un PDF de la orden.")
    };
  }

  // Preview de lo que se subió, repetido idéntico en los 3 pasos de error
  // (wizard-design regla 5) — una sola vez para no desincronizar la traducción.
  function previewImgHtml() {
    if (!state.ordenPreview) return "";
    var zoomLbl = esc(T("upload.aria.zoom_upload", null, "Ampliar lo que subiste"));
    var altLbl = esc(T("upload.alt.uploaded", null, "Lo que subiste"));
    return '<a class="wz-err-img-btn" href="' + esc(state.ordenPreview) + '" download="order-image" aria-label="' + zoomLbl + '" ' +
      'data-photozoom data-photozoom-src="' + esc(state.ordenPreview) + '" data-photozoom-alt="' + altLbl + '">' +
      '<img class="wz-err-img" src="' + esc(state.ordenPreview) + '" alt="' + altLbl + '"></a>';
  }

  function stepFallo(kind) {
    stopReading();
    var fallas = FALLAS();
    var msg = fallas[kind] || fallas.servicio;
    host.innerHTML =
      '<div class="sec"><div class="wz-err">' +
      '<div class="wz-warn">' + msg + "</div>" +
      previewImgHtml() +
      '<div class="wz-err-act">' +
        zoneHtml() + TS_BOX +
        sidewaysLinksHtml() +
      "</div></div></div>";
    wireUploadZone();
  }

  /* ---- El documento no parece una orden médica (wizard-design regla 5):
     mensaje + preview + 3 acciones (subir otra con el MISMO control /
     "estoy seguro" / salida hacia el costado). ---- */
  function stepDocDudoso() {
    stopReading();
    var tipo = (state.ai && state.ai.documento && state.ai.documento.tipo) ||
      T("upload.doc_dudoso.other_type", null, "otro tipo de documento");
    var tipoEsc = esc(tipo);
    host.innerHTML =
      '<div class="sec"><div class="wz-err">' +
      '<div class="wz-warn">' + T("upload.doc_dudoso.msg", { tipo: tipoEsc },
        "<b>Esto parece {tipo}, no una orden médica.</b><br>¿Querés buscar y subir la orden correcta?") + "</div>" +
      previewImgHtml() +
      '<div class="wz-err-act">' +
        zoneHtml() + TS_BOX +
        '<button type="button" class="wz-override" id="prep-seguro">' +
          esc(T("upload.action.use_anyway", null, "Usar este archivo de todos modos")) + "</button>" +
        sidewaysLinksHtml() +
      "</div></div></div>";
    wireUploadZone();
    $("#prep-seguro", host).addEventListener("click", continuar);
  }

  /* ---- La imagen llegó pero no se leyó (verdict 'ilegible'): pedir otra foto
     es correcto acá. No es un callejón sin salida — se puede seguir igual y el
     hospital la revisa a mano (patient-flow-design regla 10). ---- */
  function stepIlegible() {
    stopReading();
    host.innerHTML =
      '<div class="sec"><div class="wz-err">' +
      '<div class="wz-warn">' + T("upload.error.illegible_html", null,
        "<b>No pudimos leer lo que dice la orden.</b><br>" +
        "Si podés, sacale otra foto con más luz y sin sombras. Igual podés seguir: " +
        "la orden queda guardada y la revisa el hospital.") + "</div>" +
      previewImgHtml() +
      '<div class="wz-err-act">' +
        zoneHtml() + TS_BOX +
        '<div class="wz-btnstack">' +
          '<button type="button" class="wz-cta-sec" id="prep-igual">' +
            esc(T("upload.action.continue_anyway", null, "Seguir igual")) + "</button>" +
        "</div>" +
        sidewaysLinksHtml() +
      "</div></div></div>";
    wireUploadZone();
    $("#prep-igual", host).addEventListener("click", continuar);
  }

  /* ---- Se leyó la orden y no se reconoció qué pide (TASK-52, 26.10): la
     misma pantalla que los demás problemas de la subida (wizard-design regla
     5) — mensaje, lo que se subió, el control para probar con otra foto y la
     salida hacia el costado: buscar el estudio o la especialidad. Antes era
     una frase gris sin botón ni link. ---- */
  function stepNoReconocido() {
    stopReading();
    setPage(initialTitle, initialSub);
    host.innerHTML =
      '<div class="sec"><div class="wz-err">' +
      '<div class="wz-warn">' + T("upload.result.unrecognized_html", null,
        "<b>No reconocimos qué pide la orden.</b><br>Probá con otra foto, más cerca y con buena luz, o buscá el estudio o la especialidad.") + "</div>" +
      previewImgHtml() +
      '<div class="wz-err-act">' +
        zoneHtml() + TS_BOX +
        '<div class="prep-links"><a href="/">' +
          esc(T("upload.links.search", null, "Buscar el estudio o la especialidad")) + "</a></div>" +
      "</div></div></div>";
    wireUploadZone();
  }

  function sidewaysLinksHtml() {
    var links = "";
    if (servicioId) links += '<a href="/service/' + esc(servicioId) + '">' +
      esc(T("upload.links.back_to_service", null, "Volver al servicio")) + "</a>";
    links += '<a href="/">' + esc(T("upload.links.home", null, "Ir al inicio")) + "</a>";
    return '<div class="prep-links">' + links + "</div>";
  }

  /* Orden aceptada (o análisis degradado — no bloquea, patient-flow-design
     regla 10): handoff hacia el wizard de turnos vía sessionStorage y
     resultado. */
  function continuar() {
    try {
      sessionStorage.setItem("st_order_pending", JSON.stringify({
        token: state.token, ai: state.ai, filename: state.ordenFilename, ts: Date.now()
      }));
    } catch (e) {}
    stepResultado();
  }

  /* ---- Resultado de ESTA herramienta: preparación primero y turnos después.
     El wizard de reserva vive en service.js y conserva el turno al principio. ---- */
  function stepResultado() {
    stopReading();
    var ai = state.ai || {};
    var campos = ai.campos || {};
    var practicas = (campos.practicas && campos.practicas.valor) || [];
    var especialidad = (campos.especialidad && campos.especialidad.valor) || null;
    var prepEntries = Array.isArray(ai.preparacion) ? ai.preparacion : [];

    var mostrarNombreTarjeta = prepEntries.length > 1;
    var cards = prepEntries.map(function (e) {
      return estudioCardHtml(e, mostrarNombreTarjeta);
    }).join("");
    var prepSec = cards
      ? '<div class="prep-sec"><div class="prep-cards">' + cards + "</div></div>"
      : "";
    var leidoSec = cards ? "" : queLeyoHtml(practicas, especialidad);
    var tituloTurnos = T("upload.result.where_title", null, "Dónde sacar el turno");
    setPage(cards ? tituloPreparacion(prepEntries) : tituloTurnos, "");

    host.innerHTML =
      '<div class="sec">' +
      prepSec +
      leidoSec +
      '<div class="prep-sec"><div class="prep-sec-h">' + esc(tituloTurnos) + '</div>' +
        '<div id="prep-turnos"><div class="wz-spin"><i></i> ' +
          esc(T("upload.result.searching", null, "Buscando dónde sacar el turno…")) + '</div></div></div>' +
      "</div>";
    window.scrollTo({ top: 0, behavior: "smooth" });

    // Native Search/Listing are optional only in the sense that their real
    // user-facing fallback below remains available.  Settle their imports so
    // a rejected Listing cannot strand the loading spinner forever.
    loadCatalogo().then(function () {
      return Promise.all([searchReady, listingReady]);
    }).then(function () {
      renderTurnos(practicas, especialidad);
    });
  }

  /* ¿La orden llegó SIN pasar por el lector? 'pendiente_ia' = la IA estaba
     caída y se reprocesa después; 'revision_manual' = no se pudo analizar. En
     los dos casos no hay nada leído, y no es culpa de la orden. */
  function sinAnalisis() {
    var v = state.ai && state.ai.verdict;
    return v === "pendiente_ia" || v === "revision_manual";
  }

  function queLeyoHtml(practicas, especialidad) {
    var items = practicas.length ? practicas : (especialidad ? [especialidad] : []);
    if (!items.length) return "";
    var body = items.length === 1
      ? '<p class="prep-single">' + esc(items[0]) + "</p>"
      : '<ul class="prep-bullets">' + items.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
    return '<div class="prep-sec"><div class="prep-sec-h">' +
      esc(T("upload.result.order_asks_title", null, "Tu orden pide")) + '</div>' + body + "</div>";
  }

  /* Matchea cada práctica leída (y la especialidad como respaldo) contra el
     catálogo real con Search.buscarFrase — MISMOS campos que la búsqueda de la
     home (index.html:renderResultados). Toma los mejores resultados de cada
     query, dedupeados por servicio, cortado en 5 filas. */
  function renderTurnos(practicas, especialidad) {
    var el = $("#prep-turnos", host);
    if (!el) return;
    if (!Search || !Listing) {
      el.innerHTML = '<p class="prep-noreconocido">' + esc(T("upload.result.list_error_generic", null,
        "No pudimos armar la lista de turnos ahora — buscá el estudio o la especialidad desde el inicio.")) + '</p>';
      return;
    }
    var queries = (practicas || []).slice();
    if (especialidad) queries.push(especialidad);
    // "No reconocimos qué pide" sólo vale si LEÍMOS la orden y no encontramos
    // nada. Cuando el lector automático no corrió (pendiente_ia / revision_manual)
    // decir eso es echarle la culpa a la orden por algo nuestro: la lista sale
    // vacía por la misma razón en los dos casos, pero el motivo es distinto y es
    // el motivo lo que la persona necesita saber (responsable del producto, 2026-07-20).
    if (!queries.length && sinAnalisis()) {
      el.innerHTML = '<p class="prep-noreconocido">' + esc(T("upload.result.no_analysis", null,
        "No pudimos analizar la orden en este momento — buscá el estudio o la especialidad desde el inicio. " +
        "La orden queda guardada y la revisa el hospital.")) + '</p>';
      return;
    }
    if (!queries.length) {
      stepNoReconocido();
      return;
    }
    if (!SERV || !SERV.length) {
      el.innerHTML = '<p class="prep-noreconocido">' + esc(T("upload.result.search_unavailable", null,
        "No pudimos buscar los turnos disponibles ahora — buscalo desde el inicio.")) + '</p>';
      return;
    }
    function getCampos(s) {
      var c = CENT[s.centro] || {};
      return { principal: [s.nombre], resto: [s.categoria, c.name, s.sede, s.horario].concat(s.palabras || []),
               excluidas: s.palabras_excluidas || [] };
    }
    var seen = {}, servicios = [];
    queries.forEach(function (q) {
      if (servicios.length >= 5) return;
      // buscarFrase, no buscar: la práctica viene con la letra del médico
      // ("Ergometría de 12 derivaciones") y el AND entre palabras daba cero
      // o, peor, matcheaba Cardiología por keyword antes que el servicio
      // Ergometría por nombre. La degradación + mejor-tier vive en buscar.js.
      var found = Search.buscarFrase(SERV, q, getCampos);
      for (var i = 0; i < found.length && servicios.length < 5; i++) {
        if (!seen[found[i].id]) { seen[found[i].id] = true; servicios.push(found[i]); }
      }
    });
    if (!servicios.length) {
      el.innerHTML = '<p class="prep-noreconocido">' + esc(T("upload.result.no_match", null,
        "No encontramos un turno para lo que pide la orden — buscalo desde el inicio.")) + '</p>';
      return;
    }
    // La orden ya analizada viaja al wizard vía ?order=<token> — sólo en los
    // hrefs reservables (href/slot_href), nunca en los informativos/walk-in
    // (Search.servRow ya lo filtra por opts.extraQuery).
    var extraQuery = state.token ? "order=" + encodeURIComponent(state.token) : null;
    var rows = servicios.map(function (s) { return Search.servRow(s, CENT, { extraQuery: extraQuery }); });
    // SanaListing.render() limpia el host antes de dibujar (listing.js,
    // 2026-08-05) — este el.innerHTML="" manual ya no hace falta.
    Listing.render(el, {
      cols: ["servicio", "centro", "orden", "proximo"],
      rows: rows,
      options: { search: false, sort: false, profile: false, defaultSort: false }
    });
  }

  /* ---- una tarjeta por estudio reconocido (idéntica al wizard de turnos) ----
     Qué secciones tiene una preparación y cómo se llaman lo decide prep.mjs,
     el mismo modelo que usan el wizard, la página del
     servicio y los emails. Acá se elige nada más cómo se dibuja. */
  function tituloPreparacion(entries) {
    if (!entries || entries.length !== 1) {
      return T("upload.prep.title_multi", null, "Cómo prepararte para tus estudios");
    }
    var nombre = String(entries[0].nombre || T("upload.prep.default_study_name", null, "el estudio")).trim();
    var nombreMinuscula = nombre.charAt(0).toLowerCase() + nombre.slice(1);
    if (/^ecografía abdominal$/i.test(nombre)) {
      return T("upload.prep.title_abdominal", null, "Cómo prepararte para una ecografía abdominal");
    }
    // `nombre` viene del catálogo (paquete de datos), no de la traducción de
    // interfaz: la frase que lo envuelve se traduce, el nombre del estudio no.
    return T("upload.prep.title_one", { nombre: nombreMinuscula }, "Cómo prepararte para tu " + nombreMinuscula);
  }
  function bulletsHtml(sec, headingLevel) {
    if (!sec.items.length) return "";
    var tag = headingLevel === 3 ? "h3" : "h2";
    if (sec.items.length === 1) {
      return '<section class="prep-block"><' + tag + ' class="prep-sub">' + esc(sec.titulo) + "</" + tag + ">" +
        '<p class="prep-single prep-single--' + sec.tono + '">' + esc(sec.items[0]) + "</p></section>";
    }
    return '<section class="prep-block"><' + tag + ' class="prep-sub">' + esc(sec.titulo) + "</" + tag + ">" +
      '<ul class="prep-bullets prep-bullets--' + sec.tono + '">' +
      sec.items.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul></section>";
  }
  function fuentesHtml(fuentes) {
    if (!fuentes || !fuentes.length) return "";
    return '<details class="prep-fuentes"><summary>' + esc(T("upload.result.sources", null, "Referencias")) + '</summary>' +
      fuentes.map(function (f) { return '<a href="' + esc(f.url) + '" target="_blank" rel="noopener">' + esc(f.title) + "</a>"; }).join("") +
      "</details>";
  }
  function estudioCardHtml(e, mostrarNombre) {
    var P = Prep;
    var body = "";
    var res = P.resumen(e);
    if (res) body += '<p class="prep-resumen">' + esc(res) + "</p>";
    var ay = P.ayuno(e);
    if (ay.hay) body += '<div class="prep-ayuno prep-ayuno--si">' + esc(ay.texto) + "</div>";
    if (ay.nota) body += '<p class="prep-nota">' + esc(ay.nota) + "</p>";
    var secs = P.secciones(e);
    secs.forEach(function (s) { body += bulletsHtml(s, mostrarNombre ? 3 : 2); });
    // Sólo cuando de verdad no hay NADA que decir. `sin_preparacion` significa
    // "sin ayuno ni dieta", no "no tengo nada para contarte": taparle las
    // indicaciones dejaba dos estudios distintos con la misma tarjeta genérica.
    if (!body) body += '<div class="prep-ayuno prep-ayuno--no">' + esc(P.SIN_NADA) + "</div>";
    var nombre = mostrarNombre ? '<h2 class="prep-h">' + esc(e.nombre) + "</h2>" : "";
    return '<div class="prep-card">' + nombre + body + fuentesHtml(e.sources) + "</div>";
  }

  // tsLista lleva su propio catch (fail-open), así que este Promise.all sólo
  // rompe si prep.mjs no carga — el paso de subida no se dibuja sin su modelo.
  // Esperar acá también garantiza que TS ya esté (o ya haya fallado) antes del
  // primer wireUploadZone, que monta el widget.
  Promise.all([i18nReady, prepReady, tsLista]).then(function () {
    injectStyle();
    stepUpload();
  }).catch(function (error) {
    console.error("[upload-order] canonical module load:", error);
    uploadUnavailable();
    host.innerHTML = '<div class="sec"><p class="prep-noreconocido">' +
      esc(T("upload.result.load_error", null, "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.")) +
      "</p></div>";
  });
}
