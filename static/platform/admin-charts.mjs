/* static/platform/admin-charts.mjs — inline-SVG chart library for the admin
   reports panel. Full doctrine in .claude/skills/report-design/SKILL.md:
     - never a vertical column/bar chart (explicit veto); lollipop is
       horizontal (dot + stem), not a bar.
     - numbers are PRINTED on the chart, never hover-only; the tooltip
       (<title>) only adds what does not fit visually.
     - every chart accepts opts.read: one-sentence reading, fixed prefix
       "Cómo leerlo: ".
     - no flat-color card backgrounds (tile: the pill carries color of
       text+soft fill, not the whole card).
     - no em-dashes (—) in generated text; missing data is "s/d".

   Charts: hero, tile, scatter, heatmap, treemap, slope, lollipop, linea,
   densidad (Gauss/KDE curve with the median marked), piramide (women/men
   population pyramid by age band).

   Canonical ES module (audit rows 1.1/4.1). Named exports; no browser
   global, no classic twin, no CommonJS wrapper. Lives under
   static/platform/ (row 1.1 owning package) and is loaded through
   window.SANA_STATIC / sanaImportStatic. Top-level await on
   platform/i18n.mjs so a consumer that awaits this module also awaits
   its translation dependency — T() is live from the first call.

   Every public function returns an HTML string ready for innerHTML
   (inline <svg> + minimal divs). Colors via var(--accent) etc., always
   as a `style=` attribute (never a raw `fill=` value). Each <svg> pins
   its real height in px (viewBox height = height in px, vertical scale
   always 1:1 so text never shrinks) and width 100% with
   preserveAspectRatio="none" (only the width stretches) — the chart
   never overflows horizontally and the text stays readable on mobile.

   `_internal` exposes the pure math (niceTicks, truncation, treemap
   layout, formatting) so tests can assert it without parsing SVG; it is
   not a panel public API. */
"use strict";

var I18n = await (
  (typeof window !== "undefined")
    ? ((window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"])
      : Promise.reject(new Error("[static] platform/i18n.mjs is not in window.SANA_STATIC")))
    : import(new URL("./i18n.mjs", import.meta.url))
);
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

// =====================================================================
// utilidades compartidas
// =====================================================================

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

// Ancho REAL (px) del contenedor donde se van a insertar los gráficos. El
// consumidor (admin-reports.js) lo mide una vez por render y lo setea acá;
// cada gráfico usa ese valor como su vbW, así vbW == ancho renderizado y la
// escala horizontal queda 1:1 → el texto NO se estira. Es la solución al bug
// de "texto blanco estirado": con preserveAspectRatio="none" y un vbW fijo
// (680/480) más chico que la columna (~976px), TODO el SVG (texto incluido)
// se estiraba 1,4–2x en horizontal. Sin setear (tests en node, otros
// llamadores) cada gráfico cae a su ancho de diseño histórico.
var _ancho = 0;
function setAncho(w) {
  w = Number(w);
  _ancho = isFinite(w) && w >= 320 ? Math.round(w) : 0;
}
// Fallback: si nadie llamó a setAncho(), medir el contenedor de reportes
// directamente. Así el fix de "no estirar" queda AUTOCONTENIDO en este
// archivo — no depende de que admin-reports.js setee el ancho. Node-safe
// (sin document → 0 → cae al default de diseño).
function anchoAuto() {
  if (_ancho) return _ancho;
  if (typeof document === "undefined" || typeof document.getElementById !== "function") return 0;
  var b = document.getElementById("rep-body");
  var w = b ? b.clientWidth : 0;
  return w >= 320 ? w : 0;
}
// Ancho de un gráfico full-width: el explícito, el seteado, el medido, o el
// default de diseño — en ese orden.
function anchoDe(opts, def) {
  var a = (opts && opts.ancho) || _ancho || anchoAuto() || def;
  return a >= 320 ? a : def;
}

// One number formatter for the panel and its reports (I18n.formatNumber):
// thousands grouped ("4.188"), a decimal only where it changes something
// ("6 h", "6,5 h"), separators by the panel's language.
function sinDato() { return T("admin.reports.charts.sin_dato", null, "s/d"); }
function fmtInt(n) {
  if (n == null || !isFinite(n)) return sinDato();
  return I18n.formatNumber(Math.round(n), 0);
}
function fmtDec(n, d) {
  if (n == null || !isFinite(n)) return sinDato();
  return I18n.formatNumber(Number(n), d);
}
function fmtNum(n) {
  if (n == null || !isFinite(n)) return sinDato();
  return I18n.formatNumber(Number(n), 1);
}

function styleStr(props) {
  var out = [];
  for (var k in props) { if (props[k] != null && props[k] !== "") out.push(k + ":" + props[k]); }
  return out.join(";");
}

// Ancho aproximado de un texto en unidades de viewBox, para decidir cuándo
// truncar o cuándo dos etiquetas van a chocar. Factor empírico para la pila
// tipográfica del panel (sans-serif del sistema).
function textWidth(s, fontSize) { return String(s == null ? "" : s).length * fontSize * 0.56; }

// The panel's floor for any letter (the product owner: no small print; 13px is the admin
// minimum). Every text a chart draws uses it: axes, ticks, labels, values.
var FS = 13;
// Axes, ticks and captions are labels: the panel's dark label ink, never the
// translucent --text-3 grey that read as «light gray small» (review E14).
var COL_INK = "var(--hint-ink,var(--text-2))";
// A point label that has no free place but across the dashed reference line
// keeps its letters whole: a stroke in the page colour (report charts sit on
// --bg, not on a card) under the glyphs cuts the line where it passes behind
// the text (review 15.11, «Gustavo Ibarra»).
var HALO = { stroke: "var(--bg,#fff)", "stroke-width": "4px", "stroke-linejoin": "round", "paint-order": "stroke" };
function conHalo(props, halo) {
  if (!halo) return props;
  for (var k in HALO) props[k] = HALO[k];
  return props;
}

// Real width of a text in the panel font (canvas measureText); the estimate
// above where there is no canvas (node tests). Fitting decisions — does a
// label fit whole, how many axis labels fit — use this one.
var _ctx = null, _family = "";
function medir(s, fontSize, weight) {
  s = String(s == null ? "" : s);
  if (s && typeof document !== "undefined" && document && typeof document.createElement === "function") {
    try {
      if (!_ctx) {
        var c = document.createElement("canvas");
        _ctx = c && typeof c.getContext === "function" ? c.getContext("2d") : null;
        var b = document.body;
        _family = (b && typeof getComputedStyle === "function" ? getComputedStyle(b).fontFamily : "") || "sans-serif";
      }
      if (_ctx) {
        _ctx.font = (weight || 400) + " " + fontSize + "px " + _family;
        var w = _ctx.measureText(s).width;
        if (w > 0) return w;
      }
    } catch (e) { _ctx = null; }
  }
  return textWidth(s, fontSize) * ((weight || 400) >= 600 ? 1.06 : 1);
}

// Words into lines of at most maxW. fits=false when a single word is wider
// than maxW or the text needs more than maxLines lines: the caller then
// shows the label nowhere inside the shape (or cut at a word with the full
// text in a tooltip), never a word sliced in half.
function partirEnLineas(s, maxW, fontSize, weight, maxLines) {
  var words = String(s == null ? "" : s).split(/\s+/).filter(Boolean);
  var lines = [], cur = "";
  for (var i = 0; i < words.length; i++) {
    if (medir(words[i], fontSize, weight) > maxW) return { lines: lines, fits: false };
    var probe = cur ? cur + " " + words[i] : words[i];
    if (!cur || medir(probe, fontSize, weight) <= maxW) cur = probe;
    else { lines.push(cur); cur = words[i]; }
  }
  if (cur) lines.push(cur);
  return { lines: lines, fits: lines.length > 0 && lines.length <= (maxLines || Infinity) };
}

// A label of up to maxLines lines in SVG (tspans), centred on y. When it does
// not fit whole, the last line is cut at a word with «…» and the full text
// goes to the tooltip.
function textLines(x, y, s, opts) {
  var fontSize = opts.fontSize || FS, weight = opts.weight || 400, maxLines = opts.maxLines || 2;
  var full = String(s == null ? "" : s);
  var r = partirEnLineas(full, opts.maxWidth, fontSize, weight, maxLines);
  var lines = r.lines, truncated = false;
  if (!r.fits) {
    lines = lines.slice(0, maxLines - 1);
    var rest = full.split(/\s+/).filter(Boolean).slice(lines.join(" ").split(/\s+/).filter(Boolean).length).join(" ");
    lines.push(truncateToWidth(rest, opts.maxWidth, fontSize, weight).text);
    truncated = true;
  }
  var lh = Math.round(fontSize * 1.2);
  var y0 = y - (lines.length - 1) * lh / 2;
  var spans = lines.map(function (l, i) {
    return '<tspan x="' + x + '" y="' + (y0 + i * lh) + '">' + esc(l) + "</tspan>";
  }).join("");
  return '<text text-anchor="' + (opts.anchor || "start") + '"' + puntoAttr(opts) + ' style="'
    + styleStr(conHalo({ "font-size": fontSize + "px", "font-weight": weight, fill: opts.fill || "var(--text)" }, opts.halo)) + '">'
    + spans + (truncated ? "<title>" + esc(full) + "</title>" : "") + "</text>";
}
function lineasDe(s, maxW, fontSize, weight, maxLines) {
  var r = partirEnLineas(s, maxW, fontSize, weight, maxLines);
  return r.fits ? r.lines.length : maxLines;
}

function truncateToWidth(s, maxWidth, fontSize, weight) {
  s = String(s == null ? "" : s);
  if (!maxWidth || medir(s, fontSize, weight) <= maxWidth) return { text: s, truncated: false };
  var maxChars = Math.max(1, Math.floor(maxWidth / (fontSize * 0.56)) - 1);
  if (maxChars >= s.length) maxChars = s.length - 1;
  while (maxChars > 1 && medir(s.slice(0, maxChars) + "…", fontSize, weight) > maxWidth) maxChars--;
  var cut = s.slice(0, maxChars);
  // Cut at a word, never through one ("Traumatología y…", not
  // "Traumatología y kine…"); the full text stays in the tooltip.
  var sp = cut.lastIndexOf(" ");
  if (sp > 0) cut = cut.slice(0, sp);
  cut = cut.replace(/[\s,;:·\/(-]+$/, "");
  return { text: cut + "…", truncated: true };
}

// Redondeo "humano" de ticks de eje (algoritmo clásico nice-numbers).
function niceNum(range, round) {
  if (!range || !isFinite(range) || range <= 0) return 1;
  var exponent = Math.floor(Math.log(range) / Math.LN10);
  var fraction = range / Math.pow(10, exponent);
  var niceFraction;
  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
    else niceFraction = 10;
  } else {
    if (fraction <= 1) niceFraction = 1;
    else if (fraction <= 2) niceFraction = 2;
    else if (fraction <= 5) niceFraction = 5;
    else niceFraction = 10;
  }
  return niceFraction * Math.pow(10, exponent);
}

// entero: una serie de conteos — el paso nunca baja de 1 (no hay «0,5 visitas»).
function niceTicks(min, max, count, entero) {
  count = count || 4;
  if (min == null) min = 0;
  if (max == null) max = 1;
  if (min === max) { min -= (min === 0 ? 1 : Math.abs(min) * 0.1); max += (max === 0 ? 1 : Math.abs(max) * 0.1); }
  if (min > max) { var t = min; min = max; max = t; }
  var range = niceNum(max - min, false);
  var step = niceNum(range / (count - 1), true);
  if (entero && step < 1) step = 1;
  var niceMin = Math.floor(min / step) * step;
  var niceMax = Math.ceil(max / step) * step;
  var ticks = [];
  for (var v = niceMin; v <= niceMax + step * 0.5; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return { min: niceMin, max: niceMax, step: step, ticks: ticks };
}

// svg(vbW, heightPx, inner) — ver comentario del encabezado sobre por qué
// el alto viewBox == alto real (escala vertical siempre 1:1).
function svgWrap(vbW, heightPx, inner) {
  return '<svg viewBox="0 0 ' + vbW + ' ' + heightPx + '" width="100%" height="' + heightPx
    // overflow:hidden (default SVG behavior, dicho explícito): cualquier
    // etiqueta que por error calcule una posición fuera del viewBox se
    // recorta ACÁ ADENTRO en vez de escaparse del contenedor — visible
    // pero sin desbordar la página (visto en vivo: una etiqueta cerca del
    // punto más a la derecha del scatter se perdía, invisible, fuera del
    // viewport, con overflow:visible).
    // max-width == vbW px: el <svg> nunca se agranda MÁS ALLÁ de su ancho
    // natural (donde la escala X es 1:1), así que nunca se estira; width:100%
    // sigue dejándolo achicarse si el contenedor es más angosto (mobile),
    // manteniendo el texto a su alto real (escala Y siempre 1:1).
    + '" preserveAspectRatio="none" style="display:block;width:100%;max-width:' + vbW + 'px;overflow:hidden;font-family:inherit;">'
    + inner + '</svg>';
}

// A label that names one point says which (`data-punto="x,y"`, viewBox
// units): the chart check in the browser measures it against that point.
function puntoAttr(opts) {
  return opts && opts.punto ? ' data-punto="' + opts.punto[0] + "," + opts.punto[1] + '"' : "";
}

function textEl(x, y, s, opts) {
  opts = opts || {};
  var fontSize = Math.max(FS, opts.fontSize || FS);
  var anchor = opts.anchor || "start";
  var fill = opts.fill || COL_INK;
  var weight = opts.weight || 400;
  var full = String(s == null ? "" : s);
  var t = full, truncated = false;
  if (opts.maxWidth) {
    var r = truncateToWidth(full, opts.maxWidth, fontSize, weight);
    t = r.text; truncated = r.truncated;
  }
  var title = truncated ? "<title>" + esc(full) + "</title>" : "";
  return '<text x="' + x + '" y="' + y + '" text-anchor="' + anchor + '"' + puntoAttr(opts) + ' style="'
    + styleStr(conHalo({ "font-size": fontSize + "px", "font-weight": weight, fill: fill }, opts.halo)) + '">' + esc(t) + title + "</text>";
}

function readLineHtml(read) {
  if (!read) return "";
  return '<div class="sana-chart-read">' + esc(T("admin.reports.charts.como_leerlo", null, "Cómo leerlo: ")) + esc(read) + "</div>";
}

function chartWrap(svgHtml, read) {
  injectStyle();
  return '<div class="sana-chart">' + svgHtml + readLineHtml(read) + "</div>";
}

function emptyState(msg) {
  injectStyle();
  return '<p class="rep-empty">' + esc(msg || T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")) + "</p>";
}

var STYLE_INJECTED = false;
function injectStyle() {
  if (STYLE_INJECTED || typeof document === "undefined") return;
  STYLE_INJECTED = true;
  var css =
    ".sana-chart{width:100%;}" +
    ".sana-chart-read{margin-top:8px;font-size:13.5px;line-height:1.6;color:var(--text);}" +
    ".sana-chart-note{margin:8px 0 0;font-size:13px;line-height:1.5;color:var(--hint-ink,var(--text-2));}" +
    ".sana-tile{display:flex;flex-direction:column;height:100%;box-sizing:border-box;}" +
    ".sana-tile-pill{display:inline-flex;align-items:center;font-size:13px;font-weight:650;padding:3px 10px;border-radius:12px;max-width:100%;box-sizing:border-box;line-height:1.35;}" +
    ".sana-tile-mini{margin-top:auto;padding-top:10px;}" +
    ".sana-hero{margin:0 0 22px;}" +
    ".sana-hero-line{margin:0 0 7px;font-size:14px;line-height:1.5;color:var(--text);display:flex;align-items:baseline;gap:9px;}" +
    ".sana-hero-line--lead{font-size:19px;font-weight:650;color:var(--text);line-height:1.4;}" +
    ".sana-hero-dot{display:inline-block;width:8px;height:8px;min-width:8px;border-radius:50%;flex:none;transform:translateY(-1px);}" +
    ".sana-legend{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:10px;font-size:13px;color:var(--hint-ink,var(--text-2));}" +
    ".sana-legend-item{display:inline-flex;align-items:center;gap:6px;}" +
    ".sana-legend-dot{display:inline-block;width:9px;height:9px;min-width:9px;border-radius:50%;flex:none;}" +
    ".sana-tm{position:relative;width:100%;box-sizing:border-box;}" +
    ".sana-tm-tile{position:absolute;box-sizing:border-box;padding:8px;overflow:hidden;cursor:help;}" +
    ".sana-tm-tile:focus-visible{outline:2px solid var(--focus-border);outline-offset:-3px;}" +
    ".sana-tm-bg{position:absolute;inset:1px;}" +
    ".sana-tm-label{position:relative;font-size:13px;font-weight:650;line-height:1.28;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:normal;word-break:normal;}" +
    ".sana-tm-tile--compact{padding:4px 8px;}" +
    ".sana-tm-tile--compact.sana-tm-tile--narrow{padding:4px;}" +
    ".sana-tm-rest{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 0;padding:0;list-style:none;font-size:13px;line-height:1.4;color:var(--text);}" +
    ".sana-tm-rest-item{display:inline-flex;align-items:center;gap:6px;}" +
    ".sana-sc-num{font-weight:700;min-width:1ch;}" +
    ".sana-tm-value{position:relative;margin-top:3px;font-size:13px;line-height:1.2;white-space:nowrap;}";
  var el = document.createElement("style");
  el.id = "sana-charts-style";
  el.textContent = css;
  document.head.appendChild(el);
}

// Over the accent drawn at this opacity or less, dark ink reads better than
// white on both themes (green #0d7d48, brown #874f2e over a white panel).
var OP_TEXTO_BLANCO = 0.8;
var COL_ACCENT = "var(--accent)", COL_WARN = "var(--warn)", COL_TEXT2 = "var(--text-2)",
  COL_TEXT3 = "var(--text-3)", COL_BORDER = "var(--border)";

// Categorical palette: the charts' own six hues (--cat-1..--cat-6,
// tokens.css), validated against the report background. The avatar slots it
// used to borrow are one grey in the humanrounds theme.
var CAT_PALETTE = ["var(--cat-1,#2a78d6)", "var(--cat-2,#d95926)", "var(--cat-3,#12855e)", "var(--cat-4,#6250d6)", "var(--cat-5,#a87000)", "var(--cat-6,#c94a7a)"];

function legendHtml(items) {
  // items: [{nombre, color}]
  if (!items || !items.length) return "";
  injectStyle();
  return '<div class="sana-legend">' + items.map(function (it) {
    return '<span class="sana-legend-item"><span class="sana-legend-dot" style="background:' + it.color + '"></span>' + esc(it.nombre) + "</span>";
  }).join("") + "</div>";
}

// =====================================================================
// 1. scatter — el caballo de batalla: dos variables + línea de referencia
// =====================================================================

function scatter(rows, opts) {
  opts = opts || {};
  rows = (rows || []).filter(function (r) { return r && isFinite(r.x) && isFinite(r.y); });
  var mini = !!opts.mini;
  if (!rows.length) {
    if (mini) return svgWrap(120, opts.height || 64, "");
    return chartWrap(svgWrap(680, 200, textEl(340, 100, T("admin.reports.charts.sin_datos", null, "Sin datos todavía."), { anchor: "middle", fill: COL_INK })), opts.read);
  }

  // modo "mini" (sparkline dentro de un tile): sin ejes/ticks/etiquetas —
  // a ese tamaño el texto no entra, así que se dibuja sólo la forma.
  var vbW = mini ? (opts.width || 160) : anchoDe(opts, 680);
  var vbH = mini ? (opts.height || 64) : 360;
  // El título del eje Y va HORIZONTAL arriba del plot (no rotado 90°:
  // nadie lee de costado — mejora adoptada del concept-pass 2026-07-22),
  // así que con ejeY el margen superior reserva esa banda.
  var m = mini ? { left: 6, right: 6, top: 6, bottom: 6 }
    : { left: 58, right: 24, top: opts.ejeY ? 34 : 18, bottom: 54 };
  var plotW = vbW - m.left - m.right, plotH = vbH - m.top - m.bottom;

  var xs = rows.map(function (r) { return r.x; });
  var ys = rows.map(function (r) { return r.y; });
  var linea2 = opts.linea;
  if (linea2 && linea2.tipo === "h") ys = ys.concat([linea2.valor]);
  if (linea2 && linea2.tipo === "v") xs = xs.concat([linea2.valor]);

  var xMinD = Math.min.apply(null, xs), xMaxD = Math.max.apply(null, xs);
  var yMinD = Math.min.apply(null, ys), yMaxD = Math.max.apply(null, ys);
  if (opts.baseCero !== false) { xMinD = Math.min(0, xMinD); yMinD = Math.min(0, yMinD); }
  if (linea2 && linea2.tipo === "diagonal") {
    var pend = linea2.pendiente || 1;
    yMaxD = Math.max(yMaxD, pend * xMaxD);
    if (pend < 0) yMinD = Math.min(yMinD, pend * xMaxD);
  }

  // Más marcas en X que en Y (6 vs 4): el eje horizontal tiene lugar de
  // sobra y con sólo 3 marcas (0/20/40) estimar un punto intermedio
  // obligaba a interpolar a ojo (concept-pass 2026-07-22).
  var tx = niceTicks(xMinD, xMaxD, mini ? 4 : 6);
  var ty = niceTicks(yMinD, yMaxD, 4);
  var xSpan = (tx.max - tx.min) || 1, ySpan = (ty.max - ty.min) || 1;

  function sx(x) { return m.left + (x - tx.min) / xSpan * plotW; }
  function sy(y) { return m.top + plotH - (y - ty.min) / ySpan * plotH; }

  var svg = "";

  // grilla + ejes (el modo mini no dibuja ninguno: a ese tamaño el texto
  // no entra, la forma sola alcanza)
  if (!mini) {
    tx.ticks.forEach(function (v) {
      var x = sx(v);
      svg += '<line x1="' + x + '" y1="' + m.top + '" x2="' + x + '" y2="' + (m.top + plotH)
        + '" style="stroke:' + COL_BORDER + ';stroke-width:1;opacity:.5"/>';
      svg += textEl(x, m.top + plotH + 18, fmtNum(v), { anchor: "middle", fontSize: FS, fill: COL_INK });
    });
    ty.ticks.forEach(function (v) {
      var y = sy(v);
      svg += '<line x1="' + m.left + '" y1="' + y + '" x2="' + (m.left + plotW) + '" y2="' + y
        + '" style="stroke:' + COL_BORDER + ';stroke-width:1;opacity:.5"/>';
      svg += textEl(m.left - 8, y + 4, fmtNum(v), { anchor: "end", fontSize: FS, fill: COL_INK });
    });
    svg += '<line x1="' + m.left + '" y1="' + (m.top + plotH) + '" x2="' + (m.left + plotW) + '" y2="' + (m.top + plotH)
      + '" style="stroke:' + COL_BORDER + ';stroke-width:1.3"/>';
    svg += '<line x1="' + m.left + '" y1="' + m.top + '" x2="' + m.left + '" y2="' + (m.top + plotH)
      + '" style="stroke:' + COL_BORDER + ';stroke-width:1.3"/>';
  }

  // línea de referencia
  if (linea2) {
    var lx1, ly1, lx2, ly2;
    if (linea2.tipo === "diagonal") {
      var p = linea2.pendiente || 1;
      lx1 = tx.min; ly1 = p * lx1; lx2 = tx.max; ly2 = p * lx2;
      // recorta al dominio Y visible
      if (ly2 > ty.max && p !== 0) { lx2 = ty.max / p; ly2 = ty.max; }
      if (ly2 < ty.min && p !== 0) { lx2 = ty.min / p; ly2 = ty.min; }
    } else if (linea2.tipo === "v") {
      lx1 = lx2 = linea2.valor; ly1 = ty.min; ly2 = ty.max;
    } else { // "h"
      ly1 = ly2 = linea2.valor; lx1 = tx.min; lx2 = tx.max;
    }
    svg += '<line x1="' + sx(lx1) + '" y1="' + sy(ly1) + '" x2="' + sx(lx2) + '" y2="' + sy(ly2)
      + '" style="stroke:' + COL_TEXT3 + ';stroke-width:1.5;stroke-dasharray:5,4"/>';
  }

  // color por grupo (opts.agrupar): cada r.grupo distinto de la paleta
  // categórica, en orden de primera aparición — reemplaza el color
  // acento/resaltar por chart cuando el llamador quiere clusters (ej.
  // "Médicos": un color por centro). Sin agrupar, sigue el criterio viejo
  // (resaltar en var(--warn), el resto en var(--accent)).
  var resaltar = {};
  (opts.resaltar || []).forEach(function (e) { resaltar[e] = true; });
  var grupoColor = {}, gruposOrden = [];
  if (opts.agrupar) {
    rows.forEach(function (r) {
      var g = r.grupo || "";
      if (grupoColor[g] == null) { grupoColor[g] = CAT_PALETTE[gruposOrden.length % CAT_PALETTE.length]; gruposOrden.push(g); }
    });
  }

  // puntos + etiquetas: ancla start/end según de qué lado del plot cae el
  // punto — NUNCA "middle": un texto centrado sobre un punto pegado al eje
  // Y (izquierda) se desborda hacia afuera del área de trazado, el bug
  // real que mostraba una etiqueta arrancando a la izquierda del eje. Con
  // el punto en el ~40% derecho del plot, la etiqueta ancla "end" (crece
  // hacia la izquierda) para no desbordar el borde derecho; si no, ancla
  // "start" (crece hacia la derecha). Colisión: la posición por-defecto
  // (arriba del punto) se compara por CAJA completa (no sólo el punto de
  // anclaje) contra las etiquetas ya puestas — dos nombres largos pueden
  // tener anclas lejos entre sí y su TEXTO igual superponerse (bug real:
  // "Laura Diana Mallo" tapando a "Alejo Varela"); si la caja choca,
  // la más tardía baja del otro lado del punto en vez de apilarse arriba.
  // A label is the whole name, in one or two lines, right next to its point:
  // above it, one or two steps higher, or under it inside the plot. A name
  // that has no room there (a crowded cluster on a phone) never ends in «…»
  // nor floats away from its point: the point takes a number and the name
  // is listed under the chart with that number (review E4, 08.11). Below the
  // point only inside the plot: under the X axis it ran into the tick numbers.
  var placedBoxes = [];
  var restoPuntos = [];
  var lhPt = Math.round(FS * 1.2);
  function seSuperponen(a, b) { return a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0; }
  function choca(c) { return placedBoxes.some(function (b) { return seSuperponen(c, b); }); }
  var marcas = [];
  var puntoCajas = [];
  rows.forEach(function (r) {
    var px = sx(r.x), py = sy(r.y);
    var rad = r.tam != null ? r.tam : (mini ? 3 : 5);
    var color = opts.agrupar ? (grupoColor[r.grupo || ""] || COL_ACCENT) : (resaltar[r.etiqueta] ? COL_WARN : COL_ACCENT);
    svg += '<circle cx="' + px + '" cy="' + py + '" r="' + rad + '" style="fill:' + color + '"><title>'
      + esc(r.etiqueta || r.grupo || "") + ": " + fmtNum(r.x) + " / " + fmtNum(r.y) + "</title></circle>";
    if (!mini) puntoCajas.push({ x0: px - rad - 1, x1: px + rad + 1, y0: py - rad - 1, y1: py + rad + 1 });
    if (r.etiqueta && !mini) marcas.push({ r: r, px: px, py: py, rad: rad, color: color });
  });
  // The reference line's name sits next to the line, never on a point
  // (review 15.11: «mediana turnos/hora» ran through Laura Katz's point on a
  // phone): above the line's end, under it, then further back along the
  // line; the first place clear of every point wins. Placed before the
  // point names, so they make room for it.
  // The dashed reference line is an obstacle (review 15.11: «Gustavo
  // Ibarra» and «1,02» were struck through by the median; at 390 the
  // median's own caption touched it): a label crosses it only when no other
  // place is free.
  var refSeg = (linea2 && !mini) ? { x1: sx(lx1), y1: sy(ly1), x2: sx(lx2), y2: sy(ly2) } : null;
  function cruzaRef(c) {
    if (!refSeg) return false;
    for (var k = 0; k <= 80; k++) {
      var t = k / 80, x = refSeg.x1 + (refSeg.x2 - refSeg.x1) * t, y = refSeg.y1 + (refSeg.y2 - refSeg.y1) * t;
      if (x > c.x0 - 1 && x < c.x1 + 1 && y > c.y0 - 1 && y < c.y1 + 1) return true;
    }
    return false;
  }
  if (linea2 && linea2.etiqueta && !mini) {
    var lw = medir(linea2.etiqueta, FS, 600);
    var libreDePuntos = function (c) { return !puntoCajas.some(function (b) { return seSuperponen(c, b); }); };
    var enLinea = function (t) {
      var xv = refSeg.x1 + (refSeg.x2 - refSeg.x1) * t, yv = refSeg.y1 + (refSeg.y2 - refSeg.y1) * t;
      return [xv, yv];
    };
    var lugaresRef = [];
    [1, 0.85, 0.7, 0.55, 0.4].forEach(function (t) {
      var pv = enLinea(t);
      lugaresRef.push([pv[0] - 6, pv[1] - 6]);
      lugaresRef.push([pv[0] - 6, pv[1] + FS + 6]);
    });
    var cajaRef = null, lugarRef = lugaresRef[0];
    [true, false].forEach(function (evitaLinea) {
      for (var q = 0; q < lugaresRef.length && !cajaRef; q++) {
        var lxq = lugaresRef[q][0], lyq = lugaresRef[q][1];
        var cq = { x0: lxq - lw, x1: lxq, y0: lyq - FS, y1: lyq + 3 };
        if (cq.x0 < m.left || cq.y0 < m.top - 2 || cq.y1 > m.top + plotH) continue;
        if (!libreDePuntos(cq)) continue;
        if (evitaLinea && cruzaRef(cq)) continue;
        cajaRef = cq; lugarRef = lugaresRef[q];
      }
    });
    if (!cajaRef) cajaRef = { x0: lugarRef[0] - lw, x1: lugarRef[0], y0: lugarRef[1] - FS, y1: lugarRef[1] + 3 };
    placedBoxes.push(cajaRef);
    svg += textEl(lugarRef[0], lugarRef[1], linea2.etiqueta, { anchor: "end", fontSize: FS, fill: COL_INK, weight: 600, halo: true });
  }
  puntoCajas.forEach(function (b) { placedBoxes.push(b); });
  // A label belongs to the point it is nearest to: a box closer to another
  // point than to its own reads as that neighbour's name (review 15.11:
  // «Camila Levin» two steps up sat on Emiliano Molina's point). Such a
  // candidate is skipped; a raised label that passes gets a thin leader.
  var todosPuntos = mini ? [] : rows.map(function (r) { return { px: sx(r.x), py: sy(r.y) }; });
  function distCaja(c, x, y) {
    var dx = Math.max(c.x0 - x, 0, x - c.x1), dy = Math.max(c.y0 - y, 0, y - c.y1);
    return Math.sqrt(dx * dx + dy * dy);
  }
  // Nearer is not enough: a name almost as close to a neighbour reads as
  // either point's (review 15.11 at 390: «Camila Levin» 18 px from its point
  // and 25 px from Emiliano Molina's). The own point must win by a clear
  // margin, or the label tries another place, or the point takes a number.
  // Two points closer than twice the margin cannot be told apart by any place
  // (the gap can never exceed their distance): there the margin is half of it.
  var MARGEN_PROPIO = 8;
  // And by height: the eye reads a name as the point at its own height
  // (review 15.11 at 390: «Camila Levin» at the height of Emiliano Molina's
  // point, hers lower). A point beside the text, no nearer to the text's
  // middle line than its own, takes the name away from it. A label raised
  // with a leader line is tied to its point by the line, not by height.
  var VECINO_X = 48;
  function esDeOtro(c, px, py, conGuia) {
    var propia = distCaja(c, px, py);
    var cy = (c.y0 + c.y1) / 2, propiaY = Math.abs(cy - py);
    return todosPuntos.some(function (q) {
      if (Math.abs(q.px - px) < 0.01 && Math.abs(q.py - py) < 0.01) return false;
      var margen = Math.min(MARGEN_PROPIO, Math.hypot(q.px - px, q.py - py) / 2);
      if (distCaja(c, q.px, q.py) < propia + margen) return true;
      if (conGuia) return false;
      var dx = Math.max(c.x0 - q.px, 0, q.px - c.x1);
      return dx <= VECINO_X && Math.abs(cy - q.py) < propiaY;
    });
  }
  marcas.forEach(function (mk) {
    var r = mk.r, px = mk.px, py = mk.py, rad = mk.rad;
    // The side away from the nearer edge first, then the other side; on each,
    // above the point, beside it, one or two steps higher, under it.
    var rightZone = px > m.left + plotW * 0.6;
    var lados = rightZone ? ["end", "start"] : ["start", "end"];
    var puesto = false;
    [true, false].forEach(function (evitaRef) {
    lados.forEach(function (anchor) {
      if (puesto) return;
      var x0 = Math.max(m.left + 2, Math.min(m.left + plotW - 2, anchor === "end" ? px - rad - 7 : px + rad + 7));
      var maxW = Math.min(240, anchor === "end" ? (x0 - m.left - 4) : (m.left + plotW - x0 - 4));
      if (maxW < 24) return;
      var partes = partirEnLineas(r.etiqueta, maxW, FS, 600, 2);
      if (!partes.fits) return;
      var lines = partes.lines;
      var w = Math.max.apply(null, lines.map(function (l) { return medir(l, FS, 600); }));
      var alto = (lines.length - 1) * lhPt;
      // caja de un texto cuya ÚLTIMA línea tiene la base en yb
      var cajaEn = function (yb) {
        var bx0 = anchor === "start" ? x0 : x0 - w, bx1 = anchor === "start" ? x0 + w : x0;
        return { x0: bx0, x1: bx1, y0: yb - alto - FS, y1: yb + 3 };
      };
      var arriba = py - rad - 6;
      var candidatos = [arriba, py + 4 + alto / 2, arriba - (alto + 16), arriba - 2 * (alto + 16), py + rad + 6 + alto + FS];
      for (var i = 0; i < candidatos.length && !puesto; i++) {
        var yb = candidatos[i], caja = cajaEn(yb);
        if (caja.y0 < 1 || caja.y1 > m.top + plotH - 1) continue;
        if (choca(caja)) continue;
        var conGuia = i === 2 || i === 3;
        if (esDeOtro(caja, px, py, conGuia)) continue;
        if (evitaRef && cruzaRef(caja)) continue;
        var guia = null;
        if (conGuia) {
          // raised one or two steps: a thin line from the text to its point,
          // which must not strike through what blocked the lower slot
          var lxl = anchor === "start" ? caja.x0 + 2 : caja.x1 - 2;
          guia = { x0: Math.min(lxl, px) - 1, x1: Math.max(lxl, px) + 1, y0: caja.y1 + 1, y1: py - rad - 2 };
          if (guia.y1 > guia.y0 && choca(guia)) continue;
        }
        placedBoxes.push(caja);
        if (guia) {
          if (guia.y1 > guia.y0) placedBoxes.push(guia);
          svg += '<line class="sana-sc-leader" x1="' + lxl + '" y1="' + (caja.y1 - 1) + '" x2="' + px + '" y2="' + (py - rad - 1)
            + '" style="stroke:' + COL_TEXT3 + ';stroke-width:1"/>';
        }
        var halo = !evitaRef && cruzaRef(caja);
        if (lines.length === 1) {
          svg += textEl(x0, yb, lines[0], { anchor: anchor, fontSize: FS, fill: "var(--text)", weight: 600, halo: halo, punto: guia ? null : [px, py] });
        } else {
          svg += textLines(x0, yb - alto / 2, r.etiqueta, { anchor: anchor, fontSize: FS, fill: "var(--text)", weight: 600, maxWidth: maxW, maxLines: 2, halo: halo, punto: guia ? null : [px, py] });
        }
        puesto = true;
      }
    });
    });
    if (puesto) return;
    var num = String(restoPuntos.length + 1);
    var nw = medir(num, FS, 700);
    // A number too must read as its own point's (review 15.11 at 390: a «1»
    // between two points): first only places nearer its point by the clear
    // margin, then any free place.
    var lugares = [
      [px + rad + 3, py + 4, "start"], [px - rad - 3, py + 4, "end"],
      [px, py - rad - 4, "middle"], [px, py + rad + FS + 1, "middle"],
      [px + rad + 2, py - rad - 2, "start"], [px - rad - 2, py - rad - 2, "end"],
      [px + rad + 2, py + rad + FS, "start"], [px - rad - 2, py + rad + FS, "end"],
    ];
    lugares = lugares.map(function (l) { return l.concat([true]); })
      .concat(lugares.map(function (l) { return l.concat([false]); }));
    var marcado = false;
    for (var j = 0; j < lugares.length && !marcado; j++) {
      var lx = lugares[j][0], ly = lugares[j][1], la = lugares[j][2];
      var bx0 = la === "start" ? lx : la === "end" ? lx - nw : lx - nw / 2;
      var cj = { x0: bx0, x1: bx0 + nw, y0: ly - FS, y1: ly + 3 };
      if (cj.x0 < m.left || cj.x1 > m.left + plotW || cj.y0 < m.top - 2 || cj.y1 > m.top + plotH + 1) continue;
      if (choca(cj)) continue;
      if (lugares[j][3] && esDeOtro(cj, px, py)) continue;
      placedBoxes.push(cj);
      svg += textEl(lx, ly, num, { anchor: la, fontSize: FS, fill: "var(--text)", weight: 700, punto: [px, py] });
      marcado = true;
    }
    restoPuntos.push({ num: marcado ? num : "", nombre: r.etiqueta, color: mk.color, x: r.x, y: r.y });
  });

  if (!mini) {
    // X-axis title at the end of the axis, under the tick numbers, in the ink
    // of the tick labels (review 15.11: the centred title at the very bottom
    // read as a caption of nothing).
    if (opts.ejeX) svg += textEl(m.left + plotW, m.top + plotH + 40, opts.ejeX, { anchor: "end", fontSize: FS, fill: COL_INK, weight: 600 });
    if (opts.ejeY) {
      svg += textEl(6, 14, opts.ejeY, { anchor: "start", fontSize: FS, fill: COL_INK, weight: 600 });
    }
  }

  // en modo mini el llamador ya está dentro de un .sana-tile-mini — no hay
  // wrapper ni línea "Cómo leerlo" (no entraría, y el tile no es un gráfico
  // con lectura propia).
  if (mini) return svgWrap(vbW, vbH, svg);
  var legend = opts.agrupar ? legendHtml(gruposOrden.map(function (g, i) {
    return { nombre: g || T("admin.reports.charts.sin_grupo", null, "Sin grupo"), color: CAT_PALETTE[i % CAT_PALETTE.length] };
  })) : "";
  var lista = "";
  if (restoPuntos.length) {
    injectStyle();
    lista = '<ul class="sana-tm-rest sana-sc-rest">' + restoPuntos.map(function (p) {
      return '<li class="sana-tm-rest-item">' + (p.num ? '<b class="sana-sc-num">' + p.num + "</b>" : '<span class="sana-legend-dot" style="background:' + p.color + '"></span>')
        + esc(p.nombre) + "</li>";
    }).join("") + "</ul>";
  }
  return chartWrap(svgWrap(vbW, vbH, svg) + legend + lista, opts.read);
}

// =====================================================================
// 2. heatmap — grilla día × hora
// =====================================================================

function heatmap(grid, opts) {
  opts = opts || {};
  grid = grid || {};
  var dias = grid.dias || [], horas = grid.horas || [], celdas = grid.celdas || [];
  if (!dias.length || !horas.length) return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);

  // Alto de celda FIJO (cuadrada de diseño); el ancho de celda se estira
  // para llenar la columna, pero el SVG va 1:1 (vbW == ancho renderizado),
  // así el número que se imprime en la celda NO se deforma — antes, con
  // preserveAspectRatio="none" y vbW ~480 en una columna de ~976px, el "4,2"
  // blanco de la celda más cargada salía estirado al doble de ancho.
  var CELL_H = 26, leftGutter = 84, topGutter = 22, rightPad = 6, bottomPad = 4;
  var natW = leftGutter + horas.length * CELL_H + rightPad;
  // On a phone the column is narrower than square cells need: the cells get
  // narrower instead of the whole SVG shrinking, so the hour and day labels
  // keep their 13px (review, 08.11: 9.7px on a 390 screen). Below 14px a
  // cell is too thin to read; only then does the drawing scale down.
  var minW = leftGutter + horas.length * 14 + rightPad;
  var vbW = Math.max(minW, anchoDe(opts, natW));
  var CELL_W = (vbW - leftGutter - rightPad) / horas.length;
  var vbH = topGutter + dias.length * CELL_H + bottomPad;

  var max = 0;
  celdas.forEach(function (row) { (row || []).forEach(function (v) { if (v > max) max = v; }); });
  if (!max) max = 1;
  // Umbral por defecto = 1: una celda con valor fraccionario (<1, típico
  // de repartir preferencias de franja en partes iguales entre servicios)
  // sigue mostrando su tinte pero no imprime el número — una grilla
  // alfombrada de "0,2" es ruido, no señal (round 2026-07-22, "carpeted
  // heatmap").
  var umbral = opts.umbral != null ? opts.umbral : 1;

  var marcar = {};
  (opts.marcar || []).forEach(function (m) { marcar[m.d + "|" + m.h] = true; });

  var svg = "";
  // Hour labels whole, never cut: on a narrow screen every other one (or
  // every third) is printed, the cell's tooltip still names its hour.
  var horaW = Math.max.apply(null, horas.map(function (h) { return medir(h, FS); })) + 4;
  var cada = Math.max(1, Math.ceil(horaW / CELL_W));
  horas.forEach(function (h, hi) {
    if (hi % cada) return;
    var hx = Math.max(horaW / 2, Math.min(vbW - horaW / 2, leftGutter + hi * CELL_W + CELL_W / 2));
    svg += textEl(hx, topGutter - 8, h, { anchor: "middle", fontSize: FS, fill: COL_INK });
  });
  dias.forEach(function (d, di) {
    var y = topGutter + di * CELL_H;
    svg += textEl(leftGutter - 8, y + CELL_H / 2 + 4, d, { anchor: "end", fontSize: FS, fill: COL_INK, maxWidth: leftGutter - 14 });
    (celdas[di] || []).forEach(function (v, hi) {
      v = v || 0;
      var op = max ? v / max : 0;
      var x = leftGutter + hi * CELL_W;
      var outlined = marcar[d + "|" + horas[hi]];
      svg += '<rect x="' + (x + 0.75) + '" y="' + (y + 0.75) + '" width="' + (CELL_W - 1.5) + '" height="' + (CELL_H - 1.5)
        + '" style="fill:' + COL_ACCENT + ';fill-opacity:' + (op > 0 ? Math.max(op, 0.06).toFixed(3) : 0)
        + (outlined ? ';stroke:' + COL_WARN + ';stroke-width:2' : '') + '"><title>' + esc(d) + " " + esc(horas[hi]) + ": " + fmtNum(v) + "</title></rect>";
      // A number wider than its cell would spill onto the neighbour's: it
      // stays in the cell's tooltip instead.
      if (v >= umbral && medir(fmtNum(v), FS, 650) <= CELL_W - 3) {
        svg += textEl(x + CELL_W / 2, y + CELL_H / 2 + 4, fmtNum(v), {
          anchor: "middle", fontSize: FS, weight: 650, fill: op > OP_TEXTO_BLANCO ? "#fff" : "var(--text)",
        });
      }
    });
  });

  return chartWrap(svgWrap(vbW, vbH, svg), opts.read);
}

// =====================================================================
// 3. treemap — composición, slice-and-dice binario (áreas exactamente
//    proporcionales al valor, sin bordes/redondeo)
// =====================================================================

function treemapLayout(items, x, y, w, h, horizontal) {
  items = (items || []).filter(function (i) { return i && i.valor > 0; });
  if (!items.length || w <= 0 || h <= 0) return [];
  if (items.length === 1) return [{ item: items[0], x: x, y: y, w: w, h: h }];
  horizontal = horizontal !== false;
  var total = items.reduce(function (a, i) { return a + i.valor; }, 0);
  var half = total / 2, acc = 0, splitIdx = 1;
  for (var i = 0; i < items.length; i++) {
    acc += items[i].valor;
    if (acc >= half) { splitIdx = i + 1; break; }
  }
  splitIdx = Math.max(1, Math.min(items.length - 1, splitIdx));
  var groupA = items.slice(0, splitIdx), groupB = items.slice(splitIdx);
  var totalA = groupA.reduce(function (a, i) { return a + i.valor; }, 0);
  var fracA = totalA / total;
  if (horizontal) {
    var wA = w * fracA;
    return treemapLayout(groupA, x, y, wA, h, false).concat(treemapLayout(groupB, x + wA, y, w - wA, h, false));
  }
  var hA = h * fracA;
  return treemapLayout(groupA, x, y, w, hA, true).concat(treemapLayout(groupB, x, y + hA, w, h - hA, true));
}

// Luminancia aproximada (YIQ) de un color literal "rgb(r,g,b)" o "#rrggbb"
// — sólo se usa cuando el llamador pasa item.color (un tono continuo, no
// uno de los dos tokens fijos COL_ACCENT/COL_WARN): con un color arbitrario
// el contraste de texto blanco-vs-oscuro ya no puede decidirse por la
// opacidad de la tier (esa lógica sigue viva para los llamadores viejos).
// Devuelve null si el string no matchea (texto cae al criterio de opacidad).
function relLuma(colorStr) {
  var r, g, b;
  var m = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(String(colorStr));
  if (m) { r = +m[1]; g = +m[2]; b = +m[3]; }
  else {
    var hex = String(colorStr).replace("#", "");
    if (hex.length === 3) hex = hex.split("").map(function (c) { return c + c; }).join("");
    if (!/^[0-9a-fA-F]{6}$/.test(hex)) return null;
    r = parseInt(hex.substr(0, 2), 16); g = parseInt(hex.substr(2, 2), 16); b = parseInt(hex.substr(4, 2), 16);
  }
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

// HTML (no SVG) a propósito: un nombre largo ("Evitable antes de enviar")
// parte en varias líneas sin envolver a mano; el tile sigue siendo "flat,
// sin bordes/redondeo" (doctrina) — el hueco de 1px entre tiles es el fondo
// del panel asomando, no un borde dibujado.
// A tile shows its name only when the whole name fits its inner box (words
// never cut, at most 3 lines), and its value only when the value fits whole
// under it. Tiles too small for their name are listed under the treemap with
// their colour, name and value, so no category is ever shown as «Trat…» or
// only on hover (review E4). The layout is computed in the chart's real
// pixels (anchoDe), so the fit decision holds on a phone as on a desk.
function treemap(items, opts) {
  opts = opts || {};
  // El tile YA imprime el valor en su segunda línea: el nombre que llega no
  // debe traer el número embebido ("Trato del personal · 129" + "129" era
  // el mismo dato dos veces, round 2026-07-22). opts.fmtValor permite
  // unidades ("96,5 h") sin tocar el nombre.
  var fmtV = opts.fmtValor || fmtNum;
  var sorted = (items || []).filter(function (i) { return i && i.valor > 0; }).slice().sort(function (a, b) { return b.valor - a.valor; });
  if (!sorted.length) return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);

  // The height grows with the number of tiles: one category alone is the
  // whole composition and says nothing a tile 260 px tall would add — on a
  // phone it was a solid brown square across the screen (ui-expert, 08.11).
  // One tile 80 px, two 128, three 192, four or more 256-260.
  var n = sorted.length;
  var vbW = anchoDe(opts, 680), vbH = Math.min(260, Math.max(80, n * 64));
  var tiles = treemapLayout(sorted, 0, 0, vbW, vbH, true);
  var LH = Math.round(FS * 1.28 * 10) / 10;

  var html = "", resto = [];
  tiles.forEach(function (t) {
    var rank = sorted.indexOf(t.item);
    var tier = n <= 1 ? 0 : Math.floor((rank / n) * 3); // 0,1,2
    // item.color: tono continuo pasado por el llamador (p.ej. la escala de
    // sentimiento de Centros) — pisa el par fijo tono="mal"/COL_ACCENT y
    // corre a opacidad plena. El contraste del texto se decide por luminancia
    // real del color, no por la opacidad de la tier.
    var customColor = t.item.color;
    var op = customColor ? 1 : [0.88, 0.62, 0.4][Math.min(tier, 2)];
    var color = customColor || (t.item.tono === "mal" ? COL_WARN : COL_ACCENT);
    var luma = customColor ? relLuma(customColor) : null;
    // White only on a near-solid tile: at the middle tier (0.62) white on the
    // accent mixed with the panel was 2.6-2.8:1 on both themes, dark ink is
    // about 6:1 there (review E14, 08.11).
    var textColor = luma != null ? (luma > 0.55 ? "var(--text)" : "#fff") : (op > OP_TEXTO_BLANCO ? "#fff" : "var(--text)");
    // A short tile trims only its top and bottom, so its name starts at the
    // same left edge as its neighbours'; only a narrow one trims the sides.
    var compact = t.w < 80 || t.h < 44, narrow = t.w < 80;
    var padX = narrow ? 4 : 8, padY = compact ? 4 : 8;
    var innerW = t.w - 2 * padX - 2, innerH = t.h - 2 * padY;
    var maxLines = Math.min(3, Math.floor(innerH / LH));
    var nombre = String(t.item.nombre);
    var fit = maxLines >= 1 && innerW > 0 ? partirEnLineas(nombre, innerW, FS, 650, maxLines) : { fits: false, lines: [] };
    var valTxt = fmtV(t.item.valor);
    var pos = "left:" + (100 * t.x / vbW) + "%;top:" + (100 * t.y / vbH) + "%;width:" + (100 * t.w / vbW) + "%;height:" + (100 * t.h / vbH) + "%";
    var inner = "";
    // Every tile keeps one layout, name over value. No room for the value: the
    // tile shows the name alone and the value lives in a list under the chart —
    // the chart's own (sana-tm-rest) or, with opts.valoresEnLista, the list the
    // report already prints. Never name and value run together on one line in
    // some tiles and stacked in the others (ui-expert 15.11: «PAMI 5»).
    var valorDebajo = fit.fits && innerH >= fit.lines.length * LH + 3 + LH && medir(valTxt, FS) <= innerW;
    if (fit.fits) inner += '<div class="sana-tm-label" style="color:' + textColor + '">' + esc(nombre) + "</div>";
    if (valorDebajo) {
      inner += '<div class="sana-tm-value" style="color:' + textColor + '">' + valTxt + "</div>";
    } else if (!fit.fits || !opts.valoresEnLista) {
      resto.push({ nombre: nombre, valor: valTxt, color: color, op: op });
    }
    var hint = esc(nombre) + ": " + valTxt;
    html += '<div class="sana-tm-tile' + (compact ? " sana-tm-tile--compact" : "") + (narrow ? " sana-tm-tile--narrow" : "") + '" style="' + pos
      + '" title="' + hint + '" aria-label="' + hint + '" tabindex="0">'
      + '<div class="sana-tm-bg" style="background:' + color + ';opacity:' + op + '"></div>' + inner + "</div>";
  });

  var lista = "";
  if (resto.length) {
    lista = '<ul class="sana-tm-rest">' + resto.map(function (r) {
      return '<li class="sana-tm-rest-item"><span class="sana-legend-dot" style="background:' + r.color + ';opacity:' + r.op + '"></span>'
        + esc(r.nombre) + " " + r.valor + "</li>";
    }).join("") + "</ul>";
  }

  injectStyle();
  return chartWrap('<div class="sana-tm" style="height:' + vbH + 'px">' + html + "</div>" + lista, opts.read);
}

// =====================================================================
// 4. slope — antes/después por ítem
// =====================================================================

function slope(pares, opts) {
  opts = opts || {};
  pares = (pares || []).filter(function (p) { return p && isFinite(p.antes) && isFinite(p.despues); });
  if (!pares.length) return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);

  var vbW = anchoDe(opts, 560), rowH = 34;
  var vbH = Math.max(200, pares.length * rowH + 70);
  var m = { top: 40, bottom: 24 };
  var xLeft = 116, xRight = vbW - 150;
  var plotTop = m.top, plotBottom = vbH - m.bottom;

  var vals = [];
  pares.forEach(function (p) { vals.push(p.antes, p.despues); });
  var vMin = Math.min(0, Math.min.apply(null, vals)), vMax = Math.max.apply(null, vals);
  if (vMax === vMin) vMax = vMin + 1;

  function sy(v) { return plotBottom - (v - vMin) / (vMax - vMin) * (plotBottom - plotTop); }

  var mejorSube = !!opts.mejorSube;
  var svg = "";
  svg += textEl(xLeft, m.top - 18, opts.antes || T("admin.reports.charts.antes", null, "Antes"), { anchor: "middle", fontSize: FS, weight: 650, fill: COL_INK });
  svg += textEl(xRight, m.top - 18, opts.despues || T("admin.reports.charts.despues", null, "Después"), { anchor: "middle", fontSize: FS, weight: 650, fill: COL_INK });

  var leftLabels = pares.map(function (p) { return { p: p, y: sy(p.antes) }; }).sort(function (a, b) { return a.y - b.y; });
  var rightLabels = pares.map(function (p) { return { p: p, y: sy(p.despues) }; }).sort(function (a, b) { return a.y - b.y; });
  [leftLabels, rightLabels].forEach(function (arr) {
    for (var i = 1; i < arr.length; i++) { if (arr[i].y - arr[i - 1].y < 16) arr[i].y = arr[i - 1].y + 16; }
  });
  var leftY = {}, rightY = {};
  leftLabels.forEach(function (e) { leftY[e.p.nombre] = e.y; });
  rightLabels.forEach(function (e) { rightY[e.p.nombre] = e.y; });

  pares.forEach(function (p) {
    var y1 = sy(p.antes), y2 = sy(p.despues);
    var sube = p.despues > p.antes;
    var mejora = mejorSube ? sube : !sube;
    var color = mejora ? COL_ACCENT : COL_WARN;
    svg += '<line x1="' + xLeft + '" y1="' + y1 + '" x2="' + xRight + '" y2="' + y2 + '" style="stroke:' + color + ';stroke-width:2"/>';
    svg += '<circle cx="' + xLeft + '" cy="' + y1 + '" r="3.5" style="fill:' + color + '"/>';
    svg += '<circle cx="' + xRight + '" cy="' + y2 + '" r="3.5" style="fill:' + color + '"/>';
    svg += textEl(xLeft - 10, leftY[p.nombre] + 4, fmtNum(p.antes), { anchor: "end", fontSize: FS, fill: "var(--text)", weight: 600, punto: [xLeft, y1] });
    svg += textEl(xRight + 10, rightY[p.nombre] + 4, fmtNum(p.despues) + " · " + p.nombre,
      { anchor: "start", fontSize: FS, fill: "var(--text)", weight: 600, maxWidth: vbW - xRight - 18, punto: [xRight, y2] });
  });

  return chartWrap(svgWrap(vbW, vbH, svg), opts.read);
}

// =====================================================================
// 5. lollipop — ranking horizontal contra una referencia
// =====================================================================

function lollipop(rows, opts) {
  opts = opts || {};
  // opts.fmtValor prints the point's value the way the page's table does.
  var fmtV = opts.fmtValor || fmtNum;
  rows = (rows || []).filter(function (r) { return r && isFinite(r.valor); });
  if (!rows.length) return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);

  var vbW = anchoDe(opts, 680);
  // The label column grows to the longest label (up to 55% of the width on a
  // phone, 45% on a desk), never narrower than its longest single word: a
  // long name wraps to a second line instead of being cut (review A9/E4).
  var largo = Math.max.apply(null, rows.map(function (r) { return medir(r.nombre, FS); }));
  var palabra = Math.max.apply(null, rows.map(function (r) {
    return Math.max.apply(null, String(r.nombre).split(/\s+/).map(function (w) { return medir(w, FS); }));
  }));
  var tope = vbW * (vbW < 560 ? 0.55 : 0.45);
  var left = Math.round(Math.min(Math.max(vbW < 560 ? 96 : 168, largo + 20), Math.max(tope, palabra + 20)));
  var labelW = left - 18;
  var dosLineas = rows.some(function (r) { return lineasDe(r.nombre, labelW, FS, 400, 2) > 1; });
  var rowH = dosLineas ? 40 : 32;
  var m = { top: opts.ref && opts.ref.etiqueta ? 32 : 14, bottom: 14, left: left, right: 60 };
  var vbH = Math.max(120, rows.length * rowH + m.top + m.bottom);
  var plotW = vbW - m.left - m.right;

  var maxV = Math.max.apply(null, rows.map(function (r) { return r.valor; }).concat(opts.ref ? [opts.ref.valor] : [0]));
  if (!maxV) maxV = 1;
  var domainMax = maxV * 1.12;

  function sx(v) { return m.left + (v / domainMax) * plotW; }

  var resaltar = {};
  (opts.resaltar || []).forEach(function (e) { resaltar[e] = true; });

  var svg = "";
  if (opts.ref) {
    var rx = sx(opts.ref.valor);
    svg += '<line x1="' + rx + '" y1="' + (m.top - 6) + '" x2="' + rx + '" y2="' + (vbH - m.bottom + 4)
      + '" style="stroke:' + COL_TEXT3 + ';stroke-width:1.5;stroke-dasharray:4,4"/>';
    if (opts.ref.etiqueta) {
      // The reference's caption stays whole inside the frame: centred on the
      // line, pushed in from either edge when the line sits near one.
      var refW = medir(opts.ref.etiqueta, FS);
      var lx = Math.min(Math.max(rx, refW / 2 + 2), vbW - refW / 2 - 2);
      svg += textEl(lx, m.top - 12, opts.ref.etiqueta, { anchor: "middle", fontSize: FS, fill: COL_INK });
    }
  }

  // A value printed after its point must not run over the reference line.
  // One that would goes just before its own point, on a page-colour halo over
  // the stem (review 15.11: a shared column past the line left «1,02» or
  // «0,4» far from their points with nothing tying them).
  var refX = opts.ref ? sx(opts.ref.valor) : null;

  rows.forEach(function (r, i) {
    var y = m.top + i * rowH + rowH / 2;
    var x0 = m.left, x1 = sx(r.valor);
    var color = resaltar[r.nombre] ? COL_WARN : COL_ACCENT;
    var vw = medir(fmtV(r.valor), FS, 600);
    var cruza = refX != null && x1 < refX && x1 + 10 + vw + 4 > refX;
    var antes = cruza && x1 - 10 - vw > m.left + 4;
    // no room before it either (a very narrow plot): stays by its point,
    // a halo keeps it readable over the line
    var vx = antes ? x1 - 10 : x1 + 10;
    svg += textLines(m.left - 10, y + 4, r.nombre, { anchor: "end", fontSize: FS, fill: "var(--text)", maxWidth: labelW, maxLines: 2 });
    svg += '<line x1="' + x0 + '" y1="' + y + '" x2="' + x1 + '" y2="' + y + '" style="stroke:' + color + ';stroke-width:1.5;opacity:.55"/>';
    svg += '<circle cx="' + x1 + '" cy="' + y + '" r="5" style="fill:' + color + '"/>';
    svg += textEl(vx, y + 4, fmtV(r.valor), antes
      ? { anchor: "end", fontSize: FS, fill: "var(--text)", weight: 600, halo: true, punto: [x1, y] }
      : { anchor: "start", fontSize: FS, fill: "var(--text)", weight: 600, halo: cruza, maxWidth: Math.max(20, vbW - vx - 4), punto: [x1, y] });
  });

  return chartWrap(svgWrap(vbW, vbH, svg), opts.read);
}

// =====================================================================
// 6. linea — series de tiempo + banda típica
// =====================================================================

function linea(series, opts) {
  opts = opts || {};
  series = (series || []).filter(function (s) { return s && (s.puntos || []).length; });
  var mini = !!opts.mini;
  if (!series.length) {
    if (mini) return svgWrap(opts.width || 160, opts.height || 64, "");
    return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);
  }
  series = series.slice(0, 2);

  // modo "mini" (sparkline dentro de un tile): sin ejes/ticks/etiquetas,
  // sólo la banda y las líneas.
  var vbW = mini ? (opts.width || 160) : anchoDe(opts, 680);
  var vbH = mini ? (opts.height || 64) : 260;
  // right:44 (no 20) — deja lugar al valor impreso en el último punto, que
  // se ancla justo en el borde derecho del área de trazado.
  // The series names get their own row above the plot (review 15.11: drawn
  // at m.top they sat on the top gridline, read as that tick's caption).
  var conNombre = !mini && series.some(function (s) { return s.nombre; });
  var m = mini ? { left: 4, right: 4, top: 6, bottom: 6 } : { left: 54, right: 44, top: conNombre ? 30 : 16, bottom: 40 };
  if (!mini) {
    // The right margin holds the widest last value whole: a four-digit week
    // («3.214») was cut to «3.2…» in a fixed 44.
    series.forEach(function (s) {
      var ult = null;
      s.puntos.forEach(function (p) { if (typeof p.y === "number" && isFinite(p.y)) ult = p.y; });
      if (ult != null) m.right = Math.max(m.right, Math.ceil(medir(fmtNum(ult), FS, 650)) + 12);
    });
  }
  var plotW = vbW - m.left - m.right, plotH = vbH - m.top - m.bottom;

  var base = series[0].puntos;
  var n = base.length;
  var banda = opts.banda || [];
  var bandaByX = {};
  banda.forEach(function (b) { bandaByX[b.x] = b; });

  var yVals = [];
  series.forEach(function (s) { s.puntos.forEach(function (p) { if (isFinite(p.y)) yVals.push(p.y); }); });
  banda.forEach(function (b) { if (isFinite(b.y0)) yVals.push(b.y0); if (isFinite(b.y1)) yVals.push(b.y1); });
  if (!yVals.length) yVals = [0, 1];
  var yMinD = opts.baseCero !== false ? Math.min(0, Math.min.apply(null, yVals)) : Math.min.apply(null, yVals);
  var yMaxD = Math.max.apply(null, yVals);
  var todoEntero = yVals.every(function (v) { return Number.isInteger(v); });
  var ty = niceTicks(yMinD, yMaxD, 4, todoEntero);
  var ySpan = (ty.max - ty.min) || 1;

  function sxi(i) { return n <= 1 ? m.left + plotW / 2 : m.left + (i / (n - 1)) * plotW; }
  function sy(v) { return m.top + plotH - (v - ty.min) / ySpan * plotH; }

  var svg = "";
  if (!mini) {
    ty.ticks.forEach(function (v) {
      var y = sy(v);
      svg += '<line x1="' + m.left + '" y1="' + y + '" x2="' + (m.left + plotW) + '" y2="' + y
        + '" style="stroke:' + COL_BORDER + ';stroke-width:1;opacity:.5"/>';
      svg += textEl(m.left - 8, y + 4, fmtNum(v), { anchor: "end", fontSize: FS, fill: COL_INK });
    });
  }

  // banda típica (rango esperado)
  if (banda.length) {
    var top = [], bottom = [];
    base.forEach(function (p, i) {
      var b = bandaByX[p.x];
      if (b && isFinite(b.y0) && isFinite(b.y1)) { top.push(sxi(i) + "," + sy(b.y1)); bottom.push(sxi(i) + "," + sy(b.y0)); }
    });
    if (top.length) {
      var pts = top.concat(bottom.reverse()).join(" ");
      svg += '<polygon points="' + pts + '" style="fill:' + COL_ACCENT + ';fill-opacity:.12"/>';
    }
  }

  // eje X: etiquetas, salteando si hay demasiadas categorías
  if (!mini) {
    // Thinned by the labels' real width: every label shown is whole, never
    // cut to «Sem 1…» (review E4). The last point always keeps its label; the
    // one before it drops out when the two would touch.
    var etq = function (p) { return String(p.etiqueta != null ? p.etiqueta : p.x); };
    var lw = Math.max.apply(null, base.map(function (p) { return medir(etq(p), FS); })) + 12;
    var maxLabels = Math.max(2, Math.floor(plotW / lw));
    var step = Math.max(1, Math.ceil(n / maxLabels));
    var mostrar = [];
    base.forEach(function (p, i) { if (i % step === 0) mostrar.push(i); });
    if (mostrar[mostrar.length - 1] !== n - 1) {
      if (mostrar.length && sxi(n - 1) - sxi(mostrar[mostrar.length - 1]) < lw) mostrar.pop();
      mostrar.push(n - 1);
    }
    mostrar.forEach(function (i) {
      var t = etq(base[i]), w = medir(t, FS);
      var x = Math.min(Math.max(sxi(i), w / 2 + 2), vbW - w / 2 - 2);
      // 22 below the axis, not 18: a long first date clamped to the left
      // edge sat on the «0» of the Y axis (review E4).
      svg += textEl(x, vbH - m.bottom + 22, t, { anchor: "middle", fontSize: FS, fill: COL_INK });
    });
    svg += '<line x1="' + m.left + '" y1="' + (m.top + plotH) + '" x2="' + (m.left + plotW) + '" y2="' + (m.top + plotH) + '" style="stroke:' + COL_BORDER + ';stroke-width:1.3"/>';
  }

  var colors = [COL_ACCENT, COL_TEXT2];
  var dotR = mini ? 1.6 : 2.75;
  // The two series' last values end at the same X: when they are close the
  // second one goes under its point instead of on top of the first («33» over
  // «36», review E4).
  var finCajas = [];
  var nombreX = m.left;
  series.forEach(function (s, si) {
    var color = colors[si] || COL_ACCENT;
    var pathPts = [], lastValid = null;
    s.puntos.forEach(function (p, i) {
      if (!isFinite(p.y)) return;
      pathPts.push(sxi(i) + "," + sy(p.y));
      lastValid = { i: i, y: p.y };
    });
    if (pathPts.length > 1) svg += '<polyline points="' + pathPts.join(" ") + '" style="fill:none;stroke:' + color + ';stroke-width:' + (mini ? 1.75 : 2.25) + '"/>';
    if (!mini) s.puntos.forEach(function (p, i) { if (isFinite(p.y)) svg += '<circle cx="' + sxi(i) + '" cy="' + sy(p.y) + '" r="' + dotR + '" style="fill:' + color + '"/>'; });
    if (lastValid && !mini) {
      // igual que en scatter: el presupuesto de texto no puede superar lo
      // que queda hasta el borde del viewBox, o un valor largo se recorta
      // invisible en vez de con elipsis.
      var lastX = sxi(lastValid.i) + 6, lastY = sy(lastValid.y) - 8;
      var chocaFin = function (yb) { return finCajas.some(function (c) { return yb - FS < c.y1 && yb + 3 > c.y0; }); };
      if (chocaFin(lastY)) lastY = sy(lastValid.y) + FS + 6;
      if (chocaFin(lastY)) lastY = finCajas[finCajas.length - 1].y1 + FS;
      finCajas.push({ y0: lastY - FS, y1: lastY + 3 });
      svg += textEl(lastX, lastY, fmtNum(lastValid.y), { anchor: "start", fontSize: FS, fill: si ? COL_INK : color, weight: 650, maxWidth: Math.max(20, vbW - lastX - 4) });
    }
    if (s.nombre && !mini) {
      svg += textEl(nombreX, 14, s.nombre, { anchor: "start", fontSize: FS, fill: si ? COL_INK : color, weight: 600 });
      nombreX += medir(s.nombre, FS, 600) + 20;
    }
  });

  if (mini) return svgWrap(vbW, vbH, svg);
  return chartWrap(svgWrap(vbW, vbH, svg), opts.read);
}

// =====================================================================
// 7. densidad — curva de Gauss (KDE) con la mediana marcada, honesta con
//    distribuciones desparejas (muchos ceros + una cola larga: la curva
//    tiene que mostrar ese pico, nunca "normalizarlo" a una campana pareja
//    que no representa los datos — pedido literal del responsable del producto).
//    Kernel gaussiano, ancho de banda de Silverman (h = 1.06·σ·n^(-1/5));
//    si σ es 0 o casi 0 (todos los valores iguales), h cae a un mínimo
//    visible en vez de colapsar a una línea recta. Dominio: min(0,min) a
//    max con un margen chico a la derecha. Igual convención "mini" que
//    scatter/linea: se dibuja sólo la forma (+ la marca de la mediana,
//    sin texto — mismo criterio que la línea de referencia de scatter en
//    mini, que se dibuja pero sin etiqueta) porque a ese tamaño el texto
//    no entra; el número ya vive en la tarjeta que la contiene.
// =====================================================================

function densidad(valores, opts) {
  opts = opts || {};
  var mini = !!opts.mini;
  var vals = (valores || []).filter(function (v) { return isFinite(v); });
  var fmtX = opts.fmtX || fmtNum;

  if (vals.length < 3) {
    if (mini) return svgWrap(opts.width || 160, opts.height || 64, "");
    return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);
  }

  var sorted = vals.slice().sort(function (a, b) { return a - b; });
  var n = sorted.length;
  var median = n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
  var mean = vals.reduce(function (a, v) { return a + v; }, 0) / n;
  var variance = vals.reduce(function (a, v) { return a + (v - mean) * (v - mean); }, 0) / n;
  var sigma = Math.sqrt(variance);
  var vMin = sorted[0], vMax = sorted[n - 1];

  // Silverman con salvavidas: una muestra degenerada (σ≈0, ej. todos los
  // valores iguales) igual dibuja un bulto visible en vez de colapsar.
  var h = sigma > 1e-9 ? 1.06 * sigma * Math.pow(n, -0.2) : 0;
  if (!h || h < 1e-6) h = Math.max((vMax - vMin) / 10, 1);

  var domainMin = Math.min(0, vMin);
  var domainMax = vMax + Math.max(h, (vMax - domainMin) * 0.06 || 1);
  if (domainMax <= domainMin) domainMax = domainMin + 1;

  var SAMPLES = 80;
  var kNorm = 1 / Math.sqrt(2 * Math.PI);
  function kde(x) {
    var s = 0;
    for (var j = 0; j < n; j++) {
      var u = (x - vals[j]) / h;
      s += kNorm * Math.exp(-0.5 * u * u);
    }
    return s / (n * h);
  }
  var pts = [];
  for (var i = 0; i <= SAMPLES; i++) {
    var x = domainMin + (domainMax - domainMin) * i / SAMPLES;
    pts.push({ x: x, y: kde(x) });
  }
  var maxY = Math.max.apply(null, pts.map(function (p) { return p.y; })) || 1;

  var vbW = mini ? (opts.width || 160) : anchoDe(opts, 680);
  var vbH = mini ? (opts.height || 64) : (opts.height || 200);
  var m = mini ? { left: 3, right: 3, top: 6, bottom: 6 } : { left: 18, right: 18, top: 34, bottom: 34 };
  var plotW = vbW - m.left - m.right, plotH = vbH - m.top - m.bottom;

  function sx(x) { return m.left + (x - domainMin) / (domainMax - domainMin) * plotW; }
  function sy(y) { return m.top + plotH - (y / maxY) * plotH; }

  var baseline = m.top + plotH;
  var linePts = pts.map(function (p) { return sx(p.x) + "," + sy(p.y); }).join(" ");
  var areaPts = sx(domainMin) + "," + baseline + " " + linePts + " " + sx(domainMax) + "," + baseline;

  var svg = "";
  // área rellena (tinte suave del acento) + curva fina + línea de base.
  svg += '<polygon points="' + areaPts + '" style="fill:' + COL_ACCENT + ';fill-opacity:.16"/>';
  svg += '<polyline points="' + linePts + '" style="fill:none;stroke:' + COL_ACCENT + ';stroke-width:' + (mini ? 1.4 : 1.75) + '"/>';
  svg += '<line x1="' + sx(domainMin) + '" y1="' + baseline + '" x2="' + sx(domainMax) + '" y2="' + baseline
    + '" style="stroke:' + COL_BORDER + ';stroke-width:1"/>';

  // mediana: la marca (hairline, mismo criterio visual que la línea de
  // referencia de scatter/lollipop) se dibuja SIEMPRE, incluso en mini —
  // es el punto entero del gráfico; sólo el texto se omite en mini.
  var mx = sx(median);
  svg += '<line x1="' + mx + '" y1="' + (mini ? m.top : 8) + '" x2="' + mx + '" y2="' + baseline
    + '" style="stroke:' + COL_TEXT3 + ';stroke-width:1.25;stroke-dasharray:3,3"/>';

  if (!mini) {
    var etiqueta = (opts.etiquetaMediana || T("admin.reports.charts.mediana", null, "mediana")) + " " + fmtX(median);
    var anchorEnd = mx > vbW * 0.62;
    svg += textEl(anchorEnd ? mx - 6 : mx + 6, 20, etiqueta,
      { anchor: anchorEnd ? "end" : "start", fontSize: FS, fill: "var(--text)", weight: 650 });
    svg += textEl(sx(domainMin), baseline + 18, fmtX(domainMin), { anchor: "start", fontSize: FS, fill: COL_INK });
    svg += textEl(sx(domainMax), baseline + 18, fmtX(domainMax), { anchor: "end", fontSize: FS, fill: COL_INK });
  }

  if (mini) return svgWrap(vbW, vbH, svg);
  return chartWrap(svgWrap(vbW, vbH, svg), opts.read);
}

// =====================================================================
// 7b. piramide — pirámide poblacional mujeres/varones por rango etario
//     (round 2026-07-22: un ranking de barras de una sola dimensión no
//     cuenta nada de la composición; mujeres/varones enfrentados sí). Eje
//     espejado: las barras de mujeres crecen hacia la izquierda desde el
//     centro, las de varones hacia la derecha — el rango etario vive en
//     el centro. Cuentas con sexo X o sin cargar suman al total del
//     bucket pero no tienen barra propia (no hay lado dónde dibujarlas);
//     si eso deja un hueco entre cantidad y f+m, se imprime UNA sola
//     línea callada debajo del gráfico entero (no una por bucket).
// =====================================================================

function piramide(buckets, opts) {
  opts = opts || {};
  buckets = (buckets || []).filter(function (b) { return b && b.rango; });
  if (!buckets.length) return chartWrap(emptyState(T("admin.reports.charts.sin_datos", null, "Sin datos todavía.")), opts.read);

  var vbW = anchoDe(opts, 680), rowH = 30;
  var m = { top: 8, bottom: 6 };
  var vbH = m.top + buckets.length * rowH + m.bottom;
  var centerX = vbW / 2, gutter = 58; // espacio central para el rango etario

  var maxV = 0, etiquetaW = 0;
  buckets.forEach(function (b) {
    maxV = Math.max(maxV, b.f || 0, b.m || 0);
    [b.f, b.m].forEach(function (v) { if (v > 0) etiquetaW = Math.max(etiquetaW, medir(fmtInt(v), FS, 600)); });
  });
  if (!maxV) maxV = 1;
  // The longest bar leaves room for its count printed past its end: a fixed
  // 14 cut «19» to «9» and «1.010» to «0» on the women's side.
  var plotHalf = centerX - gutter - Math.max(14, Math.ceil(etiquetaW) + 10);

  var COL_F = CAT_PALETTE[0], COL_M = CAT_PALETTE[1];

  var svg = "";
  svg += '<line x1="' + centerX + '" y1="' + m.top + '" x2="' + centerX + '" y2="' + (vbH - m.bottom)
    + '" style="stroke:' + COL_BORDER + ';stroke-width:1"/>';

  buckets.forEach(function (b, i) {
    var y = m.top + i * rowH;
    var yMid = y + rowH / 2 + 4;
    var f = b.f || 0, varones = b.m || 0;
    var wF = (f / maxV) * plotHalf, wM = (varones / maxV) * plotHalf;
    var barH = rowH - 10;
    if (wF > 0) svg += '<rect x="' + (centerX - gutter - wF) + '" y="' + (y + 5) + '" width="' + wF + '" height="' + barH + '" style="fill:' + COL_F + '"/>';
    if (wM > 0) svg += '<rect x="' + (centerX + gutter) + '" y="' + (y + 5) + '" width="' + wM + '" height="' + barH + '" style="fill:' + COL_M + '"/>';
    if (f > 0) svg += textEl(centerX - gutter - wF - 6, yMid, fmtInt(f), { anchor: "end", fontSize: FS, fill: "var(--text)", weight: 600 });
    if (varones > 0) svg += textEl(centerX + gutter + wM + 6, yMid, fmtInt(varones), { anchor: "start", fontSize: FS, fill: "var(--text)", weight: 600 });
    svg += textEl(centerX, yMid, b.rango, { anchor: "middle", fontSize: FS, fill: COL_INK, weight: 600 });
  });

  var legend = legendHtml([
    { nombre: T("admin.reports.charts.mujeres", null, "Mujeres"), color: COL_F },
    { nombre: T("admin.reports.charts.varones", null, "Varones"), color: COL_M }
  ]);
  var faltantes = buckets.reduce(function (a, b) { return a + Math.max(0, (b.cantidad || 0) - (b.f || 0) - (b.m || 0)); }, 0);
  injectStyle();
  var nota = faltantes > 0
    ? '<p class="sana-chart-note">' + fmtInt(faltantes) + " " + esc(faltantes === 1
      ? T("admin.reports.charts.sin_sexo_singular", null, "cuenta sin sexo cargado no se grafica")
      : T("admin.reports.charts.sin_sexo_plural", null, "cuentas sin sexo cargado no se grafican")) + ".</p>"
    : "";

  return chartWrap(svgWrap(vbW, vbH, svg) + legend + nota, opts.read);
}

// =====================================================================
// 8. tile — KPI "para qué sirve" (ley 7 de la doctrina)
// =====================================================================

var TILE_TONOS = {
  ok: { color: "var(--done)", bg: "var(--state-done-bg)" },
  atencion: { color: "var(--warn)", bg: "var(--warn-soft)" },
  critico: { color: "var(--danger)", bg: "var(--danger-soft)" },
  neutral: { color: "var(--text-2)", bg: "var(--bg-2)" },
};

function tile(o) {
  o = o || {};
  injectStyle();
  var pillHtml = "";
  if (o.pill) {
    var t = TILE_TONOS[o.pill.tono] || TILE_TONOS.neutral;
    pillHtml = '<div style="margin-top:8px"><span class="sana-tile-pill" style="color:' + t.color + ';background:' + t.bg + '">'
      + esc(o.pill.texto) + "</span></div>";
  }
  return '<div class="rep-kpi sana-tile">'
    + '<div class="rep-kpi-value">' + esc(o.valor == null ? T("admin.reports.charts.sin_dato", null, "s/d") : o.valor) + "</div>"
    + '<div class="rep-kpi-label">' + esc(o.etiqueta || "") + "</div>"
    + (o.sub ? '<div class="rep-bar-sub" style="margin-top:4px">' + esc(o.sub) + "</div>" : "")
    + pillHtml
    + (o.mini ? '<div class="sana-tile-mini">' + o.mini + "</div>" : "")
    + "</div>";
}

// =====================================================================
// 9. hero — bloque narrativo (ley 1 de la doctrina)
// =====================================================================

var HERO_DOT = { ok: "var(--done)", atencion: "var(--warn)", critico: "var(--danger)" };

// A phrase that names a panel screen (`enlace: {href, texto}`) reads the same
// with or without the link: the screen's name inside the text is the link.
function frase(f) {
  var texto = String((f && f.texto) || ""), e = f && f.enlace;
  var at = e && e.texto && /^\/admin\/[a-z0-9/-]+$/.test(e.href || "") ? texto.indexOf(e.texto) : -1;
  if (at < 0) return esc(texto);
  return esc(texto.slice(0, at)) + '<a href="' + esc(e.href) + '">' + esc(e.texto) + "</a>"
    + esc(texto.slice(at + e.texto.length));
}

function hero(frases) {
  frases = (frases || []).filter(function (f) { return f && f.texto; }).slice(0, 3);
  if (!frases.length) return "";
  injectStyle();
  var html = frases.map(function (f, i) {
    var color = HERO_DOT[f.tono] || "var(--text-3)";
    // The line is a flex row: a sentence with a link inside stays ONE item.
    return '<p class="sana-hero-line' + (i === 0 ? " sana-hero-line--lead" : "") + '">'
      + '<span class="sana-hero-dot" style="background:' + color + '"></span>'
      + (f.enlace ? "<span>" + frase(f) + "</span>" : esc(f.texto)) + "</p>";
  }).join("");
  return '<div class="sana-hero">' + html + "</div>";
}

// =====================================================================


var _internal = {
  esc: esc,
  fmtInt: fmtInt,
  fmtDec: fmtDec,
  fmtNum: fmtNum,
  textWidth: textWidth,
  truncateToWidth: truncateToWidth,
  niceNum: niceNum,
  niceTicks: niceTicks,
  treemapLayout: treemapLayout,
};

export {
  setAncho,
  scatter,
  heatmap,
  treemap,
  slope,
  lollipop,
  linea,
  densidad,
  piramide,
  tile,
  hero,
  frase,
  _internal,
};
