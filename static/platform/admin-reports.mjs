/* Native Reports controller: thirteen query-selected views, with payload
 * contracts owned by admin_panel/reports.py. Import is inert. Each mount
 * owns its cache, selected period/month, host, listeners and requests;
 * replacing a mount disposes the previous owner. Request generations guard
 * both publication and session transitions, including asynchronous decoding.
 * The template owns boot, page-exit disposal and failed-import recovery.
 * Navigation remains full page loads through ?view= keys; the shared shell
 * retains its title and navigation. Charts measure the existing #rep-body
 * contract; explicit width setters are retained where the renderer used them.
 * This file is the implementation, not a facade over a classic controller. */
const mounts = new WeakMap();

export function mountReports(options) {
  options = options || {};
  var root = options.root;
  if (!root) throw new Error("Reports root is required");
  if (mounts.has(root)) mounts.get(root)();

  var disposed = false;
  var ready = false;
  var generation = 0;
  var request = null;

  function alive() { return !disposed && root.isConnected !== false; }
  function dispose() {
    if (disposed) return;
    disposed = true;
    if (request) request.abort();
    root.removeEventListener("click", retryLoad);
    if (doc && doc.removeEventListener) {
      doc.removeEventListener("click", fueraDelRoot);
      doc.removeEventListener("keydown", teclaEscape);
    }
    if (mounts.get(root) === dispose) mounts.delete(root);
  }
  // Data failures resolve .ready after painting their specific message.
  // Recovery therefore belongs to the mounted content, not just its loader.
  // The same root listener closes Recorrido's step list on an outside click.
  function retryLoad(event) {
    if (alive() && event.target && event.target.id === "reports-retry") { location.reload(); return; }
    cerrarPasosRecorrido(event);
    cerrarListaInformes(event);
  }
  // An outside click closes the list of reports that the breadcrumb opens.
  function cerrarListaInformes(e) {
    if (!alive() || !root.querySelector) return;
    var lista = root.querySelector(".rep-switch");
    if (!lista || !lista.open || (e.target && lista.contains(e.target))) return;
    lista.open = false;
  }
  // Clicks outside the reports root (sidebar, top bar) and Escape close the
  // same two lists, like any other menu of the panel.
  var doc = root.ownerDocument || (typeof document !== "undefined" ? document : null);
  function fueraDelRoot(event) {
    if (!alive() || (event.target && root.contains && root.contains(event.target))) return;
    cerrarPasosRecorrido(event);
    cerrarListaInformes(event);
  }
  function teclaEscape(event) {
    if (!alive() || event.key !== "Escape") return;
    var cerrar = { target: null };
    cerrarPasosRecorrido(cerrar);
    cerrarListaInformes(cerrar);
  }
  root.addEventListener("click", retryLoad);
  if (doc && doc.addEventListener) {
    doc.addEventListener("click", fueraDelRoot);
    doc.addEventListener("keydown", teclaEscape);
  }
  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing Reports dependency: " + name));
  }

  // All five dependencies must settle before the first fetch/render.
  // Missing modules fail closed through .ready/onError.
  var Dates = null;
  var datesReady = importStatic("platform/dates.mjs").then(function (m) { Dates = m; return m; });
  var Session = null;
  var sessionReady = importStatic("platform/admin-session.mjs").then(function (m) { Session = m; return m; });
  var Charts = null;
  var chartsReady = importStatic("platform/admin-charts.mjs").then(function (m) { Charts = m; return m; });
  var Search = null;
  var searchReady = importStatic("platform/search.mjs").then(function (m) { Search = m; return m; });
  var I18n = null;
  var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
  // The shared «Descargar» button of the panel's table engine. Optional: a
  // report still paints when it is missing, only without the button.
  var Table = null;
  var tableReady = importStatic("platform/admin-table.mjs").then(function (m) { Table = m; return m; }, function () { return null; });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }

  var $ = function (s, r) { return (r || root).querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  var PERIODOS = [7, 30, 90];
  var DEFAULT_DIAS = 30;
  // Recupero/SUMAR+ comparten el mismo default de 90 días que sus rutas
  // hermanas *.csv en reports.py (_DEFAULT_DIAS_CSV) — un reporte de
  // facturación quiere una ventana más ancha que Gestión/Pacientes por
  // default, no los últimos 30 días.
  var _DEFAULT_DIAS_CSV = 90;

  // The screen passes its country-pack currency explicitly to this mount.
  // This restores the retired shell's input without publishing another
  // global. AI costs stay USD; only their numeric locale is shared.
  var _CURRENCY = options.currency || { code: "ARS", symbol: "$", locale: "es-AR", iso2: "AR" };
  var LOCALE = _CURRENCY.locale;
  // "AR$" (ISO2 + símbolo, sin espacio) es la misma convención que "US$" más
  // abajo — genérico para cualquier país (GB£, US$, AR$...).
  var MONEDA_LOCAL = _CURRENCY.iso2 + _CURRENCY.symbol;

  // Every number of the reports goes through the panel's ONE number
  // formatter (i18n.mjs formatNumber/formatPercent, format.mjs
  // numero/porcentaje): thousands always grouped («4.188 de 10.238»), never
  // a trailing zero («1.466 h», «44,5 h», «3», not «3,0»), and every
  // percentage one way — «52%», a decimal only under 10 or just short of
  // 100 (ui-expert 2026-11-15: «41 %», «49,0 %» and «69,2 %» on one screen).
  function numero(n, d) {
    if (n == null || !isFinite(Number(n))) return "—";
    return I18n && I18n.formatNumber ? I18n.formatNumber(n, d) : String(n);
  }
  function fmtInt(n) { return numero(n == null ? n : Math.round(Number(n)), 0); }
  function fmtDec(n, d) { return numero(n, d); }
  function fmtHoras(n) { return numero(n, 1); }
  function fmtPct(n) {
    if (n == null || !isFinite(Number(n))) return "—";
    return I18n && I18n.formatPercent ? I18n.formatPercent(n) : String(n) + "%";
  }
  // The number of a percentage whose catalog text carries its own sign
  // («{pct}% del total»): same digits as fmtPct.
  function pctNum(n) { return fmtPct(n).replace(/%$/, ""); }
  // Los costos son chicos (una orden ~US$0.004): mostramos suficientes decimales
  // para que un número real no se redondee a "US$ 0.00". Totales grandes con 2.
  function fmtUsd(n) {
    if (n == null) return "—";
    var v = Number(n);
    var dec = v >= 1 ? 2 : (v >= 0.1 ? 3 : 4);
    return "US$ " + v.toLocaleString(LOCALE, { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: "always" });
  }
  // Moneda local redondeada, sin decimales (la cifra es una estimación de
  // horas × costo/hora, no una transacción real) — "AR$ 12.345", nunca
  // "AR$ 12345".
  function fmtArs(n) {
    if (n == null) return "—";
    // in the installation currency's own locale (the mount's contract), with
    // grouping always on: es-AR Intl leaves «AR$ 8500» ungrouped otherwise
    return MONEDA_LOCAL + " " + Math.round(Number(n)).toLocaleString(LOCALE, { useGrouping: "always" });
  }
  function fechaLabel(iso) {
    if (Dates) { var l = Dates.dia(iso); if (l) return l; }
    return iso;
  }
  // Ancho de barra en %: piso de 4% para que un valor chico pero real siga
  // siendo visible, PERO un valor genuinamente cero no dibuja barra — el piso
  // no puede mentir que hay algo donde no hay nada.
  function barPct(value, max) {
    if (!value || value <= 0 || !max || max <= 0) return 0;
    return Math.max(4, Math.round((value / max) * 100));
  }
  // No data, no block (the product owner R30, 05.10: «Hide the empty tiles…»; critical
  // review 08.11 E9). A figure that prints as zero — «0», «US$ 0,0000»,
  // «0,00», or «—» for none — says nothing a missing tile would not, and a
  // composition of one category is not a composition: the title already
  // names it.
  function muestraCero(texto) { return !/[1-9]/.test(String(texto == null ? "" : texto)); }
  function treemapDeVarios(items, opts) {
    var conValor = (items || []).filter(function (i) { return i && i.valor > 0; });
    return conValor.length >= 2 ? Charts.treemap(conValor, opts) : "";
  }

  // The noun next to a number follows the same rule as Tn (i18n.mjs pluralCategory).
  function plural(n, uno, muchos) {
    var one = I18n && I18n.pluralCategory ? I18n.pluralCategory(n) === "one" : Number(n) === 1;
    return one ? uno : muchos;
  }

  // =====================================================================
  // Each load owns a new generation and AbortController. Only the latest
  // live generation may publish, including when revisiting a cached period.
  // =====================================================================

  function beginLoad() {
    generation += 1;
    if (request) request.abort();
    request = new AbortController();
    return { gen: generation, signal: request.signal };
  }
  function owns(gen) { return alive() && gen === generation; }

  function fetchJson(url, signal) {
    const isCurrent = () => alive() && !signal?.aborted;
    function requireCurrent() {
      if (!isCurrent()) throw new DOMException("Reports request no longer owns this load", "AbortError");
    }
    return fetch(url, { credentials: "same-origin", signal: signal }).then(function (res) {
      requireCurrent();
      // The session boundary shares current ownership, never stale authority.
      var boundary = (res.status === 401 || res.status === 403)
        ? Session.confirmar(res, undefined, isCurrent)
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        requireCurrent();
        // Sólo un 401 que whoami confirma se lleva la pantalla a otra página
        // ("redirect"); uno sin confirmar (Basic Auth del sitio, un proxy)
        // llega a bodyErrorHtml con su botón de recarga.
        if (transitioned) { dispose(); throw new Error("redirect"); }
        if (res.status === 401) throw new Error("401");
        if (res.status === 403) throw new Error("403");
        if (res.status === 503) throw new Error("503");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json().then(function (body) { requireCurrent(); return body; });
      });
    });
  }

  // =====================================================================
  // bloques compartidos por los sub-reportes
  // =====================================================================

  function kpiTile(label, value, sub) {
    return '<div class="rep-kpi"><div class="rep-kpi-value">' + esc(value) + '</div>'
      + '<div class="rep-kpi-label">' + esc(label) + "</div>"
      + (sub ? '<div class="rep-bar-sub" style="margin-top:4px">' + esc(sub) + "</div>" : "")
      + "</div>";
  }

  function barRow(name, pct, valueLabel, subLabel, variant) {
    return '<div class="rep-bar-item">'
      + '<div class="rep-bar-head"><span class="rep-bar-name">' + esc(name) + '</span>'
      + '<span class="rep-bar-num">' + esc(valueLabel) + "</span></div>"
      + '<div class="rep-bar-track"><div class="rep-bar-fill' + (variant ? " rep-bar-fill--" + variant : "")
      + '" style="width:' + pct + '%"></div></div>'
      + (subLabel ? '<div class="rep-bar-sub">' + esc(subLabel) + "</div>" : "")
      + "</div>";
  }

  // A section is its title and the chart right under it: no gray subtitle
  // line in between (the product owner R11, critical review 08.11 E12). What a subtitle
  // carried that matters goes into the title.
  function seccion(titulo, bodyHtml) {
    return '<div class="rep-section"><h2 class="rep-h2">' + esc(titulo) + "</h2>"
      + bodyHtml + "</div>";
  }

  // Progressive disclosure (responsable del producto, 2026-10): una sección cuyo único contenido
  // sería un placeholder "sin datos" NO se dibuja — ni título ni línea vacía.
  // Los builders de gráfico devuelven "" cuando no tienen nada que graficar y
  // esta variante omite la sección entera en ese caso.
  function seccionOpcional(titulo, bodyHtml) {
    return bodyHtml ? seccion(titulo, bodyHtml) : "";
  }

  // La única línea que ve quien abre un informe sin datos: debajo del
  // breadcrumb, la lista de informes disponibles y el selector de período
  // (si lo hay, porque otro período puede tener datos), nunca una lista de
  // secciones vacías.
  function informeVacioHtml(texto) {
    return '<p class="rep-empty">' + esc(texto || T("admin.reports.reports.informe_sin_datos", null, "Todavía no hay datos para este informe.")) + "</p>";
  }

  // Barras verticales por día (volumen). `list` = [{fecha, ...}], `valorDe` lee
  // el número a graficar de cada fila, `tituloDe` arma el tooltip. El eje
  // visible (rango de fechas) va debajo: un gráfico sin ninguna marca visible
  // sólo "se muestra y no se explica" (responsable del producto, 2026-09-16) — el detalle fino
  // queda en el hover, pero el rango se lee sin tocar nada.
  function chartBarrasDiarias(list, valorDe, tituloDe) {
    list = list || [];
    if (!list.some(function (r) { return valorDe(r) > 0; })) return "";
    var max = 0;
    list.forEach(function (r) { var v = valorDe(r); if (v > max) max = v; });
    if (!max) max = 1;
    var cols = list.map(function (r) {
      var h = Math.max(3, Math.round((valorDe(r) / max) * 100));
      return '<div class="rep-vol-col" title="' + esc(tituloDe(r)) + '"><div class="rep-vol-bar" style="height:' + h + '%"></div></div>';
    }).join("");
    var eje = '<div class="rep-mini-axis"><span>' + esc(fechaLabel(list[0].fecha)) + "</span><span>"
      + esc(fechaLabel(list[list.length - 1].fecha)) + "</span></div>";
    return '<div class="rep-vol-wrap"><div class="rep-vol-bars">' + cols + "</div></div>" + eje;
  }

  function periodoControlHtml(id, diasActual) {
    // «Últimos 30 días» says what the control is: its name goes to the screen
    // reader only, not as a caption next to it.
    return '<select id="' + id + '" class="adm-sel" aria-label="' + esc(T("admin.reports.reports.periodo_label", null, "Período")) + '">'
      + PERIODOS.map(function (d) {
        return '<option value="' + d + '"' + (d === diasActual ? " selected" : "") + '>'
          + esc(T("admin.reports.reports.periodo_opcion", { dias: d }, "Últimos " + d + " días")) + "</option>";
      }).join("")
      + "</select>";
  }

  function bodyErrorHtml(e) {
    var msg = e && e.message;
    var message = String(msg) === "403"
      ? T("admin.reports.reports.error_403", null, "No tenés permiso para ver esta sección.")
      : String(msg) === "503"
        ? T("admin.reports.reports.error_503", null, "La base de datos no está disponible.")
        : T("admin.reports.reports.error_generico", null, "No se pudo cargar. Reintentá.");
    return '<p class="adm-empty" role="alert">' + esc(message) + '</p>'
      + '<button type="button" class="btn" id="reports-retry">'
      + esc(T("admin.content.common.reload", null, "Recargar")) + '</button>';
  }

  // One-line breadcrumb, the first thing every sub-report draws (never the
  // front page): a link to «Reportes» + " / " + the report's name, never a Back
  // button (ui-rules). The name opens the list of the OTHER reports that hold
  // data (navHtml), so one moves between reports without the front page and no
  // row of links sits above each report.
  function crumbHtml(key) {
    var sub = SUBS_BY_KEY[key];
    var label = sub ? subLabel(sub) : key;
    var inicio = '<a href="' + reportHref("resumen") + '">' + esc(T("admin.reports.reports.crumb_reportes", null, "Reportes")) + '</a> / ';
    var nav = navHtml(key);
    if (!nav) return '<p class="cnt-crumb">' + inicio + esc(label) + "</p>";
    // The report's own name opens the list of the other reports: one line of
    // header at every width instead of a row of twelve links above the report.
    return '<div class="cnt-crumb rep-crumb">' + inicio
      + '<details class="rep-switch"><summary>' + esc(label) + '<span class="adm-icon" aria-hidden="true"></span></summary>'
      + nav + "</details></div>";
  }

  // Qué informes tienen datos. La portada lo sabe (su fan-out trae los doce
  // payloads) y lo deja en sessionStorage como conveniencia por visitante;
  // cada sub-reporte actualiza su propia entrada al renderizar. Un
  // sub-reporte abierto por deep link sin esa memoria NO dispara doce fetches
  // (no hay CDN: menos requests, nunca más) — lista todos los informes y
  // deja que cada uno diga lo suyo con una línea. Cualquier fallo del
  // storage (ventana privada, datos bloqueados) se trata como "no se sabe".
  var DISPONIBLES_KEY = "hr.reportes.disponibles";
  function leerDisponibles() {
    try {
      var raw = sessionStorage.getItem(DISPONIBLES_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (e) { return null; }
  }
  function guardarDisponibles(mapa) {
    try { sessionStorage.setItem(DISPONIBLES_KEY, JSON.stringify(mapa)); } catch (e) { /* conveniencia, nunca requisito */ }
  }
  function marcarDisponible(key, tiene) {
    var mapa = leerDisponibles() || {};
    if (mapa[key] === !!tiene) return;
    mapa[key] = !!tiene;
    guardarDisponibles(mapa);
  }

  function navHtml(actual) {
    var mapa = leerDisponibles();
    var otros = SUBS.filter(function (s) {
      if (s.key === "resumen" || s.key === actual) return false;
      // sin memoria, o sin entrada para esta clave: no se sabe, se lista
      return !mapa || !(s.key in mapa) || mapa[s.key];
    });
    if (!otros.length) return "";
    return '<nav class="rep-nav" aria-label="' + esc(T("admin.reports.reports.nav_otros_informes", null, "Otros informes")) + '">'
      + otros.map(function (s) { return '<a href="' + reportHref(s.key) + '">' + esc(subLabel(s)) + "</a>"; }).join("")
      + "</nav>";
  }

  // =====================================================================
  // sub-reporte: Gestión (?view=gestion)
  // =====================================================================

  var CACHE_GESTION = {};
  var DIAS_GESTION = DEFAULT_DIAS;

  function fetchGestion(dias, force, signal) {
    if (!force && CACHE_GESTION[dias]) return Promise.resolve(CACHE_GESTION[dias]);
    return fetchJson("/api/admin/reports?days=" + dias, signal).then(function (j) { CACHE_GESTION[dias] = j; return j; });
  }

  // KPIs de Gestión con Charts.tile: cada tile responde una pregunta
  // (ley 7 de report-design). A pill appears only when the value asks to be
  // looked at; a figure that is fine stands alone (never a "critical" tone
  // here: that scale is reserved for genuine lack of capacity, see
  // reports.py:_hero_inversion).
  function kpisGestionTilesHtml(k, dias) {
    var pctAprob = k.pct_aprobadas;
    var horasRes = k.mediana_horas_resolucion;
    var diasTurno = k.mediana_dias_turno;
    var tiles = "";
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.gestion_pendientes_ahora", null, "Pendientes ahora"), valor: k.pendientes == null ? null : fmtInt(k.pendientes) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.gestion_solicitudes_en_dias", { dias: dias }, "Solicitudes en " + dias + " días"), valor: k.solicitudes_periodo == null ? null : fmtInt(k.solicitudes_periodo) });
    // valor: null (no "—" preformateado) — Charts.tile ya resuelve el
    // dato faltante como s/d, la convención de admin-charts.mjs (nunca un
    // guión largo en HTML de gráfico, ver su propio test de la doctrina).
    tiles += Charts.tile({
      etiqueta: T("admin.reports.reports.gestion_aprobadas", null, "Aprobadas ahora, de las resueltas"), valor: pctAprob == null ? null : fmtPct(pctAprob),
      // A pill only when the figure asks for a look: a figure that is fine
      // stands alone, without a label praising it.
      pill: pctAprob == null || pctAprob >= 70 ? null : {
        texto: T("admin.reports.reports.gestion_revisar_cuellos", null, "Revisar los cuellos de botella"),
        tono: "atencion",
      },
    });
    // Three different spans, three different names: how long an answered
    // request took to be answered (here), how long the turno was from the
    // request (next tile), how long the pending ones have gone unanswered (the
    // Resumen card). Requests nobody had to answer are not in this median.
    tiles += Charts.tile({
      etiqueta: T("admin.reports.reports.gestion_horas_resolver", null, "Horas hasta resolver (mediana)"), valor: horasRes == null ? null : fmtHoras(horasRes) + " h",
      pill: horasRes == null || horasRes <= 24 ? null : {
        texto: T("admin.reports.reports.gestion_mas_de_un_dia", null, "Más de un día"),
        tono: "atencion",
      },
    });
    tiles += Charts.tile({
      etiqueta: T("admin.reports.reports.gestion_dias_hasta_turno", null, "Días del pedido al turno (mediana)"), valor: diasTurno == null ? null : fmtDec(diasTurno, 1),
    });
    return '<div class="rep-tiles">' + tiles + "</div>";
  }

  // -----------------------------------------------------------------------
  // secciones story-driven de Gestión (reemplazan las barras viejas) — cada
  // una: gráfico con números impresos + read: + cierre en una acción
  // (report-design SKILL.md, leyes 3/4/5/6).
  // -----------------------------------------------------------------------

  function seccionOfertaDemanda(od) {
    var clusters = (od && od.clusters) || [];
    if (!clusters.some(function (r) { return r.solicitudes > 0 || r.horas_semana > 0; })) return "";
    var equilibrio = od.equilibrio;
    var peores = clusters.filter(function (r) { return r.solicitudes >= 3; })
      .slice().sort(function (a, b) {
        if (!!a.sin_oferta !== !!b.sin_oferta) return a.sin_oferta ? -1 : 1;
        return (b.solicitudes_por_hora || 0) - (a.solicitudes_por_hora || 0);
      }).slice(0, 3).map(function (r) { return r.nombre; });
    // Con 2 docenas de clusters, nombrar TODOS amontona texto ilegible cerca
    // del origen (la mayoría pide pocos turnos y vive apretada ahí) — se
    // etiquetan a lo sumo 6: los resaltados siempre, y después los de mayor
    // volumen (piso >=3); el resto sigue plotteado, sin nombre.
    var conNombre = {};
    peores.forEach(function (n) { conNombre[n] = true; });
    clusters.slice().sort(function (a, b) { return b.solicitudes - a.solicitudes; })
      .forEach(function (r) {
        if (Object.keys(conNombre).length < 6 && r.solicitudes >= 3) conNombre[r.nombre] = true;
      });
    var rows = clusters.map(function (r) { return { x: r.horas_semana, y: r.solicitudes, etiqueta: conNombre[r.nombre] ? r.nombre : "" }; });
    var chart = Charts.scatter(rows, {
      ejeX: T("admin.reports.reports.eje_horas_semanales_ofrecidas", null, "Horas semanales ofrecidas"),
      ejeY: T("admin.reports.reports.eje_solicitudes_en_periodo", null, "Solicitudes en el período"),
      linea: equilibrio ? { tipo: "diagonal", pendiente: equilibrio, etiqueta: T("admin.reports.reports.linea_equilibrio_periodo", null, "equilibrio del período") } : null,
      resaltar: peores,
      read: T("admin.reports.reports.read_oferta_demanda", null, "Arriba de la línea de equilibrio, una especialidad pide más turnos por hora ofrecida que la mediana del período; resaltado, dónde más rendiría una hora médica más."),
    });
    // sugerencias menores a 1 h/semana son ruido ("Sumar ~0,5 h" no es una
    // acción real) — se descartan antes de rankear. Las horas se imprimen
    // enteras a partir de 10 (nunca "~132,0"); arriba de 40 h la sugerencia
    // ya no prescribe horas (un refuerzo puntual no cubre esa demanda): se
    // reemplaza por la misma frase de presión que usa el hero
    // (reports.py:_hero_inversion, round 2026-07-22 "weird text").
    function horasLabel(h) { return h >= 10 ? fmtInt(h) : fmtDec(h, 1); }
    var sugeridos = clusters.filter(function (r) { return r.sugerencia_horas && r.sugerencia_horas >= 1; })
      .slice().sort(function (a, b) { return b.sugerencia_horas - a.sugerencia_horas; }).slice(0, 3);
    var acciones = sugeridos.length
      ? '<ul class="rep-action-list">' + sugeridos.map(function (r) {
        if (r.sugerencia_horas > 40) {
          var tasa = fmtDec(r.solicitudes_por_hora, 2);
          return "<li>" + esc(T("admin.reports.reports.accion_demanda_excede_refuerzo", { nombre: r.nombre, tasa: tasa },
            "" + r.nombre + " recibe " + tasa + " solicitudes por hora ofrecida, lejos del equilibrio: la demanda excede un refuerzo puntual.")) + "</li>";
        }
        var horas = horasLabel(r.sugerencia_horas);
        return "<li>" + esc(T("admin.reports.reports.accion_sumar_horas_equilibrio", { horas: horas, nombre: r.nombre },
          "Sumar ~" + horas + " h semanales a " + r.nombre + " la llevaría al equilibrio.")) + "</li>";
      }).join("") + "</ul>"
      : '<p class="rep-empty">' + esc(T("admin.reports.reports.ninguna_especialidad_sobre_equilibrio", null, "Ninguna especialidad quedó, este período, por encima de la razón de equilibrio.")) + '</p>';
    return seccion(T("admin.reports.reports.titulo_donde_rinde_hora_medica", null, "Dónde rinde más una hora médica"),
      chart + acciones);
  }

  function seccionHeatmap(hm) {
    hm = hm || {};
    var hayDemanda = (hm.demanda || []).some(function (fila) {
      return (fila || []).some(function (v) { return v > 0; });
    });
    if (!(hm.dias || []).length || !hayDemanda) return "";
    var horas = (hm.horas || []).map(function (h) { return h + "h"; });
    var grid = { dias: hm.dias, horas: horas, celdas: hm.demanda };
    var marcar = (hm.desajuste || []).map(function (d) { return { d: d.dia, h: d.hora + "h" }; });
    var chart = Charts.heatmap(grid, {
      marcar: marcar,
      // Cells tinted without a number (<1) are minor pressure spread over
      // several slots: named once inside the one reading line, not as a
      // second footnote under the chart (critical review 08.11, E13).
      read: T("admin.reports.reports.read_heatmap", null, "Cada celda es la presión de turnos pedidos por día y hora: cuanto más oscura, mayor. El contorno marca dónde la demanda supera la agenda que el catálogo tiene cargada para ese horario."),
    });
    // The list names the three worst bands, each once (several hours share a
    // band label); the server already orders the cells by difference.
    var vistos = {};
    var peores = (hm.desajuste || []).filter(function (d) {
      if (vistos[d.label]) return false;
      vistos[d.label] = true;
      return true;
    }).slice(0, 3);
    var acciones = peores.length
      ? '<ul class="rep-action-list">' + peores.map(function (d) {
        var dif = fmtDec(d.diferencia, 1);
        return "<li>" + esc(T("admin.reports.reports.accion_desajuste_horario", { label: d.label, diferencia: dif },
          "" + d.label + " concentra pedidos sin agenda ofrecida (+" + dif + ").")) + "</li>";
      }).join("") + "</ul>"
      : '<p class="rep-empty">' + esc(T("admin.reports.reports.sin_desajuste_horarios", null, "No hay horarios con más demanda que agenda ofrecida en este período.")) + '</p>';
    return seccion(T("admin.reports.reports.titulo_cuando_llega_la_gente", null, "Cuándo llega la gente y cuándo hay agenda"),
      chart + acciones);
  }

  // `titular`: the page's hero phrases. The closing sentence is not printed a
  // second time under the chart when the hero has already said it.
  function seccionPalancas(p, titular) {
    var items = ((p && p.grupos) || []).filter(function (g) { return g.n > 0; }).map(function (g) {
      return { nombre: g.nombre, valor: g.n, tono: g.id === "prevenible_antes" ? "mal" : undefined };
    });
    if (!items.length) return "";
    var cierre = String(p.hero || "").trim();
    var yaDicho = (titular || []).some(function (f) { return String((f && f.texto) || "").trim() === cierre; });
    var chart = treemapDeVarios(items, {
      read: T("admin.reports.reports.read_palancas", null, "El bloque más grande es la causa de rechazo más frecuente; el color de alerta marca lo evitable con mejores controles antes de enviar."),
    });
    var cuerpo = chart + (p.hero && !yaDicho ? '<p class="rep-action">' + esc(p.hero) + "</p>" : "");
    // One group draws no chart; with its sentence already in the headline the
    // section would be a heading over nothing.
    if (!cuerpo) return "";
    return seccion(T("admin.reports.reports.titulo_rechazos_evitables", null, "Rechazos que se pueden evitar antes"), cuerpo);
  }

  // The booking path of the period, with the steps, names and numbers of the
  // Recorrido report (the server counts both from one function,
  // reports_funnel.summary) — never a second dictionary of its own (critical
  // review 08.11, E7). Before the first step: who picked a time on a service
  // with a real schedule (the soft-lock of reservas.py); many requests arrive
  // without picking one, so its % is printed only on a genuine drop, and only
  // against «Pidieron turno»: a hold pairs with the request it became, and any
  // other first step (the admin can reorder or turn steps off) counts a
  // population the holds do not contain, so that share would read like a
  // conversion it is not.
  // A phrase links to the screen it names only for someone whose menu offers
  // that screen; for anyone else (the reports-only role) it is the same
  // sentence without the link, never a link to a screen that refuses them.
  function pantallaEnMenu(href) {
    if (typeof document === "undefined" || !document.querySelectorAll) return false;
    return Array.prototype.some.call(document.querySelectorAll("#adm-nav a"), function (a) {
      return a.getAttribute("href") === href && !(a.style && a.style.display === "none");
    });
  }

  function fraseAbrible(f) {
    if (!f || !f.enlace || pantallaEnMenu(f.enlace.href)) return f;
    return { tono: f.tono, texto: f.texto };
  }

  function seccionFunnel(f, le) {
    f = f || {};
    var pasos = (f.pasos || []).filter(function (p) { return p && p.label; });
    if (!(f.holds_creados > 0 || (pasos.length && pasos[0].n > 0))) return "";
    var etapas = [{ key: "holds", label: T("admin.reports.reports.funnel_eligieron_horario", null, "Eligieron un horario"), value: f.holds_creados || 0 }]
      .concat(pasos.map(function (p) { return { key: p.key, label: p.label, value: p.n }; }));
    var tiles = etapas.map(function (e, i) {
      var sub = "";
      if (i > 0 && etapas[i - 1].value && e.value <= etapas[i - 1].value
        && (etapas[i - 1].key !== "holds" || e.key === "pedido")) {
        var pct = pctNum(100 * e.value / etapas[i - 1].value), etapaPrev = etapas[i - 1].label;
        sub = T("admin.reports.reports.funnel_pct_de_etapa", { pct: pct, etapa: etapaPrev }, "" + pct + "% de «" + etapaPrev + "»");
      }
      return Charts.tile({ etiqueta: e.label, valor: fmtInt(e.value), sub: sub });
    });

    var html = '<div class="rep-tiles">' + tiles.join("") + "</div>";
    // Two different «approved»: «Les dieron turno» counts every request that was ever given one, the tile
    // above counts those still approved today. The difference was approved and cancelled afterwards.
    if (f.con_turno > 0 && f.aprobadas >= 0 && f.con_turno > f.aprobadas) {
      html += '<p class="rep-action">' + esc(T("admin.reports.reports.funnel_dos_aprobadas",
        { con: fmtInt(f.con_turno), ahora: fmtInt(f.aprobadas), despues: fmtInt(f.con_turno - f.aprobadas) },
        "Les dieron turno: " + fmtInt(f.con_turno) + ". Siguen aprobadas: " + fmtInt(f.aprobadas)
        + "; aprobadas y canceladas después: " + fmtInt(f.con_turno - f.aprobadas) + ".")) + "</p>";
    }
    if (le && le.turnos_recuperados) {
      var nTurnos = fmtInt(le.turnos_recuperados);
      var verbo = plural(le.turnos_recuperados, T("admin.reports.reports.turno_liberado_singular", null, "turno liberado volvió"), T("admin.reports.reports.turno_liberado_plural", null, "turnos liberados volvieron"));
      html += '<p class="rep-action">' + esc(T("admin.reports.reports.accion_lista_espera", { n: nTurnos, verbo: verbo },
        "" + nTurnos + " " + verbo + " a usarse gracias a la lista de espera.")) + "</p>";
    }
    // Where most requests stayed behind and what to do: the same sentences as
    // the Recorrido hero, plus where the desk marks attendance when some is missing.
    (f.frases || []).forEach(function (fr) {
      if (fr && fr.texto) html += '<p class="rep-action">' + Charts.frase(fraseAbrible(fr)) + "</p>";
    });
    return seccion(T("admin.reports.reports.titulo_del_clic_al_consultorio", null, "Del clic al consultorio"), html);
  }

  function seccionVolumenLinea(vol) {
    vol = vol || [];
    if (!vol.some(function (v) { return v.total > 0 || v.aprobadas > 0; })) return "";
    var puntosAprob = vol.map(function (v, i) { return { x: i, etiqueta: fechaLabel(v.fecha), y: v.aprobadas }; });
    var puntosTotal = vol.map(function (v, i) { return { x: i, etiqueta: fechaLabel(v.fecha), y: v.total }; });
    // The two series carry the names of the booking-path steps they count
    // (one dictionary with Recorrido and the funnel above, E7).
    var chart = Charts.linea([
      { nombre: T("admin.srv.reports.recorrido.paso.aprobada", null, "Les dieron turno"), puntos: puntosAprob },
      { nombre: T("admin.srv.reports.recorrido.paso.pedido", null, "Pidieron turno"), puntos: puntosTotal },
    ], { read: T("admin.reports.reports.read_volumen_linea", null, "La línea de color son las solicitudes a las que les dieron turno; la gris, todas las que pidieron turno ese día.") });
    return seccion(T("admin.reports.reports.titulo_solicitudes_por_dia", null, "Solicitudes por día"), chart);
  }

  function renderGestion(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataGestion(data);
    marcarDisponible("gestion", tiene);
    var html = crumbHtml("gestion");
    if (tiene) html += Charts.hero(data.hero);
    html += '<div class="rep-controls">' + periodoControlHtml("rep-periodo", DIAS_GESTION)
      + (tiene ? '<a class="rep-csv-link" href="/api/admin/reports/requests.csv">' + esc(T("admin.reports.reports.descargar_csv_solicitudes", null, "Descargar CSV de solicitudes (últimos 90 días)")) + '</a>' : "")
      + "</div>";
    if (!tiene) {
      html += informeVacioHtml();
    } else {
      html += kpisGestionTilesHtml(data.kpis, data.dias);
      html += seccionOfertaDemanda(data.oferta_demanda);
      html += seccionHeatmap(data.heatmap);
      html += seccionPalancas(data.palancas, data.hero);
      html += seccionFunnel(data.funnel, data.lista_espera);
      html += seccionVolumenLinea(data.volumen);
    }
    body.innerHTML = html;

    var sel = $("#rep-periodo", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_GESTION = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      cargarGestion();
    });
  }

  function cargarGestion() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchGestion(DIAS_GESTION, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderGestion(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Médicos (?view=medicos) — sin endpoint nuevo, agrega
  // client-side sobre /api/admin/doctors/stats + /api/admin/content/doctors
  // (esto último para el nombre a mostrar en los gráficos Y, desde
  // 2026-07-22, para centro/especialidad — el color de cluster del scatter
  // y las tiles del treemap de especialidad).
  // =====================================================================

  var CACHE_MEDICOS = null;

  function fetchMedicos(force, signal) {
    if (!force && CACHE_MEDICOS) return Promise.resolve(CACHE_MEDICOS);
    return Promise.all([
      fetchJson("/api/admin/doctors/stats", signal),
      fetchJson("/api/admin/content/doctors", signal),
    ]).then(function (res) {
      var stats = res[0].medicos || {};
      var meta = {};
      (res[1].items || []).forEach(function (m) { if (m.id) meta[m.id] = m; });
      var rows = Object.keys(stats).map(function (id) {
        var st = stats[id];
        var m = meta[id] || {};
        return {
          id: id,
          nombre: m.nombre || id,
          especialidad: m.especialidad || "",
          centro: m.centro || "",
          centroNombre: m.centro ? slugTitulo(m.centro) : "",
          horas_semana: st.horas_semana || 0,
          turnos_14d: st.turnos_14d || 0,
          slots_14d: st.slots_14d || 0,
          ocupacion_14d: st.ocupacion_14d,
          tiene_foto: !!st.tiene_foto,
          tiene_login: !!st.tiene_login,
        };
      });
      CACHE_MEDICOS = rows;
      return rows;
    });
  }

  // Load of the next 14 days, all doctors together: the Reportes card and the
  // Médicos tile both read it from here.
  function cargaTotal(rows) {
    var slots = rows.reduce(function (a, r) { return a + (r.slots_14d || 0); }, 0);
    var turnos = rows.reduce(function (a, r) { return a + (r.turnos_14d || 0); }, 0);
    return { slots: slots, turnos: turnos, pct: slots ? 100 * turnos / slots : null };
  }

  function kpisMedicosHtml(rows) {
    var total = rows.length;
    var conAgenda = rows.filter(function (r) { return r.horas_semana > 0; }).length;
    var horasTotal = rows.reduce(function (a, r) { return a + r.horas_semana; }, 0);
    // The same figure as the Médicos card on Reportes: booked turnos over
    // places of the next 14 days, all doctors together. An average of each
    // doctor's own load read «0%» next to the card's «0,1%» (ui-expert
    // 2026-11-15): one number, one computation.
    var ocupTotal = cargaTotal(rows).pct;
    var sinFoto = rows.filter(function (r) { return !r.tiene_foto; }).length;
    var sinLogin = rows.filter(function (r) { return !r.tiene_login; }).length;
    var pctTotalTxt = total ? T("admin.reports.reports.pct_del_total", { pct: fmtPct(100 * conAgenda / total) }, "" + fmtPct(100 * conAgenda / total) + " del total") : "";
    return '<div class="rep-kpis">'
      + kpiTile(T("admin.reports.reports.medicos_en_catalogo", null, "Médicos en el catálogo"), fmtInt(total))
      + kpiTile(T("admin.reports.reports.con_agenda_real", null, "Con agenda real"), fmtInt(conAgenda), pctTotalTxt)
      + kpiTile(T("admin.reports.reports.horas_semana_ofrecidas_total", null, "Horas/semana ofrecidas (total)"), fmtHoras(horasTotal))
      + kpiTile(T("admin.reports.reports.ocupacion_14d_total", null, "Ocupación de los próximos 14 días"), ocupTotal == null ? "—" : fmtPct(ocupTotal))
      + kpiTile(T("admin.reports.reports.sin_foto", null, "Sin foto"), fmtInt(sinFoto))
      + kpiTile(T("admin.reports.reports.sin_cuenta_acceso", null, "Sin cuenta de acceso"), fmtInt(sinLogin))
      + "</div>";
  }

  // mediana de turnos-por-hora entre los médicos con agenda Y con turnos
  // aprobados en la ventana (denominador real, nunca dividiendo por 0) — es
  // la diagonal de referencia del scatter; si nadie tiene turnos todavía,
  // se omite (report-design: nunca una línea de referencia inventada).
  function medianaTurnosPorHora(rows) {
    var ratios = rows.filter(function (r) { return r.horas_semana > 0 && r.turnos_14d > 0; })
      .map(function (r) { return r.turnos_14d / r.horas_semana; });
    if (!ratios.length) return null;
    ratios.sort(function (a, b) { return a - b; });
    var n = ratios.length;
    return n % 2 ? ratios[(n - 1) / 2] : (ratios[n / 2 - 1] + ratios[n / 2]) / 2;
  }

  // «Cómo leerlo» of the hours × turnos scatter. How many points sit on the
  // floor (agenda with no approved turno yet) is counted from the rows, never
  // asserted: the old fixed «la mayoría cae en y=0» read false on a full demo
  // (ui-expert 15.11).
  function lecturaAgendaMedicos(conAgenda) {
    var base = T("admin.reports.reports.read_agenda_medicos", null, "Cada punto es un médico con agenda, coloreado por centro.");
    var enCero = conAgenda.filter(function (r) { return !(r.turnos_14d > 0); });
    var ceros = enCero.length;
    if (!ceros) return base;
    var horas = fmtHoras(enCero.reduce(function (a, r) { return a + r.horas_semana; }, 0));
    var vars = { n: fmtInt(ceros), total: fmtInt(conAgenda.length), horas: horas };
    var piso = ceros === conAgenda.length
      ? T("admin.reports.reports.read_agenda_medicos_todos_cero", vars, "Ninguno tiene turnos aprobados todavía en los próximos 14 días, con " + horas + " h semanales ofrecidas: todos quedan sobre el eje horizontal.")
      : T("admin.reports.reports.read_agenda_medicos_ceros", vars, "" + vars.n + " de " + vars.total + ", con " + horas + " h semanales, sin turnos aprobados todavía en los próximos 14 días: quedan sobre el eje horizontal.");
    return base + " " + piso;
  }

  // "Más horas"/"más turnos": dos rankings de una sola dimensión, vetados
  // (report-design ley 3, round 2026-07-22 — "one dimension graphs...are
  // not interesting"). Reemplazo: un scatter horas×turnos coloreado por
  // centro (dónde ya rinde la agenda) + un treemap de horas por
  // especialidad (dónde están las horas ofrecidas).
  function seccionAgendaMedicos(rows) {
    var conAgenda = rows.filter(function (r) { return r.horas_semana > 0; });
    if (!conAgenda.length) return "";
    var porTurnos = conAgenda.slice().sort(function (a, b) { return b.turnos_14d - a.turnos_14d; }).slice(0, 5).map(function (r) { return r.nombre; });
    var porHoras = conAgenda.slice().sort(function (a, b) { return b.horas_semana - a.horas_semana; }).slice(0, 3).map(function (r) { return r.nombre; });
    var conNombre = {};
    porTurnos.concat(porHoras).forEach(function (n) { conNombre[n] = true; });
    var sinCentro = T("admin.reports.reports.sin_centro", null, "Sin centro");
    var puntos = conAgenda.map(function (r) {
      return { x: r.horas_semana, y: r.turnos_14d, etiqueta: conNombre[r.nombre] ? r.nombre : "", grupo: r.centroNombre || sinCentro };
    });
    var mediana = medianaTurnosPorHora(conAgenda);
    var chart = Charts.scatter(puntos, {
      ejeX: T("admin.reports.reports.eje_horas_semanales_ofrecidas", null, "Horas semanales ofrecidas"),
      ejeY: T("admin.reports.reports.eje_turnos_aprobados_14d", null, "Turnos aprobados en 14 días"), agrupar: true,
      linea: mediana ? { tipo: "diagonal", pendiente: mediana, etiqueta: T("admin.reports.reports.linea_mediana_turnos_hora", null, "mediana turnos/hora") } : null,
      read: lecturaAgendaMedicos(conAgenda),
    });
    var topAbsorbe = conAgenda.slice().sort(function (a, b) { return b.turnos_14d - a.turnos_14d; })[0];
    var accion = "";
    if (topAbsorbe && topAbsorbe.turnos_14d > 0) {
      var nTurnosTop = fmtInt(topAbsorbe.turnos_14d);
      var palabraTurno = plural(topAbsorbe.turnos_14d, T("admin.reports.reports.turno_singular", null, "turno"), T("admin.reports.reports.turno_plural", null, "turnos"));
      var horasTop = fmtHoras(topAbsorbe.horas_semana);
      accion = esc(T("admin.reports.reports.accion_top_absorbe", { nombre: topAbsorbe.nombre, n: nTurnosTop, palabra: palabraTurno, horas: horasTop },
        "" + topAbsorbe.nombre + " ya absorbe " + nTurnosTop + " " + palabraTurno + " de los próximos 14 días con " + horasTop + " h semanales."));
    }
    var accionHtml = accion ? '<p class="rep-action">' + accion + "</p>" : "";
    return seccion(T("admin.reports.reports.titulo_donde_rinde_agenda", null, "Dónde ya rinde la agenda"),
      chart + accionHtml);
  }

  function horasPorEspecialidad(rows) {
    var acc = {};
    var sinEspecialidad = T("admin.reports.reports.sin_especialidad", null, "Sin especialidad");
    rows.forEach(function (r) {
      if (!(r.horas_semana > 0)) return;
      var esp = r.especialidad || sinEspecialidad;
      acc[esp] = (acc[esp] || 0) + r.horas_semana;
    });
    return Object.keys(acc).map(function (k) { return { nombre: k, valor: acc[k] }; });
  }

  function seccionHorasEspecialidad(rows) {
    var agregado = horasPorEspecialidad(rows);
    if (!agregado.length) return "";
    // el tile ya imprime su valor: el nombre va limpio, la unidad la pone fmtValor
    var chart = treemapDeVarios(agregado, {
      fmtValor: function (v) { return fmtHoras(v) + " h"; },
      read: T("admin.reports.reports.read_horas_especialidad", null, "El bloque más grande es la especialidad con más horas semanales ofrecidas, sumadas entre todos sus médicos."),
    });
    return seccionOpcional(T("admin.reports.reports.titulo_donde_estan_horas_ofrecidas", null, "Dónde están las horas ofrecidas"), chart);
  }

  function renderMedicos(rows) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataMedicos(rows);
    marcarDisponible("medicos", tiene);
    var html = crumbHtml("medicos");
    if (!tiene) {
      body.innerHTML = html + informeVacioHtml();
      return;
    }
    html += kpisMedicosHtml(rows);
    var cargas = cargaPorMedico(rows);
    html += seccionOpcional(T("admin.reports.reports.titulo_reparto_carga_medicos", null, "Cómo se reparte la carga entre médicos"),
      cargas.length ? Charts.densidad(cargas, {
        fmtX: function (v) { return fmtPct(v); },
        read: T("admin.reports.reports.read_reparto_carga_medicos", null, "La curva es la distribución de ocupación de los próximos 14 días entre los médicos con agenda; la línea vertical marca la mediana."),
      }) : "");
    html += seccionAgendaMedicos(rows);
    html += seccionHorasEspecialidad(rows);
    body.innerHTML = html;
  }

  function cargarMedicos() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchMedicos(false, load.signal).then(function (rows) {
      if (!owns(load.gen)) return null;
      renderMedicos(rows);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Pacientes (?view=pacientes)
  // =====================================================================

  var CACHE_PACIENTES = {};
  var DIAS_PACIENTES = DEFAULT_DIAS;

  function fetchPacientes(dias, force, signal) {
    if (!force && CACHE_PACIENTES[dias]) return Promise.resolve(CACHE_PACIENTES[dias]);
    return fetchJson("/api/admin/reports/patients?days=" + dias, signal).then(function (j) { CACHE_PACIENTES[dias] = j; return j; });
  }

  // The headline figure of Pacientes — ONE function for the overview tile and
  // the first number of the report it opens (critical review 08.11, E7: the
  // tile said 68 and the report opened on 117 under another name). Patients
  // who asked for an appointment in the period; with none yet, the registered
  // accounts (never a 0 that reads as «no data»).
  function figuraPacientes(p) {
    var dias = p.dias || DIAS_PACIENTES;
    var act = p.actividad || {}, k = p.kpis || {};
    if (!(act.pacientes_con_solicitud > 0) && k.total_cuentas > 0) {
      return { cuentas: true, valor: k.total_cuentas, etiqueta: T("admin.reports.reports.cuentas_registradas", null, "Cuentas registradas") };
    }
    var sub = act.promedio_por_paciente == null ? "" : Tn("admin.reports.reports.solicitudes_promedio_cu", fmtDec(act.promedio_por_paciente, 1), null, "{n} solicitud c/u en promedio", "" + fmtDec(act.promedio_por_paciente, 1) + " solicitudes c/u en promedio");
    return {
      cuentas: false, valor: act.pacientes_con_solicitud, sub: sub,
      etiqueta: T("admin.reports.reports.pacientes_pidieron_turno_dias", { dias: dias }, "Pacientes que pidieron turno en " + dias + " días"),
    };
  }

  function kpisPacientesHtml(data) {
    var k = data.kpis, c = data.cobertura, dep = data.dependientes;
    var depSub = fmtInt(dep.adultos_con_dependientes) + " " + plural(dep.adultos_con_dependientes, T("admin.reports.reports.cuenta_singular", null, "cuenta"), T("admin.reports.reports.cuenta_plural", null, "cuentas"));
    var fig = figuraPacientes(data);
    return '<div class="rep-kpis">'
      + kpiTile(fig.etiqueta, fig.valor == null ? "—" : fmtInt(fig.valor), fig.sub)
      + (fig.cuentas ? "" : kpiTile(T("admin.reports.reports.cuentas_registradas", null, "Cuentas registradas"), fmtInt(k.total_cuentas)))
      + kpiTile(T("admin.reports.reports.nuevas_en_dias", { dias: data.dias }, "Nuevas en " + data.dias + " días"), fmtInt(k.nuevas_periodo))
      + kpiTile(T("admin.reports.reports.con_email", null, "Con email"), k.pct_con_email == null ? "—" : fmtPct(k.pct_con_email))
      + kpiTile(T("admin.reports.reports.con_domicilio_cargado", null, "Con domicilio cargado"), k.pct_con_domicilio == null ? "—" : fmtPct(k.pct_con_domicilio))
      + kpiTile(T("admin.reports.reports.con_obra_social", null, "Con obra social"), c.pct_con_obra_social == null ? "—" : fmtPct(c.pct_con_obra_social))
      + kpiTile(T("admin.reports.reports.con_prepaga", null, "Con prepaga"), c.pct_con_prepaga == null ? "—" : fmtPct(c.pct_con_prepaga))
      + kpiTile(T("admin.reports.reports.dependientes_declarados", null, "Dependientes declarados"), fmtInt(dep.total), depSub)
      + "</div>";
  }

  // Edades: pirámide mujeres/varones (round 2026-07-22 — un ranking de
  // barras de una sola dimensión no cuenta nada de la composición). Título
  // de hallazgo: el bucket dominante va en el título (critical review 08.11,
  // E12 — no hay línea gris debajo; la pirámide ya imprime cada conteo).
  function seccionEdades(list) {
    list = list || [];
    if (!list.some(function (r) { return r.cantidad > 0; })) return "";
    var top = list.slice().sort(function (a, b) { return b.cantidad - a.cantidad; })[0];
    var chart = Charts.piramide(list, {
      read: T("admin.reports.reports.read_piramide_edad", null, "Cada fila es un rango etario; mujeres a la izquierda, varones a la derecha, con el conteo impreso sobre cada barra."),
    });
    return seccion(T("admin.reports.reports.titulo_quienes_se_registran", { rango: top.rango },
      "Quiénes se registran: sobre todo de " + top.rango + " años"), chart);
  }

  // Obras sociales: composición (treemap), no un ranking de una dimensión.
  function seccionObrasSociales(list) {
    if (!list || !list.length) return "";
    var items = list.map(function (o) { return { nombre: o.nombre, valor: o.cantidad }; });
    var chart = treemapDeVarios(items, {
      read: T("admin.reports.reports.read_obras_sociales", null, "El bloque más grande es la obra social o prepaga más frecuente entre las cuentas registradas."),
    });
    return seccionOpcional(T("admin.reports.reports.titulo_obras_sociales_frecuentes", null, "Obras sociales más frecuentes"), chart);
  }

  function renderPacientes(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataPacientes(data);
    marcarDisponible("pacientes", tiene);
    var html = crumbHtml("pacientes");
    html += '<div class="rep-controls">' + periodoControlHtml("pac-periodo", DIAS_PACIENTES) + "</div>";
    if (!tiene) {
      html += informeVacioHtml();
    } else {
      html += kpisPacientesHtml(data);
      html += seccionOpcional(T("admin.reports.reports.titulo_cuentas_nuevas_por_dia", null, "Cuentas nuevas por día"),
        chartBarrasDiarias(data.volumen, function (r) { return r.total; }, function (r) {
          var palabra = plural(r.total, T("admin.reports.reports.cuenta_nueva_singular", null, "cuenta nueva"), T("admin.reports.reports.cuenta_nueva_plural", null, "cuentas nuevas"));
          return fechaLabel(r.fecha) + ": " + r.total + " " + palabra;
        }));
      html += seccionEdades(data.edades);
      html += seccionObrasSociales((data.cobertura || {}).top_obras_sociales);
    }
    body.innerHTML = html;

    var sel = $("#pac-periodo", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_PACIENTES = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      cargarPacientes();
    });
  }

  function cargarPacientes() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchPacientes(DIAS_PACIENTES, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderPacientes(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Búsquedas (?view=busquedas, 2026-07-22) — antes eran dos
  // tablas al fondo de Gestión ("Lo que más buscan los pacientes" / "Lo
  // que la gente busca y el catálogo no nombra"). Ahora tienen su propia
  // página, y las búsquedas se CLUSTERIZAN: "card"/"cardio"/"cardiologia"
  // son la MISMA intención, se muestran como una fila con el mejor match
  // del catálogo. El endpoint (/api/admin/reports/searches) sólo da el log
  // crudo — el matching corre acá con el matcher de PRODUCCIÓN
  // (Search de platform/search.mjs, awaited in ready) contra
  // /api/services + /api/doctors, los mismos que arma la búsqueda de la
  // home (mismos getCampos que index.html — ver clusterizarBusquedas).
  // =====================================================================

  var CACHE_BUSQUEDAS = {};
  var DIAS_BUSQUEDAS = DEFAULT_DIAS;
  var CACHE_CATALOGO_BUSQUEDAS = null;  // no depende del período: servicios+médicos+centros vivos

  function fetchBusquedas(dias, force, signal) {
    if (!force && CACHE_BUSQUEDAS[dias]) return Promise.resolve(CACHE_BUSQUEDAS[dias]);
    return fetchJson("/api/admin/reports/searches?days=" + dias, signal).then(function (j) { CACHE_BUSQUEDAS[dias] = j; return j; });
  }

  function fetchCatalogoBusquedas(force, signal) {
    if (!force && CACHE_CATALOGO_BUSQUEDAS) return Promise.resolve(CACHE_CATALOGO_BUSQUEDAS);
    return Promise.all([
      fetchJson("/api/services", signal),
      fetchJson("/api/doctors", signal),
      fetchJson("/api/centers", signal),
    ]).then(function (res) {
      var centrosBySlug = {};
      (res[2].centros || []).forEach(function (c) { if (c.slug) centrosBySlug[c.slug] = c; });
      CACHE_CATALOGO_BUSQUEDAS = { servicios: res[0].servicios || [], medicos: res[1].medicos || [], centrosBySlug: centrosBySlug };
      return CACHE_CATALOGO_BUSQUEDAS;
    });
  }

  // getCampos IDÉNTICOS a los de index.html (búsqueda de la home) — si acá
  // divergieran, el "mejor match" del reporte podría no ser el que el
  // paciente realmente ve primero al escribir esa misma búsqueda.
  function camposServicioBusqueda(centrosBySlug) {
    return function (s) {
      var c = centrosBySlug[s.centro] || {};
      return { principal: [s.nombre], resto: [s.categoria, c.name, s.sede, s.horario].concat(s.palabras || []) };
    };
  }
  function camposMedicoBusqueda(m) {
    return { principal: [m.nombre], resto: [m.especialidad, m.centro].concat(m.palabras || []) };
  }

  // Mejor entidad para UNA query: compara el mejor servicio contra el mejor
  // médico (Search.buscarConPuntaje expone el puntaje de cada uno — menor
  // = más relevante) y gana el de menor puntaje; empate va al servicio (responsable del producto:
  // "tie goes to the service"). null si ninguno matchea (grupo sin match).
  function mejorEntidadBusqueda(q, catalogo) {
    var sv = Search.buscarConPuntaje(catalogo.servicios, q, camposServicioBusqueda(catalogo.centrosBySlug));
    var md = Search.buscarConPuntaje(catalogo.medicos, q, camposMedicoBusqueda);
    var topS = sv[0], topM = md[0];
    if (!topS && !topM) return null;
    if (!topM || (topS && topS.score <= topM.score)) return { tipo: "servicio", row: topS.row };
    return { tipo: "medico", row: topM.row };
  }

  // La ÚNICA función de clustering (la reusa el sub-reporte completo Y la
  // tarjeta de Resumen — report-design: "reuse the same clustering
  // function, don't fork it"). Cluster = todas las variantes de búsqueda
  // (q distintos) que resolvieron a la MISMA entidad del catálogo. Devuelve
  // las tres intenciones de servicio + "personas" (médicos) + el grupo sin
  // match (reemplaza a la vieja tabla "catálogo no nombra").
  function clusterizarBusquedas(top, catalogo) {
    var porEntidad = {}, sinMatch = [];
    (top || []).forEach(function (r) {
      var mejor = mejorEntidadBusqueda(r.q, catalogo);
      if (!mejor) { sinMatch.push({ q: r.q, veces: r.veces }); return; }
      var key = mejor.tipo === "servicio" ? "servicio:" + mejor.row.id : "medico:" + (mejor.row.href || mejor.row.nombre);
      if (!porEntidad[key]) porEntidad[key] = { tipo: mejor.tipo, row: mejor.row, variantes: [], total: 0 };
      porEntidad[key].variantes.push({ q: r.q, veces: r.veces });
      porEntidad[key].total += r.veces;
    });
    var especialidades = [], estudios = [], personas = [];
    Object.keys(porEntidad).forEach(function (k) {
      var c = porEntidad[k];
      c.variantes.sort(function (a, b) { return b.veces - a.veces; });
      if (c.tipo === "medico") personas.push(c);
      else if (c.row.cluster === "diagnostico") estudios.push(c);
      else especialidades.push(c);
    });
    var porTotalDesc = function (a, b) { return b.total - a.total; };
    especialidades.sort(porTotalDesc);
    estudios.sort(porTotalDesc);
    personas.sort(porTotalDesc);
    sinMatch.sort(function (a, b) { return b.veces - a.veces; });
    return { especialidades: especialidades, estudios: estudios, personas: personas, sinMatch: sinMatch };
  }

  function heroBusquedas(top, agrupado) {
    var totalVeces = (top || []).reduce(function (a, r) { return a + r.veces; }, 0);
    if (!totalVeces) return [];
    var frases = [];
    var todos = agrupado.especialidades.concat(agrupado.estudios, agrupado.personas);
    if (todos.length) {
      var topCluster = todos.slice().sort(function (a, b) { return b.total - a.total; })[0];
      var nBusquedas = fmtInt(totalVeces), palabraBusq1 = plural(totalVeces, T("admin.reports.reports.busqueda_singular", null, "búsqueda"), T("admin.reports.reports.busqueda_plural", null, "búsquedas"));
      var nTopBusquedas = fmtInt(topCluster.total), palabraBusq2 = plural(topCluster.total, T("admin.reports.reports.busqueda_singular", null, "búsqueda"), T("admin.reports.reports.busqueda_plural", null, "búsquedas"));
      frases.push({
        texto: T("admin.reports.reports.hero_busquedas_top", { n: nBusquedas, palabra1: palabraBusq1, nombre: topCluster.row.nombre, nTop: nTopBusquedas, palabra2: palabraBusq2 },
          "En el período se registraron " + nBusquedas + " " + palabraBusq1 + "; lo más buscado fue " + topCluster.row.nombre + ", con " + nTopBusquedas + " " + palabraBusq2 + "."),
        tono: "ok",
      });
    }
    if (agrupado.sinMatch.length) {
      var sinMatchVeces = agrupado.sinMatch.reduce(function (a, r) { return a + r.veces; }, 0);
      var nSinMatch = fmtInt(agrupado.sinMatch.length);
      var palabraSinMatch = plural(agrupado.sinMatch.length, T("admin.reports.reports.busqueda_no_encontro_singular", null, "búsqueda distinta no encontró"), T("admin.reports.reports.busqueda_no_encontro_plural", null, "búsquedas distintas no encontraron"));
      var nVeces = fmtInt(sinMatchVeces), palabraVez = plural(sinMatchVeces, T("admin.reports.reports.vez_singular", null, "vez"), T("admin.reports.reports.vez_plural", null, "veces"));
      frases.push({
        texto: T("admin.reports.reports.hero_busquedas_sin_match", { n: nSinMatch, palabra: palabraSinMatch, veces: nVeces, palabraVez: palabraVez },
          "" + nSinMatch + " " + palabraSinMatch + " nada en el catálogo (" + nVeces + " " + palabraVez
          + " en total): sumarlas como palabras clave del servicio correspondiente evita que la próxima persona golpee una pared."),
        tono: "atencion",
      });
    }
    return frases;
  }

  function filaClusterBusquedaHtml(c) {
    var variantes = c.variantes.map(function (v) { return esc(v.q) + " ×" + fmtInt(v.veces); }).join(" · ");
    var href = c.tipo === "servicio" ? "/service/" + c.row.id : c.row.href;
    var nombre = href ? '<a href="' + esc(href) + '">' + esc(c.row.nombre) + "</a>" : esc(c.row.nombre);
    return "<tr><td>" + nombre + "</td><td>" + variantes + '</td><td class="cnt-numeric">' + fmtInt(c.total) + "</td></tr>";
  }

  // A report table's heading with its own «Descargar»: the file holds that one
  // table (the tables of a tab have different columns), named «Reportes ·
  // <heading>». Without the table engine the heading stays bare.
  var tablaSeq = 0;
  function tablaAbre(clase, titulo) {
    var id = "rep-tabla-" + (++tablaSeq);
    var boton = Table && Table.exportHtml ? Table.exportHtml({ scope: "#" + id, titlePart: titulo }) : "";
    return '<div class="' + clase + '" id="' + id + '"><div class="rep-table-head"><h2 class="rep-h2">' + esc(titulo) + "</h2>" + boton + "</div>";
  }

  function tablaClustersBusqueda(titulo, clusters) {
    if (!clusters.length) return "";
    var body = clusters.map(filaClusterBusquedaHtml).join("");
    return tablaAbre("rep-section rep-busquedas-table", titulo) + '<div class="cnt-table-wrap">'
      + '<table class="cnt-table"><thead><tr><th class="cnt-th">' + esc(T("admin.reports.reports.th_coincidencia", null, "Coincidencia")) + '</th>'
      + '<th class="cnt-th">' + esc(T("admin.reports.reports.th_como_lo_buscaron", null, "Cómo lo buscaron")) + '</th>'
      + '<th class="cnt-th cnt-numeric">' + esc(T("admin.reports.reports.th_veces", null, "Veces")) + '</th></tr></thead>'
      + "<tbody>" + body + "</tbody></table></div></div>";
  }

  function tablaSinMatchBusqueda(rows) {
    var titulo = T("admin.reports.reports.titulo_catalogo_no_nombra", null, "Lo que la gente busca y el catálogo no nombra");
    if (!rows.length) return "";
    var body = rows.map(function (r) { return "<tr><td>" + esc(r.q) + '</td><td class="cnt-numeric">' + fmtInt(r.veces) + "</td></tr>"; }).join("");
    return tablaAbre("rep-section rep-busquedas-table", titulo) + '<div class="cnt-table-wrap">'
      + '<table class="cnt-table"><thead><tr><th class="cnt-th">' + esc(T("admin.reports.reports.th_busqueda", null, "Búsqueda")) + '</th>'
      + '<th class="cnt-th">' + esc(T("admin.reports.reports.th_veces", null, "Veces")) + '</th></tr></thead>'
      + "<tbody>" + body + "</tbody></table></div></div>";
  }

  function renderBusquedas(data, catalogo) {
    var body = $("#rep-body");
    var top = data.top || [];
    var tiene = hasDataBusquedas(data, catalogo);
    marcarDisponible("busquedas", tiene);
    if (!tiene) {
      body.innerHTML = crumbHtml("busquedas")
        + '<div class="rep-controls">' + periodoControlHtml("busq-periodo", DIAS_BUSQUEDAS) + "</div>"
        + '<p class="rep-empty">' + esc(T("admin.reports.reports.sin_datos_busquedas", null, "Sin datos todavía: el registro de búsquedas arrancó el 2026-07-16.")) + '</p>';
      var selVacio = $("#busq-periodo", body);
      selVacio.addEventListener("change", function () {
        if (!alive() || !ready) return;
        var d = parseInt(selVacio.value, 10);
        DIAS_BUSQUEDAS = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
        cargarBusquedas();
      });
      return;
    }
    var agrupado = clusterizarBusquedas(top, catalogo);
    var html = crumbHtml("busquedas");
    html += Charts.hero(heroBusquedas(top, agrupado));
    html += '<div class="rep-controls">' + periodoControlHtml("busq-periodo", DIAS_BUSQUEDAS) + "</div>";
    html += tablaClustersBusqueda(T("admin.reports.reports.titulo_especialidades_y_servicios", null, "Especialidades y servicios"), agrupado.especialidades);
    html += tablaClustersBusqueda(T("admin.reports.reports.titulo_estudios_y_analisis", null, "Estudios y análisis"), agrupado.estudios);
    html += tablaClustersBusqueda(T("admin.reports.reports.titulo_personas", null, "Personas"), agrupado.personas);
    html += tablaSinMatchBusqueda(agrupado.sinMatch);
    body.innerHTML = html;

    var sel = $("#busq-periodo", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_BUSQUEDAS = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      cargarBusquedas();
    });
  }

  function cargarBusquedas() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return Promise.all([fetchBusquedas(DIAS_BUSQUEDAS, false, load.signal), fetchCatalogoBusquedas(false, load.signal)])
      .then(function (res) {
        if (!owns(load.gen)) return null;
        renderBusquedas(res[0], res[1]);
      })
      .catch(function (e) {
        if (!owns(load.gen)) return null;
        if (String(e && e.message) === "redirect") return null;
        $("#rep-body").innerHTML = bodyErrorHtml(e);
      });
  }

  // =====================================================================
  // sub-reporte: Centros (?view=centros) — espejo gatto-nero del análisis
  // de reseñas de Google. GET /api/admin/reports/reviews, SIN período (ver
  // el contrato al tope de reports.py). Ángulo editorial (report-design
  // SKILL.md): el personal es el héroe de los datos, la espera de guardia
  // se enmarca como señal de triage/carga, nunca como una falla de
  // atención — y ningún bloque acá es una tarjeta de testimonio con borde
  // de acento IZQUIERDO (ui-rules): la "tarjeta de cita" usa un fondo
  // tenue + una comilla grande decorativa, nunca una franja de color al
  // costado. Rework completo 2026-07-22 (ver el clásico retirado).
  // =====================================================================

  var CACHE_CENTROS = null;

  function fetchCentros(force, signal) {
    if (!force && CACHE_CENTROS) return Promise.resolve(CACHE_CENTROS);
    return fetchJson("/api/admin/reports/reviews", signal).then(function (j) { CACHE_CENTROS = j; return j; });
  }

  function slugTitulo(slug) {
    return String(slug || "").replace(/-/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  // Escala de sentimiento continua (rating_prom -> color de tile), 3 paradas
  // apagadas/DS-compatibles — nunca semáforo saturado: terracota apagado en
  // lo negativo, ámbar apagado en el medio, verde salvia apagado en lo
  // positivo (la misma familia de verde que --done del tema "humanrounds").
  // El rating_prom que llega es maquinaria interna del análisis (ley 14,
  // report-design: ninguna estrella se imprime) — acá sólo decide el color
  // del tile, nunca un texto. Interpola LOW↔MID entre 2 y 3, MID↔HIGH entre
  // 3 y 4 (así 2,5 sale rojo-anaranjado y 3,5 sale amarillo-verdoso, ambos
  // a mitad de camino) y se aplana en los extremos: <=2 rojo puro, >=4
  // verde puro. rating_prom null (p.ej. un tema sin lecturas de sentimiento
  // propio) cae al gris neutro, nunca a un color inventado.
  var SENT_LOW = [178, 94, 71];    // terracota apagado
  var SENT_MID = [196, 158, 74];   // ámbar apagado
  var SENT_HIGH = [111, 153, 89];  // verde salvia apagado
  var SENT_NEUTRAL = "rgb(158,160,150)";

  function mezclaRgb(a, b, t) {
    return [0, 1, 2].map(function (i) { return Math.round(a[i] + (b[i] - a[i]) * t); });
  }

  function sentimentColor(rating) {
    if (rating == null || !isFinite(rating)) return SENT_NEUTRAL;
    var from, to, t;
    if (rating <= 3) { from = SENT_LOW; to = SENT_MID; t = rating - 2; }
    else { from = SENT_MID; to = SENT_HIGH; t = rating - 3; }
    t = Math.max(0, Math.min(1, t));
    return "rgb(" + mezclaRgb(from, to, t).join(",") + ")";
  }

  // Treemap de temas coloreado por sentimiento (2026-07-22): el tamaño del
  // tile sigue siendo cuánto se habla de un tema (n menciones), pero el
  // COLOR es cómo se habla de él (avg_rating de esas menciones vía
  // sentimentColor). "Personal nombrado por su nombre" vive DENTRO de
  // c.themes y se excluye acá a propósito: tiene su propia sección de
  // reconocimiento más abajo (personalNombradoHtml) y no compite por
  // espacio en el treemap.
  function temasTreemapHtml(temas) {
    var keys = Object.keys(temas || {}).filter(function (k) { return k !== "personal_nombrado"; });
    if (!keys.length) return "";
    var items = keys.map(function (k) {
      var t = temas[k];
      return { nombre: t.name, valor: t.n, color: sentimentColor(t.avg_rating) };
    });
    return treemapDeVarios(items, {
      read: T("admin.reports.reports.read_temas_treemap", null, "El bloque más grande es el tema más mencionado; el color va de verde (elogian) a terracota (se quejan) según cómo califican esas reseñas ese tema."),
    });
  }

  // Sección de reconocimiento al personal (2026-07-22): tono cálido — es un
  // agradecimiento, nunca una evaluación (doctrina: sympathy to staff). Sin
  // cita no hay tarjeta: un conteo aislado no explica el reconocimiento.
  function personalNombradoHtml(lista) {
    var items = (lista || []).filter(function (p) { return !!p.quote; })
      .slice().sort(function (a, b) { return b.n - a.n; }).slice(0, 6);
    if (!items.length) return "";
    var cards = items.map(function (p) {
      return '<div class="rep-staff-card">'
        + '<div class="rep-staff-name">' + esc(p.name) + "</div>"
        + '<p class="rep-staff-quote">“' + esc(p.quote) + '”</p></div>';
    }).join("");
    return '<div class="rep-staff"><h3 class="rep-staff-h">' + esc(T("admin.reports.reports.reconocidos_por_su_nombre", null, "Reconocidos por su nombre")) + '</h3>'
      + '<div class="rep-staff-grid">' + cards + "</div></div>";
  }

  // Un ícono por tema de triage (2026-07-22: El responsable del producto pidió una marca por tema,
  // sólo en el subtítulo; 2026-10-21: íconos Icons8 sf-regular del pack del
  // proyecto en lugar de emojis — la regla de íconos manda en toda pantalla).
  // Máscara en la tinta del texto, 18px, PNG a 2x. Fallback neutro para
  // cualquier tema futuro que el backend agregue y todavía no esté mapeado.
  var TRIAGE_ICON = {
    "Estudios y diagnóstico": "test-tube",
    "Turnos y acceso telefónico": "phone",
    "Espera en guardia": "stopwatch",
    "Limpieza e instalaciones": "broom",
    "Trato del personal": "handshake",
    "Pediatría y vacunas": "stroller",
    "Resolución del problema": "checked",
  };
  var TRIAGE_GROUP_ICON = {
    "Para aliviar al equipo": "lightning",
    "Se decide no perseguir": "compass",
  };
  function triageIconHtml(name) {
    var url = "url(/static/icons/report-" + (name || "clipboard") + "-sf-regular.png)";
    return '<span class="rep-triage-ico" aria-hidden="true" style="-webkit-mask-image:' + url + ";mask-image:" + url + '"></span>';
  }
  // Claves literales en español: son las MISMAS strings que se pasan a
  // grupo() más abajo, usadas para el lookup de emoji arriba — el mapeo a
  // key de i18n vive separado para no congelar el texto traducido a nivel de
  // módulo (mismo bug pattern que PERFIL_CAMPOS en admin-consulta.js).
  var TRIAGE_GROUP_I18N = {
    "Para aliviar al equipo": "admin.reports.reports.grupo_aliviar_equipo",
    "Se decide no perseguir": "admin.reports.reports.grupo_decide_no_perseguir",
  };

  // Triage a una sola columna de texto corrido (2026-07-22): cada tema es
  // un subtítulo más grande que el cuerpo (ícono + nombre) seguido de un
  // párrafo con la acción — el h3 de grupo sigue marcando, pero deja de ser
  // una columna con su propia lista.
  function triageTopicHtml(item) {
    return '<div class="rep-triage-topic">'
      + '<h4 class="rep-triage-topic-h">' + triageIconHtml(TRIAGE_ICON[item.topic]) + esc(item.topic) + "</h4>"
      + '<p class="rep-triage-p">' + esc(item.action) + "</p>"
      + "</div>";
  }
  function triageHtml(triage) {
    triage = triage || {};
    var arreglar = triage.fix || [], declinar = triage.decline || [];
    if (!arreglar.length && !declinar.length) return "";
    function grupo(tituloEs, items) {
      if (!items.length) return "";
      var titulo = T(TRIAGE_GROUP_I18N[tituloEs] || "", null, tituloEs);
      return '<h3 class="rep-triage-grupo">' + triageIconHtml(TRIAGE_GROUP_ICON[tituloEs])
        + esc(titulo) + "</h3>" + items.map(triageTopicHtml).join("");
    }
    return '<div class="rep-triage">' + grupo("Para aliviar al equipo", arreglar) + grupo("Se decide no perseguir", declinar) + "</div>";
  }

  // Hasta 2 citas verbatim: la de elogio al personal primero (fuente
  // "analisis", categoria "trato"), después la de mayor rating que quede.
  // Fallback "curado" (2026-07-10) sólo trae c.quotes plano.
  function mejoresQuotes(c) {
    var candidatos = [];
    if (c.themes) {
      Object.keys(c.themes).forEach(function (k) {
        var t = c.themes[k];
        (t.quotes || []).forEach(function (q) { candidatos.push({ text: q.text, rating: q.rating, staff: t.category === "trato" }); });
      });
    } else if (c.quotes) {
      c.quotes.forEach(function (q) { candidatos.push({ text: q.text, rating: q.rating, staff: false }); });
    }
    var vistos = {}, unicos = [];
    candidatos.forEach(function (q) { if (q.text && !vistos[q.text]) { vistos[q.text] = true; unicos.push(q); } });
    unicos.sort(function (a, b) {
      if (!!a.staff !== !!b.staff) return a.staff ? -1 : 1;
      return (b.rating || 0) - (a.rating || 0);
    });
    return unicos.slice(0, 2);
  }

  // Ley 14 (report-design, round 2026-07-22): ninguna estrella visible en
  // ningún lado del reporte — el rating sigue viviendo en el dato (q.rating)
  // sólo para ordenar candidatos en mejoresQuotes(), nunca para imprimirse.
  function quotesHtml(c) {
    var qs = mejoresQuotes(c);
    if (!qs.length) return "";
    return qs.map(function (q) {
      return '<p class="rep-quote">“' + esc(q.text) + '”</p>';
    }).join("");
  }

  // Orden de la tarjeta (2026-07-22, revisión del responsable del producto): encabezado, frase de
  // hallazgo, treemap de temas, reconocimiento al personal, triage, citas.
  function centroCardHtml(slug, c) {
    var nombre = c.name || slugTitulo(slug);
    var count = c.count != null ? fmtInt(c.count) : "—";
    var head = '<div class="rep-centro-head">'
      + '<div><h2 class="rep-centro-title">' + esc(nombre) + '</h2>'
      + '<span class="rep-centro-rating">' + esc(Tn("admin.reports.reports.n_resenas", c.count, { n: count }, "{n} reseña", "" + count + " reseñas")) + "</span></div>"
      + (c.maps ? '<a class="rep-csv-link" href="' + esc(c.maps) + '" target="_blank" rel="noopener">' + esc(T("admin.reports.reports.ver_en_google", null, "Ver en Google")) + '</a>' : "")
      + "</div>";
    // The finding phrase reads as text, not as a gray subtitle (E12).
    var heroLine = c.hero && c.hero[0] ? '<p class="rep-centro-hero">' + esc(c.hero[0]) + "</p>" : "";
    return '<div class="rep-centro-card">' + head + heroLine
      + temasTreemapHtml(c.themes)
      + personalNombradoHtml(c.named_staff)
      + triageHtml(c.triage)
      + quotesHtml(c)
      + "</div>";
  }

  function renderCentros(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var centros = data.centers || {};
    var slugs = Object.keys(centros).sort(function (a, b) { return (centros[b].count || 0) - (centros[a].count || 0); });
    var tiene = hasDataCentros(data);
    marcarDisponible("centros", tiene);
    if (!tiene) {
      body.innerHTML = crumbHtml("centros") + informeVacioHtml(T("admin.reports.reports.sin_analisis_resenas", null, "Todavía no hay análisis de reseñas cargado."));
      return;
    }
    var heroFrases = ((data.comparison && data.comparison.hero) || []).map(function (t, i) {
      return { texto: t, tono: i === 1 ? "atencion" : "ok" };
    });
    var html = crumbHtml("centros") + Charts.hero(heroFrases);
    html += '<div class="rep-centros-grid">' + slugs.map(function (s) { return centroCardHtml(s, centros[s]); }).join("") + "</div>";
    body.innerHTML = html;
  }

  function cargarCentros() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchCentros(false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderCentros(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Recupero de costos (?view=recupero, 2026-07-24) —
  // consultas realizadas con obra social declarada, cruzadas contra
  // usuarios.cobertura (reports.py:_recupero). La página se queda en
  // agregados por obra social (report-design ley 12); el CSV hermano lleva
  // dni/nombre/nro de afiliado a propósito — es el handoff hacia la oficina
  // de facturación, nunca el reporte en pantalla.
  // =====================================================================

  var CACHE_RECUPERO = {};
  var DIAS_RECUPERO = _DEFAULT_DIAS_CSV;

  function fetchRecupero(dias, force, signal) {
    if (!force && CACHE_RECUPERO[dias]) return Promise.resolve(CACHE_RECUPERO[dias]);
    return fetchJson("/api/admin/reports/cost-recovery?days=" + dias, signal).then(function (j) { CACHE_RECUPERO[dias] = j; return j; });
  }

  function kpisRecuperoHtml(k) {
    var tiles = "";
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.consultas_con_cobertura", null, "Consultas con cobertura"), valor: fmtInt(k.consultas_con_cobertura) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.pacientes_distintos", null, "Pacientes distintos"), valor: fmtInt(k.pacientes_con_cobertura) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.se_presentaron", null, "Se presentaron"), valor: fmtInt(k.presentes) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.no_se_presentaron", null, "No se presentaron"), valor: fmtInt(k.ausentes) });
    return '<div class="rep-tiles">' + tiles + "</div>";
  }

  // Composición (treemap, nunca un ranking de barras) + una lista de apoyo con
  // los conteos exactos — el treemap sólo comunica proporción, la lista da el
  // número que la oficina de facturación necesita citar.
  function seccionPorObraSocial(list) {
    if (!list || !list.length) return "";
    var items = list.map(function (o) { return { nombre: o.nombre, valor: o.consultas }; });
    var chart = treemapDeVarios(items, {
      valoresEnLista: true, // the list right under it carries every count
      read: T("admin.reports.reports.read_obra_social_recupero", null, "El bloque más grande es la obra social con más consultas para gestionar el recupero en el período."),
    });
    var listado = '<ul class="rep-action-list">' + list.map(function (o) {
      var nConsultas = fmtInt(o.consultas), palabraConsulta = plural(o.consultas, T("admin.reports.reports.consulta_singular", null, "consulta"), T("admin.reports.reports.consulta_plural", null, "consultas"));
      var nPacientes = fmtInt(o.pacientes), palabraPaciente = plural(o.pacientes, T("admin.reports.reports.paciente_distinto_singular", null, "paciente distinto"), T("admin.reports.reports.paciente_distinto_plural", null, "pacientes distintos"));
      return "<li>" + esc(o.nombre) + ": " + nConsultas + " " + palabraConsulta + " · " + nPacientes + " " + palabraPaciente + "</li>";
    }).join("") + "</ul>";
    return seccion(T("admin.reports.reports.titulo_x_concentra_mayor_volumen_recupero", { nombre: list[0].nombre }, "" + list[0].nombre + " concentra el mayor volumen para el recupero"),
      chart + listado);
  }

  function seccionTendenciaRecupero(list) {
    if (!list || !list.length) return "";
    var puntos = list.map(function (r, i) { return { x: i, etiqueta: fechaLabel(r.semana), y: r.consultas }; });
    var chart = Charts.linea([{ nombre: T("admin.reports.reports.serie_consultas_con_cobertura", null, "Consultas con cobertura"), puntos: puntos }],
      { read: T("admin.reports.reports.read_tendencia_recupero", null, "Consultas facturables por semana: sólo las que tienen alguna obra social declarada.") });
    return seccion(T("admin.reports.reports.titulo_evolucion_volumen_facturable", null, "Cómo evoluciona el volumen facturable"), chart);
  }

  function renderRecupero(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataRecupero(data);
    marcarDisponible("recupero", tiene);
    var html = crumbHtml("recupero");
    if (tiene) html += Charts.hero(data.hero);
    html += '<div class="rep-controls">' + periodoControlHtml("rec-periodo", DIAS_RECUPERO)
      + (tiene ? '<a class="rep-csv-link" href="/api/admin/reports/cost-recovery.csv?days=' + data.dias
        + '">' + esc(T("admin.reports.reports.descargar_csv_facturacion", null, "Descargar CSV para facturación (con datos personales)")) + '</a>' : "")
      + "</div>";
    if (!tiene) html += informeVacioHtml();
    else {
      html += kpisRecuperoHtml(data.kpis);
      html += seccionPorObraSocial(data.por_obra_social);
      html += seccionTendenciaRecupero(data.tendencia);
    }
    if (tiene && data.sin_dato) {
      var nSinDato = fmtInt(data.sin_dato);
      var palabraSinDato = plural(data.sin_dato, T("admin.reports.reports.consulta_no_tiene_singular", null, "consulta no tiene"), T("admin.reports.reports.consulta_no_tiene_plural", null, "consultas no tienen"));
      html += '<p class="rep-quiet">' + esc(T("admin.reports.reports.sin_obra_social_declarada_periodo", { n: nSinDato, palabra: palabraSinDato },
        "" + nSinDato + " " + palabraSinDato + " ninguna obra social declarada en este período (incluye prepaga sola y cuentas sin cobertura cargada).")) + "</p>";
    }
    body.innerHTML = html;

    var sel = $("#rec-periodo", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_RECUPERO = PERIODOS.indexOf(d) !== -1 ? d : _DEFAULT_DIAS_CSV;
      cargarRecupero();
    });
  }

  function cargarRecupero() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchRecupero(DIAS_RECUPERO, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderRecupero(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: SUMAR+ (?view=sumar, 2026-07-24) — prestaciones a
  // población con cobertura EXCLUSIVAMENTE pública (reports.py:_sumar),
  // desglosadas en las trazadoras del programa (embarazo, niño sano, otras).
  // Mismo carve-out de PII que Recupero: la página es agregada, el CSV lleva
  // identidad para dar de alta a cada paciente en el programa.
  // =====================================================================

  var CACHE_SUMAR = {};
  var DIAS_SUMAR = _DEFAULT_DIAS_CSV;

  function fetchSumar(dias, force, signal) {
    if (!force && CACHE_SUMAR[dias]) return Promise.resolve(CACHE_SUMAR[dias]);
    return fetchJson("/api/admin/reports/sumar?days=" + dias, signal).then(function (j) { CACHE_SUMAR[dias] = j; return j; });
  }

  function kpisSumarHtml(k) {
    var tiles = "";
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.prestaciones", null, "Prestaciones"), valor: fmtInt(k.prestaciones) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.pacientes_distintos", null, "Pacientes distintos"), valor: fmtInt(k.pacientes) });
    return '<div class="rep-tiles">' + tiles + "</div>";
  }

  function seccionTrazadoras(list) {
    var items = (list || []).filter(function (t) { return t.consultas > 0; }).map(function (t) { return { nombre: t.label, valor: t.consultas }; });
    if (!items.length) return "";
    var chart = treemapDeVarios(items, {
      read: T("admin.reports.reports.read_trazadoras", null, "El bloque más grande es la trazadora con más prestaciones: embarazo, niño sano u otras."),
    });
    var top = items.slice().sort(function (a, b) { return b.valor - a.valor; })[0];
    return seccion(T("admin.reports.reports.titulo_x_concentra_prestaciones_sumar", { nombre: top.nombre }, "" + top.nombre + " concentra la mayor parte de las prestaciones SUMAR+"), chart);
  }

  function seccionTendenciaSumar(list) {
    if (!list || !list.length) return "";
    var puntos = list.map(function (r, i) { return { x: i, etiqueta: fechaLabel(r.semana), y: r.consultas }; });
    var chart = Charts.linea([{ nombre: T("admin.reports.reports.serie_prestaciones_sumar", null, "Prestaciones SUMAR+"), puntos: puntos }],
      { read: T("admin.reports.reports.read_tendencia_sumar", null, "Prestaciones semanales a población con cobertura exclusivamente pública.") });
    return seccion(T("admin.reports.reports.titulo_evolucion_demanda_programa", null, "Cómo evoluciona la demanda del programa"), chart);
  }

  function renderSumar(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataSumar(data);
    marcarDisponible("sumar", tiene);
    var html = crumbHtml("sumar");
    if (tiene) html += Charts.hero(data.hero);
    html += '<div class="rep-controls">' + periodoControlHtml("sum-periodo", DIAS_SUMAR)
      + (tiene ? '<a class="rep-csv-link" href="/api/admin/reports/sumar.csv?days=' + data.dias
        + '">' + esc(T("admin.reports.reports.descargar_csv_sumar", null, "Descargar CSV para SUMAR+ (con datos personales)")) + '</a>' : "")
      + "</div>";
    if (!tiene) html += informeVacioHtml();
    else {
      html += kpisSumarHtml(data.kpis);
      html += seccionTrazadoras(data.trazadoras);
      html += seccionTendenciaSumar(data.tendencia);
    }
    body.innerHTML = html;

    var sel = $("#sum-periodo", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_SUMAR = PERIODOS.indexOf(d) !== -1 ? d : _DEFAULT_DIAS_CSV;
      cargarSumar();
    });
  }

  function cargarSumar() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchSumar(DIAS_SUMAR, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderSumar(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Estadística mensual (?view=estadistica, 2026-07-24) —
  // estilo C2, un mes calendario completo por vez (reports.py:_estadistica).
  // Selector de MES (no de días) — mesesOpciones() calcula los últimos 12
  // meses calendario completos en el reloj del navegador DENTRO de cada
  // mount (nunca a nivel de módulo: congelaría el reloj del import); el
  // valor seleccionado siempre se sincroniza con el `mes` que devuelve el
  // backend (que decide en hora ART) para que selector y datos nunca
  // divergan. Sin PII en ningún lado (ni la página ni el CSV).
  // =====================================================================

  // Nombres de mes: NUNCA guardar el string ya traducido en un array a nivel
  // de módulo (se congelaría al idioma vigente cuando el archivo se cargó,
  // mismo bug pattern que PERFIL_CAMPOS en admin-consulta.js) — mesNombre()
  // resuelve la traducción en cada llamada, dentro de mesesOpciones()/
  // mesLabelDe(), que corren en cada render.
  var MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
    "Septiembre", "Octubre", "Noviembre", "Diciembre"];
  // Keys completas como literales (nunca concatenadas en runtime): así
  // aparecen enteras en el source para cualquier barrido automático que arme
  // el catálogo de locales/, mismo criterio que SUBS más abajo.
  var MESES_KEYS = [
    "admin.reports.reports.mes_enero", "admin.reports.reports.mes_febrero", "admin.reports.reports.mes_marzo",
    "admin.reports.reports.mes_abril", "admin.reports.reports.mes_mayo", "admin.reports.reports.mes_junio",
    "admin.reports.reports.mes_julio", "admin.reports.reports.mes_agosto", "admin.reports.reports.mes_septiembre",
    "admin.reports.reports.mes_octubre", "admin.reports.reports.mes_noviembre", "admin.reports.reports.mes_diciembre",
  ];

  function mesNombre(idx) {
    return T(MESES_KEYS[idx], null, MESES_ES[idx]);
  }

  // Año y mes de HOY en la instalación (el mismo huso que dates.mjs y admin-queue.mjs; el
  // cliente no recibe el nombre del server): sin Intl, el reloj del navegador.
  function mesInstalacion() {
    var hoy = new Date();
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Argentina/Buenos_Aires", year: "numeric", month: "2-digit"
      }).formatToParts(hoy);
      var v = {};
      for (var i = 0; i < parts.length; i++) v[parts[i].type] = +parts[i].value;
      if (v.year && v.month) return { y: v.year, m: v.month - 1 };
    } catch (e) { /* sin Intl: reloj del navegador */ }
    return { y: hoy.getFullYear(), m: hoy.getMonth() };
  }

  function mesesOpciones() {
    var hoy = mesInstalacion();
    var ref = new Date(hoy.y, hoy.m, 0); // día 0 = último día del mes anterior
    var y = ref.getFullYear(), m = ref.getMonth() + 1;
    var out = [];
    for (var i = 0; i < 12; i++) {
      out.push({ value: y + "-" + (m < 10 ? "0" + m : m), label: mesNombre(m - 1) + " " + y });
      m--; if (m === 0) { m = 12; y--; }
    }
    return out;
  }

  function mesLabelDe(mesStr) {
    if (!mesStr) return "";
    var partes = mesStr.split("-");
    var y = parseInt(partes[0], 10), m = parseInt(partes[1], 10);
    if (!m || m < 1 || m > 12) return mesStr;
    return mesNombre(m - 1).toLowerCase() + " de " + y;
  }

  var DEFAULT_MES_ESTADISTICA = mesesOpciones()[0].value;
  var CACHE_ESTADISTICA = {};
  var MES_ESTADISTICA = DEFAULT_MES_ESTADISTICA;

  function mesSelectHtml(id, mesActual) {
    var opciones = mesesOpciones();
    // el mes vigente puede no estar entre los últimos 12 calculados acá si el
    // reloj del navegador y el de ART difieren en un borde de mes — se agrega
    // al tope en vez de perderlo silenciosamente.
    if (mesActual && !opciones.some(function (o) { return o.value === mesActual; })) {
      opciones.unshift({ value: mesActual, label: mesLabelDe(mesActual) });
    }
    return '<label for="' + id + '">' + esc(T("admin.reports.reports.mes_label", null, "Mes")) + '</label>'
      + '<select id="' + id + '" class="adm-sel">'
      + opciones.map(function (o) { return '<option value="' + o.value + '"' + (o.value === mesActual ? " selected" : "") + '>' + esc(o.label) + '</option>'; }).join("")
      + "</select>";
  }

  function fetchEstadistica(mes, force, signal) {
    if (!force && CACHE_ESTADISTICA[mes]) return Promise.resolve(CACHE_ESTADISTICA[mes]);
    return fetchJson("/api/admin/reports/statistics?mes=" + mes, signal).then(function (j) { CACHE_ESTADISTICA[mes] = j; return j; });
  }

  function kpisEstadisticaHtml(k) {
    var tiles = "";
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.consultas", null, "Consultas"), valor: fmtInt(k.consultas_totales) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.presentes", null, "Presentes"), valor: fmtInt(k.presentes) });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.ausentes", null, "Ausentes"), valor: fmtInt(k.ausentes) });
    if (!muestraCero(fmtInt(k.sin_marca))) tiles += Charts.tile({ etiqueta: T("admin.reports.reports.sin_marca_todavia", null, "Sin marca todavía"), valor: fmtInt(k.sin_marca) });
    tiles += Charts.tile({
      etiqueta: T("admin.reports.reports.asistencia_efectiva", null, "Asistencia efectiva"), valor: k.pct_asistencia == null ? null : fmtPct(k.pct_asistencia),
      pill: k.pct_asistencia == null ? null : {
        texto: k.pct_asistencia >= 80
          ? T("admin.reports.reports.buena_asistencia", null, "Buena asistencia")
          : T("admin.reports.reports.conviene_reforzar_recordatorios", null, "Conviene reforzar recordatorios"),
        tono: k.pct_asistencia >= 80 ? "ok" : "atencion",
      },
    });
    return '<div class="rep-tiles">' + tiles + "</div>";
  }

  // Tabla completa por especialidad — un dato por celda (table-design):
  // consultas, presentes/ausentes/sin marca y el rendimiento por hora ofrecida
  // en columnas separadas, nunca apiladas en una sola.
  function seccionPorEspecialidadTabla(rows) {
    if (!rows || !rows.length) return "";
    // data-label: on a phone the row becomes a card (admin-direct.css,
    // .rep-esp-table) — specialty and consultations on the first line, the
    // other figures below with their names, nothing scrolled sideways (E1).
    var L = {
      consultas: T("admin.reports.reports.consultas", null, "Consultas"),
      presentes: T("admin.reports.reports.presentes", null, "Presentes"),
      ausentes: T("admin.reports.reports.ausentes", null, "Ausentes"),
      sinMarca: T("admin.reports.reports.th_sin_marca", null, "Sin marca"),
      horas: T("admin.reports.reports.th_horas_ofrecidas", null, "Horas ofrecidas"),
      porHora: T("admin.reports.reports.th_consultas_por_hora", null, "Consultas/hora"),
    };
    function num(label, v) { return '<td class="cnt-numeric" data-label="' + esc(label) + '">' + v + "</td>"; }
    // A count column that prints zero on every row is not shown (E9): with
    // every visit marked, «Sin marca» was a column of zeros.
    var cols = [
      { label: L.consultas, valor: function (r) { return fmtInt(r.consultas); } },
      { label: L.presentes, valor: function (r) { return fmtInt(r.presentes); } },
      { label: L.ausentes, valor: function (r) { return fmtInt(r.ausentes); } },
      { label: L.sinMarca, valor: function (r) { return fmtInt(r.sin_marca); } },
      { label: L.horas, valor: function (r) { return fmtDec(r.horas_ofrecidas, 1); } },
      { label: L.porHora, valor: function (r) { return r.consultas_por_hora == null ? "—" : fmtDec(r.consultas_por_hora, 2); } },
    ].filter(function (c) { return rows.some(function (r) { return !muestraCero(c.valor(r)); }); });
    var body = rows.map(function (r) {
      return "<tr><td>" + esc(r.label) + "</td>" + cols.map(function (c) { return num(c.label, c.valor(r)); }).join("") + "</tr>";
    }).join("");
    var tabla = '<div class="cnt-table-wrap"><table class="cnt-table rep-esp-table"><thead><tr>'
      + '<th class="cnt-th">' + esc(T("admin.reports.reports.th_especialidad", null, "Especialidad")) + '</th>'
      + cols.map(function (c) { return '<th class="cnt-th cnt-numeric">' + esc(c.label) + "</th>"; }).join("")
      + "</tr></thead><tbody>" + body + "</tbody></table></div>";
    return seccion(T("admin.reports.reports.titulo_consultas_por_especialidad", null, "Consultas por especialidad"), tabla);
  }

  function medianaDe(vals) {
    if (!vals.length) return null;
    var s = vals.slice().sort(function (a, b) { return a - b; });
    var n = s.length;
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }

  // Lollipop contra la mediana (nunca un ranking de barras suelto): a la
  // derecha de la línea, la especialidad rinde más consultas por cada hora de
  // agenda invertida.
  function seccionConsultasPorHora(porEspecialidad) {
    // Printed with the table's two decimals. A point that prints zero is not
    // drawn: the specialty keeps its row, with its 0,00, in the table below.
    // Fewer than two points left compare nothing, so there is no chart (E9).
    function porHora(v) { return fmtDec(v, 2); }
    var rows = (porEspecialidad || []).filter(function (r) { return r.consultas_por_hora != null && !muestraCero(porHora(r.consultas_por_hora)); })
      .map(function (r) { return { nombre: r.label, valor: r.consultas_por_hora }; })
      .sort(function (a, b) { return b.valor - a.valor; });
    if (rows.length < 2) return "";
    var mediana = medianaDe(rows.map(function (r) { return r.valor; }));
    var chart = Charts.lollipop(rows, {
      fmtValor: porHora,
      ref: mediana != null ? { valor: mediana, etiqueta: T("admin.reports.reports.etiqueta_mediana", null, "mediana") } : null,
      read: T("admin.reports.reports.read_consultas_por_hora", null, "Cada punto es cuántas consultas entraron por cada hora de agenda ofrecida ese mes; a la derecha de la mediana, la especialidad rinde más consultas por hora invertida."),
    });
    var top = rows[0];
    var accion = top.valor > 0
      ? '<p class="rep-action">' + esc(T("admin.reports.reports.accion_top_concentra_consultas_hora", { nombre: top.nombre, valor: fmtDec(top.valor, 2) },
        "" + top.nombre + " concentra " + fmtDec(top.valor, 2) + " consultas por hora ofrecida: ahí una hora extra rinde más pacientes atendidos.")) + "</p>"
      : "";
    return seccion(T("admin.reports.reports.titulo_consultas_por_hora_ofrecida", null, "Consultas por hora ofrecida"),
      chart + accion);
  }

  function seccionEdadesEstadistica(list, mesLabel) {
    list = list || [];
    if (!list.some(function (r) { return r.cantidad > 0; })) return "";
    var top = list.slice().sort(function (a, b) { return b.cantidad - a.cantidad; })[0];
    // The age is taken on the month's last day; that stays in the reading
    // line, the dominant range moves into the title (E12).
    var chart = Charts.piramide(list, {
      read: T("admin.reports.reports.read_piramide_edad_mes", { mes: mesLabel }, "Cada fila es un rango etario, con la edad al último día de " + mesLabel + "; mujeres a la izquierda, varones a la derecha, con el conteo impreso sobre cada barra."),
    });
    return seccion(T("admin.reports.reports.titulo_quienes_se_atendieron", { mes: mesLabel, rango: top.rango },
      "Quiénes se atendieron en " + mesLabel + ": sobre todo de " + top.rango + " años"), chart);
  }

  // Ranking por centro: permitido como LISTA de apoyo (report-design ley 3 —
  // barRow sobrevive como lista rankeada), no como la única visual de la
  // página: acompaña a la tabla, el lollipop y la pirámide de arriba.
  function seccionPorCentroEstadistica(list, mesLabel) {
    if (!list || !list.length) return "";
    var max = list.reduce(function (a, r) { return Math.max(a, r.consultas); }, 0);
    var rows = list.map(function (r) { return barRow(r.centro, barPct(r.consultas, max), fmtInt(r.consultas)); }).join("");
    return seccion(T("admin.reports.reports.titulo_x_concentra_volumen_consultas_mes", { centro: list[0].centro, mes: mesLabel },
      "" + list[0].centro + " concentra el mayor volumen de consultas de " + mesLabel), rows);
  }

  function renderEstadistica(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var mesLabel = mesLabelDe(data.mes);
    var tiene = hasDataEstadistica(data);
    marcarDisponible("estadistica", tiene);
    var html = crumbHtml("estadistica");
    if (tiene) html += Charts.hero(data.hero);
    html += '<div class="rep-controls">' + mesSelectHtml("est-mes", data.mes)
      + (tiene ? '<a class="rep-csv-link" href="/api/admin/reports/statistics.csv?mes=' + esc(data.mes)
        + '">' + esc(T("admin.reports.reports.descargar_csv_estadistica", null, "Descargar CSV para estadística (sin datos personales)")) + '</a>' : "")
      + "</div>";
    if (!tiene) html += informeVacioHtml();
    else {
      html += kpisEstadisticaHtml(data.kpis);
      html += seccionPorEspecialidadTabla(data.por_especialidad);
      html += seccionConsultasPorHora(data.por_especialidad);
      html += seccionEdadesEstadistica(data.edades, mesLabel);
      html += seccionPorCentroEstadistica(data.por_centro, mesLabel);
    }
    body.innerHTML = html;

    var sel = $("#est-mes", body);
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      MES_ESTADISTICA = sel.value;
      cargarEstadistica();
    });
  }

  function cargarEstadistica() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchEstadistica(MES_ESTADISTICA, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      MES_ESTADISTICA = data.mes; // sincroniza si el backend cayó a otro default (hora ART)
      renderEstadistica(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Capacidad y temporada (?view=capacidad, 2026-07-24) —
  // mirada hacia adelante: ocupación de los próximos 14 días, demanda de las
  // últimas 8 semanas y la proyección de temporada alta (reports.py:
  // _capacidad). Sin selector de período — el payload siempre mira "ahora".
  // =====================================================================

  var CACHE_CAPACIDAD = null;

  function fetchCapacidad(force, signal) {
    if (!force && CACHE_CAPACIDAD) return Promise.resolve(CACHE_CAPACIDAD);
    return fetchJson("/api/admin/reports/capacity", signal).then(function (j) { CACHE_CAPACIDAD = j; return j; });
  }

  // La especialidad «más ocupada» sólo existe si alguien está ocupado y una
  // sobresale: con 0 % en todas no hay a quién nombrar, y si varias empatan
  // en la cifra (a un decimal, como se muestra) elegir una por el orden de la
  // lista sería inventar un hallazgo. Devuelve null sin ocupación, o la
  // primera con las que comparten su cifra.
  function especialidadMasOcupada(list) {
    var conPct = (list || []).filter(function (r) { return r.pct > 0; })
      .sort(function (a, b) { return b.pct - a.pct; });
    if (!conPct.length) return null;
    var cifra = Math.round(conPct[0].pct * 10);
    var empatadas = conPct.filter(function (r) { return Math.round(r.pct * 10) === cifra; });
    return { top: conPct[0], empatan: empatadas.length, empatadas: empatadas };
  }

  // «Sumar horas ahí» es una recomendación, y sólo vale cuando la agenda está
  // por llenarse: lo decide el servidor con la regla del titular
  // (_capacity_rule en admin_panel/reports.py, hero[0].regla === "atencion");
  // el cliente no guarda otro umbral. Debajo, el gráfico ya dice cuánto hay
  // ocupado.
  function reglaCapacidad(data) {
    return (((data || {}).hero || [])[0] || {}).regla;
  }

  // Las que empatan arriba, por su nombre: hasta tres, y las demás contadas.
  function nombresEmpate(mas) {
    var nombres = mas.empatadas.slice(0, 3).map(function (r) { return r.label; });
    var resto = mas.empatadas.length - nombres.length;
    if (resto > 0) nombres.push(T("admin.reports.reports.n_mas", { n: fmtInt(resto) }, "" + fmtInt(resto) + " más"));
    try {
      return new Intl.ListFormat(LOCALE, { style: "long", type: "conjunction" }).format(nombres);
    } catch (_e) {
      return nombres.join(", ");
    }
  }

  function textoEmpateCapacidad(mas) {
    var nombres = nombresEmpate(mas);
    return T("admin.reports.reports.hallazgo_capacidad_empate_nombres", { nombres: nombres },
      "" + nombres + " empatan como las más ocupadas de los próximos 14 días.");
  }

  // Lollipop contra el 100% de capacidad (nunca barras): a la derecha de la
  // línea, la demanda ya superó la agenda cargada.
  function seccionOcupacionCapacidad(list, regla) {
    var rows = (list || []).filter(function (r) { return r.pct != null; }).map(function (r) { return { nombre: r.label, valor: r.pct }; });
    // Every specialty at 0 % is a column of zeros, not a finding (E9).
    if (!rows.some(function (r) { return !muestraCero(pctNum(r.valor)); })) return "";
    // Each point prints its percent sign: a bare «2,1» next to the 100% line
    // read as «2,1 times the capacity» and the honest scale looked broken.
    var chart = Charts.lollipop(rows, {
      fmtValor: fmtPct,
      ref: { valor: 100, etiqueta: T("admin.reports.reports.etiqueta_capacidad_completa", null, "capacidad completa") },
      read: T("admin.reports.reports.read_ocupacion_capacidad", null, "Cada punto es cuánto de la capacidad de los próximos 14 días ya está ocupada; a la derecha del 100% la demanda ya superó la agenda cargada."),
    });
    var mas = especialidadMasOcupada(list);
    var accion = "";
    if (!mas || regla !== "atencion") {
      accion = "";
    } else if (mas.empatan > 1) {
      var nombres = nombresEmpate(mas);
      accion = '<p class="rep-action">' + esc(T("admin.reports.reports.accion_empate_al_pct_capacidad", { nombres: nombres, pct: pctNum(mas.top.pct) },
        "" + nombres + " están al " + pctNum(mas.top.pct) + "% de su capacidad de los próximos 14 días: sumar horas ahí es la inversión de mayor impacto.")) + "</p>";
    } else {
      var top = mas.top;
      accion = '<p class="rep-action">' + esc(T("admin.reports.reports.accion_top_al_pct_capacidad", { label: top.label, pct: pctNum(top.pct), ocupados: fmtInt(top.ocupados_14d), capacidad: fmtInt(top.capacidad_14d) },
        "" + top.label + " está al " + pctNum(top.pct) + "% de su capacidad de los próximos 14 días (" + fmtInt(top.ocupados_14d) + " de " + fmtInt(top.capacidad_14d) + " turnos): sumar horas ahí es la inversión de mayor impacto.")) + "</p>";
    }
    return seccion(T("admin.reports.reports.titulo_ocupacion_14d", null, "Ocupación de los próximos 14 días"),
      chart + accion);
  }

  function seccionDemandaSemanalCapacidad(list) {
    list = list || [];
    if (!list.some(function (r) { return r.solicitudes > 0 || r.busquedas > 0; })) return "";
    var puntosSol = list.map(function (r, i) { return { x: i, etiqueta: fechaLabel(r.semana), y: r.solicitudes }; });
    var puntosBus = list.map(function (r, i) { return { x: i, etiqueta: fechaLabel(r.semana), y: r.busquedas }; });
    var chart = Charts.linea([
      { nombre: T("admin.reports.reports.serie_solicitudes", null, "Solicitudes"), puntos: puntosSol },
      { nombre: T("admin.reports.reports.serie_busquedas", null, "Búsquedas"), puntos: puntosBus },
    ], { read: T("admin.reports.reports.read_demanda_semanal", null, "La línea de color son las solicitudes que se convirtieron en turno; la gris, las búsquedas que todavía no encontraron uno.") });
    return seccion(T("admin.reports.reports.titulo_demanda_ultimas_8_semanas", null, "Demanda de las últimas 8 semanas"), chart);
  }

  function seccionSinOfertaCapacidad(list) {
    if (!list || !list.length) return "";
    var items = list.map(function (r) { return { nombre: r.label, valor: r.solicitudes }; });
    var chart = treemapDeVarios(items, {
      read: T("admin.reports.reports.read_sin_oferta_capacidad", null, "El bloque más grande es la especialidad con más solicitudes de los últimos 90 días sin ninguna hora de agenda cargada hoy."),
    });
    var top = list.slice().sort(function (a, b) { return b.solicitudes - a.solicitudes; })[0];
    return seccion(T("admin.reports.reports.titulo_x_no_tiene_hora_agenda", { label: top.label }, "" + top.label + " no tiene ninguna hora de agenda pese a la demanda reciente"),
      chart + '<p class="rep-action">' + esc(T("admin.reports.reports.accion_cargar_agenda_convierte_demanda", null, "Cargar agenda ahí convierte demanda represada en turnos reales.")) + '</p>');
  }

  // Nunca se inventa un factor estacional (report-design ley 10): sin
  // historia de temporadas anteriores la sección directamente no se dibuja,
  // nunca simula una proyección.
  function seccionTemporada(t) {
    if (!t || !t.disponible) return "";
    var tile = Charts.tile({
      etiqueta: T("admin.reports.reports.demanda_proyectada_temporada_alta", null, "Demanda proyectada de temporada alta"), valor: fmtInt(Math.round(t.proyeccion)),
      sub: T("admin.reports.reports.factor_x", { factor: fmtDec(t.factor, 2) }, "factor ×" + fmtDec(t.factor, 2)),
    });
    return seccion(T("admin.reports.reports.titulo_proyeccion_temporada_alta", null, "Proyección de temporada alta"),
      '<div class="rep-tiles">' + tile + "</div>" + '<p class="rep-quiet">' + esc(t.supuesto) + "</p>");
  }

  function renderCapacidad(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataCapacidad(data);
    marcarDisponible("capacidad", tiene);
    var html = crumbHtml("capacidad");
    if (!tiene) html += informeVacioHtml();
    else {
      html += Charts.hero(data.hero);
      html += seccionOcupacionCapacidad(data.ocupacion, reglaCapacidad(data));
      html += seccionDemandaSemanalCapacidad(data.demanda_semanal);
      html += seccionSinOfertaCapacidad(data.sin_oferta);
      html += seccionTemporada(data.temporada);
    }
    body.innerHTML = html;
  }

  function cargarCapacidad() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchCapacidad(false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderCapacidad(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Costos de IA (?view=ia) — antes admin-costos.js
  // =====================================================================

  var CACHE_IA = {};
  var DIAS_IA = DEFAULT_DIAS;

  function fetchIa(dias, force, signal) {
    if (!force && CACHE_IA[dias]) return Promise.resolve(CACHE_IA[dias]);
    // El costo (costos_ia.py) y el "ahorro" (reports.py, ver el docstring
    // de ese módulo) son dos fuentes distintas para el mismo período — si la
    // segunda falla (permiso, 503) la sección de ahorro simplemente no se
    // dibuja, nunca rompe el resto del tab de Costos de IA.
    return Promise.all([
      fetchJson("/api/admin/costs?days=" + dias, signal),
      fetchJson("/api/admin/reports?days=" + dias, signal).catch(function () { return { ahorro: { disponible: false } }; }),
    ]).then(function (res) {
      var j = res[0];
      j.ahorro = (res[1] && res[1].ahorro) || { disponible: false };
      CACHE_IA[dias] = j;
      return j;
    });
  }

  // Lo que la automatización le DEVUELVE al equipo, no un recorte de gasto
  // (ver "Editorial angle" en report-design SKILL.md) — omitida enteramente
  // si costos_ia todavía no tiene ninguna fila (ahorro.disponible=false).
  function seccionAhorro(ahorro) {
    if (!ahorro || !ahorro.disponible) return "";
    var tiles = "";
    tiles += Charts.tile({
      etiqueta: T("admin.reports.reports.horas_devueltas_al_equipo", null, "Horas devueltas al equipo"), valor: fmtHoras(ahorro.horas_operador) + " h",
      sub: fmtInt(ahorro.operaciones) + " " + plural(ahorro.operaciones, T("admin.reports.reports.operacion_automatizada_singular", null, "operación automatizada"), T("admin.reports.reports.operacion_automatizada_plural", null, "operaciones automatizadas")),
    });
    tiles += Charts.tile({ etiqueta: T("admin.reports.reports.equivalente_horas_operador", null, "Equivalente en horas de operador"), valor: fmtArs(ahorro.ahorro_ars) });
    var supuestos = (ahorro.supuestos || []).map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("");
    // The assumptions are for whoever doubts the figure: one line that opens,
    // not seven lines under two numbers.
    var html = '<div class="rep-tiles">' + tiles + "</div>"
      + (supuestos ? '<details class="rep-more rep-quiet"><summary>' + esc(T("admin.reports.reports.supuestos_de_esta_estimacion_abrir", null, "Supuestos de esta estimación")) + '</summary><ul>' + supuestos + "</ul></details>" : "");
    return seccion(T("admin.reports.reports.titulo_lo_que_automatizacion_devuelve", null, "Lo que la automatización le devuelve al equipo"),
      html);
  }

  // A cost tile that prints «US$ 0,0000» or «—» is an empty tile (E9).
  function kpiTileSiHay(label, value, sub) { return muestraCero(value) ? "" : kpiTile(label, value, sub); }

  function kpisIaHtml(r, dias) {
    return '<div class="rep-kpis">'
      + kpiTile(T("admin.reports.reports.gasto_en_dias", { dias: dias }, "Gasto en " + dias + " días"), fmtUsd(r.total_usd))
      + kpiTile(T("admin.reports.reports.ritmo_mensual", null, "Ritmo mensual proyectado"), fmtUsd(r.run_rate_mensual_usd))
      + kpiTileSiHay(T("admin.reports.reports.costo_por_operacion", null, "Costo por operación"), fmtUsd(r.costo_prom_op), fmtInt(r.operaciones) + " " + plural(r.operaciones, T("admin.reports.reports.llamada_singular", null, "llamada"), T("admin.reports.reports.llamada_plural", null, "llamadas")))
      + kpiTile(T("admin.reports.reports.se_reconocio", null, "Se reconoció"), fmtPct(r.tasa_reconocimiento), r.no_reconocidas > 0 ? T("admin.reports.reports.n_sin_reconocer", { n: fmtInt(r.no_reconocidas) }, "" + fmtInt(r.no_reconocidas) + " sin reconocer") : "")
      + kpiTileSiHay(T("admin.reports.reports.gasto_por_persona_mediana", null, "Gasto por persona (mediana)"), fmtUsd(r.costo_mediano_por_ip), fmtInt(r.ips) + " " + plural(r.ips, T("admin.reports.reports.dispositivo_singular", null, "dispositivo"), T("admin.reports.reports.dispositivo_plural", null, "dispositivos")))
      + kpiTileSiHay(T("admin.reports.reports.cada_1000_pacientes", null, "Cada 1000 pacientes"), fmtUsd(r.por_1000_pacientes_usd))
      + "</div>";
  }

  function chartOperaciones(list) {
    // «En qué se va el gasto»: an operation that cost nothing is not where
    // the money goes (E9).
    list = (list || []).filter(function (r) { return !muestraCero(fmtUsd(r.costo_usd)); });
    if (!list.length) return "";
    var max = 0;
    list.forEach(function (r) { if (r.costo_usd > max) max = r.costo_usd; });
    return list.map(function (r) {
      var palabraLlamada = plural(r.cantidad, T("admin.reports.reports.llamada_singular", null, "llamada"), T("admin.reports.reports.llamada_plural", null, "llamadas"));
      var sub = fmtInt(r.cantidad) + " " + palabraLlamada
        + " · " + fmtUsd(r.costo_prom) + " " + T("admin.reports.reports.c_u", null, "c/u")
        + (r.tasa_reconocimiento != null ? " · " + fmtPct(r.tasa_reconocimiento) + " " + T("admin.reports.reports.reconocidas", null, "reconocidas") : "");
      return barRow(r.label || r.operacion, barPct(r.costo_usd, max), fmtUsd(r.costo_usd), sub);
    }).join("");
  }

  function fairUseHtml(data) {
    var sos = data.sospechosos || [];
    // Sin IP marcada no hay sección: el estado del corte automático es
    // configuración, no un hallazgo del período. With one, the cutoff state
    // closes the section as what happens next, in words — never the name of
    // the switch (critical review 08.11, E8/E12).
    if (!sos.length) return "";
    var gate = data.abuso_activo
      ? T("admin.reports.reports.corte_automatico_activo", null, "El corte automático está activo: una IP con muchas subidas irreconocibles en una hora deja de gastar tokens hasta que baje.")
      : T("admin.reports.reports.corte_automatico_apagado", null, "El corte automático está apagado: sólo se registra, no se bloquea.");
    var max = 0;
    sos.forEach(function (t) { if (t.costo_usd > max) max = t.costo_usd; });
    var rows = sos.map(function (t) {
      var palabraLlamada = plural(t.operaciones, T("admin.reports.reports.llamada_singular", null, "llamada"), T("admin.reports.reports.llamada_plural", null, "llamadas"));
      return barRow(t.ip, barPct(t.costo_usd, max), fmtUsd(t.costo_usd),
        fmtInt(t.operaciones) + " " + palabraLlamada + " · " + T("admin.reports.reports.n_sin_reconocer", { n: fmtInt(t.no_reconocidas) }, "" + fmtInt(t.no_reconocidas) + " sin reconocer"), "gap");
    }).join("");
    var palabraIp = plural(sos.length, T("admin.reports.reports.ip_marcada_singular", null, "IP marcada"), T("admin.reports.reports.ip_marcada_plural", null, "IPs marcadas"));
    return seccion(T("admin.reports.reports.titulo_uso_indebido_n_ips", { n: sos.length, palabra: palabraIp }, "Uso indebido — " + sos.length + " " + palabraIp),
      rows + '<p class="rep-action">' + esc(gate) + "</p>");
  }

  // A private or loopback address is the installation's own proxy, not a
  // visitor: behind it every call looks like one device.
  function ipInterna(ip) {
    var s = String(ip || "").toLowerCase();
    if (!s || s === "::1" || s.indexOf("127.") === 0 || s.indexOf("10.") === 0 || s.indexOf("192.168.") === 0) return true;
    if (s.indexOf("fc") === 0 || s.indexOf("fd") === 0) return s.indexOf(":") !== -1;
    var m = /^172\.(\d+)\./.exec(s);
    return !!m && +m[1] >= 16 && +m[1] <= 31;
  }

  // The table compares devices; with fewer than two real visitor addresses
  // there is nothing to compare and an internal address means nothing to
  // the reader (critical review 08.11, E8).
  function tablaTopIps(list) {
    list = (list || []).filter(function (t) { return !ipInterna(t.ip); });
    if (list.length < 2) return "";
    var body = list.map(function (t) {
      var flag = t.sospechoso ? ' <span class="rep-bar-sub" style="color:var(--accent)">· ' + esc(T("admin.reports.reports.uso_indebido", null, "uso indebido")) + '</span>' : "";
      return "<tr><td>" + esc(t.ip) + flag + "</td>" + '<td class="cnt-numeric">' + fmtInt(t.operaciones) + "</td>"
        + '<td class="cnt-numeric">' + fmtInt(t.no_reconocidas) + '</td><td class="cnt-numeric">' + fmtUsd(t.costo_usd) + "</td></tr>";
    }).join("");
    return tablaAbre("rep-top-ips", T("admin.reports.reports.titulo_quien_gasta_mas", null, "Quién gasta más, por dispositivo"))
      + '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr><th class="cnt-th">' + esc(T("admin.reports.reports.th_ip", null, "IP")) + '</th>'
      + '<th class="cnt-th cnt-numeric">' + esc(T("admin.reports.reports.th_llamadas", null, "Llamadas")) + '</th>'
      + '<th class="cnt-th cnt-numeric">' + esc(T("admin.reports.reports.th_sin_reconocer", null, "Sin reconocer")) + '</th>'
      + '<th class="cnt-th cnt-numeric">' + esc(T("admin.reports.reports.th_gasto", null, "Gasto")) + '</th></tr></thead>'
      + "<tbody>" + body + "</tbody></table></div></div>";
  }

  function renderIa(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var r = data.resumen || {};
    var tiene = hasDataIa(data);
    marcarDisponible("ia", tiene);
    var html = crumbHtml("ia");
    html += '<div class="rep-controls">' + periodoControlHtml("ia-periodo", DIAS_IA) + "</div>";

    if (!tiene) {
      html += informeVacioHtml(T("admin.reports.reports.sin_llamadas_ia_todavia", null, "Todavía no se registró ninguna llamada de IA en este período. Cuando un paciente suba la foto del DNI o una orden, el costo aparece acá."));
      body.innerHTML = html;
      wireIaPeriodo(body);
      return;
    }

    html += kpisIaHtml(r, data.dias);
    html += seccionOpcional(T("admin.reports.reports.titulo_en_que_se_va_el_gasto", null, "En qué se va el gasto"),
      chartOperaciones(data.por_operacion));
    html += seccionOpcional(T("admin.reports.reports.titulo_gasto_por_dia", null, "Gasto por día"),
      chartBarrasDiarias(data.diario, function (r2) { return r2.costo_usd; }, function (r2) {
        var palabraLlamada2 = plural(r2.operaciones, T("admin.reports.reports.llamada_singular", null, "llamada"), T("admin.reports.reports.llamada_plural", null, "llamadas"));
        return fechaLabel(r2.fecha) + ": " + fmtUsd(r2.costo_usd) + " (" + fmtInt(r2.operaciones) + " " + palabraLlamada2 + ")";
      }));
    html += fairUseHtml(data);
    var topIps = tablaTopIps(data.top_ips || []);
    if (topIps) html += '<div class="rep-tables">' + topIps + "</div>";
    html += seccionAhorro(data.ahorro);
    body.innerHTML = html;
    wireIaPeriodo(body);
  }

  function wireIaPeriodo(body) {
    var sel = $("#ia-periodo", body);
    if (!sel) return;
    sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      DIAS_IA = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      cargarIa();
    });
  }

  function cargarIa() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchIa(DIAS_IA, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderIa(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Panel de login (?view=login) — la fricción del login del
  // personal (panel_login.py / reports.py). El experimento del gate (memes
  // vs consejos) se cerró el 19.07 y ya no se reporta; del beacon queda lo
  // que importa: qué parte de las sesiones que intentan entrar choca con un
  // error, y cuánto tarda la que entra.
  //   GET /api/admin/reports/login-panel?days=30   (reports.py, rol admin)
  //     -> {dias, sesiones, logins, errores, sesiones_con_error, ms_mediana_login}
  // =====================================================================

  var CACHE_LOGIN = {};

  function fetchLogin(dias, force, signal) {
    if (!force && CACHE_LOGIN[dias]) return Promise.resolve(CACHE_LOGIN[dias]);
    return fetchJson("/api/admin/reports/login-panel?days=" + dias, signal).then(function (j) { CACHE_LOGIN[dias] = j; return j; });
  }

  function renderLogin(data) {
    var tiene = hasDataLogin(data);
    marcarDisponible("login", tiene);
    if (!tiene) {
      $("#rep-body").innerHTML = crumbHtml("login") + informeVacioHtml(T("admin.reports.reports.sin_sesiones_todavia", null, "Sin sesiones todavía"));
      return;
    }
    var d = data || {};
    var sesiones = d.sesiones || 0, conError = d.sesiones_con_error || 0, errores = d.errores || 0;
    var dias = d.dias || DEFAULT_DIAS;
    var heroFrases;
    if (!conError) {
      heroFrases = [{ tono: "ok", texto: T("admin.reports.reports.login_hero_sin_errores", { dias: dias },
        "Ninguna sesión de login de los últimos " + dias + " días chocó con un error.") }];
    } else {
      var pct = 100 * conError / sesiones;
      heroFrases = [{ tono: "atencion", texto: T("admin.reports.reports.login_hero_con_errores", { m: fmtInt(conError), total: fmtInt(sesiones), pct: pctNum(pct) },
        "" + fmtInt(conError) + " de " + fmtInt(sesiones) + " sesiones de login chocaron con un error (" + pctNum(pct) + "%).") }];
    }
    var tiles = Charts.tile({ etiqueta: T("admin.reports.reports.login_tile_sesiones", null, "Sesiones con intento de login"), valor: fmtInt(sesiones) });
    if (conError) {
      var pctErr = 100 * conError / sesiones;
      tiles += Charts.tile({
        etiqueta: T("admin.reports.reports.login_tile_con_error", null, "Chocaron con un error"),
        valor: fmtPct(pctErr),
      });
      if (errores > conError) {
        tiles += Charts.tile({
          etiqueta: T("admin.reports.reports.login_tile_reintentos", null, "Reintentos por sesión que falla"),
          valor: fmtDec(errores / conError, 1),
        });
      }
    }
    if (d.ms_mediana_login != null) {
      tiles += Charts.tile({
        etiqueta: T("admin.reports.reports.login_tile_mediana", null, "Segundos hasta entrar (mediana)"),
        valor: fmtDec(d.ms_mediana_login / 1000, 1) + " s",
      });
    }
    var html = crumbHtml("login") + Charts.hero(heroFrases) + '<div class="rep-tiles">' + tiles + "</div>";
    if (conError) {
      html += '<p class="rep-action">' + esc(T("admin.reports.reports.login_action", null,
        "Cada sesión con error es alguien del equipo que no pudo entrar a la primera: conviene preguntarle qué lo frenó y dejarle el acceso listo.")) + "</p>";
    }
    $("#rep-body").innerHTML = html;
  }

  function cargarLogin() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchLogin(DEFAULT_DIAS, false, load.signal).then(function (data) {
      if (!owns(load.gen)) return null;
      renderLogin(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Visitas (?view=visitas) — el flujo anónimo de la analítica
  // (turnos/analytics_report.py): por dónde entra la gente al sitio, qué
  // busca, hasta qué paso del turno llega y qué días de la agenda pública no
  // tienen cupo. Sólo conteos; ninguna visita nombra a una persona. Con el
  // flujo apagado (TURNOS_ANALYTICS) el informe no aparece. Abajo, las claves
  // de sólo lectura con las que se baja la misma información por API.
  //   GET /api/admin/reports/visits?days=30   (rol admin)
  //   GET|POST|DELETE /api/admin/analytics/keys
  // =====================================================================

  var CACHE_VISITAS = {};
  var VIS = { dias: DEFAULT_DIAS, clave: null };

  function fetchVisitas(dias, force, signal) {
    if (!force && CACHE_VISITAS[dias]) return Promise.resolve(CACHE_VISITAS[dias]);
    return fetchJson("/api/admin/reports/visits?days=" + dias, signal).then(function (j) { CACHE_VISITAS[dias] = j; return j; });
  }

  function hasDataVisitas(data) {
    return !!(data && data.visitas > 0);
  }

  function sumTileVisitas(data) {
    var sub = SUBS_BY_KEY.visitas;
    var d = data || {};
    var emb = d.embudo || [];
    var hechas = emb.length ? emb[emb.length - 1].n : 0;
    // «0 terminaron una solicitud» is not a finding for a card: with none, the
    // card says only what its figure counts.
    if (!hechas) {
      return sumCard(sub, fmtInt(d.visitas || 0),
        T("admin.reports.reports.visitas_card_sin", { dias: d.dias || DEFAULT_DIAS }, "Visitas de los últimos {dias} días."));
    }
    return sumCard(sub, fmtInt(d.visitas || 0),
      Tn("admin.reports.reports.visitas_card", hechas, { dias: d.dias || DEFAULT_DIAS, n: fmtInt(hechas) },
        "Visitas de los últimos {dias} días; {n} terminó una solicitud.",
        "Visitas de los últimos {dias} días; {n} terminaron una solicitud."));
  }

  // What the site records as a code is shown as words: where a visit came
  // from, the device, the language, the kind of page.
  function origenVisitaLabel(clave) {
    if (clave === "direct") return T("admin.reports.reports.visitas_origen_directo", null, "Entrada directa");
    if (clave === "internal") return T("admin.reports.reports.visitas_origen_interno", null, "Otra página del sitio");
    return String(clave).replace(/^www\./, "");
  }
  function dispositivoVisitaLabel(clave) {
    var L = {
      desktop: T("admin.reports.reports.visitas_dispositivo_desktop", null, "Computadora"),
      mobile: T("admin.reports.reports.visitas_dispositivo_mobile", null, "Teléfono"),
      tablet: T("admin.reports.reports.visitas_dispositivo_tablet", null, "Tableta"),
    };
    return L[clave] || clave;
  }
  var nombresIdioma = {};
  function idiomaVisitaLabel(clave) {
    // Named in the panel's language, not in the currency's locale.
    try {
      var idioma = (I18n && I18n.activeLang && I18n.activeLang()) || LOCALE;
      var dn = nombresIdioma[idioma] || (nombresIdioma[idioma] = new Intl.DisplayNames([idioma], { type: "language" }));
      var nombre = dn.of(String(clave));
      return nombre ? nombre.charAt(0).toUpperCase() + nombre.slice(1) : clave;
    } catch (e) { return clave; }
  }
  function paginaVisitaLabel(clave) {
    var partes = String(clave).split("|"), ruta = partes[0], cual = partes.slice(1).join(" ");
    var L = {
      "/doctor/:id": T("admin.reports.reports.visitas_pagina_medico", null, "Médico"),
      "/d/:id": T("admin.reports.reports.visitas_pagina_seccion", null, "Sección"),
      "/service/:id": T("admin.reports.reports.visitas_pagina_servicio", null, "Servicio"),
      "/center/:slug": T("admin.reports.reports.visitas_pagina_centro", null, "Centro"),
      "/study/:id": T("admin.reports.reports.visitas_pagina_estudio", null, "Estudio"),
      "/preparation/:id": T("admin.reports.reports.visitas_pagina_preparacion", null, "Preparación"),
    };
    var nombre = cual.replace(/-/g, " ");
    nombre = nombre.charAt(0).toUpperCase() + nombre.slice(1);
    return L[ruta] ? L[ruta] + ": " + nombre : (ruta + " " + cual).trim();
  }

  function pasoVisitaLabel(k) {
    var L = {
      visit: T("admin.reports.reports.visitas_paso_visit", null, "Visitaron el sitio"),
      search: T("admin.reports.reports.visitas_paso_search", null, "Buscaron"),
      profile: T("admin.reports.reports.visitas_paso_profile", null, "Abrieron un médico o un servicio"),
      slots_shown: T("admin.reports.reports.visitas_paso_slots", null, "Vieron horarios"),
      submit: T("admin.reports.reports.visitas_paso_submit", null, "Enviaron la solicitud"),
      done: T("admin.reports.reports.visitas_paso_done", null, "Solicitud confirmada"),
    };
    return L[k] || k;
  }

  function rankingVisitas(filas, claveDe, read) {
    // Searches arrive counted as `veces`, the other rankings as `n`.
    // Two codes that read as one name («www.google.com», «google.com») are one row.
    var rows = [], porNombre = {};
    (filas || []).forEach(function (f) {
      var nombre = claveDe(f), valor = f.n != null ? f.n : f.veces;
      if (!(valor > 0)) return;
      if (porNombre[nombre]) { porNombre[nombre].valor += valor; return; }
      rows.push(porNombre[nombre] = { nombre: nombre, valor: valor });
    });
    if (!rows.length) return "";
    rows.sort(function (a, b) { return b.valor - a.valor; });
    return Charts.lollipop(rows, { read: read });
  }

  function seccionAgendaVisitas(agenda) {
    var dias = (agenda && agenda.dias) || [];
    var sin = dias.filter(function (d) { return d.web > 0 && d.free === 0; });
    // No day without free hours is no finding: the section is not drawn.
    if (!dias.length || !sin.length) return "";
    var body = '<p class="rep-quiet">' + esc(T("admin.reports.reports.visitas_agenda_resumen", { n: fmtInt(sin.length), total: fmtInt(dias.length) },
      "" + fmtInt(sin.length) + " de los próximos " + fmtInt(dias.length) + " días con agenda no tienen ningún horario libre para pedir por la web.")) + "</p>";
    body += '<p class="rep-quiet">' + sin.slice(0, 20).map(function (d) { return esc(fechaLabel(d.day)); }).join(" · ") + "</p>";
    return seccion(T("admin.reports.reports.visitas_agenda_titulo", null, "Días sin horarios libres"), body);
  }

  function clavesVisitasHtml(info) {
    var rows = (info.keys || []).map(function (k) {
      return "<tr><td>" + esc(k.label) + "</td><td>" + esc(k.created_at ? fechaLabel(k.created_at.slice(0, 10)) : "") + "</td><td>"
        + esc(k.revoked_at ? T("admin.reports.reports.visitas_clave_revocada", null, "Revocada")
          : (k.last_used_at ? fechaLabel(k.last_used_at.slice(0, 10)) : T("admin.reports.reports.visitas_clave_sin_uso", null, "Sin usar")))
        + "</td><td>" + (k.revoked_at ? "" : '<button type="button" class="btn" data-vis-revocar="' + k.id + '">'
          + esc(T("admin.reports.reports.visitas_clave_revocar", null, "Revocar")) + "</button>") + "</td></tr>";
    }).join("");
    var nueva = VIS.clave
      ? '<p class="rep-quiet" role="status">' + esc(T("admin.reports.reports.visitas_clave_nueva", null, "Copiá la clave ahora; no se vuelve a mostrar:"))
        + ' <code>' + esc(VIS.clave) + "</code></p>"
      : "";
    // Keys are the integrator's business, not a finding of the period: one
    // line at the end of the report that opens (open right after a key is made).
    return '<details class="rep-more rep-section"' + (VIS.clave || VIS.clavesAbiertas ? " open" : "") + "><summary>"
      + esc(T("admin.reports.reports.visitas_claves_titulo", null, "Acceso por API")) + "</summary>"
      + nueva
      + '<p class="rep-quiet">' + esc(T("admin.reports.reports.visitas_claves_texto", null,
        "Con una clave se descargan los eventos y la agenda pública en /api/analytics/events y /api/analytics/schedule (Authorization: Bearer …).")) + "</p>"
      + (rows ? '<table class="rep-table"><tbody>' + rows + "</tbody></table>" : "")
      + '<p><input id="vis-clave-nombre" class="adm-sel" maxlength="80" aria-label="'
      + esc(T("admin.reports.reports.visitas_clave_nombre", null, "Para quién es la clave")) + '" placeholder="'
      + esc(T("admin.reports.reports.visitas_clave_nombre", null, "Para quién es la clave")) + '"> '
      + '<button type="button" class="btn" id="vis-clave-crear">' + esc(T("admin.reports.reports.visitas_clave_crear", null, "Crear clave")) + "</button></p></details>";
  }

  function renderVisitas(data, claves) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataVisitas(data);
    marcarDisponible("visitas", tiene);
    var html = crumbHtml("visitas");
    html += '<div class="rep-controls">' + periodoControlHtml("vis-periodo", VIS.dias) + "</div>";
    if (!tiene) {
      html += informeVacioHtml(T("admin.reports.reports.visitas_sin_datos", null, "Sin visitas registradas todavía"));
    } else {
      var emb = data.embudo || [];
      var visitas = data.visitas, hechas = emb.length ? emb[emb.length - 1].n : 0;
      var busc = (emb[1] || {}).n || 0;
      // Cada cifra concuerda con su verbo («hubo 1 visita», «1 buscó»).
      var heroA = Tn("admin.reports.reports.visitas_hero_a", visitas, { n: fmtInt(visitas) }, "hubo {n} visita", "hubo {n} visitas");
      var heroB = Tn("admin.reports.reports.visitas_hero_b", busc, { n: fmtInt(busc) }, "{n} buscó algo", "{n} buscaron algo");
      var heroH = Tn("admin.reports.reports.visitas_hero_h", hechas, { n: fmtInt(hechas) }, "{n} terminó una solicitud", "{n} terminaron una solicitud");
      html += Charts.hero([{ tono: "ok", texto: T("admin.reports.reports.visitas_hero", { dias: data.dias, a: heroA, b: heroB, h: heroH },
        "En los últimos {dias} días {a}; {b} y {h}.") }]);
      var serie = (data.serie || []).map(function (r, i) { return { x: i, etiqueta: fechaLabel(r.day), y: r.visitas }; });
      html += seccion(T("admin.reports.reports.visitas_serie_titulo", null, "Visitas por día"),
        Charts.linea([{ nombre: T("admin.reports.reports.visitas_serie", null, "Visitas"), puntos: serie }],
          { read: T("admin.reports.reports.visitas_serie_read", null, "Cada punto es cuántas visitas distintas tuvo el sitio ese día.") }));
      html += seccion(T("admin.reports.reports.visitas_embudo_titulo", null, "Hasta dónde llegan"),
        Charts.lollipop(emb.map(function (p) { return { nombre: pasoVisitaLabel(p.paso), valor: p.n }; }),
          { read: T("admin.reports.reports.visitas_embudo_read", null, "Cada punto es cuántas visitas pasaron por ese paso; una visita puede saltarse la búsqueda.") }));
      html += seccionOpcional(T("admin.reports.reports.visitas_busquedas_titulo", null, "Qué buscan"),
        rankingVisitas(data["busquedas"],function (f) { return f.q + (f.con_resultados ? "" : " ✕"); },
          T("admin.reports.reports.visitas_busquedas_read", null, "Veces que se buscó cada texto. Las palabras que no son del catálogo público se ocultan; ✕ marca lo que no encontró nada.")));
      html += seccionOpcional(T("admin.reports.reports.visitas_paginas_titulo", null, "Páginas más vistas"),
        rankingVisitas(data.paginas, function (f) { return paginaVisitaLabel(f.clave); }));
      html += seccionOpcional(T("admin.reports.reports.visitas_origenes_titulo", null, "De dónde llegan"),
        rankingVisitas(data.origenes, function (f) { return origenVisitaLabel(f.clave); }));
      html += seccionOpcional(T("admin.reports.reports.visitas_dispositivos_titulo", null, "Dispositivos"),
        rankingVisitas(data.dispositivos, function (f) { return dispositivoVisitaLabel(f.clave); }));
      html += seccionOpcional(T("admin.reports.reports.visitas_idiomas_titulo", null, "Idiomas"),
        rankingVisitas(data.idiomas, function (f) { return idiomaVisitaLabel(f.clave); }));
      html += seccionAgendaVisitas(data.agenda);
    }
    html += claves ? clavesVisitasHtml(claves) : "";
    body.innerHTML = html;
    conectarVisitas(body);
  }

  function fetchClavesVisitas(signal) {
    return fetchJson("/api/admin/analytics/keys", signal).catch(function () { return null; });
  }

  function conectarVisitas(body) {
    var sel = $("#vis-periodo", body);
    if (sel) sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      VIS.dias = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      cargarVisitas();
    });
    var crear = $("#vis-clave-crear", body);
    if (crear) crear.addEventListener("click", function () {
      // Whoever works on the keys keeps the block open across the reload.
      VIS.clavesAbiertas = true;
      var nombre = ($("#vis-clave-nombre", body) || {}).value || "";
      crear.disabled = true;
      fetch("/api/admin/analytics/keys", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: nombre }),
      }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) {
        VIS.clave = j && j.key ? j.key : null;
        return alive() ? cargarVisitas() : null;
      });
    });
    Array.prototype.forEach.call(body.querySelectorAll("[data-vis-revocar]"), function (b) {
      b.addEventListener("click", function () {
        b.disabled = true;
        VIS.clavesAbiertas = true;
        fetch("/api/admin/analytics/keys?id=" + encodeURIComponent(b.getAttribute("data-vis-revocar")), {
          method: "DELETE", credentials: "same-origin",
        }).then(function () { return alive() ? cargarVisitas() : null; });
      });
    });
  }

  function cargarVisitas() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return Promise.all([fetchVisitas(VIS.dias, true, load.signal), fetchClavesVisitas(load.signal)]).then(function (res) {
      if (!owns(load.gen)) return null;
      renderVisitas(res[0], res[1]);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Recorrido del paciente (?view=recorrido) — hasta qué paso
  // llegaron los pedidos del período (admin_panel/reports_funnel.py). Período,
  // centro, servicio y médico filtran; qué pasos se ven y en qué orden es una
  // configuración que el admin cambia desde el engranaje del propio informe
  // (POST /api/admin/reports/funnel/steps), sin tocar código. Sólo conteos.
  // =====================================================================

  var CACHE_RECORRIDO = {};
  var REC_FILTROS = ["center", "service", "doctor"];
  var REC = (function () {
    var q = new URLSearchParams(location.search);
    var d = parseInt(q.get("days"), 10);
    var estado = { dias: PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS, filtros: {}, popAbierto: false, filtrosAbiertos: false, guardando: false };
    if (q.get("view") === "recorrido") REC_FILTROS.forEach(function (k) { if (q.get(k)) estado.filtros[k] = q.get(k); });
    return estado;
  })();

  function recorridoUrl(dias, filtros) {
    var url = "/api/admin/reports/funnel?days=" + dias;
    REC_FILTROS.forEach(function (k) { if (filtros && filtros[k]) url += "&" + k + "=" + encodeURIComponent(filtros[k]); });
    return url;
  }

  function fetchRecorrido(dias, force, signal, filtros) {
    var url = recorridoUrl(dias, filtros);
    if (!force && CACHE_RECORRIDO[url]) return Promise.resolve(CACHE_RECORRIDO[url]);
    return fetchJson(url, signal).then(function (j) { CACHE_RECORRIDO[url] = j; return j; });
  }

  function hasDataRecorrido(data) {
    var pasos = data && data.pasos;
    return !!(pasos && pasos.length && pasos[0].n > 0);
  }

  function sumTileRecorrido(data) {
    var sub = SUBS_BY_KEY.recorrido;
    if (!hasDataRecorrido(data)) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var pasos = data.pasos, last = pasos[pasos.length - 1];
    var hero0 = (data.hero && data.hero[0]) || {};
    // The big number and the «n de total» phrase under it share one
    // denominator: the server puts the phrase's own share next to it.
    // Without that share (one step, nothing decided yet) the card is the
    // count of requests and the lead phrase, never a share of another base.
    return sumCard(sub, pasos.length > 1 && hero0.pct != null ? fmtPct(hero0.pct) : fmtInt(pasos[0].n), hero0.texto || "");
  }

  function hayFiltroRecorrido() {
    return REC_FILTROS.some(function (k) { return !!REC.filtros[k]; });
  }

  // Un filtro con una sola opción no filtra nada: no se dibuja, salvo que ya
  // esté elegido (así siempre se puede volver a la opción vacía).
  function filtroRecorridoHtml(clave, id, label, opciones) {
    opciones = opciones || [];
    var actual = REC.filtros[clave] || "";
    if (opciones.length < 2 && !actual) return "";
    // The empty choice carries the field's name («Centro»), so the control
    // needs no caption and three of them fit one row.
    return '<span class="rep-ctl">'
      + '<select id="' + id + '" class="adm-sel" data-rec-filtro="' + clave + '" aria-label="' + esc(label) + '">'
      + '<option value="">' + esc(label) + "</option>"
      + opciones.map(function (o) {
        return '<option value="' + esc(o.id) + '"' + (o.id === actual ? " selected" : "") + ">" + esc(o.nombre) + "</option>";
      }).join("")
      + "</select></span>";
  }

  // `sinDatos`: the steps the system does not record. Named once, inside the
  // step chooser where the reader decides which steps to show — not as a
  // footnote under the report (critical review 08.11, E13).
  function pasosRecorridoHtml(config, sinDatos) {
    var nota = sinDatos && sinDatos.length
      ? '<p class="rep-rec-nota">' + esc(T("admin.reports.reports.recorrido_no_se_mide", { pasos: sinDatos.join("; ") },
        "No se mide, porque el sistema no lo registra: " + sinDatos.join("; ") + ".")) + "</p>"
      : "";
    var encendidos = config.filter(function (s) { return s.on; }).length;
    var filas = config.map(function (s, i) {
      var unico = s.on && encendidos === 1;
      return '<div class="rep-rec-step">'
        + '<label class="cnt-gear-opt"><input type="checkbox" data-rec-on="' + esc(s.key) + '"'
        + (s.on ? " checked" : "") + (unico || REC.guardando ? " disabled" : "") + "><span>" + esc(s.label) + "</span></label>"
        + '<button type="button" class="rep-rec-move rep-rec-move--up" data-rec-move="-1" data-rec-key="' + esc(s.key) + '"'
        + ' aria-label="' + esc(T("admin.reports.reports.recorrido_subir", { paso: s.label }, "Subir «" + s.label + "»")) + '"'
        + (i === 0 || REC.guardando ? " disabled" : "") + '><span class="adm-icon" aria-hidden="true"></span></button>'
        + '<button type="button" class="rep-rec-move" data-rec-move="1" data-rec-key="' + esc(s.key) + '"'
        + ' aria-label="' + esc(T("admin.reports.reports.recorrido_bajar", { paso: s.label }, "Bajar «" + s.label + "»")) + '"'
        + (i === config.length - 1 || REC.guardando ? " disabled" : "") + '><span class="adm-icon" aria-hidden="true"></span></button>'
        + "</div>";
    }).join("");
    var label = T("admin.reports.reports.recorrido_elegir_pasos", null, "Elegir pasos");
    return '<div class="cnt-gear-wrap rep-rec-gear" id="rep-rec-gear-wrap">'
      + '<button type="button" class="cnt-gear-btn" id="rep-rec-gear" aria-label="' + esc(label) + '" title="' + esc(label) + '"'
      + ' aria-haspopup="true" aria-expanded="' + (REC.popAbierto ? "true" : "false") + '"><span class="adm-icon" aria-hidden="true"></span></button>'
      + '<div class="cnt-gear-pop" id="rep-rec-pop"' + (REC.popAbierto ? ' style="display:flex"' : "") + ">" + filas + nota
      + '<p class="rep-rec-err" aria-live="assertive" hidden></p></div></div>';
  }

  // `acciones` ({step key: phrase}): what the desk does on a screen of its own
  // about a step's losses (marking attendance), said next to that step when the
  // hero has not said it already.
  function perdidasRecorridoHtml(pasos, acciones) {
    var items = [];
    for (var i = 1; i < pasos.length; i++) {
      var p = pasos[i], prev = pasos[i - 1];
      if (p.n === prev.n) continue;
      var linea = T("admin.reports.reports.recorrido_paso_de", { paso: p.label, n: fmtInt(p.n), de: fmtInt(prev.n) },
        "«" + p.label + "»: " + fmtInt(p.n) + " de " + fmtInt(prev.n) + ".");
      // The action stands in for its own reason in the list («3 sin asistencia
      // marcada — se marca en Pacientes de hoy»): one phrase, one count.
      var accion = acciones && acciones[p.key];
      var puesta = false;
      var motivos = (p.motivos || []).map(function (m) {
        if (accion && accion.texto && accion.motivo === m.key && accion.texto.indexOf(m.texto) === 0) { puesta = true; return Charts.frase(fraseAbrible(accion)); }
        return esc(m.texto);
      });
      // An action that names no reason of its own is a sentence of its own after them.
      var aparte = accion && accion.texto && !puesta ? " " + Charts.frase(fraseAbrible(accion)) : "";
      items.push("<li><b>" + esc(linea) + "</b>" + (motivos.length ? " " + motivos.join(", ") + "." : "") + aparte + "</li>");
    }
    return items.length ? '<ul class="rep-rec-list">' + items.join("") + "</ul>" : "";
  }

  function renderRecorrido(data) {
    var body = $("#rep-body");
    Charts.setAncho(body.clientWidth);
    var tiene = hasDataRecorrido(data);
    if (!hayFiltroRecorrido()) marcarDisponible("recorrido", tiene);
    var op = data.opciones || {};
    var html = crumbHtml("recorrido");
    var sinDatos = tiene ? (data.sin_datos || []).map(function (s) { return s.label; }) : [];
    if (tiene) html += Charts.hero((data.hero || []).map(fraseAbrible));
    var filtros = filtroRecorridoHtml("center", "rep-rec-centro", T("admin.reports.reports.recorrido_centro", null, "Centro"), op.centros)
      + filtroRecorridoHtml("service", "rep-rec-servicio", T("admin.reports.reports.recorrido_servicio", null, "Servicio"), op.servicios)
      + filtroRecorridoHtml("doctor", "rep-rec-medico", T("admin.reports.reports.recorrido_medico", null, "Médico"), op.medicos);
    // On a phone the period stays in sight and the three filters open from the
    // funnel icon (open by themselves while one of them is set).
    var filtrar = T("admin.reports.reports.recorrido_filtrar", null, "Filtrar");
    var abiertos = REC.filtrosAbiertos || hayFiltroRecorrido();
    html += '<div class="rep-controls rep-controls--rec' + (abiertos ? " rep-controls--abiertos" : "") + '">'
      + '<span class="rep-ctl">' + periodoControlHtml("rep-periodo", REC.dias) + "</span>"
      + (filtros ? '<button type="button" class="cnt-gear-btn rep-rec-filtrar" id="rep-rec-filtrar" aria-label="' + esc(filtrar) + '" title="' + esc(filtrar)
        + '" aria-expanded="' + (abiertos ? "true" : "false") + '"><span class="adm-icon" aria-hidden="true"></span></button>' : "")
      + pasosRecorridoHtml(data.pasos_config || [], sinDatos)
      + (filtros ? '<span class="rep-rec-filtros">' + filtros + "</span>" : "")
      + "</div>";
    if (!tiene) {
      html += informeVacioHtml(hayFiltroRecorrido()
        ? T("admin.reports.reports.recorrido_sin_pedidos_filtro", null, "Ninguna solicitud con estos filtros en el período.")
        : null);
    } else {
      var mayor = data.mayor_perdida;
      var etiquetas = {};
      data.pasos.forEach(function (p) { etiquetas[p.key] = p.label; });
      var chart = Charts.lollipop(data.pasos.map(function (p) { return { nombre: p.label, valor: p.n }; }), {
        resaltar: mayor ? [etiquetas[mayor.hasta]] : [],
        read: T("admin.reports.reports.read_recorrido", null, "Cada punto es cuántas solicitudes del período llegaron a ese paso; cada paso cuenta sólo a las que pasaron también los anteriores."),
      });
      html += seccion(T("admin.reports.reports.titulo_recorrido_hasta_donde", null, "Hasta dónde llegaron"), chart);
      html += seccionOpcional(T("admin.reports.reports.titulo_recorrido_donde_quedaron", null, "Dónde se quedaron"), perdidasRecorridoHtml(data.pasos, data.acciones));
    }
    body.innerHTML = html;
    conectarRecorrido(body, data.pasos_config || []);
  }

  function actualizarUrlRecorrido() {
    try {
      var q = new URLSearchParams();
      q.set("view", "recorrido");
      if (REC.dias !== DEFAULT_DIAS) q.set("days", String(REC.dias));
      REC_FILTROS.forEach(function (k) { if (REC.filtros[k]) q.set(k, REC.filtros[k]); });
      history.replaceState(history.state, "", location.pathname + "?" + q.toString());
    } catch (e) { /* la URL es comodidad, nunca requisito */ }
  }

  function guardarPasosRecorrido(body, config) {
    REC.guardando = true;
    Array.prototype.forEach.call(body.querySelectorAll("#rep-rec-pop input, #rep-rec-pop button"), function (el) { el.disabled = true; });
    var err = $("#rep-rec-pop .rep-rec-err", body);
    return fetch("/api/admin/reports/funnel/steps", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps: config.map(function (s) { return { key: s.key, on: !!s.on }; }) }),
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (j) {
        if (!res.ok) throw new Error((j && j.error) || T("admin.reports.reports.recorrido_error_guardar", null, "No se pudo guardar. Probá de nuevo."));
        return j;
      });
    }).then(function () {
      REC.guardando = false;
      if (!alive()) return null;
      CACHE_RECORRIDO = {};
      return cargarRecorrido();
    }, function (e) {
      REC.guardando = false;
      if (!alive()) return null;
      Array.prototype.forEach.call(body.querySelectorAll("#rep-rec-pop input, #rep-rec-pop button"), function (el) { el.disabled = false; });
      if (err) { err.textContent = e && e.message ? e.message : ""; err.hidden = false; }
      return null;
    });
  }

  function conectarRecorrido(body, config) {
    var sel = $("#rep-periodo", body);
    if (sel) sel.addEventListener("change", function () {
      if (!alive() || !ready) return;
      var d = parseInt(sel.value, 10);
      REC.dias = PERIODOS.indexOf(d) !== -1 ? d : DEFAULT_DIAS;
      actualizarUrlRecorrido();
      cargarRecorrido();
    });
    Array.prototype.forEach.call(body.querySelectorAll("[data-rec-filtro]"), function (s) {
      s.addEventListener("change", function () {
        if (!alive() || !ready) return;
        var k = s.getAttribute("data-rec-filtro");
        if (s.value) REC.filtros[k] = s.value; else delete REC.filtros[k];
        // The row the user just touched stays open, even when it went back to empty.
        REC.filtrosAbiertos = true;
        actualizarUrlRecorrido();
        cargarRecorrido();
      });
    });
    var filtrar = $("#rep-rec-filtrar", body);
    if (filtrar) filtrar.addEventListener("click", function () {
      // A set filter is never hidden: the figures would read as the whole period.
      if (hayFiltroRecorrido()) return;
      REC.filtrosAbiertos = !(filtrar.getAttribute("aria-expanded") === "true");
      filtrar.setAttribute("aria-expanded", REC.filtrosAbiertos ? "true" : "false");
      var caja = filtrar.parentNode;
      if (caja && caja.classList) caja.classList.toggle("rep-controls--abiertos", REC.filtrosAbiertos);
    });
    var gear = $("#rep-rec-gear", body), pop = $("#rep-rec-pop", body);
    if (gear && pop) gear.addEventListener("click", function () {
      REC.popAbierto = pop.style.display !== "flex";
      pop.style.display = REC.popAbierto ? "flex" : "none";
      gear.setAttribute("aria-expanded", REC.popAbierto ? "true" : "false");
    });
    if (!pop) return;
    pop.addEventListener("change", function (e) {
      var key = e.target && e.target.getAttribute("data-rec-on");
      if (!key || REC.guardando) return;
      guardarPasosRecorrido(body, config.map(function (s) {
        return { key: s.key, on: s.key === key ? !!e.target.checked : s.on };
      }));
    });
    pop.addEventListener("click", function (e) {
      var btn = e.target && e.target.closest ? e.target.closest("[data-rec-move]") : null;
      if (!btn || btn.disabled || REC.guardando) return;
      var key = btn.getAttribute("data-rec-key"), paso = parseInt(btn.getAttribute("data-rec-move"), 10);
      var i = config.findIndex(function (s) { return s.key === key; }), j = i + paso;
      if (i < 0 || j < 0 || j >= config.length) return;
      var nueva = config.slice();
      nueva[i] = config[j];
      nueva[j] = config[i];
      guardarPasosRecorrido(body, nueva);
    });
  }

  // Un click fuera del engranaje cierra la lista de pasos (como «Elegir columnas»).
  function cerrarPasosRecorrido(e) {
    if (!REC.popAbierto || !alive()) return;
    var wrap = root.querySelector("#rep-rec-gear-wrap");
    if (!wrap || (e.target && wrap.contains(e.target))) return;
    REC.popAbierto = false;
    var pop = wrap.querySelector("#rep-rec-pop"), gear = wrap.querySelector("#rep-rec-gear");
    if (pop) pop.style.display = "none";
    if (gear) gear.setAttribute("aria-expanded", "false");
  }

  function cargarRecorrido() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    return fetchRecorrido(REC.dias, false, load.signal, REC.filtros).then(function (data) {
      if (!owns(load.gen)) return null;
      renderRecorrido(data);
    }).catch(function (e) {
      if (!owns(load.gen)) return null;
      if (String(e && e.message) === "redirect") return null;
      $("#rep-body").innerHTML = bodyErrorHtml(e);
    });
  }

  // =====================================================================
  // sub-reporte: Resumen (portada/default) — hero de Gestión + una tarjeta
  // por sub-reporte, cada tarjeta linkeando al sub-reporte completo. Ningún
  // fetch propio: reusa fetch*()/CACHE_* de las secciones de arriba en
  // paralelo (Promise.all), cada una envuelta en su propio .catch(→null)
  // para que UN endpoint caído nunca deje la portada en blanco
  // (report-design ley 1: el hero cae a una frase neutra con poca data,
  // nunca revienta). Períodos: el que cada sub-reporte ya tiene
  // seleccionado (DIAS_GESTION/DIAS_PACIENTES/DIAS_IA) o DEFAULT_DIAS donde
  // el sub-reporte no tiene selector propio (Médicos, Centros, Panel de
  // login).
  // =====================================================================

  function sumCard(sub, valor, hallazgo, chartHtml, extraClass) {
    return '<a class="rep-sumcard' + (chartHtml ? ' rep-sumcard--chart' : '')
      + (extraClass ? ' ' + extraClass : '') + '" href="' + reportHref(sub.key) + '">'
      + '<div class="rep-sumcard-copy">'
      + '<div class="rep-sumcard-title">' + esc(subLabel(sub)) + "</div>"
      + '<div class="rep-sumcard-value">' + esc(valor) + "</div>"
      + '<div class="rep-sumcard-find">' + esc(hallazgo) + "</div>"
      + '</div>'
      + (chartHtml || '')
      + "</a>";
  }

  function esperaCorta(horas) {
    if (horas == null) return T("admin.reports.reports.sin_dato", null, "s/d");
    if (horas < 48) return fmtDec(horas, horas < 10 ? 1 : 0) + " h";
    return fmtDec(horas / 24, 1) + " días";
  }

  // Curva mini de la espera pendiente (Charts.densidad): con >=48h de
  // mediana la serie entera pasa a días (más legible que "72 h"), si no
  // queda en horas. Con <3 valores no hay curva que valga (KDE de 1-2
  // puntos es ruido, no señal) — la tarjeta queda sólo con el número grande
  // y el hallazgo, sin el "rep-density-mini" div.
  function densidadEsperaHtml(horas) {
    horas = (horas || []).filter(function (v) { return isFinite(v); });
    if (horas.length < 3) return "";
    var sorted = horas.slice().sort(function (a, b) { return a - b; });
    var n = sorted.length;
    var medianaH = n % 2 ? sorted[(n - 1) / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    var enDias = medianaH >= 48;
    var vals = enDias ? horas.map(function (hh) { return hh / 24; }) : horas;
    var fmtX = function (v) { return fmtDec(v, 1) + (enDias ? " días" : " h"); };
    return '<div class="rep-card-chart rep-density-mini">' + Charts.densidad(vals, { mini: true, fmtX: fmtX }) + "</div>";
  }

  // ---------------------------------------------------------------------
  // hasData*(payload): UNA por sub-reporte, al lado de su sumTile*. Decide
  // tanto si la portada dibuja la tarjeta como si la página del sub-reporte
  // tiene algo que mostrar (progressive disclosure, responsable del producto 2026-10: "just
  // don't show the items"). Conservadoras: payload null (fetch caído), todo
  // en cero o listas vacías = sin datos.
  // ---------------------------------------------------------------------
  function hasDataGestion(g) {
    var k = g && g.kpis;
    if (!k) return false;
    var f = g.funnel || {};
    return k.solicitudes_periodo > 0 || k.pendientes > 0 || f.solicitudes > 0 || f.aprobadas > 0;
  }

  function sumTileGestion(g) {
    var sub = SUBS_BY_KEY.gestion;
    if (!g || !g.kpis) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var k = g.kpis;
    var valor = esperaCorta(k.mediana_horas_pendiente);
    var hallazgo;
    if (k.mediana_horas_pendiente == null) {
      hallazgo = T("admin.reports.reports.no_hay_pendientes_cola_al_dia", null, "No hay solicitudes pendientes: la cola está al día.");
    } else {
      var nPend = fmtInt(k.pendientes);
      var cola = Tn("admin.reports.reports.hallazgo_espera_mediana_pendientes", k.pendientes, { n: nPend }, "Es lo que lleva sin resolver la única solicitud pendiente.", "La mitad de las " + nPend + " solicitudes pendientes lleva ese tiempo o más sin resolver.");
      // What the figure is; the oldest request only when it waits well beyond
      // the median (it is shown nowhere else). Two catalog sentences: the space
      // between them is put here (generated catalogs trim it).
      hallazgo = cola.replace(/\s+$/, "");
      var maxH = k.max_horas_pendiente;
      if (k.pendientes > 1 && maxH != null && maxH >= 2 * k.mediana_horas_pendiente && esperaCorta(maxH) !== valor) {
        hallazgo += " " + T("admin.reports.reports.la_mas_antigua_lleva", { espera: esperaCorta(maxH) }, "La más antigua lleva " + esperaCorta(maxH) + ".");
      }
    }
    return sumCard(sub, valor, hallazgo, densidadEsperaHtml(k.esperas_pendientes_horas));
  }

  // Ocupación por médico (%) para los que tienen agenda con capacidad
  // medible (slots_14d>0) — la muestra que alimenta la curva de densidad,
  // compartida entre la tarjeta mini de Resumen y la sección de tamaño
  // completo del sub-reporte Médicos (misma fuente, un solo cómputo).
  function cargaPorMedico(rows) {
    return (rows || []).filter(function (r) { return r.slots_14d > 0; })
      .map(function (r) { return 100 * r.turnos_14d / r.slots_14d; });
  }

  // Hay datos cuando la cifra de la tarjeta no es cero: al menos un turno en
  // lugares medibles de los próximos 14 días. Agendas vacías dan «0,0%», y un
  // cero no es un dato (un médico sin agenda es catálogo, no actividad).
  function hasDataMedicos(rows) {
    if (!rows || !rows.length) return false;
    return rows.some(function (r) { return r.slots_14d > 0; })
      && rows.some(function (r) { return r.turnos_14d > 0; });
  }

  function sumTileMedicos(rows) {
    var sub = SUBS_BY_KEY.medicos;
    if (!rows || !rows.length) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_medicos_cargados_todavia", null, "Sin médicos cargados todavía."));
    var tot = cargaTotal(rows), slots = tot.slots, turnos = tot.turnos;
    if (!slots) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_agendas_capacidad_medible", null, "Todavía no hay agendas con capacidad medible."));
    var carga = 100 * turnos / slots;
    var cargas = cargaPorMedico(rows);
    var nTurnosTotal = fmtInt(turnos), nSlots = fmtInt(slots);
    var turnosTxt = Tn("admin.reports.reports.carga_turnos", turnos, { n: nTurnosTotal }, "{n} turno", "{n} turnos");
    var lugaresTxt = Tn("admin.reports.reports.carga_lugares", slots, { n: nSlots }, "{n} lugar", "{n} lugares");
    var inicio = T("admin.reports.reports.carga_total_proximos_14d", { turnos: turnosTxt, lugares: lugaresTxt }, "Carga total de los próximos 14 días: " + turnosTxt + " sobre " + lugaresTxt + ". ");
    // The summary card says the figure and nothing on how to read its curve:
    // that lives in the report's «Cómo leerlo» (ui-expert 08.11, theme 12).
    var hallazgo = cargas.length >= 3 ? inicio.trim()
      : inicio + T("admin.reports.reports.sin_suficientes_medicos_agenda", null, "Todavía no hay suficientes médicos con agenda para mostrar cómo se reparte.");
    var chart = cargas.length >= 3
      ? '<div class="rep-card-chart rep-density-mini">' + Charts.densidad(cargas, { mini: true, fmtX: function (v) { return fmtPct(v); } }) + "</div>"
      : "";
    return sumCard(sub, fmtPct(carga), hallazgo, chart);
  }

  function pacienteBarras(series) {
    series = series || [];
    // Arranca en el primer mes con actividad real: una serie que empieza con
    // varios meses en 0 (antes de que el sitio existiera) no aporta nada a
    // leer, sólo aplana la curva de los meses que sí importan.
    var i0 = 0;
    while (i0 < series.length && !(series[i0].pacientes > 0)) i0++;
    series = series.slice(i0);
    // Un solo mes con actividad no es una tendencia: una barra sola estirada
    // al ancho de la tarjeta se lee como un bloque sin significado — mejor
    // ningún gráfico hasta que haya al menos dos meses que comparar.
    if (series.length < 2) return "";
    var max = Math.max.apply(null, series.map(function (r) { return r.pacientes || 0; }).concat([1]));
    var monthLabel = function (r) {
      var d = new Date(r.mes + "T12:00:00");
      return mesNombre(d.getMonth()).slice(0, 3).toLowerCase() + " " + d.getFullYear();
    };
    return '<div class="rep-card-chart rep-patients-chart" aria-label="' + esc(T("admin.reports.reports.aria_pacientes_activos_por_mes", null, "Pacientes activos por mes, últimos 12 meses")) + '">'
      + '<div class="rep-mini-bars">' + series.map(function (r, i) {
        var h = r.pacientes ? Math.max(4, 100 * r.pacientes / max) : 0;
        var nPac = fmtInt(r.pacientes || 0);
        var title = Tn("admin.reports.reports.tooltip_mes_n_pacientes", r.pacientes || 0, { mes: monthLabel(r), n: nPac }, "{mes}: {n} paciente", "" + monthLabel(r) + ": " + nPac + " pacientes");
        var lastValue = i === series.length - 1 ? '<b>' + fmtInt(r.pacientes || 0) + '</b>' : '';
        return '<span class="rep-mini-bar" style="height:' + h.toFixed(1) + '%" title="' + esc(title) + '">' + lastValue + '</span>';
      }).join("") + '</div><div class="rep-mini-axis"><span>' + esc(monthLabel(series[0]))
      + '</span><span>' + esc(monthLabel(series[series.length - 1])) + '</span></div></div>';
  }

  function hasDataPacientes(p) {
    if (!p || !p.kpis) return false;
    var act = p.actividad || {};
    return p.kpis.total_cuentas > 0 || act.pacientes_con_solicitud > 0;
  }

  function sumTilePacientes(p) {
    var sub = SUBS_BY_KEY.pacientes;
    if (!p || !p.kpis) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var dias = p.dias || DIAS_PACIENTES;
    var fig = figuraPacientes(p);
    if (fig.cuentas) {
      return sumCard(sub, fmtInt(fig.valor), T("admin.reports.reports.hallazgo_cuentas_sin_solicitud", { dias: dias },
        "Cuentas registradas. Todavía ninguna pidió un turno en los últimos " + dias + " días."));
    }
    var valor = fig.valor == null ? T("admin.reports.reports.sin_dato", null, "s/d") : fmtInt(fig.valor);
    return sumCard(sub, valor, fig.etiqueta, pacienteBarras(p.actividad_mensual));
  }

  // Búsquedas: reusa clusterizarBusquedas() tal cual (report-design: "reuse
  // the same clustering function, don't fork it") — el número grande es el
  // total de búsquedas del período, el hallazgo nombra el cluster más
  // buscado y cierra con el conteo de búsquedas que no encontraron nada
  // (el so-what: cuánto catálogo le falta a las palabras de la gente).
  function hasDataBusquedas(data, catalogo) {
    var top = data && data.top;
    if (!top || !top.length || !catalogo) return false;
    return top.some(function (r) { return r.veces > 0; });
  }

  function sumTileBusquedas(data, catalogo) {
    var sub = SUBS_BY_KEY.busquedas;
    var top = data && data.top;
    if (!top || !top.length || !catalogo) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_busquedas_registradas_todavia", null, "Sin búsquedas registradas todavía."));
    var totalVeces = top.reduce(function (a, r) { return a + r.veces; }, 0);
    var agrupado = clusterizarBusquedas(top, catalogo);
    var todos = agrupado.especialidades.concat(agrupado.estudios, agrupado.personas);
    var hallazgo = "";
    if (todos.length) {
      var topCluster = todos.slice().sort(function (a, b) { return b.total - a.total; })[0];
      var palabraBusq = plural(topCluster.total, T("admin.reports.reports.busqueda_singular", null, "búsqueda"), T("admin.reports.reports.busqueda_plural", null, "búsquedas"));
      hallazgo += T("admin.reports.reports.hallazgo_lo_mas_buscado_fue", { nombre: topCluster.row.nombre, n: fmtInt(topCluster.total), palabra: palabraBusq },
        "Lo más buscado fue " + topCluster.row.nombre + " (" + fmtInt(topCluster.total) + " " + palabraBusq + "). ");
    }
    hallazgo += agrupado.sinMatch.length
      ? T("admin.reports.reports.hallazgo_n_busquedas_sin_match", { n: fmtInt(agrupado.sinMatch.length), palabra: plural(agrupado.sinMatch.length, T("admin.reports.reports.busqueda_no_encontro_singular", null, "búsqueda distinta no encontró"), T("admin.reports.reports.busqueda_no_encontro_plural", null, "búsquedas distintas no encontraron")) },
        "" + fmtInt(agrupado.sinMatch.length) + " " + plural(agrupado.sinMatch.length, T("admin.reports.reports.busqueda_no_encontro_singular", null, "búsqueda distinta no encontró"), T("admin.reports.reports.busqueda_no_encontro_plural", null, "búsquedas distintas no encontraron")) + " nada en el catálogo.")
      : T("admin.reports.reports.hallazgo_todo_encontro_algo_catalogo", null, "Todo lo que buscaron los pacientes encontró algo en el catálogo.");
    return sumCard(sub, fmtInt(totalVeces), hallazgo);
  }

  // El ranking "centro mejor valorado por estrellas" murió (report-design:
  // "el mimo al paciente no es el objetivo") — la tarjeta usa la misma
  // tarjeta editorial que ya arma reports.py (comparison.card, ángulo
  // de eficiencia) y sólo cae al conteo total de reseñas si esa clave
  // todavía no vino (dato viejo / análisis degradado a "curado").
  function hasDataCentros(c) {
    return !!c && Object.keys(c.centers || {}).length > 0;
  }

  function sumTileCentros(c) {
    var sub = SUBS_BY_KEY.centros;
    var centros = (c && c.centers) || {};
    var slugs = Object.keys(centros);
    if (!slugs.length) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_analisis_resenas_todavia", null, "Sin análisis de reseñas cargado todavía."));
    var tarjeta = c.comparison && c.comparison.card;
    if (tarjeta && tarjeta.value) return sumCard(sub, tarjeta.value, tarjeta.finding || "");
    var total = slugs.reduce(function (a, s) { return a + (centros[s].count || 0); }, 0);
    return sumCard(sub, fmtInt(total), Tn("admin.reports.reports.hallazgo_resenas_analizadas_centros", slugs.length, { n: fmtInt(slugs.length) },
      "Reseñas de Google analizadas en {n} centro.", "Reseñas de Google analizadas en " + fmtInt(slugs.length) + " centros."));
  }

  function hasDataIa(data) {
    var r = data && data.resumen;
    return !!r && r.operaciones > 0;
  }

  function sumTileIa(data) {
    var sub = SUBS_BY_KEY.ia;
    var r = (data && data.resumen) || {};
    if (!r.operaciones) return sumCard(sub, data ? "US$ 0" : T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_llamadas_ia_periodo", null, "Todavía no se registró ninguna llamada de IA en este período."));
    var ahorro = data.ahorro;
    // One sentence per card: what the AI gave back. How the monthly figure is
    // projected is said in the report, not on the card.
    var hallazgo = (ahorro && ahorro.disponible && ahorro.horas_operador != null)
      ? Tn("admin.reports.reports.le_devolvio_horas_equipo", Number(ahorro.horas_operador), { horas: fmtHoras(ahorro.horas_operador) }, "Le devolvió {horas} hora de trabajo al equipo.", "Le devolvió " + fmtHoras(ahorro.horas_operador) + " horas de trabajo al equipo.")
      : T("admin.reports.reports.se_reconocio_pct_subido", { pct: fmtPct(r.tasa_reconocimiento) }, "Se reconoció el " + fmtPct(r.tasa_reconocimiento) + " de lo subido.");
    return sumCard(sub, r.run_rate_mensual_usd == null ? T("admin.reports.reports.sin_dato", null, "s/d") : fmtUsd(r.run_rate_mensual_usd), hallazgo);
  }

  function loginDonut(pct, title) {
    return '<div class="rep-card-chart rep-donut-wrap" title="' + esc(title) + '">'
      + '<span class="rep-donut" style="--p:' + pct.toFixed(1) + '"></span></div>';
  }

  // Una sesión que intentó entrar ya es dato; una cifra de 0% no lo es.
  function hasDataLogin(data) {
    return !!(data && data.sesiones > 0);
  }

  function sumTileLogin(data) {
    var sub = SUBS_BY_KEY.login;
    var d = data || {};
    var sesiones = d.sesiones || 0, conError = d.sesiones_con_error || 0;
    var dias = d.dias || DEFAULT_DIAS;
    if (!sesiones) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_sesiones_todavia", null, "Sin sesiones todavía."));
    if (!conError) {
      return sumCard(sub, "0%",
        T("admin.reports.reports.login_card_sin_errores", { dias: dias },
          "Todas las sesiones de login de los últimos " + dias + " días entraron sin errores."));
    }
    var pct = 100 * conError / sesiones;
    return sumCard(sub, fmtPct(pct),
      T("admin.reports.reports.login_card_con_errores", { m: fmtInt(conError), total: fmtInt(sesiones), dias: dias },
        "" + fmtInt(conError) + " de " + fmtInt(sesiones) + " sesiones de login chocaron con un error en los últimos " + dias + " días."),
      loginDonut(pct, T("admin.reports.reports.login_tile_error_sub", { m: fmtInt(conError), total: fmtInt(sesiones) },
        "" + fmtInt(conError) + " de " + fmtInt(sesiones))));
  }

  // Cuatro tarjetas nuevas (2026-07-24, decisión #9): mismo patrón sumCard,
  // cero fetch propio (reusan fetchRecupero/fetchSumar/fetchEstadistica/
  // fetchCapacidad de sus propios sub-reportes, cada uno con su propio CACHE_*
  // — la portada nunca duplica una URL de fetch).
  function hasDataRecupero(data) {
    return !!(data && data.kpis) && data.kpis.consultas_con_cobertura > 0;
  }

  function sumTileRecupero(data) {
    var sub = SUBS_BY_KEY.recupero;
    if (!data || !data.kpis) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var k = data.kpis;
    if (!k.consultas_con_cobertura) return sumCard(sub, "0", T("admin.reports.reports.sin_consultas_obra_social_periodo", null, "Todavía no hay consultas con obra social registrada en este período."));
    var cierre = k.pct_del_total == null ? "." : T("admin.reports.reports.coma_pct_del_total", { pct: pctNum(k.pct_del_total) }, ", " + pctNum(k.pct_del_total) + "% del total.");
    var hallazgo = T("admin.reports.reports.hallazgo_consultas_obra_social_dias", { dias: data.dias }, "Consultas con obra social registrada en los últimos " + data.dias + " días") + cierre;
    return sumCard(sub, fmtInt(k.consultas_con_cobertura), hallazgo);
  }

  function hasDataSumar(data) {
    return !!(data && data.kpis) && data.kpis.prestaciones > 0;
  }

  function sumTileSumar(data) {
    var sub = SUBS_BY_KEY.sumar;
    if (!data || !data.kpis) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var k = data.kpis;
    if (!k.prestaciones) return sumCard(sub, "0", T("admin.reports.reports.sin_prestaciones_publica_periodo", null, "Todavía no hubo prestaciones a población exclusivamente pública en este período."));
    var cierre = k.pct_del_total == null ? "." : T("admin.reports.reports.coma_pct_del_total", { pct: pctNum(k.pct_del_total) }, ", " + pctNum(k.pct_del_total) + "% del total.");
    var hallazgo = T("admin.reports.reports.hallazgo_prestaciones_publica_dias", { dias: data.dias }, "Prestaciones a población con cobertura exclusivamente pública en los últimos " + data.dias + " días") + cierre;
    return sumCard(sub, fmtInt(k.prestaciones), hallazgo);
  }

  function hasDataEstadistica(data) {
    return !!(data && data.kpis) && data.kpis.consultas_totales > 0;
  }

  function sumTileEstadistica(data) {
    var sub = SUBS_BY_KEY.estadistica;
    if (!data || !data.kpis) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    var k = data.kpis;
    var mesLabel = mesLabelDe(data.mes);
    if (!k.consultas_totales) {
      return sumCard(sub, "0", T("admin.reports.reports.sin_consultas_registradas_mes", { mes: mesLabel }, "Todavía no hay consultas registradas para " + mesLabel + "."));
    }
    var cierre = k.pct_asistencia == null ? "." : T("admin.reports.reports.coma_pct_asistencia_efectiva", { pct: pctNum(k.pct_asistencia) }, ", " + pctNum(k.pct_asistencia) + "% de asistencia efectiva.");
    var hallazgo = T("admin.reports.reports.hallazgo_consultas_registradas_en", { mes: mesLabel }, "Consultas registradas en " + mesLabel) + cierre;
    return sumCard(sub, fmtInt(k.consultas_totales), hallazgo);
  }

  // Hay datos cuando la cifra de la tarjeta no es cero: una especialidad con
  // ocupación mayor que cero (al menos un turno), o, sin ocupación, las
  // especialidades pedidas sin agenda o las solicitudes de 8 semanas. Una
  // ocupación de 0 % no es un dato y no sale.
  function hasDataCapacidad(data) {
    if (!data) return false;
    var conPct = (data.ocupacion || []).some(function (r) { return r.pct > 0; });
    var demanda = (data.demanda_semanal || []).some(function (r) { return r.solicitudes > 0; });
    return conPct || demanda || (data.sin_oferta || []).length > 0;
  }

  function sumTileCapacidad(data) {
    var sub = SUBS_BY_KEY.capacidad;
    if (!data) return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_datos_disponibles_todavia", null, "Sin datos disponibles todavía."));
    // La conclusión es la del titular de la página: el servidor la elige con
    // una sola regla (_capacity_rule, con el umbral del 2 %) y la marca en
    // hero[0].regla. La tarjeta no la vuelve a deducir: nombraba «1 —
    // Vacunas y prevención» mientras la página decía que la capacidad cubre
    // la demanda (15.11).
    var regla = reglaCapacidad(data);
    var sinOferta = (data.sin_oferta || []).slice().sort(function (a, b) { return b.solicitudes - a.solicitudes; });
    if (regla === "sin_horas" && sinOferta.length) {
      var nSin = fmtInt(sinOferta.length);
      // One specialty is named, not counted as «1 especialidades» (Opus review 2026-11-04).
      if (sinOferta.length === 1) {
        return sumCard(sub, nSin, T("admin.reports.reports.hallazgo_capacidad_sin_oferta_one", { label: sinOferta[0].label },
          "" + sinOferta[0].label + " tiene demanda reciente y ninguna hora de agenda."));
      }
      return sumCard(sub, nSin, T("admin.reports.reports.hallazgo_capacidad_sin_oferta", { n: nSin, label: sinOferta[0].label },
        "" + nSin + " especialidades con demanda reciente no tienen ninguna hora de agenda; la más pedida es " + sinOferta[0].label + "."));
    }
    var mas = regla === "sin_medida" ? null : especialidadMasOcupada(data.ocupacion);
    var cubre = T("admin.reports.reports.hallazgo_capacidad_cubre", null, "La capacidad ofrecida cubre la demanda de los próximos 14 días en todas las especialidades con datos.");
    if (mas) {
      var top = mas.top;
      var cual = mas.empatan > 1 ? textoEmpateCapacidad(mas)
        : T("admin.reports.reports.hallazgo_especialidad_mas_ocupada", { label: top.label }, "" + top.label + " es la especialidad más ocupada de los próximos 14 días.");
      if (regla === "atencion") {
        return sumCard(sub, fmtPct(top.pct), cual + " " + T("admin.reports.reports.hallazgo_capacidad_sumar_horas", null, "Sumar horas ahí es la inversión de mayor impacto."));
      }
      return sumCard(sub, fmtPct(top.pct), regla === "ok" ? cubre + " " + cual : cual);
    }
    var demanda = (data.demanda_semanal || []).reduce(function (a, r) { return a + (r.solicitudes || 0); }, 0);
    if (demanda > 0) {
      if (regla === "ok") {
        return sumCard(sub, fmtInt(demanda), T("admin.reports.reports.hallazgo_capacidad_demanda_cubierta", null,
          "Solicitudes de las últimas 8 semanas; la capacidad ofrecida cubre la demanda de los próximos 14 días en todas las especialidades con datos."));
      }
      return sumCard(sub, fmtInt(demanda), T("admin.reports.reports.hallazgo_capacidad_solo_demanda", null,
        "Solicitudes de las últimas 8 semanas. Todavía no hay capacidad medible para comparar contra esa demanda."));
    }
    if (regla === "ok") return sumCard(sub, fmtPct(0), cubre);
    return sumCard(sub, T("admin.reports.reports.sin_dato", null, "s/d"), T("admin.reports.reports.sin_capacidad_medible_comparar", null, "Todavía no hay capacidad medible para comparar contra la demanda."));
  }

  // Progressive disclosure: la portada dibuja UNA tarjeta por sub-reporte
  // CON datos y nada por los demás — ni "s/d", ni "Sin datos disponibles
  // todavía" repetido doce veces. Un fetch caído (per-fetch .catch(→null)
  // abajo) cuenta como sin datos: el fan-out de TRECE llamadas nunca deja la
  // portada en blanco por UN endpoint caído, sólo sin esa tarjeta. Cuando
  // NADA tiene datos, la portada es una sola frase tranquila.
  var CARDS_RESUMEN = [
    { key: "gestion", tiene: function (p) { return hasDataGestion(p.gestion); }, card: function (p) { return sumTileGestion(p.gestion); } },
    { key: "recorrido", tiene: function (p) { return hasDataRecorrido(p.recorrido); }, card: function (p) { return sumTileRecorrido(p.recorrido); } },
    { key: "medicos", tiene: function (p) { return hasDataMedicos(p.medicos); }, card: function (p) { return sumTileMedicos(p.medicos); } },
    { key: "pacientes", tiene: function (p) { return hasDataPacientes(p.pacientes); }, card: function (p) { return sumTilePacientes(p.pacientes); } },
    { key: "busquedas", tiene: function (p) { return hasDataBusquedas(p.busquedas, p.catalogoBusquedas); }, card: function (p) { return sumTileBusquedas(p.busquedas, p.catalogoBusquedas); } },
    { key: "centros", tiene: function (p) { return hasDataCentros(p.centros); }, card: function (p) { return sumTileCentros(p.centros); } },
    { key: "recupero", tiene: function (p) { return hasDataRecupero(p.recupero); }, card: function (p) { return sumTileRecupero(p.recupero); } },
    { key: "sumar", tiene: function (p) { return hasDataSumar(p.sumar); }, card: function (p) { return sumTileSumar(p.sumar); } },
    { key: "estadistica", tiene: function (p) { return hasDataEstadistica(p.estadistica); }, card: function (p) { return sumTileEstadistica(p.estadistica); } },
    { key: "capacidad", tiene: function (p) { return hasDataCapacidad(p.capacidad); }, card: function (p) { return sumTileCapacidad(p.capacidad); } },
    { key: "ia", tiene: function (p) { return hasDataIa(p.ia); }, card: function (p) { return sumTileIa(p.ia); } },
    { key: "login", tiene: function (p) { return hasDataLogin(p.login); }, card: function (p) { return sumTileLogin(p.login); } },
    { key: "visitas", tiene: function (p) { return hasDataVisitas(p.visitas); }, card: function (p) { return sumTileVisitas(p.visitas); } },
  ];

  function renderResumen(payload) {
    var body = $("#rep-body");
    var disponibles = {};
    var cards = "";
    CARDS_RESUMEN.forEach(function (c) {
      var tiene = !!c.tiene(payload);
      disponibles[c.key] = tiene;
      if (tiene) cards += c.card(payload);
    });
    guardarDisponibles(disponibles);
    body.innerHTML = cards
      ? '<div class="rep-sumgrid">' + cards + "</div>"
      : '<p class="rep-empty">' + esc(T("admin.reports.reports.portada_sin_datos", null, "Los informes aparecen a medida que se cargan turnos y solicitudes.")) + "</p>";
  }

  function cargarResumen() {
    if (!alive() || !ready) return Promise.resolve(null);
    var load = beginLoad();
    var signal = load.signal;
    return Promise.all([
      fetchGestion(DIAS_GESTION, false, signal).catch(function () { return null; }),
      fetchMedicos(false, signal).catch(function () { return null; }),
      fetchPacientes(DIAS_PACIENTES, false, signal).catch(function () { return null; }),
      fetchBusquedas(DIAS_BUSQUEDAS, false, signal).catch(function () { return null; }),
      fetchCatalogoBusquedas(false, signal).catch(function () { return null; }),
      fetchCentros(false, signal).catch(function () { return null; }),
      fetchRecupero(DIAS_RECUPERO, false, signal).catch(function () { return null; }),
      fetchSumar(DIAS_SUMAR, false, signal).catch(function () { return null; }),
      fetchEstadistica(MES_ESTADISTICA, false, signal).catch(function () { return null; }),
      fetchCapacidad(false, signal).catch(function () { return null; }),
      fetchIa(DIAS_IA, false, signal).catch(function () { return null; }),
      fetchLogin(DEFAULT_DIAS, false, signal).catch(function () { return null; }),
      fetchRecorrido(DEFAULT_DIAS, false, signal).catch(function () { return null; }),
      fetchVisitas(DEFAULT_DIAS, false, signal).catch(function () { return null; }),
    ]).then(function (res) {
      if (!owns(load.gen)) return null;
      renderResumen({
        gestion: res[0], medicos: res[1], pacientes: res[2],
        busquedas: res[3], catalogoBusquedas: res[4],
        centros: res[5], recupero: res[6], sumar: res[7],
        estadistica: res[8], capacidad: res[9], ia: res[10], login: res[11],
        recorrido: res[12], visitas: res[13],
      });
    });
  }

  // =====================================================================
  // directorio-primero, SIN tabs persistentes (ver comentario del
  // encabezado, decisión #9 del plan "doctor day-queue + reports expansion"):
  // el bare /admin/reportes ES la portada (renderResumen) — de ahí se entra
  // a cada sub-reporte por su tarjeta, cada página de sub-reporte abre con
  // UNA línea de breadcrumb (crumbHtml), y la navegación es SIEMPRE un full
  // page load por URL de query (?view=<key>). Nada de hash routing, nada
  // de showOnly/nav/h1 del shell de una sola página (residuo retirado con
  // el hash shell, 2026-09-01): la pantalla server-routed ya renderizó nav
  // y título.
  // =====================================================================

  // .label es SIEMPRE el fallback en español, nunca el texto ya traducido: el
  // array se arma en cada mount, y subLabel() resuelve la traducción en cada
  // render (crumbHtml()/sumCard()), así nunca se congela al idioma vigente
  // en el momento del import (mismo bug pattern que PERFIL_CAMPOS).
  var SUBS = [
    { key: "resumen", label: "Resumen", labelKey: "admin.reports.reports.sub_resumen", cargar: cargarResumen },
    { key: "gestion", label: "Gestión", labelKey: "admin.reports.reports.sub_gestion", cargar: cargarGestion },
    { key: "recorrido", label: "Recorrido del paciente", labelKey: "admin.reports.reports.sub_recorrido", cargar: cargarRecorrido },
    { key: "medicos", label: "Médicos", labelKey: "admin.reports.reports.sub_medicos", cargar: cargarMedicos },
    { key: "pacientes", label: "Pacientes", labelKey: "admin.reports.reports.sub_pacientes", cargar: cargarPacientes },
    { key: "busquedas", label: "Búsquedas", labelKey: "admin.reports.reports.sub_busquedas", cargar: cargarBusquedas },
    { key: "centros", label: "Centros", labelKey: "admin.reports.reports.sub_centros", cargar: cargarCentros },
    { key: "recupero", label: "Recupero de costos", labelKey: "admin.reports.reports.sub_recupero", cargar: cargarRecupero },
    { key: "sumar", label: "SUMAR+", labelKey: "admin.reports.reports.sub_sumar", cargar: cargarSumar },
    { key: "estadistica", label: "Estadística mensual", labelKey: "admin.reports.reports.sub_estadistica", cargar: cargarEstadistica },
    { key: "capacidad", label: "Capacidad y temporada", labelKey: "admin.reports.reports.sub_capacidad", cargar: cargarCapacidad },
    { key: "ia", label: "Costos de IA", labelKey: "admin.reports.reports.sub_ia", cargar: cargarIa },
    { key: "login", label: "Panel de login", labelKey: "admin.reports.reports.sub_login", cargar: cargarLogin },
    { key: "visitas", label: "Visitas", labelKey: "admin.reports.reports.sub_visitas", cargar: cargarVisitas },
  ];
  var SUBS_BY_KEY = Object.create(null);
  SUBS.forEach(function (s) { SUBS_BY_KEY[s.key] = s; });
  function subLabel(sub) { return T(sub.labelKey, null, sub.label); }
  function reportHref(key) {
    return key === "resumen" ? "/admin/reportes" : "/admin/reportes?view=" + encodeURIComponent(key);
  }

  function subActual() {
    var direct = new URLSearchParams(location.search).get("view") || "resumen";
    return SUBS_BY_KEY[direct] ? direct : "resumen";
  }

  // El host del contenido se construye UNA sola vez por mount (no en cada
  // cargar*) — reconstruirlo parpadearía la sección entera en vez de sólo
  // cambiar el contenido (ui-rules: nada de re-renders async que parpadean).
  // #rep-body es contrato compartido con admin-charts.mjs (anchoAuto).
  function ensureShell() {
    if (root.querySelector("#rep-body")) return;
    // While the first answer is in flight the page says so: the template's
    // load deadline is over once the dependencies are in, and a slow report
    // is a wait, not a failure. A render or the data-failure message replaces it.
    root.innerHTML = '<div id="rep-body"><p class="rep-quiet" role="status">'
      + esc(T("admin.reports.entrevistas.cargando", null, "Cargando…")) + "</p></div>";
  }

  function route() {
    if (!alive()) return null;
    ensureShell();
    var key = subActual();
    return SUBS_BY_KEY[key].cargar();
  }

  mounts.set(root, dispose);
  // .ready follows the first dispatched load. Missing dependencies reject
  // with visible recovery (or delegate to the template), never silent success.
  var dependencies = Promise.all([i18nReady, datesReady, chartsReady, searchReady, sessionReady, tableReady]);
  // .loaded settles once every dependency is in (true) or one failed (false),
  // before the first data request: the template ends its load deadline there,
  // because from then on a slow answer is this module's own loading state and
  // a failed one its own data-failure message with Reload.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function () {
    if (!alive()) return null;
    ready = true;
    return route();
  }, function (error) {
    if (alive()) {
      if (options.onError) options.onError(error);
      else root.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.reports.reports.error_generico", null, "No se pudo cargar. Reintentá.")) + "</p>";
    }
    throw error;
  });
  return dispose;
}
