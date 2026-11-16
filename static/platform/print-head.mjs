/* static/platform/print-head.mjs — the head of every printed sheet.

   The shared print layer (human-rounds.css, @page) writes `var(--print-head)`
   into the top margin box of every sheet. This module fills that variable on
   <html>: the screen's name, the filters in force and the date and hour of the
   print, «Solicitudes · Pendientes · Impreso: Sábado 7 de noviembre, 15:50».
   The paper then says what it is and when it was true, which the page alone
   cannot (the product owner, 2026-11-07).

   - Screen name: the panel's title (#adm-h1), else the page's first <h1>,
     else the document title.
   - Filters: what a screen marks `data-print-filter` (its text), plus every
     filter control left on a value — a select off its "all" option, a search
     box with text, a date field — outside tables, dialogs and the menus.
   - Time: the installation clock (dates.mjs larga, never the browser's zone),
     through the catalog key date.printed_at.

   Recomputed right before printing (beforeprint, and the print media query
   turning on — what an emulated print does), so the hour is the print's.
   Fail-open: without it the sheet simply has no head.

   The same pass sizes the columns of every table for paper (the product owner's hub,
   2026-11-07: a name in three lines beside a wide «Contacto» and an empty
   «Servicio más usado»). It marks cells only; the print layer of
   human-rounds.css reads the marks, the screen never does:
   - a column empty in the whole table (no text, «—», no dot or picture once
     the controls are off the paper) — `data-print-empty`: not printed;
   - a column whose longest cell is short (a person with the age, doctors,
     a date, a number, a phone) — `data-print-nowrap`: one line, so the
     column is as wide as its content. Every column first gets its narrowest
     (its longest piece that cannot wrap: a word, a name, an address); what
     the sheet has left goes to the short columns, cheapest first. A long
     text (a question, a note) always wraps — it absorbs what is left.
   Widths come from the print styles themselves (a hidden copy of a cell
   measured by the browser) once they are on; before that, from the text.
   The screen's layout (fixed columns, icons for contacts, ellipses) says
   nothing about the paper's. */
"use strict";

var STATIC = (typeof window !== "undefined" && window.SANA_STATIC) || {};
var I18n = await import(STATIC["platform/i18n.mjs"] || new URL("./i18n.mjs", import.meta.url).href)
  .catch(function () { return null; });
var Dates = await import(STATIC["platform/dates.mjs"] || new URL("./dates.mjs", import.meta.url).href)
  .catch(function () { return null; });

function T(key, vars, fallback) {
  if (I18n && I18n.t) return I18n.t(key, vars, fallback);
  return String(fallback || key).replace(/\{(\w+)\}/g, function (m, k) {
    return vars && k in vars ? String(vars[k]) : m;
  });
}

var SEP = " \u00b7 ";
var CHROME = ".adm5-side, #stznav, .stznav, .demo-installation, footer, dialog, [role=dialog], table, #stz-chat-panel, [data-print=\"off\"]";

function clean(s) {
  return String(s == null ? "" : s).replace(/\s+/g, " ").trim();
}

function shown(el) {
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}

export function screenName(doc) {
  doc = doc || document;
  // The first title on screen: a page can keep the h1 of a hidden step.
  var hs = [doc.getElementById("adm-h1")].concat(Array.prototype.slice.call(doc.querySelectorAll("main h1, h1")));
  for (var i = 0; i < hs.length; i++) {
    var name = hs[i] && shown(hs[i]) ? clean(hs[i].textContent) : "";
    if (name) return name;
  }
  return clean(doc.title);
}

export function filters(doc) {
  doc = doc || document;
  var out = [];
  var add = function (s) { s = clean(s); if (s && out.indexOf(s) < 0) out.push(s); };
  doc.querySelectorAll("[data-print-filter]").forEach(function (el) {
    if (shown(el)) add(el.getAttribute("data-print-filter") || el.textContent);
  });
  doc.querySelectorAll("select, input[type=search], input[type=date]").forEach(function (el) {
    if (el.closest(CHROME) || !shown(el) || el.closest("[data-print-filter]")) return;
    // A form field (it has its <label>) is data, not a filter of the list.
    if (el.labels && el.labels.length) return;
    if (el.tagName === "SELECT") {
      var opt = el.selectedOptions && el.selectedOptions[0];
      // A filter left on its first option («all», the default view) says
      // nothing the title does not; the shared admin select keeps "" there.
      if (!opt || opt.value === "" || el.selectedIndex < 1) return;
      add(opt.textContent);
    } else if (el.type === "date") {
      if (el.value && Dates && Dates.larga) add(Dates.larga(el.value));
    } else if (el.value) {
      add("\u00ab" + el.value + "\u00bb");
    }
  });
  return out;
}

export function printedAt(now) {
  var when = "";
  if (Dates && Dates.ahoraInstalacion && Dates.hoyIsoInstalacion && Dates.larga) {
    var t = Dates.ahoraInstalacion(now);
    var p = function (n) { return n < 10 ? "0" + n : "" + n; };
    when = Dates.larga(Dates.hoyIsoInstalacion(now), p(t.getHours()) + ":" + p(t.getMinutes()), now);
  }
  return when ? T("date.printed_at", { when: when }, "Impreso: {when}") : "";
}

export function headText(doc, now) {
  return [screenName(doc)].concat(filters(doc), [printedAt(now)]).filter(Boolean).join(SEP);
}

// A CSS <string> token: the value of a custom property read by `content:`.
export function cssString(s) {
  return "\"" + String(s).replace(/[\\"]/g, "\\$&").replace(/[\r\n]+/g, " ") + "\"";
}

// ---- columns on paper ------------------------------------------------------

// The sheet's text width: A4 (210 mm) less the @page side margins (2 x 12 mm),
// at 96 px per inch. The screen's width says nothing about the paper's.
export var SHEET_PX = Math.floor((210 - 24) / 25.4 * 96);
// Chrome's furthest shrink of a page wider than the sheet.
var SHRINK = 1.5;
// A cell's side padding; an average character where no canvas can measure.
var CELL_PAD_PX = 20;
var CH_PX = 7;
// A column whose longest cell is longer than this is running text: it wraps.
var SHORT_CHARS = 40;
// How many of a column's longest cells the browser measures.
var PROBES = 3;
var EMPTY_TEXT = { "": 1, "—": 1, "–": 1, "-": 1 };
// What the print layer takes off the paper inside a cell (controls, icons).
var OFF_PAPER = "button.btn, button[class*=\"btn\"], .btn, [role=\"group\"] > button, select, textarea, "
  + "input, [data-print=\"off\"], .cnt-ico, [hidden], template, script, style";
// What says something without words: a status dot, a bar, a picture.
var PICTURE = "img, canvas, svg, [class*=\"dot\"], [class*=\"bar\"], [style*=\"avatar-sprite\"]";

// What the print layer keeps whole on a line (human-rounds.css): a name.
var PIECE = ".pat-person, .pat-doc-n";
var TEL = "a[href^=\"tel:\"]";

function longer(a, b) { return b.length > a.length ? b : a; }

function longestWord(s) {
  return clean(s).split(" ").reduce(longer, "");
}

// The width of a text in a cell's own font, in px (a canvas measures it with
// no layout; without one, an average character).
var canvas = null;
function measurer(cell) {
  var g = null;
  try {
    canvas = canvas || document.createElement("canvas");
    g = canvas.getContext && canvas.getContext("2d");
    var cs = getComputedStyle(cell);
    // The shorthand reads empty when a longhand has no shorthand form.
    if (g) g.font = [cs.fontStyle, cs.fontWeight, cs.fontSize, cs.fontFamily].join(" ");
  } catch (e) { g = null; }
  return function (s) { return g && g.measureText ? g.measureText(s).width : s.length * CH_PX; };
}

// Under the print styles the browser itself measures a cell: a copy of its
// content inside it, out of the flow, at its narrowest (min-content: only the
// pieces that cannot wrap) and as one line (max-content). Icons, a status
// dot beside a name, an address printed from data-copy all count.
function probe(cell) {
  var p = document.createElement("div");
  p.innerHTML = cell.innerHTML;
  p.style.cssText = "position:absolute;visibility:hidden;left:0;top:0;width:min-content;";
  cell.appendChild(p);
  var cs = getComputedStyle(cell);
  var pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0) + 1;
  var min = p.getBoundingClientRect().width;
  p.setAttribute("data-print-nowrap", "");
  p.style.width = "max-content";
  var full = p.getBoundingClientRect().width;
  p.remove();
  return { min: Math.ceil(min + pad), full: Math.ceil(full + pad) };
}

// The cells worth measuring in a column: the head and the longest ones.
function widest(c) {
  var by = function (k) {
    return c.paper.slice().sort(function (a, b) { return b[k].length - a[k].length; }).slice(0, PROBES);
  };
  var picks = by("line").concat(by("piece"));
  return picks.filter(function (x, i) { return picks.indexOf(x) === i; }).map(function (x) { return x.cell; });
}

// The printed cell: its longest line, its longest piece that cannot wrap (a
// word, a name) and whether it shows anything at all. A contact drawn as an
// icon prints its number or address (data-copy, one line each).
export function paperCell(cell) {
  var c = cell.cloneNode(true);
  var lines = [];
  var piece = "";
  c.querySelectorAll("[data-copy]").forEach(function (el) {
    var l = clean(el.getAttribute("data-copy"));
    lines.push(l);
    // A phone number prints on one line (human-rounds.css).
    if (el.matches(TEL)) piece = longer(piece, l);
    el.remove();
  });
  c.querySelectorAll("button").forEach(function (b) {
    if (!clean(b.textContent)) b.remove();
  });
  c.querySelectorAll(OFF_PAPER.replace("[hidden], ", "")).forEach(function (el) { el.remove(); });
  c.querySelectorAll(PIECE).forEach(function (el) {
    piece = longer(piece, clean(el.textContent));
  });
  // A doctor hidden behind «+N» on screen prints (the print layer shows them).
  c.querySelectorAll("[hidden]").forEach(function (el) { if (!el.matches(".pat-doc")) el.remove(); });
  var text = clean(c.textContent);
  if (!EMPTY_TEXT[text]) lines.push(text);
  var line = "";
  lines.forEach(function (l) {
    line = longer(line, l);
    piece = longer(piece, longestWord(l));
  });
  return { chars: line.length, line: line, piece: piece, shows: !!line || !!c.querySelector(PICTURE) };
}

// Marks one table's cells for paper; see the head of this file.
export function sizeTable(table, sheetPx) {
  sheetPx = sheetPx || SHEET_PX;
  // The print styles are on (an emulated print, the print media query):
  // the browser measures; before that (beforeprint) the text does.
  var layout = printing() && !!table.getBoundingClientRect;
  table.querySelectorAll("[data-print-empty], [data-print-nowrap]").forEach(function (el) {
    el.removeAttribute("data-print-empty");
    el.removeAttribute("data-print-nowrap");
  });
  var rows = Array.prototype.slice.call(table.rows || []);
  if (!rows.length) return;
  // A row-spanning cell moves the cells under it: no column to speak of.
  if (table.querySelector("td[rowspan]:not([rowspan=\"1\"]), th[rowspan]:not([rowspan=\"1\"])")) return;
  var cols = [];
  var col = function (i) {
    return cols[i] || (cols[i] = { cells: [], body: [], paper: [], shows: false, chars: 0, line: "", piece: "", head: "", th: null });
  };
  rows.forEach(function (tr) {
    var head = !!(tr.parentNode && tr.parentNode.tagName === "THEAD");
    var at = 0;
    Array.prototype.forEach.call(tr.cells, function (cell) {
      var span = Math.max(1, cell.colSpan || 1);
      if (span > 1) {
        // A cell across several columns keeps them all, and sizes none.
        var shows = head || paperCell(cell).shows;
        for (var k = 0; k < span; k++) { col(at + k).cells.push(cell); if (shows) col(at + k).shows = true; }
      } else if (head || cell.tagName === "TH" && !cell.closest("tbody")) {
        col(at).cells.push(cell);
        col(at).head = longer(col(at).head, longestWord(cell.textContent));
        col(at).th = col(at).th || cell;
      } else {
        var p = paperCell(cell);
        var c = col(at);
        c.cells.push(cell);
        c.body.push(cell);
        c.paper.push({ cell: cell, line: p.line, piece: p.piece });
        if (p.shows) c.shows = true;
        c.line = longer(c.line, p.line);
        c.chars = c.line.length;
        c.piece = longer(c.piece, p.piece);
      }
      at += span;
    });
  });
  var hasBody = cols.some(function (c) { return c && c.body.length; });
  if (!hasBody) return cols;
  // Every printed column takes at least its longest piece (a word, a name,
  // the head's longest word; running text a dozen characters a line); what
  // the sheet has left goes to the short columns, cheapest first, as one line.
  var short = [];
  var used = 0;
  cols.forEach(function (c) {
    if (!c) return;
    if (!c.shows && c.body.length) {
      c.cells.forEach(function (cell) { if ((cell.colSpan || 1) === 1) cell.setAttribute("data-print-empty", ""); });
      return;
    }
    if (layout) {
      var heads = c.th ? probe(c.th) : { min: 0, full: 0 };
      c.min = heads.min;
      c.full = heads.min;
      widest(c).forEach(function (cell) {
        var m = probe(cell);
        c.min = Math.max(c.min, m.min);
        c.full = Math.max(c.full, m.full);
      });
    } else {
      var w = measurer(c.body[0] || c.cells[0]);
      var head = c.th ? measurer(c.th)(c.head) : w(c.head);
      c.min = Math.ceil(Math.max(w(c.piece), head)) + CELL_PAD_PX;
      c.full = Math.ceil(Math.max(w(c.line), head)) + CELL_PAD_PX;
    }
    // Running text takes a dozen characters a line at the least.
    if (c.chars > SHORT_CHARS) c.min = Math.max(c.min, Math.ceil(measurer(c.body[0])("x".repeat(12))) + CELL_PAD_PX);
    c.full = Math.max(c.full, c.min);
    used += c.min;
    if (c.chars <= SHORT_CHARS) short.push(c);
  });
  // A table wider than the sheet even at its narrowest (an e-mail address is
  // one word) makes Chrome lay the page out wider and shrink it onto the
  // sheet, up to 1.5 times: that room is then the sheet's.
  var room = used > sheetPx ? sheetPx * SHRINK : sheetPx;
  short.sort(function (a, b) { return (a.full - a.min) - (b.full - b.min); });
  short = short.filter(function (c) {
    var more = c.full - c.min;
    if (more > 0 && used + more > room) return false;
    used += more;
    return true;
  });
  short.forEach(function (c) {
    c.nowrap = true;
    c.body.forEach(function (cell) { cell.setAttribute("data-print-nowrap", ""); });
  });
  return cols;
}

export function sizeTables(doc) {
  doc = doc || document;
  doc.querySelectorAll("table").forEach(function (t) {
    try { sizeTable(t); } catch (e) { /* fail-open: the table keeps the browser's widths */ }
  });
}

// The print layer hides every control, so the filters are read while the
// screen is still on screen and kept; at print time only the hour is new.
var seen = { name: "", filters: [] };

function printing() {
  return !!(typeof window !== "undefined" && window.matchMedia && window.matchMedia("print").matches);
}

export function refresh(doc, now) {
  doc = doc || document;
  try {
    if (!printing()) seen = { name: screenName(doc), filters: filters(doc) };
    var text = [seen.name || screenName(doc)].concat(seen.filters, [printedAt(now)]).filter(Boolean).join(SEP);
    doc.documentElement.style.setProperty("--print-head", cssString(text));
  } catch (e) { /* fail-open: a sheet without a head */ }
}

if (typeof window !== "undefined" && typeof document !== "undefined" && !window.__sanaPrintHead) {
  window.__sanaPrintHead = true;
  var later = 0;
  // Throttled, not debounced: a screen that redraws every second still gets read.
  var soon = function () {
    if (later) return;
    later = setTimeout(function () { later = 0; refresh(document); }, 400);
  };
  var beforePrint = function () { refresh(document); sizeTables(document); };
  window.addEventListener("beforeprint", beforePrint);
  var mq = window.matchMedia && window.matchMedia("print");
  if (mq && mq.addEventListener) mq.addEventListener("change", beforePrint);
  document.addEventListener("change", soon, true);
  document.addEventListener("input", soon, true);
  window.addEventListener("popstate", soon);
  // Screens fill their title and filters after their data arrives.
  if (window.MutationObserver) {
    new MutationObserver(soon).observe(document.body || document.documentElement, { childList: true, subtree: true });
  }
  refresh(document);
}
