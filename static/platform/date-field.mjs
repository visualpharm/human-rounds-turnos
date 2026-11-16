// static/platform/date-field.mjs — the panel's date field, in the visitor's
// language.
//
// A native <input type="date"> draws its picker and its mm/dd/yyyy mask in the
// BROWSER's language, not the page's: in a Chrome set to English a doctor
// loading a closure saw an English calendar (probador en sitio, 2026-10-21 — "una queja de
// los médicos va a ser que el calendario está en inglés"). No attribute fixes
// that, so the panel replaces it once, everywhere:
//
//   install(root) enhances every unnamed input[type=date] already in `root`
//   and every one added later (a MutationObserver — the editors re-render
//   their rows by innerHTML/outerHTML, so enhancing once at mount is not
//   enough). Named fields belong to forms submitted by name and stay native.
//
// The contract with the code that reads those inputs is unchanged: `.value`
// stays ISO `YYYY-MM-DD` (empty while the typed text is not a whole date), and
// a picked day fires the same bubbling input + change a native field fires.
// What the person sees and types is DD/MM/AAAA, and the
// month grid is calendar-grid.mjs's — the same Spanish/i18n grid the agenda
// and the patient wizard draw. Native time fields get the same treatment: a
// typed 24-hour HH:MM (enhanceTime), never the browser's AM/PM wheel.

var I18n = await (
  (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
    ? import(window.SANA_STATIC["platform/i18n.mjs"])
    : import(new URL("./i18n.mjs", import.meta.url))
);
// The month grid is fetched on the first open, never with the page: most
// panel screens show a date field without anyone opening its calendar, and
// there is no CDN — every request counts.
var Grid = null, gridLoad = null;
function loadGrid(doc) {
  if (!gridLoad) {
    gridLoad = ((typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/calendar-grid.mjs"])
      ? import(window.SANA_STATIC["platform/calendar-grid.mjs"])
      : import(new URL("./calendar-grid.mjs", import.meta.url))
    ).then(function (mod) {
      Grid = mod;
      var style = doc.createElement("style");
      style.textContent = mod.css || "";
      doc.head.appendChild(style);
      return mod;
    });
  }
  return gridLoad;
}

function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

var NATIVE = typeof HTMLInputElement !== "undefined"
  ? Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value") : null;
var ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export const css =
  ".df-pop{position:fixed;z-index:1000;background:var(--panel,#fff);border:1px solid var(--border-strong,#d6dad6);" +
  "border-radius:12px;padding:12px;box-shadow:0 14px 34px -18px rgba(40,30,20,.35);}" +
  ".df-pop .medcal-grids{margin:0;}" +
  ".df-pop .medcal-mes-h{display:none;}" +
  ".df-pop .medcal-dia{height:36px;line-height:36px;font-size:15px;color:var(--text);}" +
  ".df-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 8px;}" +
  ".df-title{font-size:16px;font-weight:600;color:var(--text);text-transform:capitalize;}" +
  ".df-nav{width:40px;height:40px;border:1px solid var(--border-strong,#d6dad6);border-radius:8px;background:var(--panel,#fff);" +
  "color:var(--text);font:inherit;font-size:20px;line-height:1;cursor:pointer;}" +
  ".df-nav:hover{background:var(--hover-bg-soft);border-color:var(--hover-bd-strong);}" +
  ".df-nav:focus-visible{outline:2px solid var(--focus-border);outline-offset:2px;}";

function pad(n) { return String(n).padStart(2, "0"); }

export function isoToDisplay(iso) {
  var m = ISO_RE.exec(String(iso || ""));
  return m ? m[3] + "/" + m[2] + "/" + m[1] : "";
}

// DD/MM/AAAA (also D/M/AAAA, with - or . between) → ISO, or "" when the text
// is not a real calendar date.
export function displayToIso(text) {
  var s = String(text || "").trim();
  if (ISO_RE.test(s)) return s;
  var m = /^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$/.exec(s);
  if (!m) return "";
  var d = +m[1], mo = +m[2], y = +m[3];
  var dt = new Date(y, mo - 1, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return "";
  return y + "-" + pad(mo) + "-" + pad(d);
}

function monthTitle(year, month) {
  return T("date.month_full." + (month + 1), null,
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
      "septiembre", "octubre", "noviembre", "diciembre"][month]) + " " + year;
}

function injectCss(doc) {
  if (doc.getElementById("df-css")) return;
  var style = doc.createElement("style");
  style.id = "df-css";
  style.textContent = css;
  doc.head.appendChild(style);
}

var open = null; // the one open popover: {input, el, cleanup}

function close() {
  if (!open) return;
  var o = open;
  open = null;
  o.cleanup();
  o.el.remove();
}

function emit(input) {
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function openFor(input) {
  if (open && open.input === input) return;
  if (!Grid) {
    loadGrid(input.ownerDocument).then(function () {
      if (input.isConnected && input.ownerDocument.activeElement === input) openFor(input);
    }).catch(function () {});
    return;
  }
  close();
  var doc = input.ownerDocument;
  var iso = input.value;
  var base = ISO_RE.test(iso) ? new Date(iso + "T00:00") : new Date();
  var view = { y: base.getFullYear(), m: base.getMonth() };
  var el = doc.createElement("div");
  el.className = "df-pop";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-label", T("date.pick", null, "Elegir fecha"));

  function paint() {
    var first = view.y + "-" + pad(view.m + 1) + "-01";
    var last = view.y + "-" + pad(view.m + 1) + "-" + pad(new Date(view.y, view.m + 1, 0).getDate());
    el.innerHTML = '<div class="df-head">' +
      '<button type="button" class="df-nav" data-df-step="-1" aria-label="' + T("date.prev_month", null, "Mes anterior") + '">‹</button>' +
      '<span class="df-title">' + monthTitle(view.y, view.m) + "</span>" +
      '<button type="button" class="df-nav" data-df-step="1" aria-label="' + T("date.next_month", null, "Mes siguiente") + '">›</button>' +
      "</div>" +
      Grid.gridHtml([{ dia: first }], { from: first, to: last, selected: input.value, dateAttr: "data-df-date" });
  }
  function place() {
    var r = input.getBoundingClientRect();
    var w = el.offsetWidth, h = el.offsetHeight;
    var vw = doc.documentElement.clientWidth, vh = doc.documentElement.clientHeight;
    var left = Math.max(8, Math.min(r.left, vw - w - 8));
    var top = r.bottom + 6 + h > vh && r.top - 6 - h > 0 ? r.top - 6 - h : r.bottom + 6;
    el.style.left = left + "px";
    el.style.top = top + "px";
  }
  el.addEventListener("mousedown", function (e) { e.preventDefault(); }); // keep focus in the field
  el.addEventListener("click", function (e) {
    var step = e.target.closest("[data-df-step]");
    if (step) {
      view.m += +step.getAttribute("data-df-step");
      if (view.m < 0) { view.m = 11; view.y--; }
      if (view.m > 11) { view.m = 0; view.y++; }
      paint(); place();
      return;
    }
    var day = e.target.closest("[data-df-date]");
    if (day) {
      input.value = day.getAttribute("data-df-date");
      close();
      emit(input);
    }
  });
  function outside(e) { if (e.target !== input && !el.contains(e.target)) close(); }
  function key(e) { if (e.key === "Escape") close(); }
  function scrolled(e) { if (!el.contains(e.target)) close(); }
  doc.addEventListener("mousedown", outside, true);
  doc.addEventListener("keydown", key, true);
  doc.defaultView.addEventListener("scroll", scrolled, true);
  doc.defaultView.addEventListener("resize", close);
  paint();
  doc.body.appendChild(el);
  place();
  open = { input: input, el: el, cleanup: function () {
    doc.removeEventListener("mousedown", outside, true);
    doc.removeEventListener("keydown", key, true);
    doc.defaultView.removeEventListener("scroll", scrolled, true);
    doc.defaultView.removeEventListener("resize", close);
  } };
}

export function enhance(input) {
  if (!input || input.dataset.dateField) return input;
  input.dataset.dateField = "1";
  var iso = displayToIso(NATIVE.get.call(input));
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  input.placeholder = T("date.input_placeholder", null, "DD/MM/AAAA");
  if (!input.getAttribute("maxlength")) input.setAttribute("maxlength", "10");
  // `.value` keeps the ISO contract of the native field it replaces.
  Object.defineProperty(input, "value", {
    configurable: true,
    get: function () { return input.dataset.iso || ""; },
    set: function (v) {
      var next = displayToIso(v);
      input.dataset.iso = next;
      NATIVE.set.call(input, next ? isoToDisplay(next) : String(v || ""));
    }
  });
  input.value = iso;
  input.addEventListener("input", function (e) {
    if (e.isTrusted === false) return;
    var next = displayToIso(NATIVE.get.call(input));
    input.dataset.iso = next;
  });
  input.addEventListener("blur", function () {
    var iso2 = input.dataset.iso;
    if (iso2) NATIVE.set.call(input, isoToDisplay(iso2));
  });
  input.addEventListener("focus", function () { openFor(input); });
  input.addEventListener("click", function () { openFor(input); });
  input.addEventListener("keydown", function (e) { if (e.key === "Tab") close(); });
  return input;
}

// "930", "9:30", "0930" → "09:30"; "" when it is not a clock time. Same
// reading of a numeric keyboard as doctor-workspace.mjs's formatTime.
export function normalizeTime(text) {
  var s = String(text || "").trim();
  var m = /^(\d{1,2}):(\d{2})$/.exec(s) || /^(\d{2})(\d{2})$/.exec(s) || /^(\d)(\d{2})$/.exec(s) || /^(\d{1,2})$/.exec(s);
  if (!m) return "";
  var h = +m[1], mi = m[2] == null ? 0 : +m[2];
  if (h > 23 || mi > 59) return "";
  return pad(h) + ":" + pad(mi);
}

// The panel's clock: a native <input type="time"> shows an AM/PM wheel in an
// English browser (the on-site tester's calendar complaint, same class); Argentina reads
// 24 hours. The panel already types times in 24 h (doctor-workspace.mjs);
// every remaining native time field becomes the same typed HH:MM control.
// `.value` keeps the native contract: "HH:MM", or "" while incomplete.
export function enhanceTime(input) {
  if (!input || input.dataset.timeField) return input;
  input.dataset.timeField = "1";
  var start = normalizeTime(NATIVE.get.call(input));
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  if (!input.placeholder) input.placeholder = T("date.time_placeholder", null, "HH:MM");
  input.setAttribute("maxlength", "5");
  Object.defineProperty(input, "value", {
    configurable: true,
    get: function () { return input.dataset.hhmm || ""; },
    set: function (v) {
      var next = normalizeTime(v);
      input.dataset.hhmm = next;
      NATIVE.set.call(input, next || String(v || ""));
    }
  });
  input.value = start;
  input.addEventListener("input", function (e) {
    if (e.isTrusted === false) return;
    var next = normalizeTime(NATIVE.get.call(input));
    input.dataset.hhmm = next;
  });
  input.addEventListener("blur", function () {
    if (input.dataset.hhmm) NATIVE.set.call(input, input.dataset.hhmm);
  });
  return input;
}

export function install(root) {
  root = root || document;
  var doc = root.ownerDocument || root;
  injectCss(doc);
  // Only the controller-read fields (no `name`): the schedule, closure and
  // holiday editors. A named field belongs to a form whose submit contract
  // (and whose tests) address it by name; those stay native.
  var DATE = "input[type=date]:not([name])", TIME = "input[type=time]:not([name])";
  function scan(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.matches && node.matches(DATE)) enhance(node);
    if (node.matches && node.matches(TIME)) enhanceTime(node);
    if (node.querySelectorAll) node.querySelectorAll(DATE).forEach(enhance);
    if (node.querySelectorAll) node.querySelectorAll(TIME).forEach(enhanceTime);
  }
  scan(root.nodeType === 9 ? root.documentElement : root);
  var observer = new MutationObserver(function (records) {
    records.forEach(function (r) { r.addedNodes.forEach(scan); });
  });
  observer.observe(root.nodeType === 9 ? root.documentElement : root, { childList: true, subtree: true });
  return function () { observer.disconnect(); close(); };
}
