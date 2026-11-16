/* static/platform/admin-hours-editor.mjs — "Horarios de atención" del médico:
   el editor multi-centro (centros[] → franjas[] + excepciones[]).

   Canonical ES module (2026-09-05 cutover). Named exports; no browser
   global, no classic twin, no CommonJS wrapper. Lives under
   static/platform/ (row 1.1's "under its owning package") and is loaded
   through window.SANA_STATIC / importStatic. Top-level await on
   platform/i18n.mjs so a consumer that awaits this module also awaits
   i18n — day labels, column heads and help texts are live T() from the
   first render, never the Spanish literal leaking through an unsettled
   import. The consumer (admin-content.js) awaits its own importStatic()
   promise at its render boundary; nothing here renders before i18n is
   bound.

   Rediseño 2026-08-04 (design-loop, "Pinamar Admin Medico Horarios Linear"):
   cada horario es UNA fila de grilla, con las etiquetas escritas una sola
   vez en el encabezado en lugar de una vez por campo; los siete días son un
   único control segmentado en vez de siete casillas sueltas; y las dos
   aclaraciones que se repetían dentro de cada centro colapsan a una nota al
   pie del formulario. Un médico de dos centros pasó de 2088px de alto a
   ~780px.

   El módulo es dueño de su markup Y de su CSS (misma forma que
   calendar-grid.mjs), así que la story de Storybook monta el código que se
   sirve, no una copia. El estado, el guardado y el cableado de eventos
   siguen en admin-content.js: acá sólo se dibuja.

   Los nombres de clase .cnt-franja-row / .cnt-exc-row / .cnt-centro-block y
   todos los atributos data-* son los hooks que admin-content.js ya lee — se
   conservan tal cual. Lo que cambia es la forma, no el contrato. */
"use strict";

function importDep(name, nodeUrl) {
  if (typeof window !== "undefined") {
    var u = (window.SANA_STATIC && window.SANA_STATIC[name]) || null;
    if (!u) {
      if (typeof console !== "undefined" && console.error) {
        console.error("[static] " + name + " is not in window.SANA_STATIC");
      }
      return Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
    }
    return import(u);
  }
  return import(nodeUrl);
}

var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url));function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

// Mismo orden y mismos valores que DIAS_UI en admin-content.js (0 = domingo,
// como lo guarda la agenda del médico).
function dias() {
  return [
    { v: 1, l: T("admin.content.hours.day.mon", null, "Lu") },
    { v: 2, l: T("admin.content.hours.day.tue", null, "Ma") },
    { v: 3, l: T("admin.content.hours.day.wed", null, "Mi") },
    { v: 4, l: T("admin.content.hours.day.thu", null, "Ju") },
    { v: 5, l: T("admin.content.hours.day.fri", null, "Vi") },
    { v: 6, l: T("admin.content.hours.day.sat", null, "Sá") },
    { v: 0, l: T("admin.content.hours.day.sun", null, "Do") }
  ];
}

function tipos() {
  return [
    { v: "cerrado", l: T("admin.content.hours.type.closed", null, "Cierre total") },
    { v: "bloqueado", l: T("admin.content.hours.type.partial", null, "Bloqueo parcial") },
    { v: "extra", l: T("admin.content.hours.type.extra", null, "Turnos extra") }
  ];
}

// El caret del select va como data-URI, así que no puede leer una custom
// property: es un gris que funciona sobre el papel de los dos temas.
var CARET = "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='8' height='5'><path d='M0 0h8L4 5z' fill='%236b706c'/></svg>\")";

var css =
    // La grilla necesita el ancho completo de la columna de trabajo: el editor
    // dejó de ser una pila de campos angosta (admin.html la limitaba a 680px).
    ".cnt-centros{max-width:none;}" +
    // Schedule cards use their own chrome rather than separator lines.
    ".hor-sec{border:1px solid var(--border);padding:16px;margin:0 0 12px;background:var(--panel);border-radius:10px;}" +
    ".hor-head{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:0 0 10px;}" +
    ".hor-lbl{font-size:16px;color:var(--hint-ink,var(--text-2));letter-spacing:0;}" +
    ".hor-head .adm-sel,.hor-head .hor-sel{width:min(36ch,100%);}" +
    ".hor-head-r{margin-left:auto;display:flex;align-items:center;gap:10px;}" +
    // ---- la grilla de horarios: encabezado y filas comparten UNA regla, así
    // no pueden desalinearse ----
    ".hor-cols,.cnt-franja-row{display:grid;align-items:center;gap:8px;" +
    "grid-template-columns:318px 130px 130px 110px minmax(300px,1fr) 48px;}" +
    ".hor-cols{align-items:end;min-height:0;padding:0 0 4px;line-height:1.25;font-size:16px;color:var(--text);}" +
    ".cnt-franja-row{min-height:48px;padding:0;border:0;}" +
    ".cnt-franja-row:first-child{padding-top:1px;}" +
    ".hor-c{min-width:0;}" +
    ".hor-c--num{text-align:left;}" +
    ".hor-c--num .hor-in--num{width:5ch;}" +
    ".hor-c--x{display:flex;justify-content:flex-end;}" +
    ".hor-c>label{display:contents;}" +
    ".hor-c .hor-web-choice{display:inline-flex;align-items:center;gap:10px;font-size:20px;text-align:left;cursor:pointer;min-height:48px;}" +
    ".hor-web-choice input{width:20px;height:20px;margin:0;accent-color:var(--accent);flex:0 0 20px;}" +
    // Shared switch and stepper geometry for doctor and service limits.
    ".hor-limits{padding:20px 0 4px;max-width:780px;font-size:15px;}" +
    ".hor-limits h2{font-size:18px;color:var(--text);margin:0 0 4px;}" +
    ".hor-limit-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:8px 16px;padding:16px 0;}" +
    ".hor-limit-copy label,.hor-limit-copy h3{display:block;font-size:16px;font-weight:700;color:var(--text);margin:0;}" +
    ".hor-limit-copy p{font-size:14px;line-height:1.4;color:var(--hint-ink,var(--text-2));margin:2px 0 0;}" +
    // The switch itself (.hor-switch) lives in admin-direct.css: the queue uses it too.
    ".hor-stepper{display:inline-flex;align-items:center;height:40px;border:1px solid var(--border-strong);border-radius:8px;overflow:hidden;background:var(--panel);}" +
    ".hor-stepper button{width:40px;height:40px;min-height:40px;padding:0;border:0;background:transparent;color:var(--text);font:inherit;font-size:20px;cursor:pointer;}" +
    ".hor-stepper input.hor-in{width:52px;min-width:0;min-height:40px;height:40px;padding:0 2px;border:0;border-radius:0;text-align:center;font-size:16px;box-shadow:none;}" +
    ".hor-stepper button:focus-visible{outline:2px solid var(--focus-border);outline-offset:-3px;}" +
    ".hor-stepper input::placeholder{color:var(--hint-ink,var(--text-2));opacity:1;}" +
    ".hor-limit-sub{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-left:12px;}" +
    ".hor-limit-sub[hidden]{display:none;}.hor-limits .hor-in[type=time],.hor-limits .hor-in[data-time-field]{width:130px;min-height:40px;height:40px;font-size:16px;padding:8px 12px;border-radius:8px;}" +
    // A limit field: the Ajustes number input, wide enough for 365.
    ".hor-in.hor-in--limit{width:9ch;min-height:40px;height:40px;}" +
    ".hor-in--limit::placeholder{color:var(--hint-ink,var(--text-2));opacity:1;}" +
    ".hor-limit-sub .hor-in--limit{width:7ch;}" +
    // In the Horarios rows the field sits at the right edge next to «Hora de
    // apertura»: the same width keeps one left edge down the column.
    ".hor-limit-row>.hor-in--limit{width:130px;}" +
    // In the service form (label left, field right) the three limits share
    // that width too; the time field otherwise stretched across the form.
    ".hor-in.hor-in--time{width:130px;max-width:100%;}.cnt-field>.hor-in--limit{width:130px;}" +
    "@media(max-width:480px){.hor-limit-row{gap:8px 12px;}.hor-limit-copy p{max-width:100%;}}" +
    ".cnt-franja-row .hor-stepper input.hor-in,.cnt-agenda .hor-stepper input.hor-in{width:52px!important;min-width:0;}" +
    // ---- los siete días son UN control segmentado ----
    ".hor-days{display:flex;flex-wrap:wrap;gap:6px;min-height:40px;width:fit-content;max-width:100%;}" +
    ".hor-day{position:relative;cursor:pointer;display:block;}" +
    ".hor-day input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer;}" +
    ".hor-day span{display:flex;align-items:center;justify-content:center;width:44px;height:40px;" +
    "font-size:15px;color:var(--text);border:1px solid var(--border-strong);border-radius:8px;}" +

    ".hor-day:hover span{background:var(--hover-bg);color:var(--hover-ink);}" +
    ".hor-day input:checked+span{background:var(--accent);color:#fff;font-weight:600;border-left-color:var(--accent);}" +
    ".hor-day input:focus-visible+span{outline:2px solid var(--accent);outline-offset:-2px;}" +
    // ---- controles de la fila ----
    ".hor-in{min-height:40px;padding:8px 12px;font:inherit;font-size:16px;line-height:1.2;color:var(--text);" +
    "background:var(--panel);border:1px solid var(--border-strong);border-radius:6px;" +
    "width:100%;box-sizing:border-box;}" +
    ".hor-in:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
    ".hor-in--num{text-align:center;padding:10px 6px;-moz-appearance:textfield;}" +
    // El reloj del <input type=time> se come 20px de una columna que ya es
    // justa (y en un navegador en inglés el campo muestra además AM/PM): la
    // hora se tipea o se sube con las flechas, no hace falta el desplegable.
    ".hor-in[type=time]::-webkit-calendar-picker-indicator{display:none;}" +
    ".hor-in--num::-webkit-inner-spin-button,.hor-in--num::-webkit-outer-spin-button{-webkit-appearance:none;margin:0;}" +
    ".hor-sel{min-height:40px;padding:8px 40px 8px 12px;font:inherit;font-size:16px;line-height:1.2;color:var(--text);" +
    "background:var(--panel) " + CARET + " no-repeat right 14px center;" +
    "border:1px solid var(--border-strong);border-radius:8px;width:100%;box-sizing:border-box;" +
    "-webkit-appearance:none;appearance:none;cursor:pointer;}" +
    ".hor-sel:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
    ".hor-sel--mut{color:var(--hint-ink);}" +
    ".hor-range{display:flex;align-items:center;gap:4px;min-width:0;}" +
    ".hor-range .hor-in{flex:1 1 0;min-width:0;}" +
    ".hor-range i{font-style:normal;color:var(--hint-ink);}" +
    ".hor-mut{font-size:16px;color:var(--hint-ink);}" +
    // The icon, its color and the 44 px zone are the shared close button
    // (.hr-x, human-rounds.css); removing a row keeps its wider cell and red hover.
    ".hor-x{width:48px;min-height:48px;}" +
    ".hor-x:hover{background:var(--danger-soft);color:var(--danger);}" +
    // El combo tipeable del servicio se dimensiona solo por su contenido
    // (comboHtml pone un width en ch): dentro de una celda de grilla manda la
    // columna, no el texto.
    ".cnt-franja-row .cnt-franja-svc{display:block;margin:0;min-width:0;}" +
    ".cnt-franja-row .cmb{display:block;min-width:0;max-width:100%;}" +
    ".cnt-franja-row .cmb-in{width:100%!important;min-width:0;min-height:40px;box-sizing:border-box;font-size:16px;}" +
    ".hor-acts{display:flex;align-items:center;gap:8px;padding-top:10px;}" +
    // .hor-add (adding hours, closures, centers) is the panel's secondary
    // button: its whole look lives in admin-direct.css (2026-11-08).
    ".hor-x:focus-visible{outline:2px solid var(--focus-border);outline-offset:2px;}" +
    // ---- cierres: el mismo idioma de grilla, con su propio encabezado ----
    ".hor-cierres{display:block;padding-top:14px;}" +
    ".hor-closure-heading{line-height:1.6;margin-bottom:8px;text-wrap:pretty;}" +
    ".hor-section-label{font-size:16px;font-weight:600;color:var(--text);margin-right:12px;}" +
    ".hor-closure-description,.hor-capacity-description{font-size:16px;line-height:1.4;color:var(--text);margin:0;text-wrap:pretty;}" +
    ".hor-capacity-description{margin:8px 0;}" +
    ".cnt-cupos .hor-capacity-description{flex-basis:100%;margin:0;}" +
    ".cnt-cupos .hor-sel{width:auto;min-width:175px;}" +
    ".hor-cierres-b{flex:1;min-width:0;}" +
    ".hor-holidays{padding-top:14px;}" +
    ".hor-hol-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px;}" +
    ".hor-hol-row{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:16px;line-height:1.4;color:var(--text);}" +
    ".hor-hol-date{min-width:110px;}" +
    ".hor-exc-cols,.cnt-exc-row{display:grid;align-items:center;gap:8px;" +
    "grid-template-columns:160px 170px 170px 260px 105px minmax(200px,1fr) 48px;}" +
    // Mientras todos los cierres del centro sean totales, "Horas" y "Cada" no
    // dicen nada: la grilla se queda en las cuatro columnas que sí se usan.
    ".hor-exc--simple .hor-exc-cols,.hor-exc--simple .cnt-exc-row{" +
    "grid-template-columns:160px 170px 170px minmax(200px,1fr) 48px;}" +
    ".hor-exc--simple .hor-c--horas,.hor-exc--simple .hor-c--cada{display:none;}" +
    ".hor-exc-cols{align-items:end;height:auto;padding:0 0 4px;line-height:1.25;font-size:16px;color:var(--text);}" +
    ".cnt-exc-row{min-height:48px;padding:0;border:0;}" +
    ".cnt-exc-row:first-child{border-top:0;}" +
    // ---- pie ----
    ".hor-foot{display:flex;align-items:center;gap:10px;padding-top:14px;}" +
    ".hor-legend{padding-top:16px;max-width:720px;}" +
    ".hor-legend p{margin:0 0 4px;font-size:16px;line-height:1.4;color:var(--hint-ink);}" +
    // ---- angosto: la grilla no cabe, así que cada campo recupera su etiqueta
    // (el encabezado desaparece) y la fila envuelve en vez de desbordar ----
    "@media(max-width:1800px){" +
      ".hor-cols,.hor-exc-cols{display:none;}" +
      ".cnt-franja-row,.cnt-exc-row{display:flex;flex-wrap:wrap;align-items:center;gap:9px 14px;" +
        "padding:16px 0;}" +
      ".cnt-franja-row .hor-c,.cnt-exc-row .hor-c{display:flex;align-items:center;gap:6px;}" +
      ".cnt-franja-row .hor-c[data-l]::before,.cnt-exc-row .hor-c[data-l]::before{" +
        "content:attr(data-l);font-size:16px;color:var(--text);}" +
      ".cnt-franja-row .hor-c--x,.cnt-exc-row .hor-c--x{margin-left:auto;}" +
      ".cnt-franja-row .hor-in,.cnt-exc-row .hor-in{width:auto;min-width:0;}" +
      ".cnt-franja-row .hor-in--num,.cnt-exc-row .hor-in--num{width:5ch;}" +
      ".cnt-franja-row .hor-sel,.cnt-exc-row .hor-sel{width:min(36ch,100%);min-width:18ch;}" +
      ".cnt-franja-row .cnt-franja-svc{flex:1 1 220px;}" +
      ".cnt-franja-row .cnt-franja-svc .cmb{flex:1;}" +
      ".cnt-franja-row .hor-c,.cnt-exc-row .hor-c{max-width:100%;}" +
      ".cnt-franja-row .hor-c--num{justify-content:space-between;}" +
    "}" +
    "@media(max-width:720px){" +
      ".cnt-franja-row{position:relative;}" +
      ".cnt-franja-row .hor-c--x{position:absolute;right:0;top:0;}" +

      ".hor-head{align-items:flex-start;}" +
      ".hor-head-r{margin-left:0;}" +
      ".hor-lbl{padding-top:13px;}" +
      ".hor-in,.hor-sel,.cnt-franja-row .cmb-in,.hor-c .hor-web-choice{font-size:16px;}" +
      ".cnt-franja-row .hor-c,.cnt-exc-row .hor-c{flex:1 1 100%;justify-content:space-between;}" +
      ".cnt-franja-row .hor-c:first-child{flex-direction:column;align-items:flex-start;gap:6px;}" +
      ".cnt-franja-row .hor-c--x,.cnt-exc-row .hor-c--x{flex:0 0 auto;margin-left:0;}" +
      ".cnt-franja-row .hor-c .hor-in,.cnt-exc-row .hor-c .hor-in,.cnt-franja-row .hor-c .hor-sel,.cnt-exc-row .hor-c .hor-sel{width:min(20ch,68vw);}" +
      ".cnt-franja-row .cnt-franja-svc{flex-basis:100%;}" +
      ".cnt-franja-row .cnt-franja-svc .cmb{width:100%;}" +
      ".hor-c .hor-in--num{width:8ch;}" +
      ".hor-range{width:min(22ch,68vw);}" +
      ".hor-range .hor-in{width:auto!important;}" +
    "}";

// ---------------------------------------------------------------- horarios

function diasHtml(selectedDays) {
    var on = selectedDays || [];
    return '<div class="hor-days">' + dias().map(function (d) {
      return '<label class="hor-day"><input type="checkbox" data-franja-dia value="' + d.v + '"' +
        (on.indexOf(d.v) !== -1 ? " checked" : "") + ' aria-label="' + esc(d.l) + '"><span>' +
        d.l + "</span></label>";
    }).join("") + "</div>";
  }

function cell(label, inner, extraCls, attrs) {
    return '<div class="hor-c' + (extraCls ? " " + extraCls : "") + '"' +
      (label ? ' data-l="' + esc(label) + '"' : "") + (attrs || "") + ">" + inner + "</div>";
  }

  // El servicio de la franja: en el panel es un combo tipeable (la lista de
  // servicios de un centro es larga), así que admin-content.js pasa el suyo.
  // Sin opciones — Storybook, o un centro todavía sin elegir — cae a un select
  // nativo con la misma clase que lee el guardado.
  function servicioDefault(fr, centro, opts) {
    var items = (opts.servicios ? opts.servicios(centro) : []) || [];
    var actual = String(fr.servicio || "");
    var elegido = items.filter(function (x) { return String(x.v) === actual; })[0];
    var vacio = centro
      ? T("admin.content.hours.service.none", null, "Sin especificar")
      : T("admin.content.hours.service.choose_center_first", null, "Elegí un centro primero");
    return '<select class="hor-sel fr-svc-sel' + (elegido ? "" : " hor-sel--mut") + '">' +
      '<option value=""' + (elegido ? "" : " selected") + ">" + esc(vacio) + "</option>" +
      items.map(function (x) {
        return '<option value="' + esc(x.v) + '"' + (elegido && String(x.v) === actual ? " selected" : "") +
          ">" + esc(x.t) + "</option>";
      }).join("") + "</select>";
  }

  function colsHtml() {
    return '<div class="hor-cols">' +
      "<div>" + T("admin.content.hours.col.days", null, "Días") + "</div>" +
      "<div>" + T("admin.content.hours.col.from", null, "Desde") + "</div>" +
      "<div>" + T("admin.content.hours.col.to", null, "Hasta") + "</div>" +
      "<div>" + T("admin.content.hours.col.every_min", null, "Cada (min)") + "</div>" +
      "<div>" + T("admin.content.hours.col.service", null, "Servicio") + "</div>" +
      "<div></div></div>";
  }

  function franjaRowHtml(ci, fi, fr, centro, opts) {
    opts = opts || {};
    fr = fr || {};
    var cupos = fr.cupos || {};
    var web = cupos.web === 0 ? 0 : 1;
    var svc = opts.servicioHtml
      ? opts.servicioHtml(fr, centro, ci, fi)
      : servicioDefault(fr, centro, opts);
    var etqDias = T("admin.content.hours.col.days", null, "Días");
    var etqDesde = T("admin.content.hours.col.from", null, "Desde");
    var etqHasta = T("admin.content.hours.col.to", null, "Hasta");
    var etqCada = T("admin.content.hours.col.every_min", null, "Cada (min)");
    var etqServicio = T("admin.content.hours.col.service", null, "Servicio");
    var quitarHorario = T("admin.content.hours.remove_slot", null, "Quitar horario");
    // Reserva web y sobreturnos ya no se dibujan por franja (ver "Límites"),
    // pero el guardado los lee fila por fila: quedan como inputs ocultos con
    // el valor propio de cada franja, que el disclosure del médico pisa sólo
    // cuando el operador lo decide explícitamente.
    var percent = overbookingPercent(cupos);
    return '<div class="cnt-franja-row" data-fi="' + fi + '">' +
      cell(etqDias, diasHtml(fr.dias)) +
      cell(etqDesde, '<input type="time" class="hor-in" data-franja-desde value="' + esc(fr.desde || "09:00") + '">') +
      cell(etqHasta, '<input type="time" class="hor-in" data-franja-hasta value="' + esc(fr.hasta || "12:00") + '">') +
      cell(etqCada, stepperHtml('min="5" step="5" data-franja-cada', fr.cada_min || 20, null, etqCada)) +
      // La celda lleva ci/fi: admin-content.js repinta ADENTRO cuando llega la
      // lista de servicios compatibles, sin tocar la celda ni su lugar en la
      // grilla.
      cell(etqServicio, svc, "cnt-franja-svc",
        ' data-servicio-ci="' + ci + '" data-servicio-fi="' + fi + '"') +
      '<input type="hidden" data-cupo-web value="' + web + '">' +
      '<input type="hidden" data-cupo-reservado value="' + esc(cupos.reservado || 0) + '">' +
      '<input type="hidden" data-cupo-sobreturnos value="' + esc(cupos.sobreturnos || 0) + '">' +
      '<input type="hidden" data-overbooking-enabled value="' + (percent > 0 ? 1 : 0) + '">' +
      '<input type="hidden" data-overbooking-percent value="' + esc(percent > 0 ? percent : 0) + '">' +
      cell("", '<button type="button" class="hor-x hr-x" data-remove-franja="' + ci + ":" + fi + '" title="' + esc(quitarHorario) + '" aria-label="' + esc(quitarHorario) + '"></button>', "hor-c--x") +
      "</div>";
  }

  // ---------------------------------------------------------------- cierres

  function webChoiceHtml(web) {
    return '<label class="hor-web-choice"><input type="checkbox" data-cupo-web aria-label="' + esc(T("admin.content.hours.web.label", null, "Permitir 1 reserva web por horario")) + '"' +
      (web ? ' checked' : '') + '><span>' + esc(T("admin.content.hours.web.per_slot", null, "1 paciente por horario")) + '</span></label>';
  }

  // Legacy per-slot allowances retain the same total allowance when saved
  // as a percentage: one extra at every regular time equals 100% extra.
  function overbookingPercent(cupos) {
    var c = cupos || {};
    return c.overbooking_percent == null ? (c.sobreturnos || 0) * 100 : c.overbooking_percent;
  }

  // ---- Límites: decisiones del médico, no de una franja ----
  // La reserva web y el porcentaje de sobreturnos se muestran UNA vez, con el
  // estado agregado de todas las franjas. Si datos viejos difieren entre
  // franjas, la casilla queda indeterminada (data-mixed) y cada franja
  // conserva su propio valor al guardar; tildar destilda aplica a todas.
  function stepperHtml(attrs, value, inherited, label) {
    return '<span class="hor-stepper"><button type="button" data-step="-1" aria-label="' + esc(T('admin.content.limits.decrease', {label: label}, 'Reducir {label}')) + '">−</button>' +
      '<input type="number" class="hor-in hor-in--num" ' + attrs + ' aria-label="' + esc(label) + '" value="' + esc(value == null ? '' : value) + '"' +
      (inherited == null ? '' : ' placeholder="' + esc(inherited) + '" data-inherited="' + esc(inherited) + '"') + '>' +
      '<button type="button" data-step="1" aria-label="' + esc(T('admin.content.limits.increase', {label: label}, 'Aumentar {label}')) + '">+</button></span>';
  }
  // A booking limit is a plain number field, the same control as Ajustes ›
  // Turnos (pinamar-turnos 03.11, decision 2a): an empty field shows the
  // inherited number as its placeholder.
  function limitFieldHtml(attrs, value, inherited, label) {
    return '<input type="number" inputmode="numeric" class="hor-in hor-in--limit" ' + attrs + ' aria-label="' + esc(label) + '" value="' + esc(value == null ? '' : value) + '"' +
      (inherited == null ? '' : ' placeholder="' + esc(inherited) + '" data-inherited="' + esc(inherited) + '"') + '>';
  }
  function switchHtml(attrs, checked, label) {
    return '<label class="hor-switch"><input type="checkbox" ' + attrs + (checked ? ' checked' : '') + ' aria-label="' + esc(label) + '"><span></span></label>';
  }
  function limitRowHtml(title, hint, control, id, sub, originHtml) {
    return '<div class="hor-limit-row"><div class="hor-limit-copy">' + (id ? '<label for="' + esc(id) + '">' : '<h3>') + esc(title) + (id ? '</label>' : '</h3>') +
      '<p>' + [hint ? esc(hint) : '', originHtml || ''].filter(Boolean).join(' ') + '</p></div>' + control + (sub || '') + '</div>';
  }
  // Whether the number is this doctor's/service's own or inherited, and from
  // where (booking_limits / booking_horizon resolve doctor → service → site):
  // «Heredado del servicio Kinesiología: 10.» while the field is empty,
  // «Propio de este médico. Vacío: hereda 10.» once it holds a number. The
  // two texts ride on the element and swap as the field is typed into.
  // A center's window inherits the doctor's own number first ('doctor'); an
  // origin nobody told us ('unknown') still says the number it falls back to.
  function limitOriginHtml(prefix, value, inherited, from) {
    from = from || { from: 'site' };
    var heredado = from.from === 'service'
      ? T('admin.content.limits.from_service', { n: inherited, name: from.name }, 'Heredado del servicio {name}: {n}.')
      : from.from === 'services'
        ? T('admin.content.limits.from_services', { n: inherited }, 'Heredado de sus servicios: {n}.')
        : from.from === 'doctor'
          ? T('admin.content.limits.from_doctor', { n: inherited }, 'Heredado del médico: {n}.')
          : from.from === 'unknown'
            ? T('admin.content.limits.from_unknown', { n: inherited }, 'Heredado: {n}.')
            : T('admin.content.limits.from_site', { n: inherited }, 'Heredado del sitio: {n}.');
    var propio = prefix === 'med'
      ? T('admin.content.limits.own_doctor', { n: inherited }, 'Propio de este médico. Vacío: hereda {n}.')
      : prefix === 'center'
        ? T('admin.content.limits.own_center', { n: inherited }, 'Propio de este centro. Vacío: hereda {n}.')
        : T('admin.content.limits.own_service', { n: inherited }, 'Propio de este servicio. Vacío: hereda {n} del sitio.');
    var own = value != null && String(value).trim() !== '';
    return '<span data-limit-origin data-own-text="' + esc(propio) + '" data-inherited-text="' + esc(heredado) + '">' +
      esc(own ? propio : heredado) + '</span>';
  }
  // `formRow(label, html)`, when given, lays the three limits out as rows of
  // the caller's own form (the service form: label left, field right, the
  // explanation under the field) instead of the Horarios limit rows.
  function bookingLimitsHtml(prefix, item, defaults, formRow) {
    defaults = defaults || {};
    var patient = T('admin.content.limits.patient', null, 'Turnos pendientes por paciente');
    var days = T('admin.content.booking_window.days', null, 'Días de anticipación');
    var opens = T('admin.content.booking_window.opens', null, 'Hora de apertura de turnos');
    var max = defaults.patient || 1, horizon = defaults.days || 31;
    var rows = [
      [patient, prefix === 'med'
        ? T('admin.content.limits.patient_doctor_what', null, 'Cuántos turnos puede tener a la vez en el mismo servicio.')
        : T('admin.content.limits.patient_service_what', null, 'Cuántos turnos puede tener a la vez en este servicio.'),
      limitFieldHtml('id="' + prefix + '-turnos-max" min="1" max="60" step="1"' + (prefix === 'med' ? ' data-limits-patient-max data-limits-patient-max-count' : ''), item.turnos_activos_max, max, patient), prefix + '-turnos-max',
      limitOriginHtml(prefix, item.turnos_activos_max, max, prefix === 'med' ? defaults.patientFrom : null)],
      [days, T('admin.content.limits.days_what', null, 'Hasta cuántos días antes se puede reservar.'),
      limitFieldHtml('id="' + prefix + '-dias-anticipacion" min="1" max="365" step="1"', item.dias_anticipacion, horizon, days), prefix + '-dias-anticipacion',
      limitOriginHtml(prefix, item.dias_anticipacion, horizon, prefix === 'med' ? defaults.daysFrom : null)],
      [opens, T('admin.content.limits.opens_hint', null, 'Desde qué hora del día se publican los turnos nuevos.'),
      '<input class="hor-in hor-in--time" id="' + prefix + '-apertura-turnos" type="time" value="' + esc(item.apertura_turnos || '') + '">', prefix + '-apertura-turnos', '']
    ];
    return rows.map(function (r) {
      return formRow
        ? formRow(r[0], r[2] + '<p class="cnt-hint">' + esc(r[1]) + (r[4] ? ' ' + r[4] : '') + '</p>')
        : limitRowHtml(r[0], r[1], r[2], r[3], null, r[4]);
    }).join('');
  }
  function limitsHtml(centros, turnosMax, options) {
    options = options || {};
    var franjas = (centros || []).flatMap(function (c) { return c.franjas || []; });
    function estado(getter) {
      var vals = franjas.map(getter), first = vals[0];
      return {mixed: vals.some(function(v) { return v !== first; }), value:first};
    }
    var web = estado(function(fr) { return (fr.cupos || {}).web === 0 ? 0 : 1; });
    var pct = estado(function(fr) { return overbookingPercent(fr.cupos); });
    var pctVal = pct.mixed ? '' : (pct.value == null || pct.value <= 0 ? 20 : pct.value);
    var webLabel = T('admin.content.limits.web', null, 'Reserva por la web');
    var extraLabel = T('admin.content.limits.overbooking', null, 'Sobreturnos');
    return '<section class="hor-limits"><h2>' + esc(T('admin.content.limits.heading', null, 'Límites de reserva')) + '</h2>' +
      limitRowHtml(webLabel, T('admin.content.limits.web_hint', null, 'Los pacientes sacan turno solos. Apagado: solo el personal asigna turnos.'),
      switchHtml('data-limits-web' + (web.mixed ? ' data-mixed="1"' : ''), !web.mixed && web.value !== 0, webLabel)) +
      limitRowHtml(extraLabel, T('admin.content.limits.overbooking_hint', null, 'Turnos extra del personal en horarios ya ocupados. Nunca se ofrecen por la web.'),
      switchHtml('data-limits-overbooking' + (pct.mixed ? ' data-mixed="1"' : ''), !pct.mixed && pct.value > 0, extraLabel), null,
      '<div class="hor-limit-sub"' + (pct.mixed || pct.value > 0 ? '' : ' hidden') + '><span>' + esc(T('admin.content.limits.up_to', null, 'Hasta')) + '</span>' +
      limitFieldHtml('min="1" step="1" data-limits-overbooking-percent' + (pct.mixed || pct.value > 0 ? '' : ' disabled'), pctVal, null, extraLabel) +
      '<span>' + esc(T('admin.content.limits.percent_day', null, '% más por día')) + '</span></div>') +
      // Los tres límites de reserva son del administrador (el servidor los
      // descarta si los manda un médico): en sesión de médico no se dibujan,
      // para no prometer un guardado que nunca va a llegar.
      (options.bookingLimits === false ? '' :
      bookingLimitsHtml('med', {turnos_activos_max:turnosMax, dias_anticipacion:options.diasAnticipacion, apertura_turnos:options.aperturaTurnos}, {patient:options.patientDefault, days:options.daysDefault, patientFrom:options.patientFrom, daysFrom:options.daysFrom})) + '</section>';
  }

  function capacityDescriptionHtml() {
    return '<p class="hor-capacity-description">' + esc(T("admin.content.hours.capacity.description", null,
      "Reserva web: 1 paciente por horario; desmarcada, solo lo asigna el personal.")) + '</p>';
  }

  function closureHeadingHtml() {
    return '<div class="hor-closure-heading"><span class="hor-section-label">' + esc(T("admin.content.hours.closures.label", null, "Cierres")) + '</span>' +
      '<span class="hor-closure-description">' + esc(T("admin.content.hours.legend.closure", null, "Sin atención en estas fechas; un bloqueo parcial afecta solo las horas indicadas.")) + '</span></div>';
  }

  function excColsHtml() {
    return '<div class="hor-exc-cols">' +
      "<div>" + T("admin.content.hours.col.type", null, "Tipo") + "</div>" +
      "<div>" + T("admin.content.hours.col.from", null, "Desde") + "</div>" +
      "<div>" + T("admin.content.hours.col.to", null, "Hasta") + "</div>" +
      '<div class="hor-c--horas">' + T("admin.content.hours.col.hours", null, "Horas") + "</div>" +
      '<div class="hor-c--cada">' + T("admin.content.hours.col.every_min", null, "Cada (min)") + "</div>" +
      "<div>" + T("admin.content.hours.col.reason", null, "Motivo") + "</div><div></div></div>";
  }

  // Un centro cuyos cierres son todos totales no necesita las columnas de
  // horas: la grilla se angosta a las cuatro que sí tienen dato.
  function excWrapCls(cierres) {
    var soloTotales = (cierres || []).every(function (e) {
      return !e || !e.tipo || e.tipo === "cerrado";
    });
    return "hor-cierres-b" + (soloTotales ? " hor-exc--simple" : "");
  }

  function excRowHtml(ci, ei, exc) {
    exc = exc || {};
    var tipo = exc.tipo || "cerrado";
    var esExtra = tipo === "extra";
    var esRango = tipo === "cerrado";
    var fecha = exc.fecha || "";
    var tipoSel = '<select class="hor-sel" data-exc-tipo>' + tipos().map(function (t) {
      return '<option value="' + t.v + '"' + (t.v === tipo ? " selected" : "") + ">" + t.l + "</option>";
    }).join("") + "</select>";

    var desde = esRango
      ? '<input type="date" class="hor-in" data-exc-desde-fecha value="' + esc(exc.desde_fecha || fecha) + '">'
      : '<input type="date" class="hor-in" data-exc-fecha value="' + esc(fecha) + '">';
    var hasta = esRango
      ? '<input type="date" class="hor-in" data-exc-hasta-fecha value="' + esc(exc.hasta_fecha || fecha) + '">'
      : '<span class="hor-mut">' + T("admin.content.hours.exc.same_day", null, "el mismo día") + '</span>';
    var horas = esRango
      ? '<span class="hor-mut">' + T("admin.content.hours.exc.all_day", null, "todo el día") + '</span>'
      : '<span class="hor-range"><input type="time" class="hor-in" data-exc-desde value="' + esc(exc.desde || "09:00") +
        '"><i>–</i><input type="time" class="hor-in" data-exc-hasta value="' + esc(exc.hasta || "12:00") + '"></span>';
    var cada = esExtra
      ? '<input type="number" min="5" step="5" class="hor-in hor-in--num" data-exc-cada value="' + esc(exc.cada_min || 20) + '">'
      : '<span class="hor-mut">—</span>';

    var etqTipo = T("admin.content.hours.col.type", null, "Tipo");
    var etqDesde = T("admin.content.hours.col.from", null, "Desde");
    var etqHasta = T("admin.content.hours.col.to", null, "Hasta");
    var etqHoras = T("admin.content.hours.col.hours", null, "Horas");
    var etqCada = T("admin.content.hours.col.every_min", null, "Cada (min)");
    var etqMotivo = T("admin.content.hours.col.reason", null, "Motivo");
    var motivoPh = T("admin.content.hours.exc.reason_placeholder", null, "Vacaciones, licencia, guardia…");
    var quitarCierre = T("admin.content.hours.remove_closure", null, "Quitar cierre");

    return '<div class="cnt-exc-row" data-ei="' + ei + '">' +
      cell(etqTipo, tipoSel) +
      cell(etqDesde, desde) +
      cell(etqHasta, hasta) +
      cell(etqHoras, horas, "hor-c--horas") +
      cell(etqCada, cada, "hor-c--cada") +
      cell(etqMotivo, '<input type="text" class="hor-in" data-exc-motivo placeholder="' + esc(motivoPh) + '" value="' + esc(exc.motivo || "") + '">') +
      cell("", '<button type="button" class="hor-x hr-x" data-remove-excepcion="' + ci + ":" + ei + '" title="' + esc(quitarCierre) + '" aria-label="' + esc(quitarCierre) + '"></button>', "hor-c--x") +
      "</div>";
  }

  // Site holidays (Settings › Feriados, site_holidays.py) close every agenda
  // on those dates. Shown read-only next to the agenda's own closures so
  // nobody is surprised by an empty day; they are edited only in Settings
  // (`href`, admins only, links the heading there). `dia` is the server's
  // friendly date (site_schedule._friendly_date), never re-formatted here.
  function holidayRowsHtml(feriados, href) {
    feriados = (feriados || []).filter(function (h) { return h && h.fecha; });
    if (!feriados.length) return "";
    // The heading carries what a holiday means here (no turnos are given, as
    // on «Mis agendas»); each row is the date, then the name — never
    // «Feriado:» on every row (the product owner, 2026-10-27).
    var label = esc(T("admin.content.hours.holidays.label", null, "Feriados sin turnos"));
    return '<div class="hor-holidays" data-site-holidays>' +
      '<div class="hor-closure-heading"><span class="hor-section-label">' +
      (href ? '<a href="' + esc(href) + '">' + label + "</a>" : label) + "</span></div>" +
      '<ul class="hor-hol-list">' + feriados.map(function (h) {
        return '<li class="hor-hol-row"><span class="hor-hol-date">' + esc(h.dia || h.fecha) + "</span>" +
          "<span>" + esc(h.nombre || "") + "</span></li>";
      }).join("") + "</ul></div>";
  }

  // ---------------------------------------------------------------- centros

  // The doctor's booking window AT this center (admin only: the server keeps
  // the stored one when a doctor saves their own hours).
  // Its own name says it is this center's, and its one line says where the
  // number comes from — the same «Heredado … / Propio …» line as the doctor's
  // window under Límites de reserva, so the two are never read as one field.
  function centroWindowHtml(ci, c, opts) {
    if (opts.bookingLimits === false) return '';
    var days = T('admin.content.booking_window.days_center', null, 'Días de anticipación en este centro');
    var doctorOwn = opts.doctorDays != null && String(opts.doctorDays).trim() !== '';
    var inherited = doctorOwn ? opts.doctorDays : (opts.daysDefault || 31);
    var from = doctorOwn ? { from: 'doctor' } : (opts.daysFrom || { from: 'unknown' });
    var id = 'med-ci' + ci + '-dias-anticipacion';
    return '<div class="hor-limits hor-limits--center">' + limitRowHtml(days, '',
      limitFieldHtml('id="' + id + '" min="1" max="365" step="1" data-centro-dias', c.dias_anticipacion, inherited, days),
      id, '', limitOriginHtml('center', c.dias_anticipacion, inherited, from)) + '</div>';
  }

  function centroBlockHtml(ci, c, opts) {
    opts = opts || {};
    c = c || {};
    var franjas = (c.franjas && c.franjas.length)
      ? c.franjas
      : [{ dias: [], desde: "09:00", hasta: "12:00", cada_min: 20 }];
    c.franjas = franjas;
    var cierres = c.excepciones || [];
    var centroSel = opts.centroOptions
      ? '<select class="hor-sel" data-centro-select>' + opts.centroOptions(c.centro) + "</select>"
      : '<select class="hor-sel" data-centro-select><option>' + esc(c.centro || "") + "</option></select>";

    return '<div class="hor-sec cnt-centro-block" data-ci="' + ci + '">' +
      '<div class="hor-head">' +
        '<span class="hor-lbl">' + esc(T("admin.content.hours.center.label", null, "Centro")) + '</span>' + centroSel +
        (opts.puedeQuitarCentro
          ? '<div class="hor-head-r"><button type="button" class="hor-add cnt-remove" data-remove-centro="' + ci + '">' +
            esc(T("admin.content.hours.center.remove", null, "Quitar centro")) + '</button></div>'
          : "") +
      "</div>" +

      colsHtml() +
      franjas.map(function (fr, fi) { return franjaRowHtml(ci, fi, fr, c.centro, opts); }).join("") +
      '<div class="hor-acts">' +
        '<button type="button" class="hor-add" data-add-franja="' + ci + '">' +
        esc(T("admin.content.hours.add.slot", null, "+ Agregar horario")) + '</button>' +
      "</div>" +
      centroWindowHtml(ci, c, opts) +
      '<div class="hor-cierres">' +
        (cierres.length ? closureHeadingHtml() : '') +
        '<div class="' + excWrapCls(cierres) + '">' +
          (cierres.length ? excColsHtml() + cierres.map(function (e, ei) { return excRowHtml(ci, ei, e); }).join("") : "") +
          '<div class="hor-acts"><button type="button" class="hor-add" data-add-excepcion="' + ci + '">' +
          esc(T("admin.content.hours.add.closure", null, "+ Agregar cierre")) + '</button></div>' +
        "</div>" +
      "</div>" +
      "</div>";
  }

  // Compatibility export: descriptions now live beside the relevant controls.
  function legendHtml() {
    return '';
  }

  function editorHtml(centros, opts) {
    opts = opts || {};
    centros = centros || [];
    var multi = centros.length > 1;
    return '<div class="cnt-centros" id="med-centros-editor">' +
      centros.map(function (c, ci) {
        return centroBlockHtml(ci, c, {
          centroOptions: opts.centroOptions,
          servicios: opts.servicios,
          servicioHtml: opts.servicioHtml,
          bookingLimits: opts.bookingLimits,
          daysDefault: opts.daysDefault,
          daysFrom: opts.daysFrom,
          doctorDays: opts.diasAnticipacion,
          puedeQuitarCentro: multi
        });
      }).join("") +
      // Once for the whole editor: a holiday closes every center alike.
      holidayRowsHtml(opts.feriados, opts.feriadosHref) +
      limitsHtml(centros, opts.turnosActivosMax, opts) +
      '<div class="hor-foot">' +
        '<button type="button" class="hor-add" data-add-centro="1">' +
        esc(T("admin.content.hours.add.center", null, "+ Agregar centro")) + '</button>' +
      "</div>" +
      legendHtml() +
      "</div>";
  }

  // Monta el editor en un host y devuelve el host — la vía que usa Storybook.
  function render(host, centros, opts) {
    host.innerHTML = editorHtml(centros, opts);
    // data-mixed marca franjas con datos viejos que difieren: la casilla va
    // indeterminada, una propiedad DOM que el string de render no puede llevar.
    each(host, "[data-mixed]", function (el) { el.indeterminate = true; });
    return host;
  }

  // Delegation survives schedule-row rerenders and also powers the real
  // component's stories. Disabled settings must not block form validation.
  // Los "Límites" escriben los inputs ocultos de TODAS las franjas del
  // editor: el guardado de admin-content.js sigue leyendo fila por fila y
  // no cambia; un cambio explícito del operador es lo único que unifica.
  if (typeof document !== "undefined") document.addEventListener("change", function (event) {
    var t = event.target;
    if (!t || !t.matches) return;
    var editor = t.closest ? t.closest("#med-centros-editor") : null;
    if (!editor) {
      return;
    }
    if (t.matches("[data-limits-web]")) {
      var on = t.checked ? "1" : "0";
      each(editor, "[data-cupo-web]", function (el) { el.value = on; });
      t.removeAttribute("data-mixed");
      t.indeterminate = false;
      return;
    }
    if (t.matches("[data-limits-overbooking]")) {
      var pctIn = editor.querySelector("[data-limits-overbooking-percent]");
      if (pctIn) { pctIn.disabled = !t.checked; pctIn.closest(".hor-limit-sub").hidden = !t.checked; }
      applyOverbooking(editor, t.checked ? Math.max(1, +(pctIn && pctIn.value) || 20) : 0);
      t.removeAttribute("data-mixed");
      t.indeterminate = false;
      return;
    }
    if (t.matches("[data-limits-overbooking-percent]")) {
      applyOverbooking(editor, Math.max(0, +t.value || 0));
      return;
    }
    if (t.matches("[data-limits-patient-max]") || t.matches("[data-limits-patient-max-count]")) {
      editor.setAttribute("data-limits-touched", "1");
      return;
      return;
    }
  });

  function each(root, sel, fn) {
    var els = root.querySelectorAll(sel);
    for (var i = 0; i < els.length; i++) fn(els[i]);
  }

  function applyOverbooking(editor, percent) {
    each(editor, "[data-overbooking-enabled]", function (el) { el.value = percent > 0 ? "1" : "0"; });
    each(editor, "[data-overbooking-percent]", function (el) { el.value = String(percent); });
  }

  if (typeof document !== 'undefined') document.addEventListener('click', function(event) {
    var button = event.target.closest && event.target.closest('[data-step]');
    if (!button) return;
    var input = button.closest('.hor-stepper').querySelector('input');
    if (!input || input.disabled) return;
    var minimum = Number(input.min || 1), maximum = input.max ? Number(input.max) : Infinity;
    var direction = Number(button.dataset.step), step = Number(input.step || 1);
    var empty = input.value.trim() === '';
    var value = empty ? Number(input.dataset.inherited || minimum) : Number(input.value);
    if (!Number.isFinite(value)) value = minimum;
    input.value = direction < 0 && value <= minimum && input.hasAttribute('data-inherited') ? ''
      : String(Math.min(maximum, Math.max(minimum, empty ? value : value + direction * step)));
    input.dispatchEvent(new Event('input', {bubbles:true}));
    input.dispatchEvent(new Event('change', {bubbles:true}));
  });

  // «Propio» / «Heredado» follows the field as it is typed into or emptied.
  if (typeof document !== 'undefined') document.addEventListener('input', function(event) {
    var input = event.target;
    if (!input || !input.hasAttribute || !input.hasAttribute('data-inherited')) return;
    var row = input.closest('.hor-limit-row, .cnt-row');
    var origin = row && row.querySelector('[data-limit-origin]');
    if (!origin) return;
    origin.textContent = input.value.trim() === '' ? origin.dataset.inheritedText : origin.dataset.ownText;
  });

  // Estado inicial que no cabe en el string de render: la casilla
  // indeterminada (una propiedad DOM, no un atributo) y el arrastre tardío
  // de #med-turnos-max, que se dibuja después que este editor.
  if (typeof document !== "undefined" && typeof MutationObserver !== "undefined") {
    new MutationObserver(function () {
      var editor = document.getElementById("med-centros-editor");
      if (!editor) return;
      each(editor, "[data-mixed]", function (el) { el.indeterminate = true; });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  // En el panel el CSS se inyecta al evaluarse el módulo (el import que hace
  // admin-content.js); en Storybook la story puede además poner `css` en su
  // propio <style> (igual que calendar-grid.mjs) — mismo texto, idempotente.
  if (typeof document !== "undefined" && document.head) {
    var tag = document.createElement("style");
    tag.textContent = css;
    document.head.appendChild(tag);
  }

export {
  css,
  dias,
  render,
  editorHtml,
  centroBlockHtml,
  franjaRowHtml,
  limitsHtml,
  stepperHtml,
  switchHtml,
  limitRowHtml,
  limitFieldHtml,
  bookingLimitsHtml,
  excRowHtml,
  holidayRowsHtml,
  colsHtml,
  excColsHtml,
  webChoiceHtml,
  capacityDescriptionHtml,
  closureHeadingHtml,
  legendHtml
};
