// static/platform/calendar-grid.mjs — grilla mensual compartida por el panel (isla MedCal) y
// el wizard de turnos del paciente.
//
// Módulo ES canónico: el anterior UMD y su global se borraron; éste es el
// dueño único del render. No publica ningún contrato global — exporta gridHtml() y css como
// bindings ES con nombre (age.mjs es el precedente del patrón).
//
// Se sirve con huella de contenido (core/static_fingerprint.py,
// CLIENT_LOADED): su URL vigente vive en window.SANA_STATIC y los
// consumidores la resuelven con import() dinámico, fail-closed (service.js,
// admin-content.js); la isla static/islands/MedCal.mjs la importa por el bare
// specifier "sana/calendar-grid" del import map. Si el import falla, cada
// consumidor sigue el camino fail-open de siempre: sin grilla, el resto de la
// pantalla no se rompe.
//
// Cero dependencias y cero acceso a `window` a nivel de módulo: sólo lo lee
// dentro de T(), con guard `typeof window`, para el SanaI18n opcional — así
// el módulo se puede importar y testear en Node sin fabricar nada.

var I18n = await (
    (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"])
      : import(new URL("./i18n.mjs", import.meta.url))
  );
  function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }

// El "hoy" de la grilla es el de la instalación (dates.mjs), no el del navegador. Si el
// módulo no carga, cae al reloj del navegador, como antes.
var Dates = await (
    (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"])
      ? import(window.SANA_STATIC["platform/dates.mjs"])
      : import(new URL("./dates.mjs", import.meta.url))
  ).catch(function () { return null; });

var MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
// lunes→domingo, mismo orden que la cabecera de la grilla. Dos letras porque
// a una letra "M" es lunes, martes O miércoles — y en 390px la cabecera de
// una letra sola se pisaba con la columna vecina (responsable del producto, 2026-09-10).
var DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
var DAY_TWO_ES = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"];

function monthName(monthIndex) { // 0-11
  return T("date.month_full." + (monthIndex + 1), null, MONTHS[monthIndex]);
}

function twoLetterDayLabels() {
  return DAY_KEYS.map(function (key, i) {
    return T("date.day_two_letter." + key, null, DAY_TWO_ES[i]);
  });
}

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
  });
}

export const css =
  ".medcal-grids{display:flex;flex-wrap:wrap;gap:22px;margin:0 0 22px;}" +
  ".medcal-mes{min-width:0;flex:0 1 auto;}" +
  ".medcal-mes-h{font-size:13px;font-weight:600;color:var(--text-2);margin:0 0 7px;text-transform:capitalize;}" +
  ".medcal-grid{display:grid;grid-template-columns:repeat(7,minmax(32px,1fr));gap:3px;}" +
  ".medcal-dow{font-size:inherit;color:var(--text);text-align:center;line-height:1.4;}" +
  ".medcal-dia{font:inherit;font-size:13px;color:var(--text-2);text-align:center;line-height:26px;height:26px;border:0;border-radius:7px;background:transparent;padding:0;cursor:pointer;}" +
  "@media(max-width:767px){.medcal-mes{flex:1 1 100%;}.medcal-dia{height:44px;line-height:44px;}}" +
  ".medcal-dia:hover{outline:1.5px solid var(--hover-bd-strong);outline-offset:-1.5px;}" +
  ".medcal-dia:focus-visible{outline:1.5px solid var(--accent);outline-offset:-1.5px;}" +
  ".medcal-dia:disabled{cursor:default;color:var(--text-4);}" +
  ".medcal-dia:disabled:hover{outline:0;}" +
  ".medcal-dia.is-selected{background:var(--accent);color:#fff;font-weight:750;box-shadow:0 0 0 2px var(--panel),0 0 0 4px var(--accent);}" +
  ".medcal-libre{background:var(--state-done-bg);color:var(--done);font-weight:600;}" +
  ".medcal-justo{background:var(--warn-soft);color:var(--warn);font-weight:600;}" +
  ".medcal-lleno{background:var(--danger-soft);color:var(--danger);font-weight:600;}" +
  ".medcal-hoy{outline:1.5px solid var(--border-strong);outline-offset:-1.5px;}" +
  // A grid that carries counts stacks the count under the day number. :where()
  // keeps this at one class of weight so a caller's own sizes (Pacientes de
  // hoy's .queue-month-calendar) still win.
  ":where(.medcal-grid:has(.medcal-count)) .medcal-dia{display:flex;flex-direction:column;align-items:center;justify-content:center;height:40px;line-height:1.15;}" +
  ".medcal-count{font-size:13px;font-weight:750;line-height:1.1;}" +
  "@media(max-width:767px){:where(.medcal-grid:has(.medcal-count)) .medcal-dia{height:48px;}}";

export function gridHtml(days, options) {
  days = Array.isArray(days) ? days : [];
  options = options || {};
  if (!days.length) return "";

  var byDate = {};
  days.forEach(function (day) {
    if (day && day.dia) byDate[day.dia] = day;
  });
  var dates = Object.keys(byDate).sort();
  if (!dates.length) return "";

  var dateAttr = /^[a-z0-9-]+$/.test(options.dateAttr || "")
    ? options.dateAttr : "data-calendar-date";
  var from = new Date((options.from || dates[0]) + "T00:00");
  var to = new Date((options.to || dates[dates.length - 1]) + "T00:00");
  var today = options.today || (Dates && Dates.hoyIsoInstalacion ? Dates.hoyIsoInstalacion()
    : new Date().toISOString().slice(0, 10));
  var html = '<div class="medcal-grids">';
  var current = new Date(from.getFullYear(), from.getMonth(), 1);

  while (current <= to) {
    var year = current.getFullYear(), month = current.getMonth();
    html += '<div class="medcal-mes"><div class="medcal-mes-h">' +
      monthName(month) + " " + year + '</div><div class="medcal-grid">';
    twoLetterDayLabels().forEach(function (label) {
      html += '<span class="medcal-dow">' + esc(label) + "</span>";
    });
    var first = new Date(year, month, 1);
    var pad = (first.getDay() + 6) % 7;
    for (var blank = 0; blank < pad; blank++) html += "<span></span>";
    var lastDay = new Date(year, month + 1, 0).getDate();

    for (var dayNum = 1; dayNum <= lastDay; dayNum++) {
      var iso = year + "-" + String(month + 1).padStart(2, "0") + "-" +
        String(dayNum).padStart(2, "0");
      var state = byDate[iso];
      var classes = "medcal-dia" + (state && state.className ? " " + state.className : "");
      if (iso === today) classes += " medcal-hoy";
      if (iso === options.selected) classes += " is-selected";
      var disabled = !!(options.disableMissing && !state);
      html += '<button type="button" class="' + esc(classes) + '" ' + dateAttr +
        '="' + iso + '"' + (state && state.title ? ' title="' + esc(state.title) + '"' : "") +
        (state && Number.isInteger(state.count) && state.count > 0 ? ' aria-label="' + esc(Tn('admin.queue.calendar.day_count', state.count, {day:dayNum}, 'Día {day}: {n} turno', 'Día {day}: {n} turnos')) + '"' : '') +
        (disabled ? " disabled" : "") + ">" + dayNum + (state && Number.isInteger(state.count) && state.count > 0 ? '<span class="medcal-count">' + state.count + '</span>' : '') + "</button>";
    }
    html += "</div></div>";
    current = new Date(year, month + 1, 1);
  }
  return html + "</div>";
}

// Date strings compare chronologically; duplicates never become extra navigation steps.
export function nextAppointmentDay(days, current, direction) {
  var sorted = [...new Set((days || []).filter(function (d) { return /^\d{4}-\d{2}-\d{2}$/.test(d); }))].sort();
  return direction < 0 ? sorted.reverse().find(function (d) { return d < current; }) || null
    : sorted.find(function (d) { return d > current; }) || null;
}
