/* static/platform/hsi-sync.mjs — the one «¿está en HSI?» control of the panel.

   Every place where a turno can reach HSI draws THIS control and nothing
   else (responsable del producto, 2026-10-27, d1122568): «Pacientes de hoy» rows, «Turno creado»,
   the approvals just made in Solicitudes and the detail of an approved
   request. Four states, one look:
     - nothing yet  → a button («Cargar en HSI» / «Enviar a HSI»);
     - on its way   → a turning sync icon («Cargando en HSI…»; with the
                      automatic send on, «Envío automático a HSI» instead of
                      the button);
     - done         → a check («Guardado en HSI» / «En HSI»);
     - failed       → the button again, red, with what to do next to it.

   Two drivers, never both on one installation:
     - server push (an EMR connector is on): `serverHtml()` paints the state
       the server keeps (`hsi_estado`); the journal resends a pending one by
       itself, so «pendiente» is the automatic-send label, not a button;
     - the desk's Chrome extension (HSI Humanizador) when there is no push:
       `extensionHtml()` draws the hook the extension knows
       (`[data-hr-hsi]` + `.hr-hsi-aviso`) and the extension paints its
       states onto it (`data-estado`, text, `disabled`). `data-hr-hsi-auto`
       marks a turno that was just approved or created: with «Cargar en HSI
       apenas apruebo o creo un turno» on, the extension sends it without a
       click. Without the extension the click reaches the panel and
       `sinExtension()` copies the DNI; `instalarHtml()`, next to the
       control, says where to install the extension until it is there.

   The look lives here (`css`, `ensureCss()`), keyed on `data-estado`, for
   the panel's control and the extension's own row button alike. */
"use strict";

function importDep(name, nodeUrl) {
  var url = typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC[name];
  return url ? import(url) : import(nodeUrl);
}

var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url)).catch(function () { return null; });
var Format = await importDep("platform/format.mjs", new URL("./format.mjs", import.meta.url)).catch(function () { return null; });
// The DNI the desk reads has its thousands dots; the clipboard gets the digits.
function dniVisible(dni) { return Format && Format.dni ? Format.dni(dni) : String(dni || ""); }

function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
function applyVars(s, vars) {
  return String(s).replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? vars[k] : m; });
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

var SYNC = "/static/icons/hsi-sync-sf-regular.svg";
var CHECK = "/static/icons/hsi-checkmark-sf-regular.svg";
function mask(url) {
  return "background:currentColor;-webkit-mask:url(" + url + ") center/contain no-repeat;mask:url(" + url + ") center/contain no-repeat;";
}

// States that are still working (the turning icon) and the one that is done
// (the check). The extension's own names (abriendo, cargando) and the
// panel's (enviando, en-cola) share them.
var GIRA = ["abriendo", "cargando", "enviando", "en-cola"];
function sel(base, estados, extra) {
  return estados.map(function (e) { return base + '[data-estado="' + e + '"]' + (extra || ""); }).join(",");
}
var BOTONES = [".hsi-sync-btn", ".hr-hsi-boton"];
function todos(estados, extra) {
  return BOTONES.map(function (b) { return sel(b, estados, extra); }).join(",");
}

var css =
  ".hsi-sync{display:inline-flex;align-items:center;gap:8px;position:relative;max-width:100%;}" +
  BOTONES.join(",") + "{display:inline-flex;align-items:center;gap:6px;}" +
  todos(GIRA.concat(["guardado"]), "::before") + "{content:\"\";flex:0 0 14px;width:14px;height:14px;}" +
  todos(GIRA, "::before") + "{" + mask(SYNC) + "animation:hsi-sync-gira 1.1s linear infinite;}" +
  todos(["guardado"], "::before") + "{" + mask(CHECK) + "color:var(--done,#0d7d48);}" +
  // Working is not disabled: dark text on the panel, whatever `.btn:disabled` says.
  todos(GIRA) + "{cursor:progress;opacity:1!important;border-color:var(--border-strong,#cbc4b4)!important;color:var(--text,#1c1f23)!important;background:var(--panel,#fff)!important;}" +
  // Done is a fact, not a button: no frame, dark text, the check in green.
  todos(["guardado"]) + "{border-color:transparent!important;background:none!important;color:var(--text,#1c1f23)!important;box-shadow:none!important;cursor:default;padding-left:0;padding-right:0;}" +
  todos(["esperando", "alta", "preguntando"]) + "{border-color:#b26a00!important;color:#8a5200!important;background:var(--panel,#fff)!important;}" +
  todos(["error"]) + "{border-color:var(--danger,#a4161a)!important;color:var(--danger,#a4161a)!important;background:var(--panel,#fff)!important;}" +
  // The server-side «En HSI» / «No está en HSI» are plain text.
  "span.hsi-sync-btn{font-weight:600;font-size:14px;color:var(--text,#1c1f23);}" +
  "span.hsi-sync-btn[data-estado=\"no\"]{color:var(--text-2,#4a5059);font-weight:500;}" +
  // Next to the panel's own control the extension's hint reads as a line,
  // not the popover of a table row.
  ".hsi-sync-linea .hr-hsi-aviso,.hsi-sync-linea .hsi-sync-aviso{position:static;width:auto;max-width:none;box-shadow:none;border:0;padding:0;background:none;font-weight:600;}" +
  ".hsi-sync-aviso{color:var(--danger,#a4161a);font-size:14px;line-height:1.35;}" +
  ".hsi-sync-aviso:empty{display:none;}" +
  // Solicitudes: the approvals just made, each with its control.
  ".hsi-recientes{margin:0 0 16px;padding:16px;border:1px solid var(--line,#d5d9de);border-radius:12px;background:var(--panel,#fff);}" +
  ".hsi-recientes[hidden]{display:none;}" +
  ".hsi-recientes-t{margin:0 0 8px;font-size:16px;font-weight:700;color:var(--text,#1c1f23);}" +
  ".hsi-recientes-l{list-style:none;margin:0;padding:0;}" +
  ".hsi-recientes-f{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 16px;padding:8px 0;border-top:1px solid var(--line,#e6e8eb);}" +
  ".hsi-recientes-f:first-child{border-top:0;}" +
  ".hsi-recientes-q{flex:1 1 240px;min-width:0;font-size:15px;line-height:1.4;color:var(--text,#1c1f23);text-wrap:pretty;}" +
  // Where to install the extension: one link, gone once it is installed; the
  // explanation and the steps in its pop-up.
  ".hsi-instalar{margin:8px 0;font-size:16px;line-height:1.45;color:var(--text,#1c1f23);}" +
  ".hsi-instalar-link{display:inline-flex;align-items:center;min-height:44px;margin:0;padding:0;border:0;background:none;font:inherit;text-align:left;color:var(--accent,#1a5fb4);cursor:pointer;}" +
  ".hsi-instalar-link:hover{color:var(--accent-hover,var(--accent,#1a5fb4));}" +
  ".hsi-instalar-pop{box-sizing:border-box;width:min(480px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto;margin:auto;padding:20px 24px 24px;border:0;border-radius:14px;background:var(--panel,#fff);color:var(--text,#1c1f23);box-shadow:0 24px 60px -20px rgba(0,0,0,.5);font-size:16px;line-height:1.5;text-wrap:pretty;}" +
  ".hsi-instalar-pop::backdrop{background:rgba(14,20,16,.45);}" +
  ".hsi-instalar-pop p{margin:0 0 12px;}" +
  ".hsi-instalar-head{display:flex;align-items:center;gap:12px;margin:0 0 8px;}" +
  ".hsi-instalar-head .hsi-instalar-t{flex:1;margin:0;font-size:18px;font-weight:650;}" +
  // Icon, color and 44 px zone: the shared close button .hr-x (human-rounds.css).
  ".hsi-instalar-x{margin:-8px -12px -8px 0;}" +
  ".hsi-instalar-pasos{margin:0 0 12px;padding-left:24px;}" +
  ".hsi-instalar-pasos li{margin:0 0 6px;}" +
  ".hsi-instalar-pop .hsi-instalar-guia{margin:0;}" +
  "html[data-hr-humanizador=\"1\"] .hsi-instalar{display:none;}" +
  "@keyframes hsi-sync-gira{to{transform:rotate(360deg);}}" +
  "@media (prefers-reduced-motion:reduce){" + todos(GIRA, "::before") + "{animation:none;}}";

function ensureCss(doc) {
  var d = doc || (typeof document !== "undefined" ? document : null);
  if (!d || d.getElementById("hsi-sync-css")) return;
  var st = d.createElement("style");
  st.id = "hsi-sync-css";
  st.textContent = css;
  (d.head || d.documentElement).appendChild(st);
}

/* The hook the desk's extension paints. `id` is the request id (the same
   id as the row of «Pacientes de hoy»), `dia` its YYYY-MM-DD (the extension
   reads that day's queue). `auto`: a turno just approved or created — the
   extension sends it by itself when its setting says so. `primario`: the
   main action of its screen (accent button). `attrs`: extra attributes for
   the button (an id the screen already relies on). */
function extensionHtml(o) {
  o = o || {};
  var cls = "hsi-sync-btn btn" + (o.primario ? " btn-accent" : "");
  return '<span class="hsi-sync hr-hsi-panel' + (o.linea ? " hsi-sync-linea" : "") + '">'
    + '<button type="button" class="' + cls + '"' + (o.attrs || "")
    + ' data-hr-hsi="' + esc(o.id) + '"'
    + (o.dia ? ' data-hr-hsi-dia="' + esc(o.dia) + '"' : "")
    + (o.auto ? " data-hr-hsi-auto" : "")
    + ' data-estado="reposo">' + esc(T("admin.hsi.sync_cargar", null, "Cargar en HSI")) + "</button>"
    + '<span class="hr-hsi-aviso" role="status"></span></span>';
}

/* Server push: the state the server keeps. `futuro` — HSI does not take a
   turno that already happened, so a past one offers no button. */
function serverHtml(estado, o) {
  o = o || {};
  var id = esc(o.id);
  var wrap = function (inner) {
    return '<span class="hsi-sync' + (o.linea ? " hsi-sync-linea" : "") + '">' + inner
      + '<span class="hsi-sync-aviso" role="status"></span></span>';
  };
  if (estado === "ok") {
    return wrap('<span class="hsi-sync-btn" data-estado="guardado">' + esc(T("admin.hsi.sync_en_hsi", null, "En HSI")) + "</span>");
  }
  if (estado === "pendiente") {
    return wrap('<span class="hsi-sync-btn" data-estado="enviando">' + esc(T("admin.hsi.sync_auto", null, "Envío automático a HSI")) + "</span>");
  }
  if (!o.futuro || !o.id) {
    return wrap('<span class="hsi-sync-btn" data-estado="no">' + esc(T("admin.hsi.sync_no_esta", null, "No está en HSI")) + "</span>");
  }
  var texto = estado === "error"
    ? T("admin.hsi.sync_reintentar", null, "Reintentar envío a HSI")
    : T("admin.hsi.sync_enviar", null, "Enviar a HSI");
  return wrap('<button type="button" class="hsi-sync-btn btn" data-estado="' + (estado === "error" ? "error" : "reposo") + '"'
    + ' data-hsi-retry="' + id + '">' + esc(texto) + "</button>");
}

/* Server push, while a «Reintentar / Enviar» request runs and after it:
   the same control, turning, then done or failed with the reason. */
function pintarServidor(boton, estado, detalle) {
  if (!boton) return;
  var caja = boton.closest ? boton.closest(".hsi-sync") : null;
  var aviso = caja && caja.querySelector(".hsi-sync-aviso");
  boton.dataset.estado = estado;
  if (estado === "enviando") {
    boton.disabled = true;
    boton.textContent = T("admin.hsi.sync_enviando", null, "Enviando a HSI…");
  } else if (estado === "guardado") {
    boton.disabled = true;
    boton.textContent = T("admin.hsi.sync_en_hsi", null, "En HSI");
  } else {
    boton.disabled = false;
    boton.textContent = T("admin.hsi.sync_reintentar", null, "Reintentar envío a HSI");
  }
  if (aviso) aviso.textContent = estado === "error" ? (detalle || "") : "";
}

/* The click reached the panel: the extension is not on this computer. The
   DNI goes to the clipboard and the hint offers the extension. */
function sinExtension(boton, o) {
  o = o || {};
  var caja = boton && boton.closest ? boton.closest(".hsi-sync") : null;
  var aviso = o.aviso || (caja && caja.querySelector(".hr-hsi-aviso"));
  if (!aviso) return Promise.resolve();
  var dni = String(o.dni || "");
  var copia = dni && navigator.clipboard && navigator.clipboard.writeText
    ? navigator.clipboard.writeText(dni).then(function () { return true; }, function () { return false; })
    : Promise.resolve(false);
  return copia.then(function (copiado) {
    var texto = !dni ? ""
      : copiado
        ? applyVars(T("admin.hsi.sync_dni_copiado", { dni: dniVisible(dni) }, "Copiamos el DNI {dni}: pegalo en HSI."), { dni: dniVisible(dni) })
        : applyVars(T("admin.hsi.sync_dni", { dni: dniVisible(dni) }, "DNI {dni}: cargalo en HSI."), { dni: dniVisible(dni) });
    aviso.innerHTML = (texto ? esc(texto) + " " : "")
      + (o.tienda ? '<a href="' + esc(o.tienda) + '" target="_blank" rel="noopener">'
        + esc(T("admin.hsi.sync_instalar", null, "Instalá HSI Humanizador para que se cargue con un toque")) + "</a>" : "");
    aviso.hidden = false;
  });
}

/* Where to install the extension, next to «Cargar en HSI» and on «Pacientes
   de hoy» (contraparte técnica, 2026-10-27: «nunca entendí cómo… llegábamos a esa
   pantalla»). `inst` is the server's `core.hsi_extension.install()`:
   {tienda, zip, guia}. The line is always in the page while the server
   offers it and the CSS hides it once the extension marks the page
   (`html[data-hr-humanizador="1"]`, set at document_idle — after the panel
   drew it). `attrs`: extra attributes (an id the screen relies on). */
/* One link on the screen (the product owner, 2026-11-09: «just "Cargar turnos en HSI con
   un toque"; if they press it, a pop-up with all the explanations, how to do
   it too»): the native popover, centred, closed by Esc, a click outside or
   its ×. Each call names its own popover, two lines on one page never open
   each other's. */
var instalarN = 0;
function instalarHtml(inst, o) {
  o = o || {};
  if (!inst || !(inst.tienda || inst.zip)) return "";
  // A line with its own id keeps its popover's name across repaints (the
  // queue repaints on every change of the day); one without counts.
  var propio = /\bid="([^"]+)"/.exec(o.attrs || "");
  var pop = propio ? propio[1] + "-pop" : "hsi-instalar-pop-" + (++instalarN);
  var ext = inst.tienda
    ? '<a href="' + esc(inst.tienda) + '" target="_blank" rel="noopener">' + esc(T("admin.hsi.instalar_tienda", null, "Instalá HSI Humanizador en Chrome")) + "</a>"
    : '<a href="' + esc(inst.zip) + '" download>' + esc(T("admin.hsi.instalar_zip", null, "Descargá HSI Humanizador (ZIP)")) + "</a>";
  var guia = inst.guia
    ? '<p class="hsi-instalar-guia"><a href="' + esc(inst.guia) + '" target="_blank" rel="noopener">' + esc(T("admin.hsi.instalar_guia", null, "Cómo se instala (PDF)")) + "</a></p>"
    : "";
  var titulo = T("admin.hsi.instalar_link", null, "Cargar turnos en HSI con un toque");
  return '<div class="hsi-instalar"' + (o.attrs || "") + ">"
    + '<button type="button" class="hsi-instalar-link" popovertarget="' + pop + '" aria-haspopup="dialog">' + esc(titulo) + "</button>"
    + '<div class="hsi-instalar-pop" id="' + pop + '" popover role="dialog" aria-label="' + esc(titulo) + '">'
    + '<div class="hsi-instalar-head"><p class="hsi-instalar-t">' + esc(titulo) + "</p>"
    + '<button type="button" class="hsi-instalar-x hr-x" popovertarget="' + pop + '" popovertargetaction="hide" aria-label="'
    + esc(T("admin.hsi.instalar_cerrar", null, "Cerrar")) + '"></button></div>'
    + "<p>" + esc(T("admin.hsi.instalar_que", null, "HSI Humanizador es una extensión de Chrome. Al lado de cada turno aparece «Cargar en HSI»: con un toque completa el «Nuevo turno» de HSI en tu pestaña, y vos revisás y apretás Confirmar.")) + "</p>"
    + '<ol class="hsi-instalar-pasos">'
    + "<li>" + ext + "</li>"
    + "<li>" + esc(T("admin.hsi.instalar_paso_hsi", null, "Abrí HSI en otra pestaña de Chrome, con tu usuario.")) + "</li>"
    + "<li>" + esc(T("admin.hsi.instalar_paso_recargar", null, "Recargá esta página: al lado de cada turno aparece «Cargar en HSI».")) + "</li>"
    + "</ol>" + guia + "</div></div>";
}

/* The extension marks the page it runs on: then the click never reaches the
   panel and a list may offer the control on rows the server cannot send. */
function extensionPresente(doc) {
  var d = doc || (typeof document !== "undefined" ? document : null);
  return !!(d && d.documentElement && d.documentElement.dataset && d.documentElement.dataset.hrHumanizador === "1");
}

export { css, ensureCss, extensionHtml, serverHtml, pintarServidor, sinExtension, instalarHtml, extensionPresente };
