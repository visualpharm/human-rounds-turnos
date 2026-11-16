/* modules/turnos/wz-upload.mjs — shared referral upload control (wizard-design
   rule 6: one control, one look, in every flow that uploads a referral).
   Named exports css, zoneHtml, wire, adoptPaintedInput, fileToOrden, analyzeHeaders. No window.WzUpload,
   no classic twin (adoptPaintedInput keeps the server-painted file input). The
   document-level paste listener remains a module
   singleton; wire() still activates only the most recently mounted
   upload control. */
"use strict";


  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.resolve(null);
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  var SVG_CAM = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L8 6H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-4l-1.5-2Z"></path><circle cx="12" cy="13" r="3.6"></circle></svg>';
  var PASTE_KEYS = /Mac|iP(hone|ad|od)/.test(navigator.platform || "") ? "⌘V" : "Ctrl+V";

  var CSS =
    // The heading IS the call to action (the product owner, 2026-09-15: the muted dashed box
    // had no visible action): a filled primary button inside a quiet panel.
    ".wz-uploadzone{border:1px solid var(--border-strong);background:var(--panel);border-radius:20px;" +
      "padding:30px 22px;display:flex;flex-direction:column;align-items:center;text-align:center;gap:10px;" +
      "cursor:pointer;transition:border-color .12s,background .12s;}" +
    ".wz-uploadzone:hover,.wz-uploadzone:focus-visible{outline:none;border-color:var(--hover-bd-strong);}" +
    ".wz-uploadzone:focus-visible .wz-up-h{outline:2px solid var(--focus-border,var(--text));outline-offset:2px;}" +
    ".wz-cam{width:64px;height:64px;border-radius:50%;background:var(--accent-soft);display:flex;" +
      "align-items:center;justify-content:center;color:var(--accent-hover);}" +
    ".wz-up-h{display:inline-flex;align-items:center;justify-content:center;min-height:52px;max-width:100%;box-sizing:border-box;" +
      "padding:0 24px;border-radius:var(--btn-radius,14px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);" +
      "font-size:18px;font-weight:600;}" +
    ".wz-uploadzone:hover .wz-up-h{background-image:var(--hover-fill);}" +
    ".wz-up-sub{font-size:16px;color:var(--text-2);}" +
    /* pista de pegado: sólo en dispositivos con teclado (wizard-design regla 7) */
    ".wz-up-paste{display:none;font-size:14.5px;color:var(--text-3);margin-top:2px;}" +
    ".wz-up-desk{display:none;}" +
    "@media(hover:hover) and (pointer:fine){.wz-up-paste{display:block;}.wz-up-desk{display:inline;}.wz-up-touch,.wz-up-sub{display:none;}}";

  // El aviso de IA va una sola vez: /upload-order ya lo trae en el HTML del
  // servidor (p[data-ai-notice="order"]); los demás pasos lo ponen acá. La copia
  // de la primera pintura del asistente (data-first-paint) vive dentro del
  // host que el cliente reemplaza: no cuenta, el cliente la vuelve a dibujar.
  function noticeHtml() {
    if (typeof document !== "undefined" && document.querySelector('p[data-ai-notice="order"]:not([data-first-paint])')) return "";
    return '<p style="font-size:16px;color:var(--text);">' + T("ai.notice.order", null, "La inteligencia artificial lee tu orden. La imagen completa se envía al proveedor; el equipo revisa la solicitud.") + '</p>';
  }

  function zoneHtml() {
    return noticeHtml() + '<div class="wz-uploadzone" id="wz-upzone" role="button" tabindex="0" aria-label="' +
        esc(T("upload.zone.aria", null, "Subir la orden")) + '">' +
      '<input id="wz-file" type="file" accept="image/*,.pdf" style="display:none">' +
      '<div class="wz-cam">' + SVG_CAM + "</div>" +
      '<div class="wz-up-h"><span class="wz-up-touch">' + esc(T("upload.zone.heading", null, "Tomá una foto de la orden")) +
        '</span><span class="wz-up-desk">' + esc(T("upload.zone.heading_file", null, "Elegí la foto o el PDF de la orden")) + '</span></div>' +
      '<div class="wz-up-sub">' + esc(T("upload.zone.sub", null, "o elegí un archivo: foto o PDF")) + '</div>' +
      '<div class="wz-up-paste">' + esc(T("upload.zone.paste", { keys: PASTE_KEYS },
        "También podés pegarla con " + PASTE_KEYS)) + "</div>" +
      "</div>";
  }

  // Un único listener de paste a nivel documento (evita acumular listeners
  // cada vez que un wizard remonta el paso): sólo dispara si el control
  // montado AHORA MISMO es el que registró wire() por última vez.
  var active = null; // { host, onFile }
  document.addEventListener("paste", function (e) {
    if (!active || !active.host.querySelector("#wz-file")) return;
    var items = (e.clipboardData && e.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === "file" && /^image\//.test(items[i].type || "")) {
        var f = items[i].getAsFile();
        if (f) { e.preventDefault(); active.onFile(f); return; }
      }
    }
  });

  function wire(host, onFile) {
    active = { host: host, onFile: onFile };
    var fileEl = host.querySelector("#wz-file");
    var zone = host.querySelector("#wz-upzone");
    if (!fileEl || !zone) return;
    zone.addEventListener("click", function (e) {
      if (zone._turnosKeyboardUntil && Date.now() < zone._turnosKeyboardUntil) {
        e.preventDefault();
        return;
      }
      fileEl.click();
    });
    zone.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        zone._turnosKeyboardUntil = Date.now() + 120;
        fileEl.click();
      }
    });
    fileEl.addEventListener("change", function () {
      var f = fileEl.files && fileEl.files[0];
      if (f) onFile(f);
    });
  }

  /* El servidor pinta una primera zona de subida (site_page_service._first_wizard_step)
     que el wizard reemplaza por la suya. El selector de archivos nativo, si ya
     se abrió sobre el input pintado, le responde a ESE nodo: si el reemplazo lo
     suelta, el archivo elegido cae en un input desconectado, ningún `change`
     llega a la página y la orden se pierde en silencio. Por eso el wizard se
     queda con el input pintado (todavía vacío y sin cablear) dentro de su zona
     nueva, en la misma tarea del reemplazo: wire() lo toma como cualquier otro. */
  function adoptPaintedInput(host, painted) {
    if (!painted || !painted.hasAttribute("data-first-paint")) return;
    painted.removeAttribute("data-first-paint");
    // Un archivo ya entregado lo retiene el puente de primer pintado
    // (takePendingFile): ese input no se reutiliza.
    if (painted.files && painted.files.length) return;
    var fresh = host.querySelector("#wz-file");
    if (fresh && fresh !== painted) fresh.replaceWith(painted);
  }

  /* archivo -> {filename,mime,data_b64} (downscale de imágenes a ≤2000px de
     lado mayor, recompresión JPEG — PDFs y no-imágenes van tal cual). */
  function fileToOrden(file) {
    return new Promise(function (resolve, reject) {
      if (!file) { resolve(null); return; }
      var isImg = /^image\//.test(file.type);
      if (!isImg) {
        var fr = new FileReader();
        fr.onload = function () {
          var s = String(fr.result || "");
          var comma = s.indexOf(",");
          resolve({ filename: file.name, mime: file.type || "application/octet-stream", data_b64: comma >= 0 ? s.slice(comma + 1) : s });
        };
        fr.onerror = function () { reject(new Error(T("upload.error.cant_read_generic", null, "No pudimos leer el archivo."))); };
        fr.readAsDataURL(file);
        return;
      }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var max = 2000, w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, max / Math.max(w, h));
        var cw = Math.round(w * scale), ch = Math.round(h * scale);
        var cv = document.createElement("canvas");
        cv.width = cw; cv.height = ch;
        cv.getContext("2d").drawImage(img, 0, 0, cw, ch);
        URL.revokeObjectURL(url);
        var durl = cv.toDataURL("image/jpeg", 0.85);
        var comma = durl.indexOf(",");
        var name = (file.name || "orden").replace(/\.[^.]+$/, "") + ".jpg";
        resolve({ filename: name, mime: "image/jpeg", data_b64: comma >= 0 ? durl.slice(comma + 1) : durl });
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error(T("upload.error.cant_process_image", null, "No pudimos procesar la imagen."))); };
      img.src = url;
    });
  }

  /* Encabezados de POST /api/analyze-order. X-Turnos-Client es un id al azar
     de ESTE navegador (localStorage): detrás de la puerta municipal todos los
     pacientes llegan con la misma IP, y los límites cuentan por navegador, no
     por hospital (ai/abuse_guard.py:identity). Sin storage, un id por carga. */
  var memoryClientId = null;
  function clientId() {
    var id = null;
    try { id = localStorage.getItem("stz_client_id"); } catch (e) { id = null; }
    if (id && /^[A-Za-z0-9_-]{16,64}$/.test(id)) return id;
    var bytes = new Uint8Array(18);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    id = btoa(String.fromCharCode.apply(null, bytes)).replace(/\+/g, "-").replace(/\//g, "_");
    try { localStorage.setItem("stz_client_id", id); } catch (e) { memoryClientId = memoryClientId || id; id = memoryClientId; }
    return id;
  }
  function analyzeHeaders() {
    return { "Content-Type": "application/json", "X-Turnos-Client": clientId() };
  }

export { CSS as css, zoneHtml, wire, adoptPaintedInput, fileToOrden, analyzeHeaders };
