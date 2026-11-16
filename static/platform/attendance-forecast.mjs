/* attendance-forecast.mjs — the attendance forecast («Historial»,
   modules/turnos/patient_ranking.py) as ONE shared piece for every panel
   table that shows it: the doctor's «Mis turnos» (admin-queue.mjs) and his
   «Pacientes» (admin-doctor-patients.mjs).

   - THREE bars (the product owner, 2026-10-27: «three bars, not five. Less
     precision»): the number of filled bars is the LEVEL, not the score —
     3 «Viene siempre», 2 «Falta a veces» (amber), 1 «Falta seguido» (red);
     «Sin historial» is a dash. The percentage is neither drawn nor named.
   - The bars are the panel's occupancy segments (`.med-occupancy`,
     admin-direct.css), so this is the same component, not a new one.
   - The «Historial» header carries an Information button (Icons8 `info`,
     sf-regular, the project's pack — static/icons/info-sf-regular.svg used as
     a CSS mask, never drawn): hover, keyboard focus and a tap open a short
     explanation; Escape and a tap elsewhere close it. On a phone the table
     header is hidden, so each row's bars are the same trigger and the
     explanation starts with that patient's own line («Falta a veces · según
     4 turnos» + the reasons).
   - Every visible string comes from the catalog with its Spanish fallback;
     callers pass their own `T(key, vars, fallback)`.

   Pure render functions plus `bind(document, T)`, which owns the one
   popover and returns its dispose. Requirements F1–F3:
   design-plans/medico-turnos-pacientes-requirements.md. */

// Only for the plural category of the page language; strings still come
// from the caller's T.
var I18n = await (
  (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
    ? import(window.SANA_STATIC["platform/i18n.mjs"])
    : import(new URL("./i18n.mjs", import.meta.url))
).catch(function () { return null; });

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

function fill(text, vars) {
  var out = String(text == null ? "" : text);
  Object.keys(vars || {}).forEach(function (k) { out = out.split("{" + k + "}").join(String(vars[k])); });
  return out;
}

// The three bands patient_ranking.BANDS emits, best first. `key` is the
// catalog key under admin.queue.patients.band.*.
export var BANDS = [
  { slug: "viene-siempre", key: "always_comes", label: "Viene siempre", nivel: "good", level: 3 },
  { slug: "falta-a-veces", key: "sometimes_misses", label: "Falta a veces", nivel: "warn", level: 2 },
  { slug: "falta-seguido", key: "often_misses", label: "Falta seguido", nivel: "bad", level: 1 }
];
var NO_HISTORY = { slug: "sin-historial", key: "no_history", label: "Sin historial" };
// Retired slugs a client may still hold (a saved list filter) → the band
// that replaced them. «Suele venir» merged into «Viene siempre» (2026-10-27).
var RETIRED = { "suele-venir": "viene-siempre" };

export function canonicalBand(slug) {
  var s = String(slug || "");
  return RETIRED[s] || s;
}

function bandOf(r) {
  var slug = canonicalBand(r && r.banda);
  if (slug === NO_HISTORY.slug) return NO_HISTORY;
  for (var i = 0; i < BANDS.length; i++) if (BANDS[i].slug === slug) return BANDS[i];
  return null;
}

// The band's words from the catalog: the server's `etiqueta` is fixed
// Spanish and leaked whole into the other languages (the product owner, 2026-09-16).
export function bandLabel(r, T) {
  var b = bandOf(r);
  if (b) return T("admin.queue.patients.band." + b.key, null, b.label);
  return (r && r.etiqueta) || "";
}

export function level(r) {
  if (!r || r.sin_historial) return 0;
  var n = { good: 3, warn: 2, bad: 1 }[r.nivel];
  if (n) return n;
  var b = bandOf(r);
  return b && b.level ? b.level : 0;
}

function tone(n) { return n === 2 ? "warn" : n === 1 ? "bad" : "good"; }

function segmentsHtml(n) {
  var out = "";
  for (var i = 1; i <= 3; i++) out += '<span class="med-occupancy-segment' + (i <= n ? " is-filled" : "") + '" aria-hidden="true"></span>';
  return out;
}

// «Viene siempre · según 7 turnos»: the level and how many appointments it
// rests on — the accessible name of the bars and the patient's line in the
// explanation.
export function patientLine(r, T) {
  var n = Number(r && r.hechos) || 0;
  var vars = { band: bandLabel(r, T), n: n };
  // Number agreement: the page language's CLDR category picks the form —
  // `_few`/`_many` exist only in Russian and Ukrainian (i18n.mjs tn()).
  var category = I18n && I18n.pluralCategory ? I18n.pluralCategory(n) : (n === 1 ? "one" : "other");
  if (category === "few" || category === "many") {
    // based_on_few / based_on_many live only in the ru and uk catalogs; the key is
    // built from a name so the es/en key scan does not read a half key.
    var base = "admin.queue.forecast.based_on";
    var form = T(base + "_" + category, vars, "");
    if (form) return fill(form, vars);
  }
  // The singular carries {n}: Russian «one» is also 21, 31…
  return category === "one"
    ? fill(T("admin.queue.forecast.based_on_one", vars, "{band} · según {n} turno"), vars)
    : fill(T("admin.queue.forecast.based_on", vars, "{band} · según {n} turnos"), vars);
}

function dashHtml(T) {
  return '<span class="queue-referral-no" aria-label="' + esc(T("admin.queue.table.sin_historial", null, "Sin historial")) + '">—</span>';
}

export function barsHtml(r, T) {
  var n = level(r);
  if (!n) return dashHtml(T);
  return '<span class="med-occupancy queue-forecast queue-forecast--' + tone(n) + '" role="img" aria-label="'
    + esc(patientLine(r, T)) + '" data-segments="' + n + '">' + segmentsHtml(n) + "</span>";
}

// One row's cell content: the bars as a button that opens the explanation
// with this patient's line (the only way in on a phone, where the header is
// hidden). No history → the dash, nothing to explain about that patient.
export function cellHtml(r, T) {
  if (!level(r)) return dashHtml(T);
  return '<button type="button" class="forecast-bars-btn" data-forecast-info="" data-no-clip-pop aria-expanded="false" aria-controls="forecast-info-pop"'
    + ' data-forecast-patient="' + esc(patientLine(r, T)) + '" data-forecast-why="' + esc(((r && r.motivos) || []).join("\n")) + '">'
    + barsHtml(r, T) + "</button>";
}

// The Information button that goes inside the «Historial» header cell.
export function infoButtonHtml(T) {
  return '<button type="button" class="forecast-info" data-forecast-info="" data-no-clip-pop aria-expanded="false" aria-controls="forecast-info-pop" aria-label="'
    + esc(T("admin.queue.forecast.info_label", null, "Qué significa Historial")) + '">'
    + '<span class="forecast-info-icon" aria-hidden="true"></span></button>';
}

// The explanation: what counts, then the three bar patterns with their
// meaning and the dash. `ctx.patient`/`ctx.why` put one patient's own line
// first when it was opened from a row.
export function popoverHtml(T, ctx) {
  var head = "";
  if (ctx && ctx.patient) {
    head += '<p class="forecast-pop-patient">' + esc(ctx.patient) + "</p>";
    var why = String(ctx.why || "").split("\n").filter(Boolean);
    if (why.length) head += '<ul class="forecast-pop-why">' + why.map(function (m) { return "<li>" + esc(m) + "</li>"; }).join("") + "</ul>";
  }
  var rows = BANDS.map(function (b) {
    return '<li class="forecast-legend-row"><span class="med-occupancy queue-forecast queue-forecast--' + b.nivel + '" aria-hidden="true" data-filled="' + b.level + '">'
      + segmentsHtml(b.level) + "</span><span>" + esc(T("admin.queue.patients.band." + b.key, null, b.label)) + "</span></li>";
  }).join("")
    + '<li class="forecast-legend-row"><span class="forecast-legend-dash" aria-hidden="true">—</span><span>'
    + esc(T("admin.queue.forecast.legend_none", null, "Sin historial todavía")) + "</span></li>";
  return head
    + '<p class="forecast-pop-what">' + esc(T("admin.queue.forecast.what_counts", null,
      "Cómo vino a sus turnos anteriores: si vino, si faltó o si canceló sobre la hora. Lo más reciente pesa más.")) + "</p>"
    + '<ul class="forecast-legend">' + rows + "</ul>";
}

// Owns the one popover of the document. Returns dispose.
export function bind(doc, T) {
  var win = doc.defaultView || window;
  var pop = null, trigger = null, pinned = false, hideTimer = null, refocusing = false;
  var offs = [];
  function on(target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    offs.push(function () { target.removeEventListener(type, fn, opts); });
  }
  function triggerOf(node) {
    return node && node.closest ? node.closest("[data-forecast-info]") : null;
  }
  function inPop(node) {
    return !!(pop && node && pop.contains(node));
  }
  function cancelHide() {
    if (hideTimer) { win.clearTimeout(hideTimer); hideTimer = null; }
  }
  function place() {
    if (!pop || !trigger) return;
    var r = trigger.getBoundingClientRect();
    var vw = win.innerWidth || 1024, vh = win.innerHeight || 768;
    var w = pop.offsetWidth || 300, h = pop.offsetHeight || 160;
    var left = Math.min(Math.max(8, r.left), Math.max(8, vw - w - 8));
    var top = r.bottom + 6;
    if (top + h > vh - 8 && r.top - h - 6 >= 8) top = r.top - h - 6;
    pop.style.left = left + "px";
    pop.style.top = Math.max(8, top) + "px";
  }
  function open(btn, pin) {
    cancelHide();
    if (trigger && trigger !== btn) trigger.setAttribute("aria-expanded", "false");
    if (!pop) {
      pop = doc.createElement("div");
      pop.setAttribute("id", "forecast-info-pop");
      pop.className = "forecast-pop";
      pop.setAttribute("role", "region");
      pop.setAttribute("aria-label", T("admin.queue.table.historial", null, "Historial"));
      doc.body.appendChild(pop);
    }
    var patient = btn.getAttribute("data-forecast-patient");
    pop.innerHTML = popoverHtml(T, patient ? { patient: patient, why: btn.getAttribute("data-forecast-why") } : null);
    pop.hidden = false;
    trigger = btn;
    pinned = !!pin;
    btn.setAttribute("aria-expanded", "true");
    place();
  }
  function close() {
    cancelHide();
    if (pop) pop.hidden = true;
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    trigger = null;
    pinned = false;
  }
  function isOpen() { return !!(pop && !pop.hidden); }
  on(doc, "pointerover", function (e) {
    if (e.pointerType && e.pointerType !== "mouse") return;
    var btn = triggerOf(e.target);
    if (btn) { if (!(isOpen() && trigger === btn)) open(btn, false); else cancelHide(); return; }
    if (inPop(e.target)) cancelHide();
  });
  on(doc, "pointerout", function (e) {
    if (e.pointerType && e.pointerType !== "mouse") return;
    if (!isOpen() || pinned) return;
    var to = e.relatedTarget;
    if ((to && trigger && trigger.contains(to)) || inPop(to)) return;
    cancelHide();
    hideTimer = win.setTimeout(function () { hideTimer = null; if (!pinned) close(); }, 150);
  });
  on(doc, "focusin", function (e) {
    if (refocusing) return;
    var btn = triggerOf(e.target);
    if (btn && !(isOpen() && trigger === btn)) open(btn, false);
  });
  on(doc, "focusout", function (e) {
    if (!isOpen() || pinned) return;
    var to = e.relatedTarget;
    if ((to && trigger && trigger.contains(to)) || inPop(to)) return;
    if (triggerOf(e.target) === trigger) close();
  });
  on(doc, "click", function (e) {
    var btn = triggerOf(e.target);
    if (btn) {
      if (e.preventDefault) e.preventDefault();
      if (isOpen() && trigger === btn && pinned) close();
      else open(btn, true);
      return;
    }
    if (isOpen() && !inPop(e.target)) close();
  });
  on(doc, "keydown", function (e) {
    if (e.key !== "Escape" || !isOpen()) return;
    var back = trigger;
    close();
    // Focus goes back to the button without reopening what Escape closed.
    refocusing = true;
    try { if (back && back.focus) back.focus(); } finally { refocusing = false; }
  });
  on(win, "resize", close);
  on(win, "scroll", place, true);
  return function dispose() {
    close();
    offs.splice(0).forEach(function (off) { off(); });
    if (pop && pop.remove) pop.remove();
    pop = null;
  };
}
