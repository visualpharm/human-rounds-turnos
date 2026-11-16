/* static/platform/admin-create-appointment.mjs — desk booking overlay.

   Canonical ES module (2026-09-05 cutover, behavior-frozen from the classic
   IIFE). Named export `abrir`; no compatibility global, no classic twin, no
   hardcoded fallback URL. Loaded through window.SANA_STATIC / import() by
   admin-app.js (Requests title button), admin-queue.js (day-queue doctor and
   free-slot actions, including the title-band button), and admin-content.js
   (doctor calendar "Crear turno"). Consumers hold an early click until this
   module resolves, then open once with the latest doctor/day/hour intent;
   a rejected import shows localized recovery on the still-current host.

   Top-level await, one block per dependency, preserving the classic failure
   semantics of the old dep-promise prologue: i18n, search and session are
   awaited fail-closed (a rejected dependency rejects this module's import, so
   every consumer's load-error path runs); dates.mjs and format.mjs stay
   fail-open to null (friendly datetime and person-name fall back to the raw
   string, the same soft dependencies as the classic file). Because the module
   body evaluates AFTER i18n and Search resolve, the first overlay paint is
   live T() and the service typeahead can search immediately.

   Overlay, not a section: the operator is mid-counter or mid-phone and
   returns to the same screen on close. Three steps appear as needed:
   patient → service → slot. The proposed slot is the FIRST free one;
   "Ver todos los horarios" opens the month calendar. An overbooking slot
   is never the automatic proposal.

   Patients from /api/admin/patients?q=, services from
   /api/admin/content/services, slots from /api/admin/appointment-slots
   (free slots match the patient grid; overbooking is desk-only), create via
   POST /api/admin/appointments. A successful save refreshes the still-current
   owner (day queue, requests list, doctor calendar) without a full reload.
*/
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

var Dates = await importDep("platform/dates.mjs", new URL("./dates.mjs", import.meta.url))
  .catch(function () { return null; });
var Format = await importDep("platform/format.mjs", new URL("./format.mjs", import.meta.url))
  .catch(function () { return null; });
var Search = await importDep("platform/search.mjs", new URL("./search.mjs", import.meta.url));
var Session = await importDep("platform/admin-session.mjs", new URL("./admin-session.mjs", import.meta.url));
var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url));
// «Turno creado» draws the panel's one HSI control.
var HsiSync = await importDep("platform/hsi-sync.mjs", new URL("./hsi-sync.mjs", import.meta.url));

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  function toast(m) { if (window.toast) window.toast(m); }
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
  // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
  function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
  // Sustituye {var} en lo que devolvió T(): esa rama NO pasa por i18n.t()
  // (quien hace el reemplazo real) cuando window.SanaI18n todavía no existe —
  // hoy es el caso de admin.html, que no carga i18n.js. Reaplicar acá es
  // inofensivo cuando SanaI18n YA sustituyó: no queda ningún "{var}" que
  // encontrar (mismo motivo que dates.mjs hace su propio fmt(), documentado
  // en su encabezado).
  function applyVars(text, vars) {
    if (!vars) return text;
    var out = text;
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) out = out.split("{" + k + "}").join(String(vars[k]));
    }
    return out;
  }

  // Nombres de mes en castellano: SÓLO como literal de `fallback` de T() más
  // abajo (el contrato del shim exige el texto exacto en el call site) — el
  // dato de verdad sale de locales/*/date.json (namespace compartido de
  // dates.mjs/calendar-grid.mjs, PR14 2026-08-04), nunca de una tabla propia.
  var MESES_FALLBACK = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  // Iniciales de día para el calendario (Do/Lu/Ma/Mi/Ju/Vi/Sá): date.json ya
  // trae day_narrow (una letra: D/L/M/M/J/V/S) y day_short (Dom/Lun/Mar…),
  // pero ninguno es este formato de DOS letras — son claves propias de esta
  // pantalla, namespace admin.queue. Siete llamadas con clave escrita entera
  // (no una sola concatenada con el índice): el escaneo de claves y el test
  // de fallbacks sólo reconocen una clave literal completa en el call site —
  // una armada por concatenación se les escapa a los dos.
  function dowLabels() {
    return [
      T("admin.queue.create_appt.dow_sun", null, "Do"),
      T("admin.queue.create_appt.dow_mon", null, "Lu"),
      T("admin.queue.create_appt.dow_tue", null, "Ma"),
      T("admin.queue.create_appt.dow_wed", null, "Mi"),
      T("admin.queue.create_appt.dow_thu", null, "Ju"),
      T("admin.queue.create_appt.dow_fri", null, "Vi"),
      T("admin.queue.create_appt.dow_sat", null, "Sá")
    ];
  }

  // «Hoy» de la instalación (dates.mjs), no el del navegador: cerca de medianoche en otro
  // huso el calendario marcaba otro día.
  function hoyISO() {
    if (Dates && Dates.hoyIsoInstalacion) return Dates.hoyIsoInstalacion();
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }

  // "Mañana, 07:30" / "Martes 29 de julio, 13:00" — el mismo idioma con el que
  // el sitio le habla al paciente, así el operador lee en voz alta lo que el
  // paciente después va a ver en su mail. Delegado a dates.mjs: esa capa
  // ya sabe hablar el idioma de la página (día/mes vía T()) — reimplementarlo
  // acá adentro fue justo el bug que mandó "Sacar on mañana, 08:00" a demo.
  function cuandoLargo(dia, hora) {
    return Dates ? Dates.amigable(dia, hora) : (dia + ", " + hora);
  }

  // "Julio 2026" del encabezado del calendario: mismo patrón que
  // calendar-grid.mjs (applyVars(T("date.month_full."+n, ...), ...)), sin repetir una tabla de
  // meses propia — sólo el literal de fallback, que el contrato de T() exige.
  function mesAnioLabel(y, m) {
    var nombre = T("date.month_full." + (m + 1), null, MESES_FALLBACK[m]);
    return nombre.charAt(0).toUpperCase() + nombre.slice(1) + " " + y;
  }

  var CSS = [
    ".ct-back{position:fixed;inset:0;background:rgba(15,23,32,.5);display:flex;align-items:flex-start;justify-content:center;padding:40px 16px;z-index:70;overflow:auto;}",
    ".ct-modal{background:var(--panel);border-radius:16px;box-shadow:0 24px 60px rgba(0,0,0,.28);width:min(680px,100%);padding:26px 28px 24px;}",
    ".ct-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin-bottom:18px;}",
    ".ct-head h2{margin:0;font-size:21px;}",
    ".ct-close{background:none;border:0;font-size:14px;color:var(--accent);cursor:pointer;padding:4px;}",
    ".ct-close:hover{color:var(--accent-hover);}",
    ".ct-paso{margin-bottom:20px;}",
    ".ct-lbl{display:block;font-size:13.5px;color:var(--hint-ink,var(--text-2));margin-bottom:6px;}",
    ".ct-in{font:inherit;font-size:16px;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--panel);color:var(--text);width:min(34ch,100%);}",
    ".ct-in:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}",
    ".ct-sug{list-style:none;margin:6px 0 0;padding:0;border:1px solid var(--border);border-radius:10px;overflow:hidden;max-width:min(46ch,100%);}",
    ".ct-sug:empty{display:none;border:0;}",
    ".ct-sug li{padding:9px 12px;cursor:pointer;font-size:14px;border-top:1px solid var(--border-soft);}",
    ".ct-sug li:first-child{border-top:0;}",
    ".ct-sug li:hover{background:var(--hover-bg);}",
    ".ct-sug li.on{background:var(--accent-soft);}",
    ".ct-sug .ct-sub{color:var(--hint-ink);font-size:13px;}",
    ".ct-svc-med{display:block;margin-top:2px;font-size:14px;color:var(--text);}",
    ".ct-cal-med{margin:14px 0 0;font-size:14px;font-weight:600;color:var(--text);}",
    // lo ya elegido se lee como un dato resuelto, no como un control
    ".ct-elegido{display:flex;align-items:baseline;gap:10px;font-size:15px;}",
    ".ct-elegido b{font-weight:600;}",
    ".ct-cambiar{font-size:13px;color:var(--accent);background:none;border:0;cursor:pointer;padding:0;}",
    ".ct-cambiar:hover{color:var(--accent-hover);}",
    ".ct-prop{border:1px solid var(--accent);background:var(--accent-soft);border-radius:12px;padding:14px 16px;}",
    ".ct-prop-cuando{font-size:18px;font-weight:600;}",
    ".ct-prop-med{font-size:13.5px;color:var(--hint-ink);margin-top:2px;}",
    ".ct-med-turno{display:grid;gap:6px;margin-top:12px;max-width:min(40ch,100%);}",
    ".ct-med-turno .ct-falta{border-color:var(--danger);outline:2px solid var(--danger);outline-offset:1px;}",
    ".ct-falta-txt{color:var(--danger);}",
    ".ct-vermas{margin-top:10px;display:inline-block;font-size:13.5px;color:var(--accent);background:none;border:0;padding:0;cursor:pointer;}",
    ".ct-vermas:hover{color:var(--accent-hover);}",
    // calendario: un mes, los días con lugar en verde; al elegir uno se
    // despliegan sus horarios
    ".ct-cal{margin-top:12px;}",
    ".ct-cal-nav{display:flex;align-items:center;gap:12px;margin-bottom:8px;font-size:14px;}",
    ".ct-cal-nav button{background:none;border:1px solid var(--border);border-radius:8px;padding:3px 9px;cursor:pointer;font:inherit;}",
    ".ct-cal-nav button[disabled]{opacity:.35;cursor:default;}",
    ".ct-cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;}",
    ".ct-cal-dow{font-size:13px;color:var(--hint-ink,var(--text-2));text-align:center;padding-bottom:2px;}",
    // alto fijo cómodo para el dedo (44px) en vez de aspect-ratio:1 — cuadrados,
    // el mes medía 500px de alto y empujaba los horarios abajo del pliegue
    ".ct-cal-dia{min-height:44px;border:1px solid var(--border);border-radius:8px;background:none;font:inherit;font-size:13px;cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;padding:2px;}",
    ".ct-cal-dia[disabled]{border-color:var(--border-soft);color:var(--text-4);cursor:default;}",
    ".ct-cal-dia.tiene{border-color:var(--accent);color:var(--text);}",
    ".ct-cal-dia .ct-cal-n{font-size:13px;color:var(--accent);}",
    ".ct-cal-dia.on{background:var(--accent);color:#fff;border-color:var(--accent);}",
    ".ct-cal-dia.on .ct-cal-n{color:#fff;}",
    ".slot-times{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;}",
    ".slot-time{border:1px solid var(--border);border-radius:9px;padding:8px 12px;background:var(--panel);font:inherit;font-size:14px;cursor:pointer;}",
    ".slot-time:hover{border-color:var(--hover-bd-strong);}",
    ".slot-time.on{background:var(--accent);color:#fff;border-color:var(--accent);}",
    // el sobreturno se ve distinto del lugar libre: es una excepción, no una
    // opción más de la grilla
    ".ct-cal-dia.solo-sobre{border-style:dashed;}",
    ".ct-sobre-lbl{font-size:13px;color:var(--hint-ink,var(--text-2));margin:14px 0 0;}",
    ".slot-time--over{border-style:dashed;}",
    ".slot-time--over.on{background:var(--text);border-color:var(--text);border-style:solid;color:#fff;}",
    ".ct-prop--sobre{border-style:dashed;background:none;}",
    ".ct-vacio{font-size:13.5px;color:var(--hint-ink);}",
    ".ct-escanear{font:inherit;font-size:13.5px;color:var(--accent);background:none;border:0;padding:0;cursor:pointer;margin-left:10px;}",
    ".ct-escanear:hover{color:var(--accent-hover);}",
    ".ct-scan-msg{margin:6px 0 0;font-size:13px;color:var(--hint-ink);}",
    ".ct-acciones{display:flex;align-items:center;gap:14px;margin-top:22px;}",
    ".ct-err{color:var(--danger);font-size:13.5px;margin:10px 0 0;}",
    ".ct-hsi-linea{margin:14px 0 0;font-size:15px;color:var(--text);}",
    // quién y qué: dato clave, en tinta (no el gris del médico propuesto)
    ".ct-creado-quien{font-size:15px;color:var(--text);margin-top:4px;}",
    ".ct-hsi-carga{position:relative;display:inline-block;}",
    ".ct-alta{display:grid;grid-template-columns:repeat(auto-fit,minmax(16ch,1fr));gap:10px 14px;max-width:min(64ch,100%);}",
    ".ct-alta-c{display:block;min-width:0;}",
    ".ct-alta-c .ct-in{width:100%;box-sizing:border-box;}",
    ".ct-alta-acc{display:flex;align-items:baseline;gap:16px;margin-top:12px;}",
    "@media(max-width:560px){.ct-modal{padding:20px 16px;}.ct-back{padding:16px 8px;}}"
  ].join("\n");

  function ensureCss() {
    if ($("#ct-css")) return;
    var st = document.createElement("style");
    st.id = "ct-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ---------------------------------------------------------------- estado
  var S = null;   // {paciente, servicio, slots, dia, hora, medico, mes}
  var GUARDANDO = false;

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    return fetch(path, opts).then(function (res) {
      var boundary = (res.status === 401 || res.status === 403)
        ? Session.confirmar(res)
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        // "redirect" = the session module is already leaving the page; any
        // other failure, an unconfirmed 401 included, stays visible.
        if (transitioned) throw new Error("redirect");
        if (res.status === 401) throw new Error("401");
        return res.json().catch(function () { return {}; }).then(function (j) { return { ok: res.ok, status: res.status, j: j }; });
      });
    });
  }

  // ------------------------------------------------------- paso 1: paciente
  function pasoPaciente() {
    if (S.alta) return pasoAlta();
    if (S.nota) {
      // Turno anotado a mano: no hay ficha, hay un texto. Se muestra tal cual
      // lo escribieron, para que sea reconocible en la cola del día.
      return '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.paciente_label", null, "Paciente") + '</span>'
        + '<div class="ct-elegido"><b>' + esc(S.nota) + "</b>"
        + '<span class="ct-sub">' + T("admin.queue.create_appt.sin_ficha", null, "Sin ficha: queda anotado así") + '</span>'
        + '<button type="button" class="ct-cambiar" data-cambiar="paciente">' + T("admin.queue.create_appt.cambiar", null, "Cambiar") + '</button></div></div>';
    }
    if (S.paciente) {
      var p = S.paciente;
      return '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.paciente_label", null, "Paciente") + '</span>'
        + '<div class="ct-elegido"><b>' + esc([p.apellido, p.nombre].filter(Boolean).join(", ")) + "</b>"
        + '<span class="ct-sub">' + applyVars(T("admin.queue.create_appt.dni_prefix", { dni: dniCopiable(p.dni) }, "DNI {dni}"), { dni: dniCopiable(p.dni) }) + "</span>"
        + '<button type="button" class="ct-cambiar" data-cambiar="paciente">' + T("admin.queue.create_appt.cambiar", null, "Cambiar") + '</button></div></div>';
    }
    // El lector de DNI de la mesa escribe en el campo que tenga el foco: no
    // hay botón para "escanear con el lector", se apoya el documento y listo
    // (ver dni-scan.mjs). El botón es para el otro caso — sacarle una foto.
    return '<div class="ct-paso">'
      + '<label class="ct-lbl" for="ct-pac">' + T("admin.queue.create_appt.paciente_label", null, "Paciente") + '</label>'
      + '<input class="ct-in" id="ct-pac" autocomplete="off" placeholder="' + esc(T("admin.queue.create_appt.paciente_placeholder", null, "DNI, nombre o apellido")) + '">'
      + ' <button type="button" class="ct-escanear" id="ct-scan">' + T("admin.queue.create_appt.escanear_dni", null, "Escanear el DNI") + '</button>'
      + '<input type="file" id="ct-scan-file" accept="image/*" hidden>'
      + '<p class="ct-scan-msg" id="ct-scan-msg" hidden></p>'
      + '<ul class="ct-sug" id="ct-pac-sug"></ul></div>';
  }

  // El paciente todavía no tiene ficha: se da de alta acá mismo, con lo que
  // HSI va a pedir para empadronarlo (documento, apellido, nombre, nacimiento
  // y sexo). Un documento leído por el lector o por foto llega con todo
  // completo y sólo queda confirmar.
  function pasoAlta() {
    var a = S.alta;
    var campo = function (id, etiqueta, valor, extra) {
      return '<label class="ct-alta-c"><span class="ct-lbl">' + esc(etiqueta) + '</span>'
        + '<input class="ct-in" id="' + id + '" autocomplete="off" value="' + esc(valor || "") + '"' + (extra || "") + "></label>";
    };
    var sexos = { "": "—", F: T("admin.queue.patients.sex.f", null, "Femenino"), M: T("admin.queue.patients.sex.m", null, "Masculino"), X: T("admin.queue.patients.sex.x", null, "X") };
    return '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.patients.new", null, "Nuevo paciente") + '</span>'
      + '<div class="ct-alta">'
      + campo("ct-alta-dni", T("admin.queue.patients.col.dni", null, "DNI"), a.dni, ' inputmode="numeric" maxlength="9"')
      + campo("ct-alta-apellido", T("admin.queue.patient_form.last_name", null, "Apellido"), a.apellido)
      + campo("ct-alta-nombre", T("admin.queue.patient_form.first_name", null, "Nombre"), a.nombre)
      + campo("ct-alta-nac", T("admin.queue.patients.col.birth", null, "Nacimiento"), a.nacimiento, ' inputmode="numeric" placeholder="' + esc(T("admin.queue.patients.birth_placeholder", null, "DD/MM/AAAA")) + '"')
      + '<label class="ct-alta-c"><span class="ct-lbl">' + T("admin.queue.patients.col.sex", null, "Sexo") + '</span>'
      + '<select class="ct-in" id="ct-alta-sexo">' + ["", "F", "M", "X"].map(function (v) {
        return '<option value="' + v + '"' + ((a.sexo || "") === v ? " selected" : "") + ">" + esc(sexos[v]) + "</option>";
      }).join("") + "</select></label>"
      + "</div>"
      + '<p class="ct-err" id="ct-alta-err" hidden></p>'
      + '<div class="ct-alta-acc"><button type="button" class="btn btn-accent" id="ct-alta-ok">' + T("admin.queue.patient_form.create", null, "Crear paciente") + "</button>"
      + '<button type="button" class="ct-cambiar" data-cambiar="paciente">' + T("admin.queue.create_appt.cancelar", null, "Cancelar") + "</button></div></div>";
  }

  function leerAlta() {
    var v = function (id) { return String((($("#" + id) || {}).value) || "").trim(); };
    return { dni: v("ct-alta-dni").replace(/\D/g, ""), apellido: v("ct-alta-apellido"), nombre: v("ct-alta-nombre"),
      nacimiento: v("ct-alta-nac"), sexo: v("ct-alta-sexo") };
  }

  function altaErr(txt) {
    var e = $("#ct-alta-err");
    if (!e) return;
    e.textContent = txt || "";
    e.hidden = !txt;
  }

  // La fecha se entiende con EL parser compartido (account-form.
  // parseFlexibleDate), el mismo de la ficha del paciente.
  var FECHAS = null;
  function cargarFechas() {
    if (!FECHAS) FECHAS = importDep("platform/account-form.mjs", new URL("./account-form.mjs", import.meta.url));
    return FECHAS;
  }

  // El server dice por qué no creó la ficha. Un código pelado no le dice nada
  // a quien está con el paciente (un médico leía «rol_no_autorizado»): el de
  // rol se traduce con su salida, cualquier otro código cae en el genérico, y
  // una frase del server («Ya existe un paciente con ese DNI.») pasa tal cual.
  var MOTIVOS_ALTA = {
    rol_no_autorizado: function () { return T("admin.queue.create_appt.alta_rol", null, "Tu rol no puede dar de alta pacientes. Tocá «Cancelar» y elegí «Anotar» para dar el turno igual; admisión completa la ficha."); }
  };
  function motivoAlta(code) {
    if (MOTIVOS_ALTA[code]) return MOTIVOS_ALTA[code]();
    return errText(code, T("admin.queue.common.save_error", null, "No se pudo guardar."));
  }

  var ALTA_EN_CURSO = false;
  function crearPaciente() {
    if (ALTA_EN_CURSO || !S.alta) return;
    var estado = S, a = leerAlta();
    S.alta = a;   // un re-render no pierde lo tipeado
    if (!/^\d{7,9}$/.test(a.dni)) { altaErr(T("admin.queue.create_appt.alta_dni", null, "Revisá el DNI: son 7 a 9 números.")); return; }
    if (!a.apellido || !a.nombre) { altaErr(T("admin.queue.create_appt.alta_nombre", null, "Faltan apellido y nombre.")); return; }
    if (!a.nacimiento || !a.sexo) { altaErr(T("admin.queue.patient_form.missing_birth_sex", null, "Faltan fecha de nacimiento y sexo.")); return; }
    ALTA_EN_CURSO = true;
    altaErr("");
    cargarFechas().then(function (mod) {
      var iso = mod.parseFlexibleDate(a.nacimiento);
      if (!iso) { altaErr(applyVars(T("admin.queue.common.date_invalid", { ejemplo: "28/10/1978" }, "No entiendo esa fecha. Probá {ejemplo}."), { ejemplo: "28/10/1978" })); return null; }
      return api("/api/admin/patients", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dni: a.dni, apellido: a.apellido, nombre: a.nombre, nacimiento: iso, sexo: a.sexo }) });
    }).then(function (r) {
      if (!r || S !== estado) return;
      if (!r.ok || !r.j || !r.j.ok || !r.j.item) {
        altaErr(motivoAlta(r.j && r.j.error));
        return;
      }
      S.paciente = r.j.item; S.alta = null; S.nota = "";
      render();
    }).catch(function () {
      if (S === estado) altaErr(T("admin.queue.common.save_error", null, "No se pudo guardar."));
    }).then(function () { ALTA_EN_CURSO = false; });
  }

  // Para quién es el turno: una ficha de paciente, o la nota escrita a mano.
  // Las dos alcanzan para seguir — de eso se trata la nota.
  function hayPaciente() { return !!(S.paciente || S.nota); }

  // ------------------------------------------------------- paso 2: servicio
  function pasoServicio() {
    if (!hayPaciente()) return "";
    var medico = S.medicoPre
      ? '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.medico_label", null, "Médico") + '</span>'
        + '<div class="ct-elegido"><b>' + esc(nombreMedico(S.medicoPre)) + "</b>"
        + '<button type="button" class="ct-cambiar" data-cambiar="medico">' + T("admin.queue.create_appt.quitar", null, "Quitar") + '</button></div></div>'
      : "";
    if (S.servicio) {
      var s = S.servicio;
      return medico + '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.servicio_label", null, "Servicio") + '</span>'
        + '<div class="ct-elegido"><b>' + esc(s.nombre) + "</b>"
        + '<span class="ct-sub">' + esc(centroNombre(s.centro)) + "</span>"
        + (!S.medicoPre && medicosDe(s).length ? '<span class="ct-sub">' + esc(medicosDe(s).join(", ")) + "</span>" : "")
        + '<button type="button" class="ct-cambiar" data-cambiar="servicio">' + T("admin.queue.create_appt.cambiar", null, "Cambiar") + '</button></div></div>';
    }
    // Con un médico fijado el catálogo son SUS servicios; si no tiene ninguno
    // se dice (y cómo salir), nunca se vuelve en silencio al catálogo entero.
    if (S.medicoPre && !serviciosDe(S.medicoPre).length) {
      var quien = esc(nombreMedico(S.medicoPre));
      return medico + '<div class="ct-paso"><p class="ct-sub" id="ct-sin-servicios">'
        + applyVars(T("admin.queue.create_appt.medico_sin_servicios", { medico: quien },
          "{medico} no tiene servicios con turno en su agenda. Quitá al médico para ver todo el catálogo."), { medico: quien })
        + "</p></div>";
    }
    return medico + '<div class="ct-paso">'
      + '<label class="ct-lbl" for="ct-svc">' + T("admin.queue.create_appt.servicio_input_label", null, "Servicio o especialidad") + '</label>'
      + '<input class="ct-in" id="ct-svc" autocomplete="off" placeholder="' + esc(T("admin.queue.create_appt.servicio_placeholder", null, "Cardiología, ecografía, control…")) + '">'
      + '<ul class="ct-sug" id="ct-svc-sug"></ul></div>';
  }

  // -------------------------------------------------------- paso 3: horario
  // Lo que se va a crear: lo elegido a mano, o el primer horario libre. Un
  // sobreturno NUNCA se propone solo — es una excepción, se elige a propósito.
  function elegido() {
    return S.sel || (S.libres || [])[0] || null;
  }

  // Minutos que no nombran a nadie (un servicio con agenda propia): el server
  // manda los médicos del servicio (`medicos`, el pool de la cola) y la mesa
  // elige quién da el turno — antes salía sin médico, «Médico no asignado»
  // (plan de lanzamiento P1). «A asignar» es no nombrar a nadie a propósito.
  var SIN_MEDICO = "-";
  function pideMedico(el) {
    return !!(el && !el.medico && !el.sobreturno && (S.medicosTurno || []).length);
  }
  function medicoDelTurno(el) {
    if (!el) return "";
    if (el.medico || !pideMedico(el)) return el.medico || "";
    return S.medicoTurno && S.medicoTurno !== SIN_MEDICO ? S.medicoTurno : "";
  }
  function medicoTurnoHtml() {
    var actual = S.medicoTurno || "";
    var op = function (valor, texto) {
      return '<option value="' + esc(valor) + '"' + (actual === valor ? " selected" : "") + ">" + esc(texto) + "</option>";
    };
    return '<div class="ct-med-turno"><label class="ct-lbl" for="ct-med-turno">' + T("admin.queue.create_appt.medico_label", null, "Médico") + "</label>"
      + '<select class="ct-in" id="ct-med-turno">'
      + op("", T("admin.queue.create_appt.medico_elegir", null, "Elegí quién atiende…"))
      + S.medicosTurno.map(function (n) { return op(n, nombreMedico(n)); }).join("")
      + op(SIN_MEDICO, T("admin.queue.create_appt.medico_a_asignar", null, "A asignar (sin médico)"))
      + "</select></div>";
  }

  function pasoHorario() {
    if (!hayPaciente() || !S.servicio) return "";
    if (S.cargandoSlots) return '<div class="ct-paso"><p class="ct-vacio">' + T("admin.queue.create_appt.buscando_horarios", null, "Buscando horarios…") + '</p></div>';
    var libres = S.libres || [], sobres = S.sobres || [];
    if (!libres.length && !sobres.length) {
      return '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.turno_label", null, "Turno") + '</span>'
        + '<p class="ct-vacio">' + T("admin.queue.create_appt.sin_horarios_mes", null, "Este servicio no tiene horarios con lugar en los próximos tres meses.") + '</p></div>';
    }
    var el = elegido();
    var html = '<div class="ct-paso"><span class="ct-lbl">' + T("admin.queue.create_appt.turno_label", null, "Turno") + '</span>';
    // The first free slot is proposed on its own. When today has none left
    // the proposal used to jump to a later day in silence (TASK-54, 26.10):
    // the desk is told before it reads the date.
    if (el && !S.sel && el.dia !== hoyISO() && !libres.some(function (t) { return t.dia === hoyISO(); })) {
      html += '<p class="ct-sub" id="ct-hoy-sin-lugar">' + T("admin.queue.create_appt.hoy_sin_lugar", null, "Hoy no hay horarios libres. El primero:") + "</p>";
    }
    if (el) {
      html += '<div class="ct-prop' + (el.sobreturno ? " ct-prop--sobre" : "") + '">'
        + '<div class="ct-prop-cuando">' + esc(cuandoLargo(el.dia, el.hora)) + "</div>"
        + (el.medico ? '<div class="ct-prop-med">' + esc(el.medico) + "</div>" : "")
        + (el.sobreturno ? '<div class="ct-prop-med">' + T("admin.queue.create_appt.sobreturno_nota", null, "Sobreturno: se agrega encima de un turno ya dado.") + '</div>' : "")
        + "</div>";
      if (pideMedico(el)) html += medicoTurnoHtml();
    } else {
      html += '<div class="ct-prop ct-prop--sobre"><div class="ct-prop-cuando">' + T("admin.queue.create_appt.sin_horarios_libres", null, "Sin horarios libres") + '</div>'
        + '<div class="ct-prop-med">' + T("admin.queue.create_appt.quedan_sobreturnos", null, "Quedan sobreturnos: elegí uno en el calendario.") + '</div></div>';
    }
    html += '<button type="button" class="ct-vermas" id="ct-vermas">'
      + (S.calendario ? T("admin.queue.create_appt.ocultar_calendario", null, "Ocultar el calendario") : T("admin.queue.create_appt.ver_calendario", null, "Ver todos los horarios")) + "</button>";
    if (S.calendario) html += calendarioHtml();
    html += "</div>";
    return html;
  }

  function calendarioHtml() {
    var libres = S.libres || [], sobres = S.sobres || [];
    var porDia = {}, sobrePorDia = {};
    libres.forEach(function (t) { (porDia[t.dia] = porDia[t.dia] || []).push(t); });
    sobres.forEach(function (t) { (sobrePorDia[t.dia] = sobrePorDia[t.dia] || []).push(t); });

    var base = new Date((S.mes || hoyISO().slice(0, 7)) + "-01T00:00:00");
    var y = base.getFullYear(), m = base.getMonth();
    var primero = new Date(y, m, 1);
    var dias = new Date(y, m + 1, 0).getDate();
    var hoy = hoyISO();

    // sólo se navega dentro del rango que el servicio realmente ofrece
    var mesesConTurno = {};
    libres.concat(sobres).forEach(function (t) { mesesConTurno[t.dia.slice(0, 7)] = true; });
    var mesActual = y + "-" + String(m + 1).padStart(2, "0");
    var todos = Object.keys(mesesConTurno).sort();
    var hayPrev = todos.some(function (x) { return x < mesActual; });
    var haySig = todos.some(function (x) { return x > mesActual; });

    var html = '<div class="ct-cal"><div class="ct-cal-nav">'
      + '<button type="button" data-mes="-1"' + (hayPrev ? "" : " disabled") + ' aria-label="' + esc(T("admin.queue.create_appt.mes_anterior", null, "Mes anterior")) + '">‹</button>'
      + "<span>" + esc(mesAnioLabel(y, m)) + "</span>"
      + '<button type="button" data-mes="1"' + (haySig ? "" : " disabled") + ' aria-label="' + esc(T("admin.queue.create_appt.mes_siguiente", null, "Mes siguiente")) + '">›</button>'
      + "</div><div class='ct-cal-grid'>";
    dowLabels().forEach(function (label) {
      html += '<div class="ct-cal-dow">' + esc(label) + "</div>";
    });
    for (var i = 0; i < primero.getDay(); i++) html += "<div></div>";
    for (var d = 1; d <= dias; d++) {
      var iso = y + "-" + String(m + 1).padStart(2, "0") + "-" + String(d).padStart(2, "0");
      var n = (porDia[iso] || []).length;
      var ns = (sobrePorDia[iso] || []).length;
      // el número es el lugar LIBRE; un día que sólo admite sobreturnos se
      // marca punteado, para que no se lea como si tuviera lugar
      var cls = "ct-cal-dia" + (n ? " tiene" : (ns ? " solo-sobre" : "")) + (iso === S.diaCal ? " on" : "");
      html += '<button type="button" class="' + cls + '" data-dia="' + iso + '"' + (n || ns ? "" : " disabled") + ">"
        + "<span>" + d + "</span>"
        + (n ? '<span class="ct-cal-n">' + n + "</span>" : "")
        + "</button>";
    }
    html += "</div>";

    var sel = S.sel || {};
    function botonHora(t, tipo, i) {
      var on = t.dia === sel.dia && t.hora === sel.hora && !!t.sobreturno === !!sel.sobreturno;
      var tituloSobre = tipo === "s" ? Tn("admin.queue.create_appt.sobreturno_lugar", t.restantes, null, "Quedan {n} lugar de sobreturno", "Quedan {n} lugares de sobreturno") : "";
      return '<button type="button" class="slot-time' + (tipo === "s" ? " slot-time--over" : "") + (on ? " on" : "")
        + '" data-slot="' + tipo + "|" + i + '"'
        + (tipo === "s" ? ' title="' + esc(tituloSobre) + '"' : "")
        + ">" + esc(t.hora) + "</button>";
    }
    // A day's times under each doctor's name: a service with several doctors
    // interleaved their hours with nothing saying whose they were.
    function horasPorMedico(lista, tipo, todas) {
      var grupos = [], porNombre = {};
      lista.forEach(function (t) {
        var k = t.medico || "";
        if (!porNombre[k]) { porNombre[k] = []; grupos.push(k); }
        porNombre[k].push(t);
      });
      return grupos.map(function (k) {
        return (k ? '<p class="ct-cal-med">' + esc(nombreMedico(k)) + "</p>" : "")
          + '<div class="slot-times">' + porNombre[k].map(function (t) {
            return botonHora(t, tipo, todas.indexOf(t));
          }).join("") + "</div>";
      }).join("");
    }
    if (S.diaCal) {
      var delDia = porDia[S.diaCal] || [];
      var sobreDia = sobrePorDia[S.diaCal] || [];
      if (delDia.length) {
        html += horasPorMedico(delDia, "l", libres);
      } else if (!sobreDia.length) {
        html += '<p class="ct-vacio">' + T("admin.queue.create_appt.dia_sin_lugar", null, "Ese día no tiene lugar.") + '</p>';
      }
      if (sobreDia.length) {
        html += '<p class="ct-sobre-lbl">' + (delDia.length
          ? T("admin.queue.create_appt.o_sobre_turno", null, "O sobre un turno ya dado:")
          : T("admin.queue.create_appt.sin_lugar_sobreturno", null, "Sin lugar libre. Se puede agregar un sobreturno:")) + "</p>"
          + horasPorMedico(sobreDia, "s", sobres);
      }
    } else {
      html += '<p class="ct-vacio" style="margin-top:10px;">' + T("admin.queue.create_appt.elegir_dia", null, "Elegí un día para ver sus horarios.") + '</p>';
    }
    html += "</div>";
    return html;
  }

  // ------------------------------------------------------------------ mapa
  var CENTROS = {};
  function centroNombre(slug) { return CENTROS[slug] || slug || ""; }

  var SERVICIOS = [], MEDICOS = [];
  function nombreMedico(nombre) { return Format && Format.nombre ? Format.nombre(nombre) : String(nombre || ""); }
  // «30.009.042», igual que en la cola del día: así la mesa reconoce la ficha.
  function dniVisible(dni) { return Format && Format.dni ? Format.dni(dni) : String(dni || ""); }
  // El DNI ya elegido se copia con un toque (Format.dniHtml); en la lista de
  // búsqueda no: ahí tocar elige a la persona.
  function dniCopiable(dni) { return (Format && Format.dniHtml && Format.dniHtml(dni)) || esc(dniVisible(dni)); }
  function cargarCatalogo() {
    if (SERVICIOS.length) return Promise.resolve();
    return Promise.all([
      api("/api/admin/content/services"),
      api("/api/admin/content/centers"),
      api("/api/admin/content/doctors")
    ]).then(function (rs) {
      // The content APIs also list disabled rows (the editors show them as
      // «Deshabilitado»); the desk offers only live services and doctors.
      // A role row (`rol`: «Técnico/a», «Administrativo.») feeds a service's
      // hours but is not a person, so it is never named as a doctor.
      SERVICIOS = ((rs[0].j || {}).items || []).filter(function (s) {
        if (s._borrado) return false;
        // sólo lo que de verdad tiene turno: un walk-in no se agenda, salvo
        // el que abre su listado el mismo día (mismo criterio que el server
        // en site_availability._offers_appointments)
        return s.modo === "solicitud" || s.modo === "solicitud_con_orden"
          || (s.apertura_mismo_dia && (s.modo === "walkin" || s.modo === "walkin_con_orden"));
      });
      ((rs[1].j || {}).items || []).forEach(function (c) { CENTROS[c.slug] = c.name; });
      MEDICOS = ((rs[2].j || {}).items || []).filter(function (m) { return !m._borrado && !m.rol; });
    });
  }

  // ---------------------------------------------------------------- render
  // El catálogo y los horarios llegan después de abrir el modal. Renderizar
  // reemplaza su HTML completo, así que guardamos la búsqueda que la persona
  // de admisión está escribiendo antes de pintar de nuevo. De otro modo
  // "Carla" desaparecía cuando terminaba de cargar el catálogo y había que
  // empezar otra vez.
  function capturaBusquedaPaciente() {
    if (hayPaciente()) return null;
    var input = $("#ct-pac");
    if (!input) return null;
    var lista = $("#ct-pac-sug");
    return {
      texto: input.value,
      inicio: typeof input.selectionStart === "number" ? input.selectionStart : input.value.length,
      fin: typeof input.selectionEnd === "number" ? input.selectionEnd : input.value.length,
      enfocado: document.activeElement === input,
      sugerencias: lista ? lista.innerHTML : ""
    };
  }

  function restauraBusquedaPaciente(busqueda) {
    if (!busqueda || hayPaciente()) return;
    var input = $("#ct-pac");
    if (!input) return;
    input.value = busqueda.texto;
    var lista = $("#ct-pac-sug");
    if (lista) lista.innerHTML = busqueda.sugerencias;
    if (busqueda.enfocado) {
      input.focus();
      if (typeof input.setSelectionRange === "function") input.setSelectionRange(busqueda.inicio, busqueda.fin);
    }
  }

  // «Turno creado»: cuándo, con quién, y cómo quedó en HSI. Con HSI
  // conectado ya se envió solo; sin conexión, «Cargar en HSI» lo carga con la
  // extensión de la mesa (que atiende el clic antes que esta página). Si el
  // clic llega acá, la extensión no está: se copia el DNI. La línea sobre los
  // botones dice dónde instalarla (contraparte técnica, 2026-10-27) hasta que la extensión
  // marca la página.
  function renderCreado() {
    var m = $("#ct-modal");
    var c = S.creado, h = c.hsi || {};
    var quien = [c.paciente, c.servicio, c.medico ? nombreMedico(c.medico) : ""].filter(Boolean).join(" · ");
    var linea = "", accion = "";
    HsiSync.ensureCss();
    if (h.conectado) {
      // Server push: «Envío automático a HSI» turning, or «En HSI» with its
      // check — the journal resends by itself, there is nothing to press.
      linea = HsiSync.serverHtml(h.estado === "ok" ? "ok" : "pendiente", { id: c.id, linea: true });
    } else if (h.extension) {
      // The extension's hook; `auto`: this turno was just created, so with
      // «Cargar en HSI apenas apruebo o creo un turno» on it goes by itself.
      accion = '<span class="ct-hsi-carga">'
        + HsiSync.extensionHtml({ id: c.id, dia: c.dia, auto: true, primario: true, attrs: ' id="ct-hsi"' }) + '</span>';
    }
    m.innerHTML = '<div class="ct-head"><h2>' + (c.sobreturno
        ? T("admin.queue.create_appt.creado_sobreturno", null, "Sobreturno creado")
        : T("admin.queue.create_appt.creado", null, "Turno creado")) + '</h2>'
      + '<button type="button" class="ct-close" id="ct-close">' + T("admin.queue.create_appt.cerrar", null, "Cerrar") + '</button></div>'
      + '<div class="ct-prop"><div class="ct-prop-cuando">' + esc(cuandoLargo(c.dia, c.hora)) + '</div>'
      + '<div class="ct-creado-quien">' + esc(quien) + '</div></div>'
      + (linea ? '<p class="ct-hsi-linea">' + linea + '</p>' : "")
      + '<p class="ct-hsi-linea" id="ct-hsi-sin-ext" hidden></p>'
      + (accion && HsiSync.instalarHtml ? HsiSync.instalarHtml(h.instalar, { attrs: ' id="ct-hsi-instalar"' }) : "")
      + '<div class="ct-acciones">' + accion
      + '<button type="button" class="btn' + (accion ? "" : " btn-accent") + '" id="ct-listo">'
      + T("admin.queue.create_appt.listo", null, "Listo") + '</button></div>';
    $("#ct-close").addEventListener("click", cerrarModal);
    $("#ct-listo").addEventListener("click", cerrarModal);
    var cargar = $("#ct-hsi");
    if (cargar) cargar.addEventListener("click", function () { sinExtension(c); });
    ($("#ct-hsi") || $("#ct-listo")).focus();
  }

  function sinExtension(c) {
    var p = $("#ct-hsi-sin-ext");
    if (p) HsiSync.sinExtension($("#ct-hsi"), { dni: c.dni, tienda: c.hsi.instalar ? null : c.hsi.extension, aviso: p });
  }

  function render() {
    var m = $("#ct-modal");
    if (!m) return;
    if (S.creado) return renderCreado();
    var busquedaPaciente = capturaBusquedaPaciente();
    var listo = hayPaciente() && S.servicio && (S.libres || []).length;
    // Falta sólo el médico: el botón no se apaga (un botón apagado no avisa
    // nada al tocarlo y se ve igual que uno prendido); dice qué falta al lado
    // y, si lo tocan igual, lleva a la elección (faltaMedicoAviso).
    var faltaMedico = listo && pideMedico(elegido()) && !S.medicoTurno;
    m.innerHTML = '<div class="ct-head"><h2>' + T("admin.queue.create_appt.crear_turno", null, "Crear turno") + '</h2>'
      + '<button type="button" class="ct-close" id="ct-close">' + T("admin.queue.create_appt.cerrar", null, "Cerrar") + '</button></div>'
      + pasoPaciente() + pasoServicio() + pasoHorario()
      + '<p class="ct-err" id="ct-err" hidden></p>'
      // Con el alta abierta hay UNA acción en pantalla: crear al paciente.
      + (S.alta ? "" : '<div class="ct-acciones">'
      + '<button type="button" class="btn btn-accent" id="ct-ok"' + (listo ? "" : " disabled")
      + (faltaMedico ? ' aria-disabled="true" aria-describedby="ct-falta-medico"' : "") + ">"
      + T("admin.queue.create_appt.crear_turno", null, "Crear turno") + "</button>"
      + (faltaMedico ? '<span class="ct-sub" id="ct-falta-medico">'
        + T("admin.queue.create_appt.falta_medico", null, "Elegí el médico para crear el turno.") + "</span>" : "")
      + "</div>");
    wire();
    restauraBusquedaPaciente(busquedaPaciente);
    var foco = $("#ct-pac") || $("#ct-svc");
    if (foco && (!busquedaPaciente || !busquedaPaciente.enfocado)) foco.focus();
  }

  function errorMsg(txt) {
    var e = $("#ct-err");
    if (!e) return;
    e.textContent = txt || "";
    e.hidden = !txt;
  }

  // -------------------------------------------------------- sugerencias
  var TIMER = null;
  var ULTIMA_BUSQUEDA = null;   // el texto que produjo la lista visible
  var PACIENTES_REQUEST = 0;

  function sugerirPacientes(q) {
    var estado = S, request = ++PACIENTES_REQUEST;
    // Un request que estaba en vuelo no puede escribir en el nodo que el
    // render del catálogo ya reemplazó, ni reemplazar una búsqueda posterior.
    // El input actual es la fuente de verdad: se vuelve a buscar recién ahí.
    if (q.length < 2) {
      var vacia = $("#ct-pac-sug"), entradaVacia = $("#ct-pac");
      if (S === estado && entradaVacia && entradaVacia.value.trim() === q && vacia) vacia.innerHTML = "";
      return Promise.resolve();
    }
    return api("/api/admin/patients?q=" + encodeURIComponent(q)).then(function (r) {
      if (!r.ok || S !== estado || request !== PACIENTES_REQUEST) return;
      var entrada = $("#ct-pac"), ul = $("#ct-pac-sug");
      if (!entrada || entrada.value.trim() !== q || !ul) return;
      var items = (r.j.items || []).filter(function (p) { return p.activo !== false; }).slice(0, 8);
      // Última opción SIEMPRE, haya o no fichas que coincidan (responsable del producto,
      // 2026-07-27): anotar el turno con lo que se escribió y seguir. Mientras
      // hay gente esperando, un turno anotado a mano vale más que uno que no
      // se cargó porque faltaba dar de alta la ficha.
      var notaLi = '<li data-nota="1"><b>' + applyVars(T("admin.queue.create_appt.anotar_texto", { q: esc(q) }, "Anotar «{q}»"), { q: esc(q) }) + '</b>'
        + ' <span class="ct-sub">' + T("admin.queue.create_appt.sin_ficha_paciente", null, "sin ficha de paciente") + '</span></li>';
      // Sin ficha que coincida, lo primero que se ofrece es darlo de alta con
      // los datos que HSI va a pedir; anotar a mano sigue siendo la última.
      var altaLi = items.length ? "" : '<li data-alta="1" class="on"><b>' + T("admin.queue.patients.new", null, "Nuevo paciente") + '</b>'
        + ' <span class="ct-sub">' + esc(q) + "</span></li>";
      ul.innerHTML = (items.length
        ? items.map(function (p, i) {
            var nombreCompleto = [p.apellido, p.nombre].filter(Boolean).join(", ");
            return '<li data-dni="' + esc(p.dni) + '"' + (i === 0 ? ' class="on"' : "") + ">"
              + (nombreCompleto ? esc(nombreCompleto) : T("admin.queue.create_appt.sin_nombre", null, "(sin nombre)"))
              + ' <span class="ct-sub">' + applyVars(T("admin.queue.create_appt.dni_prefix", { dni: esc(dniVisible(p.dni)) }, "DNI {dni}"), { dni: esc(dniVisible(p.dni)) }) + "</span></li>";
          }).join("")
        : '<li class="ct-sub" data-vacio="1">' + T("admin.queue.create_appt.ningun_paciente", null, "Ningún paciente con ese dato.") + '</li>' + altaLi) + notaLi;
      PAC_CACHE = {};
      items.forEach(function (p) { PAC_CACHE[String(p.dni)] = p; });
      NOTA_PENDIENTE = q;
    }).catch(function () {});
  }
  var PAC_CACHE = {};
  var NOTA_PENDIENTE = "";   // lo tipeado que produjo la lista visible

  function sugerirServicios(q) {
    var ul = $("#ct-svc-sug");
    if (!ul) return Promise.resolve();
    if (q.length < 2) { ul.innerHTML = ""; return Promise.resolve(); }
    // El mismo buscador del sitio: entiende "cardiólogo" para Cardiología y
    // ordena por relevancia, no alfabéticamente (platform/search.mjs).
    // Un médico abierto desde su agenda no es sólo una pista para los horarios:
    // define qué servicios puede elegir la mesa. Al quitarlo se vuelve al
    // catálogo completo, pero mientras está fijado nunca se filtra contra otro
    // profesional por accidente.
    var catalogo = S.medicoPre ? serviciosDe(S.medicoPre) : SERVICIOS;
    var filas = catalogo.map(function (s) {
      var medicos = medicosDe(s);
      return { s: s, medicos: medicos, principal: [s.nombre],
        resto: [s.categoria, centroNombre(s.centro), medicos.join(" "), (s.palabras || []).join(" ")] };
    });
    var res = Search.buscar(filas, q, function (f) { return f; });
    var items = res.slice(0, 8);
    ul.innerHTML = items.length
      ? items.map(function (f, i) {
          var s = f.s;
          return '<li data-svc="' + esc(s.id) + '"' + (i === 0 ? ' class="on"' : "") + ">"
            + esc(s.nombre) + ' <span class="ct-sub">' + esc(centroNombre(s.centro)) + "</span>"
            + (f.medicos.length ? '<span class="ct-svc-med">' + esc(f.medicos.join(", ")) + "</span>" : "") + "</li>";
        }).join("")
      : '<li class="ct-sub" data-vacio="1">' + T("admin.queue.create_appt.ningun_servicio", null, "Ningún servicio con turno coincide.") + '</li>';
    return Promise.resolve();
  }

  var SLOTS_REQUEST = 0;
  function limpiarSlots() {
    SLOTS_REQUEST += 1;
    S.cargandoSlots = false; S.libres = []; S.sobres = []; S.sel = null; S.diaCal = null;
    S.medicosTurno = []; S.medicoTurno = "";
  }

  function cargarSlots() {
    var estado = S, request = ++SLOTS_REQUEST;
    S.cargandoSlots = true; S.libres = []; S.sobres = []; S.sel = null; S.diaCal = null;
    S.medicosTurno = []; S.medicoTurno = "";
    render();
    var url = "/api/admin/appointment-slots?servicio_id=" + encodeURIComponent(S.servicio.id)
      + (S.medicoPre ? "&medico=" + encodeURIComponent(S.medicoPre) : "");
    return api(url).then(function (r) {
      if (S !== estado || request !== SLOTS_REQUEST) return;
      var j = r.j || {};
      S.libres = (j.libres || []).map(function (t) {
        return { dia: t.dia, hora: t.hora, medico: t.medico || "" };
      });
      S.sobres = (j.sobreturnos || []).map(function (t) {
        return { dia: t.dia, hora: t.hora, medico: t.medico || "", restantes: t.restantes || 1, sobreturno: true };
      });
      // Con un solo médico del servicio, ése; con varios, nadie hasta que se elija.
      S.medicosTurno = (Array.isArray(j.medicos) ? j.medicos : []).filter(Boolean);
      S.medicoTurno = S.medicosTurno.length === 1 ? S.medicosTurno[0] : "";
      S.cargandoSlots = false;
      var primero = S.libres[0] || S.sobres[0];
      if (primero) {
        // el día que pidió quien abrió el modal (el calendario de un médico)
        // manda sobre el primero libre: es el día del que están hablando
        var pre = S.diaPre && (porDia(S.diaPre).libres.length || porDia(S.diaPre).sobres.length) ? S.diaPre : null;
        S.diaCal = pre || primero.dia;
        S.mes = S.diaCal.slice(0, 7);
        // sin ningún libre no hay nada que proponer: el calendario se abre
        // solo, que es donde viven los sobreturnos
        if (pre || !S.libres.length) S.calendario = true;
        // Si vinieron por un día concreto, la propuesta tiene que ser DE ESE
        // día: proponer el primer libre del mes que viene, con el calendario
        // parado en otra fecha, hace que "Crear turno" cargue el turno
        // equivocado. Sin lugar libre ese día no se propone nada — el
        // sobreturno se elige a mano.
        if (pre) S.sel = porDia(pre).libres[0] || null;
        // Y si además vinieron por una HORA concreta (el hueco que clickearon
        // en Pacientes de hoy), esa gana sobre el primer libre del día.
        if (pre && S.horaPre) {
          var exacto = porDia(pre).libres.filter(function (t) { return t.hora === S.horaPre; })[0];
          if (exacto) { S.sel = exacto; S.calendario = false; }
        }
      }
      S.diaPre = null;
      S.horaPre = "";
      render();
    }).catch(function () { if (S === estado && request === SLOTS_REQUEST) { S.cargandoSlots = false; render(); } });
  }

  // {libres, sobres} de un día — el calendario y la propuesta miran lo mismo
  function porDia(dia) {
    return {
      libres: (S.libres || []).filter(function (t) { return t.dia === dia; }),
      sobres: (S.sobres || []).filter(function (t) { return t.dia === dia; })
    };
  }

  // ------------------------------------------------- escaneo del DNI (mesa)
  // Dos caminos al mismo dato, según lo que haya en el mostrador: el lector de
  // teclado (escribe el PDF417 en el campo con foco) y la foto del documento
  // (zxing-wasm, el mismo módulo que ya usa el registro del paciente).
  // dni-scan.mjs and dni-barcode.mjs are fingerprinted
  // (core/static_fingerprint.py, CLIENT_LOADED). A served file cannot know its
  // current URL, so the shell (templates/admin/base.html) emits it in
  // window.SANA_STATIC and importDep() resolves it there. This fails closed
  // (rejected import plus console.error, never a fixed fallback URL), matching
  // resolveUrl() in islands.mjs. The catches below already cover that path
  // (reader: null module; photo: existing user-facing error).
  var SCAN = null;      // promise of platform/dni-scan.mjs
  function cargarScan() {
    if (!SCAN) SCAN = importDep("platform/dni-scan.mjs", new URL("./dni-scan.mjs", import.meta.url)).catch(function () { return null; });
    return SCAN;
  }

  function scanMsg(txt) {
    var e = $("#ct-scan-msg");
    if (!e) return;
    e.textContent = txt || "";
    e.hidden = !txt;
  }

  // Lo leído del documento entra por el mismo camino que lo tipeado: se busca
  // la ficha por DNI. Si no existe, el nombre del documento queda listo como
  // nota — la mesa igual puede dar el turno y completar la ficha después.
  function usarDniLeido(patient) {
    var nombre = [patient.apellido, patient.nombres].filter(Boolean).join(", ");
    scanMsg(nombre
      ? applyVars(T("admin.queue.create_appt.leido_nombre", { nombre: nombre }, "Leído: {nombre}"), { nombre: nombre })
      : applyVars(T("admin.queue.create_appt.leido_dni", { dni: patient.dni }, "Leído: DNI {dni}"), { dni: patient.dni }));
    var inp = $("#ct-pac");
    if (inp) inp.value = patient.dni;
    ULTIMA_BUSQUEDA = patient.dni;
    return sugerirPacientes(patient.dni).then(function () {
      var ficha = PAC_CACHE[patient.dni];
      if (ficha) { S.paciente = ficha; S.nota = ""; render(); return; }
      // Sin ficha: el alta se abre con TODO lo que dice el documento.
      return Promise.all([cargarScan(), cargarFechas().catch(function () { return null; })]).then(function (mods) {
        var legible = (mods[0] && mods[0].nombreLegible) || function (v) { return v; };
        var arg = mods[1] && mods[1].isoToArg ? mods[1].isoToArg(patient.nacimiento) : "";
        S.alta = { dni: patient.dni, apellido: legible(patient.apellido), nombre: legible(patient.nombres),
          nacimiento: arg || patient.nacimiento || "", sexo: patient.sexo || "" };
        S.paciente = null; S.nota = "";
        render();
      });
    });
  }

  function wireLector(input) {
    if (!input || input._ctLector) return;
    input._ctLector = true;
    cargarScan().then(function (mod) {
      if (mod && mod.escucharLector) mod.escucharLector(input, usarDniLeido);
    });
  }

  function wireEscaneoFoto() {
    var btn = $("#ct-scan"), file = $("#ct-scan-file");
    if (!btn || !file) return;
    btn.addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () {
      var f = file.files && file.files[0];
      if (!f) return;
      scanMsg(T("admin.queue.create_appt.leyendo_documento", null, "Leyendo el documento…"));
      importDep("platform/dni-barcode.mjs", new URL("./dni-barcode.mjs", import.meta.url)).then(function (mod) {
        return mod.readDni(f);
      }).then(function (patient) {
        if (!patient) { scanMsg(T("admin.queue.create_appt.dni_no_leido_codigo", null, "No se pudo leer el código del DNI. Probá de nuevo o escribí el dato.")); return; }
        return usarDniLeido(patient);
      }).catch(function () {
        scanMsg(T("admin.queue.create_appt.dni_no_leido_documento", null, "No se pudo leer el documento. Escribí el dato."));
      }).then(function () { file.value = ""; });
    });
  }

  // ------------------------------------------------------------------ wire
  function wire() {
    // Los controles concretos (cerrar, buscadores) se re-atan en cada render
    // porque son elementos nuevos; el delegado del modal, no (ver más abajo).
    var cerrar = $("#ct-close");
    if (cerrar) cerrar.addEventListener("click", cerrarModal);

    var pac = $("#ct-pac");
    if (pac) {
      pac.addEventListener("input", function () {
        clearTimeout(TIMER);
        var v = pac.value.trim();
        TIMER = setTimeout(function () { ULTIMA_BUSQUEDA = v; sugerirPacientes(v); }, 220);
      });
      wireLector(pac);
    }
    wireEscaneoFoto();
    var medTurno = $("#ct-med-turno");
    if (medTurno) medTurno.addEventListener("change", function () { S.medicoTurno = medTurno.value; render(); });
    var svc = $("#ct-svc");
    if (svc) {
      svc.addEventListener("input", function () {
        clearTimeout(TIMER);
        var v = svc.value.trim();
        TIMER = setTimeout(function () { ULTIMA_BUSQUEDA = v; sugerirServicios(v); }, 160);
      });
    }

    // Enter elige la primera sugerencia (responsable del producto, 2026-07-24). OJO: no alcanza
    // con mirar la lista, porque el operador tipea un DNI de corrido y aprieta
    // Enter ANTES de que corra el debounce de 220 ms — la lista todavía está
    // vacía y el Enter no hacía nada. Así que Enter cancela el debounce,
    // dispara la búsqueda en el momento y elige recién cuando llegó.
    function elegirPrimera(inp, buscar) {
      clearTimeout(TIMER);
      var q = inp.value.trim();
      var yaEsta = $(".ct-sug li:not([data-vacio])");
      if (yaEsta && q === ULTIMA_BUSQUEDA) { yaEsta.click(); return; }
      inp.disabled = true;
      ULTIMA_BUSQUEDA = q;
      Promise.resolve(buscar(q)).then(function () {
        inp.disabled = false;
        var primera = $(".ct-sug li:not([data-vacio])");
        if (primera) primera.click(); else inp.focus();
      }).catch(function () { inp.disabled = false; });
    }
    [[pac, sugerirPacientes], [svc, sugerirServicios]].forEach(function (par) {
      var inp = par[0];
      if (!inp) return;
      inp.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        e.preventDefault();
        elegirPrimera(inp, par[1]);
      });
    });

    var m = $("#ct-modal");
    // El delegado se ata UNA vez: render() reemplaza el innerHTML y volvía a
    // atarlo, así que cada click corría N veces — "Ver todos los horarios"
    // alternaba dos veces y el calendario nunca llegaba a abrirse.
    // OJO CON EL ORDEN: todo lo que se ata a un ELEMENTO concreto va ARRIBA de
    // este guard. Puesto abajo, el segundo render (el que corre cuando llega
    // el catálogo) salía por el return y el input nuevo se quedaba sin su
    // listener — así se perdió el Enter del buscador de pacientes.
    if (m._ctWired) return;
    m._ctWired = true;
    m.addEventListener("click", function (e) {
      var li = e.target.closest("li[data-dni]");
      if (li) { S.paciente = PAC_CACHE[li.getAttribute("data-dni")]; S.nota = ""; render(); return; }
      var la = e.target.closest("li[data-alta]");
      if (la) {
        var escrito = (($("#ct-pac") || {}).value || "").trim();
        var numeros = escrito.replace(/[.\s]/g, "");
        S.alta = /^\d{6,9}$/.test(numeros) ? { dni: numeros } : { apellido: escrito };
        S.paciente = null; S.nota = "";
        render();
        var primero = $(S.alta.dni ? "#ct-alta-apellido" : "#ct-alta-dni");
        if (primero && primero.focus) primero.focus();
        return;
      }
      if (e.target.closest("#ct-alta-ok")) { crearPaciente(); return; }
      var ln = e.target.closest("li[data-nota]");
      if (ln) {
        var inp = $("#ct-pac");
        S.nota = ((inp && inp.value.trim()) || NOTA_PENDIENTE || "").slice(0, 80);
        S.paciente = null;
        if (S.nota) render();
        return;
      }
      var ls = e.target.closest("li[data-svc]");
      if (ls) {
        var id = ls.getAttribute("data-svc");
        S.servicio = SERVICIOS.filter(function (x) { return String(x.id) === id; })[0];
        cargarSlots(); return;
      }
      var cam = e.target.closest("[data-cambiar]");
      if (cam) {
        var q = cam.getAttribute("data-cambiar");
        if (q === "paciente") { S.paciente = null; S.nota = ""; S.alta = null; }
        if (q === "medico") {
          S.medicoPre = "";
          if (S.servicio) { cargarSlots(); return; }
          limpiarSlots();
        }
        if (q === "servicio" || q === "paciente") { S.servicio = null; limpiarSlots(); }
        render(); return;
      }
      if (e.target.closest("#ct-vermas")) { S.calendario = !S.calendario; render(); return; }
      var mes = e.target.closest("[data-mes]");
      if (mes) {
        var d = new Date((S.mes || hoyISO().slice(0, 7)) + "-01T00:00:00");
        d.setMonth(d.getMonth() + (+mes.getAttribute("data-mes")));
        S.mes = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
        render(); return;
      }
      var dia = e.target.closest("[data-dia]");
      if (dia) { S.diaCal = dia.getAttribute("data-dia"); render(); return; }
      var hora = e.target.closest("[data-slot]");
      if (hora) {
        var partes = hora.getAttribute("data-slot").split("|");
        var lista = partes[0] === "s" ? (S.sobres || []) : (S.libres || []);
        S.sel = lista[+partes[1]] || null;
        render(); return;
      }
      if (e.target.closest("#ct-ok")) crear();
    });

  }

  // Los códigos del server dicen POR QUÉ no se puede la excepción; el
  // operador tiene que leer qué hacer, no el código.
  var MOTIVOS = {
    sobreturno_requiere_medico: function () { return T("admin.queue.create_appt.motivo_sobreturno_requiere_medico", null, "Un sobreturno necesita un médico asignado."); },
    sobreturno_requiere_turno_previo: function () { return T("admin.queue.create_appt.motivo_sobreturno_requiere_turno_previo", null, "Ese horario quedó libre: sacá el turno normal."); },
    sobreturno_no_configurado: function () { return T("admin.queue.create_appt.motivo_sobreturno_no_configurado", null, "La agenda de ese médico no tiene sobreturnos habilitados."); },
    sobreturno_cupo_agotado: function () { return T("admin.queue.create_appt.motivo_sobreturno_cupo_agotado", null, "Se agotó el cupo de sobreturnos de esta agenda."); },
    sobreturno_sin_agenda: function () { return T("admin.queue.create_appt.motivo_sobreturno_sin_agenda", null, "Ese médico no tiene agenda cargada en ese horario."); },
    sobreturno_fuera_de_agenda: function () { return T("admin.queue.create_appt.motivo_sobreturno_fuera_de_agenda", null, "Ese horario queda fuera de la agenda del médico."); },
    sobreturno_horario_cerrado: function () { return T("admin.queue.create_appt.motivo_sobreturno_horario_cerrado", null, "Ese día el médico no atiende."); },
    sobreturno_horario_bloqueado: function () { return T("admin.queue.create_appt.motivo_sobreturno_horario_bloqueado", null, "Ese horario está bloqueado en la agenda."); },
    turno_medico_tomado: function () { return T("admin.queue.create_appt.motivo_turno_medico_tomado", null, "Ese médico ya tiene un turno a esa hora."); },
    medico_no_atiende_servicio: function () { return T("admin.queue.create_appt.motivo_medico_no_atiende_servicio", null, "Ese médico no atiende este servicio. Elegí uno de la lista del servicio."); },
    paciente_no_encontrado: function () { return T("admin.queue.create_appt.motivo_paciente_no_encontrado", null, "No encontramos a ese paciente. Cargalo primero en Pacientes."); }
  };
  // Un turno normal fuera de la agenda del médico vuelve con los códigos de la
  // aprobación (`_doctor_appointment_in_schedule`); el motivo es el mismo que
  // el del sobreturno, así que se lee con la misma frase.
  MOTIVOS.turno_medico_sin_agenda = MOTIVOS.sobreturno_sin_agenda;
  MOTIVOS.turno_medico_fuera_de_agenda = MOTIVOS.sobreturno_fuera_de_agenda;
  MOTIVOS.turno_medico_horario_cerrado = MOTIVOS.sobreturno_horario_cerrado;
  MOTIVOS.turno_medico_horario_bloqueado = MOTIVOS.sobreturno_horario_bloqueado;
  function motivoTexto(code) {
    var f = MOTIVOS[code];
    return f ? f() : errText(code, T("admin.queue.create_appt.motivo_generico", null, "No se pudo crear el turno."));
  }

  // «Crear turno» tocado sin elegir médico: nada se guarda; el foco va a la
  // elección y se marca, con la frase de al lado.
  function faltaMedicoAviso() {
    var sel = $("#ct-med-turno");
    if (sel) { sel.classList.add("ct-falta"); sel.focus(); }
    var aviso = $("#ct-falta-medico");
    if (aviso) aviso.classList.add("ct-falta-txt");
  }

  function crear() {
    var el = elegido();
    if (!hayPaciente() || !S.servicio || !el || GUARDANDO) return;
    if (pideMedico(el) && !S.medicoTurno) { faltaMedicoAviso(); return; }
    var btn = $("#ct-ok");
    btn.disabled = true;
    errorMsg("");
    var estado = S;
    var payload = {
      servicio_id: S.servicio.id,
      dia: el.dia, hora: el.hora,
      medico: medicoDelTurno(el),
      sobreturno: !!el.sobreturno
    };
    // Uno de los dos, nunca los dos: una ficha de paciente, o la nota suelta.
    if (S.paciente) payload.dni = S.paciente.dni; else payload.nota = S.nota;
    GUARDANDO = true;
    quitarModal(false);
    toast(T("admin.queue.create_appt.saving", null, "Guardando…"));
    api("/api/admin/appointments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      GUARDANDO = false;
      if (S !== estado) return;
      if (!r.ok || !r.j || r.j.ok !== true) {
        S = estado;
        montarModal();
        if (r.j && r.j.error === "turno_tomado") {
          toast(T("admin.queue.create_appt.horario_ocupado", null, "Ese horario se ocupó recién."));
          return cargarSlots().then(function () {
            errorMsg(T("admin.queue.create_appt.horario_ocupado_elegir_otro", null, "Ese horario se ocupó recién. Elegí otro."));
          });
        }
        if (r.ok && (!r.j || r.j.ok !== false)) {
          var unconfirmed = T("admin.queue.create_appt.save_outcome_unknown", null, "No se pudo confirmar el turno. Revisá la cola antes de volver a intentarlo.");
          errorMsg(unconfirmed);
          toast(unconfirmed);
        } else {
          errorMsg(motivoTexto(r.j && r.j.error));
          // En la demo el aviso es su frase única (TASK-48), no «No se guardó».
          toast(r.j && r.j.error === "demo_solo_lectura" ? motivoTexto(r.j.error)
            : T("admin.queue.create_appt.no_guardado", null, "No se guardó el turno."));
        }
        return;
      }
      // Con ficha (DNI) el modal queda abierto en «Turno creado» y dice cómo
      // quedó en HSI (contraparte técnica, 2026-10-19: la mesa no encontraba la sincro).
      // Una nota suelta no va a HSI: se cierra como siempre.
      var creado = (estado.paciente && r.j.hsi) ? {
        id: r.j.id, dia: el.dia, hora: el.hora, sobreturno: !!el.sobreturno,
        paciente: [estado.paciente.apellido, estado.paciente.nombre].filter(Boolean).join(", "),
        dni: estado.paciente.dni, servicio: estado.servicio.nombre || "", medico: payload.medico,
        hsi: r.j.hsi
      } : null;
      S = creado ? { creado: creado, desde: estado } : null;
      if (creado) montarModal();
      // Reemplaza al «Guardando…» en el acto (la tostada vive 2,6 s).
      toast((el.sobreturno
        ? T("admin.queue.create_appt.sobreturno_creado", null, "Sobreturno creado: ")
        : T("admin.queue.create_appt.turno_creado", null, "Turno creado: ")) + cuandoLargo(el.dia, el.hora) + ".");
      // la cola, las solicitudes y el calendario del médico muestran el turno
      // recién cargado sin que nadie tenga que recargar la página
      // Refresh belongs to the mounted caller, not a retired screen global.
      // A refresh failure must never turn a successful POST into a save error.
      if (estado.onCreated) {
        Promise.resolve().then(function () { return estado.onCreated(payload); }).catch(function (error) {
          console.error("[create-appointment] owner refresh failed", error);
        });
      }
      if (window.sanaShowSolicitudes) window.sanaShowSolicitudes();
      if (window.cargarSolicitudes) window.cargarSolicitudes();
    }).catch(function (e) {
      GUARDANDO = false;
      if (S !== estado) return;
      if (String(e.message) === "redirect") { S = null; return; }
      S = estado;
      montarModal();
      var unconfirmed = T("admin.queue.create_appt.save_outcome_unknown", null, "No se pudo confirmar el turno. Revisá la cola antes de volver a intentarlo.");
      errorMsg(unconfirmed);
      toast(unconfirmed);
    });
  }

  function quitarModal(limpiar) {
    var b = $("#ct-back");
    if (b) b.remove();
    document.removeEventListener("keydown", onEsc);
    if (limpiar !== false) { SLOTS_REQUEST += 1; S = null; }
  }

  function cerrarModal() { quitarModal(true); }

  // El catálogo se carga de forma asíncrona. Si se cerró y se volvió a abrir el
  // modal antes de que responda, su callback no puede elegir un servicio ni
  // pintar sobre el estado de la apertura nueva.
  function modalSigueActivo(estado) { return S === estado && !!$("#ct-back"); }

  function onEsc(e) { if (e.key === "Escape") cerrarModal(); }

  function montarModal() {
    if ($("#ct-back") || !S) return;
    var back = document.createElement("div");
    back.className = "ct-back";
    back.id = "ct-back";
    back.innerHTML = '<div class="ct-modal" id="ct-modal" role="dialog" aria-modal="true" aria-label="Crear turno"></div>';
    back.addEventListener("mousedown", function (e) { if (e.target === back) cerrarModal(); });
    document.body.appendChild(back);
    document.addEventListener("keydown", onEsc);
    render();
  }

  function abrir(pre, options) {
    ensureCss();
    if ($("#ct-back") || GUARDANDO) return;
    pre = pre || {};
    options = options || {};
    // pre.medico (Pacientes de hoy, calendario del médico): filtra los
    // horarios a los de esa persona — es de quien están hablando — y es lo
    // que habilita el sobreturno, que vive en la agenda de un médico concreto.
    // pre.dia: el día que estaba mirando quien abrió el modal.
    // pre.hora: el hueco concreto que clickearon en Pacientes de hoy — llega
    // ya elegido, así lo único que falta es decir quién es el paciente.
    // pre.paciente ({dni, apellido, nombre}): la ficha de Pacientes al
    // reprogramar un turno cancelado — el paciente ya está elegido.
    var pacPre = pre.paciente && pre.paciente.dni ? pre.paciente : null;
    S = { paciente: pacPre, servicio: null, libres: [], sobres: [], calendario: false,
          onCreated: typeof options.onCreated === "function" ? options.onCreated : null,
          medicoPre: pre.medico || "", diaPre: pre.dia || "", horaPre: pre.hora || "" };
    SLOTS_REQUEST += 1;
    montarModal();
    var estado = S;
    cargarCatalogo().then(function () {
      if (!modalSigueActivo(estado)) return;
      if (pre.servicio_id) {
        var uno = SERVICIOS.filter(function (s) { return String(s.id) === String(pre.servicio_id); })[0];
        var permitidos = pre.medico ? serviciosDe(pre.medico) : SERVICIOS;
        if (uno && permitidos.some(function (s) { return String(s.id) === String(uno.id); })) { S.servicio = uno; cargarSlots(); return; }
      }
      if (pre.medico) {
        var suyos = serviciosDe(pre.medico);
        if (suyos.length === 1) { S.servicio = suyos[0]; cargarSlots(); return; }
      }
      render();
    });
    return function () { if (S === estado || (S && S.desde === estado)) cerrarModal(); };
  }

  // Los servicios en los que atiende ese médico: el catálogo de servicios no
  // lista profesionales, así que el vínculo se lee del lado del médico
  // (cluster + centro, la misma regla que usa la web para armar la agenda).
  // The doctors who see a service at its center, the inverse of serviciosDe
  // (contraparte técnica, 2026-10-19: «les ahorraría tiempo si le aparecieran el nombre del
  // profesional además del caps y los horarios»).
  // El vínculo médico ↔ servicio NO se recalcula acá (2026-10-23): el servidor
  // lo manda en cada médico como `_servicios_pool` (schedule_policy.belongs —
  // etiqueta de la franja, o cluster + centro, y la especialidad en los
  // clusters comodín), la misma regla con la que ofrece la web, bloquea el
  // calendario y asigna la cola. Sin ese dato no se adivina: ningún servicio.
  function enPool(m, s) {
    return (m._servicios_pool || []).some(function (id) { return String(id) === String(s.id); });
  }

  function medicosDe(s) {
    return (MEDICOS || []).filter(function (m) {
      return m.nombre && !m._borrado && !m.rol && enPool(m, s);
    }).map(function (m) { return nombreMedico(m.nombre); });
  }

  function serviciosDe(nombre) {
    var med = (MEDICOS || []).filter(function (m) {
      return String(m.nombre || "").toLocaleLowerCase("es") === String(nombre).toLocaleLowerCase("es");
    })[0];
    if (!med) return [];
    return SERVICIOS.filter(function (s) { return enPool(med, s); });
  }

export { abrir };
