/* lab-results.js — "Resultados de laboratorio" en la cuenta del paciente
   (2026-08-03; agrupado por informe 2026-08-05; rediseño 2026-08-06 —
   docs/architecture/laboratorio-paciente.md: una lectura cautelosa de la IA
   por informe, filtro de sensibilidad server-side, y una segunda vista
   "Evolución" con gráficos de tendencia). Mismo patrón que my-requests.js/
   dependents.js: accounts.py monta esta pestaña on-demand (#lr-host) la
   primera vez que se abre.

   Dos vistas adentro de la misma pestaña — "Resultados" (lista, default) y
   "Evolución" (gráficos) — con un toggle segmentado que COPIA el control
   Pendientes/Historia de admin-patient-summary.mjs (misma forma: .rp-tabs/
   .rp-tab -> acá .lr-tabs/.lr-tab). El estado de vista vive en memoria, sin
   tocar la URL.

   Cada informe es su propia tarjeta (fecha + tipo de estudio + resumen
   cauteloso de la IA), NO la fila de grupo genérica de listing.js: un
   informe sensible no tiene filas de determinación (server-side redaction,
   nunca llegan valores), así que no hay "primera fila" para abrirlo — cada
   tarjeta arma su propio encabezado reusando las clases que YA existen y
   YA se ven así en esta pantalla (`lst-grp-d`/`lst-grp-l`, inyectadas por
   listing.js) y monta, sólo si hay determinaciones, una tabla listing.js
   propia y chica (search:false, sort:false — sigue siendo una historia
   corta, no un directorio). listing.js es el ÚNICO motor de tabla del
   sitio paciente (invariante del repo); acá se llama una vez por informe.

   GET /api/account/lab-results (lab_results.py) ya resuelve la identidad
   por la sesión: esta pestaña nunca manda ni recibe un DNI. */
/* Canonical Account Labs controller.  It never installs browser globals and
 * returns a disposer that invalidates every late fetch, timeout and history
 * callback created for this mount. */
const mounts = new WeakMap();
export function mount(host, options) {
  "use strict";
  options = options || {};
  if (!host) return function () {};
  if (mounts.has(host)) mounts.get(host)();
  var disposed = false;
  var retryTimer = null;
  var popstateHandler = null;
  var request = null;
  var renderGeneration = 0;
  function alive() { return !disposed && host.isConnected !== false; }
  function dispose() {
    if (disposed) return;
    disposed = true;
    if (retryTimer != null) { clearTimeout(retryTimer); retryTimer = null; }
    if (popstateHandler) { window.removeEventListener("popstate", popstateHandler); popstateHandler = null; }
    if (request && request.abort) request.abort();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  var noteHost = document.getElementById("lr-note");
  var Dates = null;
  var datesUrl = staticUrl("platform/dates.mjs");
  var datesReady = datesUrl ? import(datesUrl).then(function (m) { Dates = m; })
    : Promise.reject(new Error("platform/dates.mjs is not present in window.SANA_STATIC"));
  var Listing = null;

  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.reject(new Error("platform/i18n.mjs is not present in window.SANA_STATIC"));
  var ready = Promise.all([datesReady, i18nReady]).catch(function (error) {
    return i18nReady.catch(function () {}).then(function () { throw error; });
  });
  ready.catch(function () {});
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // Fecha del informe en palabras, con año siempre (un informe puede ser de
  // hace 20 años) y sin día de la semana — MISMO formateador que la fecha de
  // nacimiento (dates.mjs): "one formatter per datum"
  // no exige un nombre nuevo por cada pantalla que lo necesita.
  function fechaLarga(fecha) {
    if (Dates && Dates.nacimiento) return Dates.nacimiento(fecha);
    return String(fecha || "");
  }

  // Fecha corta para las etiquetas de los ejes del gráfico — reusa
  // dates.mjs (día abreviado + mes, ya traducido), nunca un formateador
  // propio; sin ese módulo cargado muestra el valor crudo en vez de imitarlo.
  function fechaCorta(fecha) {
    if (Dates && Dates.corta) return Dates.corta(fecha);
    return fechaLarga(fecha);
  }

  function fmtNum(v) {
    if (v == null || v === "") return "";
    var n = Number(v);
    if (!isFinite(n)) return String(v);
    var r = Math.round(n * 100) / 100;
    return String(r).replace(".", ",");
  }

  // Rango CON unidad ("70–110 mg/dL", nunca "70–110" a secas — mockup
  // Responsable del producto 2026-08-05: sin la unidad el número no dice nada por sí solo).
  function rangoTexto(rango, unidad) {
    if (!rango || rango.length !== 2) return "";
    var base = fmtNum(rango[0]) + "–" + fmtNum(rango[1]);
    return unidad ? base + " " + unidad : base;
  }

  // ---- estilos (una vez) ----------------------------------------------------
  // Deliberadamente chico: la fecha/tipo del informe y el punto de estado
  // reusan las clases que listing.js YA inyecta (lst-grp-d/lst-grp-l,
  // lst-dot-i) — acá sólo lo que no existía: el toggle de vista y los
  // gráficos de Evolución.
  function inyectarCss() {
    if (document.getElementById("lr-style")) return;
    var css = [
      ".lr-tabs{display:flex;gap:18px;border-bottom:1px solid var(--border);margin:0 0 18px;}",
      ".lr-tab{font:inherit;font-size:14px;border:0;background:none;cursor:pointer;padding:6px 2px 10px;color:var(--hint-ink,var(--text-2));border-bottom:2px solid transparent;margin-bottom:-1px;}",
      ".lr-tab.on{color:var(--text);font-weight:650;border-bottom-color:var(--accent);}",
      ".lr-pending{font-size:13px;color:var(--text-2);margin:0 0 14px;}",
      ".lr-report{margin-top:24px;}",
      ".lr-report:first-child{margin-top:0;}",
      ".lr-report-head{padding:0 0 9px;margin:0 0 10px;border-bottom:1px solid var(--border-soft);}",
      // El resumen es información clínica clave — nunca en el gris apagado
      // que usa la etiqueta corta de la tabla (responsable del producto: la letra chica gris no
      // es para elderly/baja visión). El punto reusa el tamaño ya definido
      // por listing.js (.lst-dot-i) y sólo agrega el color.
      ".lr-summary{display:flex;gap:8px;align-items:flex-start;margin:2px 0 14px;font-size:14.5px;line-height:1.5;color:var(--text);}",
      ".lr-summary-i--ok{background:var(--done);margin-top:6px;}",
      ".lr-summary-i--revisar{background:var(--warn);margin-top:6px;}",
      ".lr-sensible{margin:2px 0 4px;font-size:15px;color:var(--text);}",
      ".lr-sensible-link{margin:0;font-size:14.5px;}",
      // "Ver todos los resultados": link discreto bajo la tabla corta de
      // fuera-de-rango. Mismo tono que cualquier <a> del sitio (accent-is-link),
      // sin botón — no es una acción primaria, es un atajo a la página del informe.
      // Margin-top generoso: separa el atajo de la última fila de la tabla sin
      // un divisor (un border ahí merguearía con el borde propio de la tabla).
      ".lr-show-all{display:block;margin:12px 0 0;font-size:14px;font-weight:500;}",
      // Conteo "N de M valores fuera de rango" entre el resumen y la tabla:
      // letra de cuerpo, no de etiqueta (informational, no un estado).
      ".lr-oob-count{margin:0 0 10px;font-size:13.5px;color:var(--text-2);}",
      // Vista de detalle: el TÍTULO del estudio es lo prominente (no la fecha).
      // La fecha queda como secundaria, gris, chica. El bloque lleva aire abajo
      // antes del resumen para que el título respire como encabezado de página.
      ".lr-detail-head{margin:0 0 12px;}",
      ".lr-detail-title{font-size:20px;font-weight:700;margin:0;color:var(--text);line-height:1.22;letter-spacing:-.01em;text-wrap:balance;}",
      ".lr-detail-date{margin:3px 0 0;font-size:13.5px;color:var(--text-2);}",
      ".lr-evol-empty{color:var(--text-2);font-size:14px;margin:8px 0 0;}",
      ".lr-chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;}",
      ".lr-chart{min-width:0;border:1px solid var(--border);border-radius:10px;padding:14px 14px 10px;background:var(--panel);}",
      ".lr-chart-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;}",
      ".lr-chart-t{font-size:14px;font-weight:700;margin:0;color:var(--text);}",
      ".lr-chart-latest{font:700 15px/1 var(--font);white-space:nowrap;color:var(--text);}",
      ".lr-chart-sub{font-size:13px;color:var(--text-2);margin:2px 0 8px;}",
      ".lr-chart svg{width:100%;height:auto;display:block;}",
      ".lr-chart-band{fill:var(--done);opacity:.09;}",
      ".lr-chart-line{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;stroke-linecap:round;}",
      ".lr-chart-pt{fill:var(--accent);}",
      ".lr-chart-pt--revisar{fill:var(--warn);}",
      ".lr-chart-axis{font-size:13px;fill:var(--text-3);}",
      // Mobile (<768px, regla full-bleed del responsable del producto): la tabla de determinaciones
      // va a los dos bordes de la tarjeta — cancela el padding horizontal del
      // .dcard sólo donde hay tabla, sin tocar el resto del contenido del
      // informe (encabezado, resumen, conteo, link). Un informe sin tabla
      // (todo-normal) no se ve afectado.
      // Zona para subir un resultado propio: MISMO control que la orden de
      // derivación del wizard (modules/turnos/wz-upload.mjs) — mismas clases,
      // mismo look, misma forma de elegir archivo; sólo cambia el texto. El
      // wizard y la cuenta nunca se montan en la misma página, así que las
      // reglas no compiten; cambiar una es cambiar las dos.
      ".wz-uploadzone{border:2px dashed var(--border-strong);background:var(--bg-2);border-radius:20px;" +
        "padding:34px 22px;display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px;" +
        "cursor:pointer;transition:border-color .12s,background .12s;}",
      ".wz-uploadzone:hover,.wz-uploadzone:focus-visible{outline:none;border-color:var(--hover-bd-strong);background:var(--hover-bg-soft);}",
      ".wz-cam{width:76px;height:76px;border-radius:50%;background:var(--accent-soft);display:flex;" +
        "align-items:center;justify-content:center;margin-bottom:6px;color:var(--accent-hover);}",
      ".wz-up-h{font-size:22px;font-weight:700;color:var(--text);}",
      ".wz-up-sub{font-size:17px;color:var(--text-2);}",
      // «Ver resultados del laboratorio»: botón secundario del sitio (mismos
      // tokens que .wz-cta-sec), sólo si la instalación tiene la clave.
      ".lr-provider{display:flex;align-items:center;justify-content:center;width:100%;min-height:52px;margin:24px 0 20px;" +
        "padding:12px 20px;border:1px solid var(--btn2-bd,var(--border-strong));border-radius:var(--btn-radius,15px);" +
        "background:var(--btn2-bg,var(--panel));color:var(--btn2-fg,var(--text));font:700 17px var(--font);" +
        "text-decoration:none;transition:background .12s;}",
      ".lr-provider:hover{background:var(--btn2-bg-hover,var(--hover-bg));}",
      ".lr-upload{margin:28px 0 0;}",
      ".lr-upload-msg{margin:10px 0 0;font-size:14.5px;color:var(--text);}",
      // Name and reference range are what the patient reads first: body ink,
      // not the muted text listing.mjs gives a secondary text cell.
      ".lr-report-table td.lst-col-determinacion>*,.lr-report-table td.lst-col-referencia>*{color:var(--text);}",
      "@media(max-width:767px){.lr-report-table{margin:0 -28px;--lst-row-pad-x:28px;}}",
      "@media(max-width:640px){.lr-chart-grid{grid-template-columns:1fr}}"
    ].join("");
    var tag = document.createElement("style");
    tag.id = "lr-style";
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  // platform/listing.mjs is content-hash fingerprinted (core/static_fingerprint.py,
  // CLIENT_LOADED): a served file can't know its current URL, so the shell
  // publishes it as window.SANA_STATIC and this resolves through that map —
  // fail-closed (null + console.error, never a hardcoded fallback URL), the
  // same shape as islands.mjs's resolveUrl(). A missing entry takes the exact
  // path a failed import takes: cb() runs, Listing stays absent.
  function staticUrl(name) {
    var m = (typeof window !== "undefined") && window.SANA_STATIC;
    var u = m && m[name];
    if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
    return u || null;
  }

  function loadListing(cb) {
    if (Listing) { cb(); return; }
    var src = staticUrl("platform/listing.mjs");
    if (!src) { cb(); return; }
    import(src).then(function (m) { Listing = m; cb(); }).catch(function () { cb(); });
  }

  // `conEstado` false on the list card: it shows only out-of-range rows, so an
  // Estado column would repeat "Fuera de rango" on every row (review 2026-11-15).
  function tableCols(conEstado) {
    var cols = [
      { t: "text", key: "determinacion", label: T("account.mine.lab.col_determination", null, "Determinación") },
      { t: "text", key: "resultado", outKey: "fuera_de_rango", label: T("account.mine.lab.col_result", null, "Resultado") },
      { t: "text", key: "referencia", label: T("account.mine.lab.col_reference", null, "Valores de referencia") }
    ];
    if (conEstado !== false) cols.push({ t: "dot", key: "estado", label: T("account.mine.lab.col_status", null, "Estado") });
    return cols;
  }

  // Una fila por determinación (no por informe): cada renglón es un hecho
  // solo — "un dato por celda" (regla table-design). `items` acepta
  // analitos sin catálogo (clave null) — el rango que se pinta es el que
  // trae el propio informe, catalogado o no.
  function itemsRows(items) {
    return (items || []).map(function (it) {
      var fuera = !!it.fuera;
      return {
        determinacion: it.nombre || "",
        resultado: fmtNum(it.valor) + (it.unidad ? " " + it.unidad : ""),
        fuera_de_rango: fuera,
        referencia: rangoTexto(it.rango, it.unidad) || "—",
        estado: {
          ok: !fuera,
          label: fuera
            ? T("account.mine.lab.out_of_range", null, "Fuera de rango")
            : T("account.mine.lab.in_range", null, "En rango")
        }
      };
    });
  }

  // ---- una tarjeta por informe -----------------------------------------------

  // "Todos los valores dentro del rango de referencia" / "Hay valores para
  // revisar con tu médico" — nunca alarmante, nunca un diagnóstico. tono
  // desconocido cae a "revisar" (el enum cerrado real vive en el server;
  // acá sólo hace falta no reventar con un valor inesperado).
  function summaryHtml(inf) {
    if (!inf.resumen) return "";
    var tono = inf.tono === "ok" ? "ok" : "revisar";
    return '<p class="lr-summary"><span class="lst-dot-i lr-summary-i--' + tono + '"></span>' +
      '<span>' + esc(inf.resumen) + "</span></p>";
  }

  // Informe sensible: fecha + "Tu médico tiene tus resultados." + el mismo
  // link a un clínico que el resto del sitio usa — NUNCA tipo, resumen ni
  // valores. La redacción ya pasó server-side (lab_results.py nunca los
  // manda), esto no es un CSS que los esconde.
  function sensibleHtml(inf, clinicoHref) {
    var linkTxt = esc(T("account.mine.lab.sensitive_link", null, "Sacá turno con un clínico"));
    var link = '<a href="' + esc(clinicoHref || "/doctors") + '">' + linkTxt + "</a>";
    return '<p class="lr-sensible">' + esc(T("account.mine.lab.sensitive", null, "Tu médico tiene tus resultados.")) + "</p>" +
      '<p class="lr-sensible-link">' + link + "</p>";
  }

  // Encabezado fecha + tipo de estudio, reusando las clases que YA existían
  // en esta pantalla (listing.js las inyecta): "una tarjeta nueva copia el
  // control que ya hace ese trabajo", no le inventa una fuente nueva.
  function encabezadoHtml(fecha, tipo, propio) {
    return '<p class="lr-report-head"><span class="lst-grp-d">' + esc(fechaLarga(fecha)) + "</span>" +
      (tipo ? '<span class="lst-grp-l"> · ' + esc(tipo) + "</span>" : "") +
      (propio ? '<span class="lst-grp-l"> · ' + esc(subidoPorVos()) + "</span>" : "") + "</p>";
  }

  // Un informe que subió el propio paciente se dice, en su cuenta y al lado
  // del servicio — nunca se mezcla con lo que trajo el hospital.
  function subidoPorVos() {
    return T("account.mine.lab.uploaded_by_you", null, "Subido por vos");
  }

  // Encabezado de la vista de DETALLE: al revés del de lista — el TÍTULO del
  // estudio es lo prominente (responsable del producto 2026-08-10: que el título salte, no la
  // fecha). La fecha queda como secundaria. No reusa lst-grp-d/l porque esa
  // jerarquía es fecha-primero; acá hace falta su propia jerarquía.
  function encabezadoDetalleHtml(fecha, tipo, propio) {
    return '<header class="lr-detail-head"><p class="lr-detail-title">' + esc(tipo) + "</p>" +
      '<p class="lr-detail-date">' + esc(fechaLarga(fecha)) +
      (propio ? " · " + esc(subidoPorVos()) : "") + "</p></header>";
  }

  // Monta la tabla listing.js de determinaciones en `section`. Una sola
  // helper porque lista y detalle la usan igual — sólo cambia qué filas
  // reciben (filtradas vs. todas).
  function montarTabla(section, rows, conEstado) {
    if (!rows.length) return;
    var tableMount = document.createElement("div");
    tableMount.className = "lr-report-table";
    section.appendChild(tableMount);
    Listing.render(tableMount, {
      cols: tableCols(conEstado),
      rows: rows,
      options: {
        // Un informe ya viene en el orden clínico real (las determinaciones
        // tal como las extrajo la IA) — reordenar o buscar en una tabla de
        // pocas filas por informe no aporta nada.
        sort: false, search: false, defaultSort: false,
        empty: T("account.mine.lab.empty_short", null, "Todavía no tenés resultados de laboratorio cargados.")
      }
    });
  }

  // Arma UNA tarjeta de LISTA (elemento real, no un string) y la agrega a
  // `cont`. Real (no innerHTML del padre) porque el mount de la tabla necesita
  // una referencia propia para SanaListing.render.
  //
  // Lista = sólo lo fuera de rango (responsable del producto 2026-08-10: "no me muestres los
  // normales"). Si el informe no tiene nada fuera de rango, la tarjeta sigue
  // existiendo (título + resumen) pero sin tabla, con un link "ver todos los
  // resultados" que abre la página del informe. Sensible → igual que antes.
  function pintarInforme(cont, inf, idx, onVerTodos) {
    var section = document.createElement("section");
    section.className = "lr-report";
    if (inf.sensible) {
      section.innerHTML = encabezadoHtml(inf.fecha, "", inf.tercero) + sensibleHtml(inf, inf._clinicoHref);
      cont.appendChild(section);
      return;
    }
    var tipo = inf.tipo_estudio || inf.servicio || T("account.mine.lab.default_study", null, "Laboratorio");
    var allRows = itemsRows(inf.items);
    var oobRows = allRows.filter(function (r) { return r.fuera_de_rango; });
    section.innerHTML = encabezadoHtml(inf.fecha, tipo, inf.tercero) + summaryHtml(inf);
    cont.appendChild(section);
    // Conteo "N de M fuera de rango" sólo si hay determinaciones Y hay al
    // menos una fuera de rango — un informe todo-normal no necesita la línea
    // (el resumen ya lo dice); un informe con 0 items no la necesita tampoco.
    // Shown only when some values are hidden: "3 de 3" repeats the summary.
    var parcial = oobRows.length < allRows.length;
    if (oobRows.length && parcial) {
      var p = document.createElement("p");
      p.className = "lr-oob-count";
      // Sin SanaI18n cargado, T() devuelve el fallback con los placeholders
      // sin sustituir — se reemplazan acá; con SanaI18n es un no-op.
      p.textContent = Tn("account.mine.lab.out_of_count", allRows.length,
        { n: oobRows.length, total: allRows.length },
        "{n} de {total} valor fuera de rango", "{n} de {total} valores fuera de rango")
        .split("{n}").join(String(oobRows.length))
        .split("{total}").join(String(allRows.length));
      section.appendChild(p);
    }
    if (oobRows.length) montarTabla(section, oobRows, false);
    // El link "ver todos los resultados" aparece cuando la tarjeta esconde
    // algún valor (los normales) — es el atajo a la página del informe con
    // TODO. Si ya se ven todos, o es sensible, o no hay items, no lo lleva.
    if (allRows.length && parcial) {
      var a = document.createElement("a");
      a.className = "lr-show-all";
      a.href = "#labs&r=" + idx;
      a.textContent = T("account.mine.lab.show_all", null, "Ver todos los resultados");
      a.addEventListener("click", function (ev) {
        if (ev && ev.preventDefault) ev.preventDefault();
        if (onVerTodos) onVerTodos(idx);
      });
      section.appendChild(a);
    }
  }

  // Vista de DETALLE: un solo informe, título prominente, TODAS las filas
  // (normales y fuera de rango). Se llega vía el link "ver todos" de la lista
  // o por hash compartible (#labs&r=<idx>); se sale con el botón atrás del
  // navegador o volviendo a "Resultados" en el toggle — sin botón "Volver"
  // propio (regla del responsable del producto: el browser ya tiene uno).
  function pintarDetalle(panel, inf) {
    panel.innerHTML = "";
    var section = document.createElement("section");
    section.className = "lr-report";
    if (inf.sensible) {
      section.innerHTML = encabezadoDetalleHtml(inf.fecha,
        T("account.mine.lab.default_study", null, "Laboratorio"), inf.tercero) +
        sensibleHtml(inf, inf._clinicoHref);
      panel.appendChild(section);
      return;
    }
    var tipo = inf.tipo_estudio || inf.servicio || T("account.mine.lab.default_study", null, "Laboratorio");
    section.innerHTML = encabezadoDetalleHtml(inf.fecha, tipo, inf.tercero) + summaryHtml(inf);
    panel.appendChild(section);
    montarTabla(section, itemsRows(inf.items));
  }

  function pintarLista(panel, informes, clinicoHref, onVerTodos) {
    panel.innerHTML = "";
    informes.forEach(function (inf, idx) {
      inf._clinicoHref = clinicoHref;
      pintarInforme(panel, inf, idx, onVerTodos);
    });
  }

  // ---- subir un resultado propio --------------------------------------------
  // El paciente trae papeles que el hospital todavía no digitalizó. Es el
  // MISMO control que la orden de derivación del wizard (wz-uploadzone), con
  // su propio texto: un control nuevo copia al que ya hace ese trabajo.

  // Icons8 SF Regular files in static/icons, painted by mask with the
  // text colour (review 2026-11-15: nothing drawn by hand in the account).
  function labIco(file, px, color) {
    return '<span aria-hidden="true" style="display:inline-block;flex:none;vertical-align:middle;width:' + px + 'px;height:' + px + 'px;' +
      'background-color:' + (color || 'currentColor') + ';-webkit-mask:url(/static/icons/' + file + ') center/contain no-repeat;' +
      'mask:url(/static/icons/' + file + ') center/contain no-repeat;"></span>';
  }
  var SVG_CAM = labIco("camera-sf-regular.png", 40);

  // Botón al sitio del laboratorio. El href es siempre la ruta propia
  // (/account/lab-results/open), que firma y redirige: la dirección del
  // proveedor nunca está en la página. Sin clave en la instalación el
  // servidor manda null y no hay botón.
  function montarProveedor(cont, data) {
    var href = data && data.laboratorio_href;
    if (!href) return;
    var a = document.createElement("a");
    a.className = "lr-provider";
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = T("account.mine.lab.provider_link", null, "Ver resultados del laboratorio");
    cont.appendChild(a);
  }

  function montarSubida(cont) {
    var zona = document.createElement("div");
    zona.className = "lr-upload";

    var botonZona = document.createElement("div");
    botonZona.className = "wz-uploadzone";
    botonZona.setAttribute("role", "button");
    botonZona.setAttribute("tabindex", "0");
    botonZona.setAttribute("aria-label", T("account.mine.lab.upload_aria", null, "Subí un resultado"));

    var fileEl = document.createElement("input");
    fileEl.type = "file";
    fileEl.accept = "image/*,.pdf";
    fileEl.style.display = "none";
    botonZona.appendChild(fileEl);

    var cam = document.createElement("div");
    cam.className = "wz-cam";
    cam.innerHTML = SVG_CAM;
    botonZona.appendChild(cam);

    var titulo = document.createElement("div");
    titulo.className = "wz-up-h";
    titulo.textContent = T("account.mine.lab.upload_heading", null, "Subí un resultado");
    botonZona.appendChild(titulo);

    var sub = document.createElement("div");
    sub.className = "wz-up-sub";
    sub.textContent = T("account.mine.lab.upload_sub", null,
      "Sacale una foto o elegí un archivo — PDF o JPG");
    botonZona.appendChild(sub);

    zona.appendChild(botonZona);
    cont.appendChild(zona);
    var msg = null;

    function decir(texto) {
      if (!msg) {
        msg = document.createElement("p");
        msg.className = "lr-upload-msg";
        msg.setAttribute("role", "status");
        zona.appendChild(msg);
      }
      msg.textContent = texto;
    }

    botonZona.addEventListener("click", function () { fileEl.click(); });
    botonZona.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileEl.click(); }
    });
    fileEl.addEventListener("change", function () {
      var f = fileEl.files && fileEl.files[0];
      if (!f) return;
      decir(T("account.mine.lab.upload_sending", null, "Subiendo tu resultado…"));
      var fd = new FormData();
      fd.append("archivo", f, f.name || "resultado");
      fetch("/api/account/lab-results/upload",
        { method: "POST", credentials: "same-origin", body: fd })
        .then(function (r) { if (!r.ok) throw new Error("upload failed"); return r.json(); })
        .then(function () {
          if (!alive()) return;
          reintentoHecho = false;
          cargar();
        })
        .catch(function () {
          if (!alive()) return;
          decir(T("account.mine.lab.upload_error", null,
            "No pudimos subir ese archivo. Probá de nuevo con una foto o un PDF."));
        });
    });
  }

  // ---- Evolución: gráficos SVG en código, nunca dibujados por la IA -------

  function fueraDeSerie(serie) {
    var pts = serie.puntos || [];
    if (!pts.length || !serie.rango_ok) return false;
    var v = pts[pts.length - 1][1];
    return v < serie.rango_ok[0] || v > serie.rango_ok[1];
  }

  // Fuera de rango primero, después por cantidad de puntos (la serie con más
  // historia cuenta más) — orden fijo, nunca al azar entre aperturas.
  function ordenarSeries(series) {
    return (series || []).slice().sort(function (a, b) {
      var af = fueraDeSerie(a) ? 1 : 0, bf = fueraDeSerie(b) ? 1 : 0;
      if (af !== bf) return bf - af;
      return (b.puntos || []).length - (a.puntos || []).length;
    });
  }

  var CHART_W = 320, CHART_H = 110, CHART_PAD = { l: 8, r: 8, t: 10, b: 20 };

  // Sin ejes de más (Tufte): banda de referencia + línea + fecha del primer
  // y último punto + el último valor ya alcanza para leer una tendencia. Los
  // puntos EXACTOS de la línea vienen todos del código (extracción ya
  // vetada por la IA en el pase de laboratorio, nunca dibujados por ella —
  // ver docs/architecture/laboratorio-paciente.md §7).
  function chartSvg(serie) {
    var pts = serie.puntos || [];
    if (!pts.length) return "";
    var vals = pts.map(function (p) { return p[1]; });
    var rango = serie.rango_ok;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (rango) { min = Math.min(min, rango[0]); max = Math.max(max, rango[1]); }
    var margen = (max - min) * 0.15 || Math.abs(max) * 0.1 || 1;
    min -= margen; max += margen;
    var f0 = Date.parse(pts[0][0]), f1 = Date.parse(pts[pts.length - 1][0]);
    if (!(f1 > f0)) f1 = f0 + 1;
    var x = function (f) { return CHART_PAD.l + (Date.parse(f) - f0) / (f1 - f0) * (CHART_W - CHART_PAD.l - CHART_PAD.r); };
    var y = function (v) { return CHART_PAD.t + (max - v) / (max - min) * (CHART_H - CHART_PAD.t - CHART_PAD.b); };
    var ultimo = pts[pts.length - 1];
    var fuera = fueraDeSerie(serie);
    var descripcion = (serie.label || "") + ": " + fmtNum(ultimo[1]) + " " + (serie.unidad || "") +
      " el " + fechaCorta(ultimo[0]);
    var out = '<svg viewBox="0 0 ' + CHART_W + " " + CHART_H + '" role="img" aria-label="' + esc(descripcion) + '">';
    if (rango) {
      out += '<rect class="lr-chart-band" x="' + CHART_PAD.l + '" y="' + y(rango[1]).toFixed(1) +
        '" width="' + (CHART_W - CHART_PAD.l - CHART_PAD.r) + '" height="' +
        Math.max(0, y(rango[0]) - y(rango[1])).toFixed(1) + '"/>';
    }
    var poly = pts.map(function (p) { return x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); }).join(" ");
    out += '<polyline class="lr-chart-line" points="' + poly + '"/>';
    pts.forEach(function (p, i) {
      var esUltimo = i === pts.length - 1;
      var r = esUltimo ? 4 : 2.5;
      var cls = "lr-chart-pt" + (esUltimo && fuera ? " lr-chart-pt--revisar" : "");
      out += '<circle class="' + cls + '" cx="' + x(p[0]).toFixed(1) + '" cy="' + y(p[1]).toFixed(1) + '" r="' + r + '">' +
        "<title>" + esc(fechaCorta(p[0])) + ": " + fmtNum(p[1]) + " " + esc(serie.unidad || "") + "</title></circle>";
    });
    out += '<text class="lr-chart-axis" x="' + CHART_PAD.l + '" y="' + (CHART_H - 4) + '">' + esc(fechaCorta(pts[0][0])) + "</text>";
    out += '<text class="lr-chart-axis" x="' + (CHART_W - CHART_PAD.r) + '" y="' + (CHART_H - 4) + '" text-anchor="end">' +
      esc(fechaCorta(pts[pts.length - 1][0])) + "</text>";
    return out + "</svg>";
  }

  function chartCardHtml(serie) {
    var pts = serie.puntos || [];
    var ultimo = pts[pts.length - 1];
    var latest = ultimo ? fmtNum(ultimo[1]) + (serie.unidad ? " " + serie.unidad : "") : "";
    var rango = T("account.mine.lab.chart_range",
      { from: fechaLarga(pts[0][0]), to: fechaLarga(ultimo[0]) },
      "de {from} a {to}");
    return '<section class="lr-chart"><div class="lr-chart-head"><p class="lr-chart-t">' + esc(serie.label || "") + "</p>" +
      '<span class="lr-chart-latest">' + esc(latest) + "</span></div>" +
      '<p class="lr-chart-sub">' + esc(rango) + "</p>" +
      chartSvg(serie) + "</section>";
  }

  function pintarEvolucion(panel, series) {
    var ordenadas = ordenarSeries(series).filter(function (s) { return (s.puntos || []).length; });
    if (!ordenadas.length) {
      panel.innerHTML = '<p class="lr-evol-empty">' + esc(T("account.mine.lab.evolution_empty", null,
        "Todavía no hay suficientes resultados para ver una evolución.")) + "</p>";
      return;
    }
    panel.innerHTML = '<div class="lr-chart-grid">' + ordenadas.map(chartCardHtml).join("") + "</div>";
  }

  // ---- armado de la pestaña: toggle + dos paneles ----------------------------

  function armarChrome() {
    var tabs = document.createElement("div");
    tabs.className = "lr-tabs";
    tabs.setAttribute("role", "tablist");
    var btnResultados = document.createElement("button");
    btnResultados.type = "button";
    btnResultados.className = "lr-tab on";
    btnResultados.setAttribute("role", "tab");
    btnResultados.setAttribute("aria-selected", "true");
    btnResultados.textContent = T("account.mine.lab.tab_results", null, "Resultados");
    var btnEvolucion = document.createElement("button");
    btnEvolucion.type = "button";
    btnEvolucion.className = "lr-tab";
    btnEvolucion.setAttribute("role", "tab");
    btnEvolucion.setAttribute("aria-selected", "false");
    btnEvolucion.textContent = T("account.mine.lab.tab_evolution", null, "Evolución");
    tabs.appendChild(btnResultados);
    tabs.appendChild(btnEvolucion);

    var panelResultados = document.createElement("div");
    panelResultados.setAttribute("data-panel", "resultados");
    var panelEvolucion = document.createElement("div");
    panelEvolucion.setAttribute("data-panel", "evolucion");
    panelEvolucion.style.display = "none";
    // Panel de detalle (un solo informe, vista "ver todos los resultados"):
    // hermano oculto de los otros dos. El toggle Resultados/Evolución se
    // esconde mientras el detalle está abierto — no es un sub-estado de
    // ninguno de los dos, es su propia pantalla.
    var panelDetalle = document.createElement("div");
    panelDetalle.setAttribute("data-panel", "detalle");
    panelDetalle.style.display = "none";

    host.appendChild(tabs);
    host.appendChild(panelResultados);
    host.appendChild(panelEvolucion);
    host.appendChild(panelDetalle);

    return { tabs: tabs, btnResultados: btnResultados, btnEvolucion: btnEvolucion,
      panelResultados: panelResultados, panelEvolucion: panelEvolucion,
      panelDetalle: panelDetalle };
  }

  function limpiarNota() {
    if (!alive()) return;
    if (noteHost) noteHost.innerHTML = "";
  }

  // Salida hacia adelante (mockup el responsable del producto 2026-08-05): un valor fuera de rango
  // no es un diagnóstico, y el paciente tiene un paso concreto — nunca lo
  // deja mirando un número solo. Vive en su PROPIO host (#lr-note, sibling
  // de #lr-host): #lr-host se redibuja entero en cada carga, si la nota
  // viviera adentro desaparecería.
  function pintarNota(clinicoHref) {
    if (!noteHost) return;
    var link = '<a href="' + esc(clinicoHref || "/doctors") + '">' +
      esc(T("account.mine.lab.footer_link", null, "sacá turno con un clínico")) + "</a>";
    noteHost.innerHTML = '<p class="dcard-p lr-note">' +
      // Sin SanaI18n cargado, T() devuelve el fallback con {link} sin
      // sustituir — se reemplaza acá; con SanaI18n es un no-op.
      T("account.mine.lab.footer_note", { link: link },
        "Un valor fuera de rango no es un diagnóstico: quien lo interpreta es tu médico. " +
        "Llevá el resultado a tu próxima consulta o {link}.")
        .split("{link}").join(link) + "</p>";
  }

  function vacio() {
    limpiarNota();
    host.innerHTML =
      '<p style="font-size:16px;color:var(--text);">' + T("ai.notice.lab", null, "Estos resultados se leen con inteligencia artificial. Consultalos con tu médico. Si subís una imagen, se envía completa al proveedor.") + '</p>' +
      '<p class="dcard-p">' + T("account.mine.lab.empty", null,
        "Todavía no tenés resultados de laboratorio cargados. " +
        "Van a aparecer acá apenas el hospital digitalice tu historia clínica.") + '</p>' +
      '<p class="dcard-p"><a href="/d/diagnostico">' +
      T("account.mine.lab.empty_cta", null, "Pedir un turno de laboratorio") + '</a></p>';
  }

  // Estado del toggle en memoria (nunca en la URL) — sobrevive al refresh
  // silencioso de analisis_pendiente para no devolver al paciente a
  // "Resultados" si estaba mirando "Evolución".
  var VISTA = "resultados";
  var reintentoHecho = false;

  function render(data) {
    if (!alive()) return;
    var generation = ++renderGeneration;
    loadListing(function () {
      if (!alive() || generation !== renderGeneration) return;
      if (!Listing) { showLoadError(); return; }
      paint(data);
    });
  }

  function paint(data) {
    if (popstateHandler) {
      window.removeEventListener("popstate", popstateHandler);
      popstateHandler = null;
    }
    var informes = (data && data.informes) || [];
    var clinicoHref = data && data.clinico_href;
    limpiarNota();
    host.innerHTML = '<p style="font-size:16px;color:var(--text);">' + T("ai.notice.lab", null, "Estos resultados se leen con inteligencia artificial. Consultalos con tu médico. Si subís una imagen, se envía completa al proveedor.") + '</p>';

    var pendiente = Number(data && data.analisis_pendiente) || 0;
    if (pendiente > 0) {
      var p = document.createElement("p");
      p.className = "lr-pending";
      p.textContent = T("account.mine.lab.pending_note", null,
        "Seguimos leyendo algunos estudios; esta lista se completa sola.");
      host.appendChild(p);
    }

    // Sin resultados todavía, el control de subida es lo único accionable de
    // la pestaña: el paciente que tiene el papel en la mano lo carga acá.
    if (!informes.length) { inyectarCss(); vacio(); montarProveedor(host, data); montarSubida(host); return; }

    (function () {
      if (!alive()) return;
      if (!Listing) {
        host.innerHTML += '<p class="dcard-p">' + T("account.mine.lab.load_error", null,
          "No pudimos mostrar tus resultados ahora. Volvé a intentar en un rato.") + '</p>';
        return;
      }
      inyectarCss();
      var elems = armarChrome();
      var evolucionLista = false;
      // Índice del informe abierto en la vista de detalle (undefined cuando
      // no se está viendo uno). Vive acá, en memoria, pero se refleja en el
      // hash (#labs&r=<idx>) para que la URL sea compartible y el botón atrás
      // del navegador vuelva a la lista.
      var detalleIdx;

      function mostrar(vista, idx, historyMode) {
        if (!alive()) return;
        VISTA = vista;
        var enResultados = vista === "resultados";
        var enEvolucion = vista === "evolucion";
        var enDetalle = vista === "detalle";
        detalleIdx = enDetalle ? idx : undefined;
        // El toggle Resultados/Evolución sólo tiene sentido en lista/evolución;
        // en detalle se esconde (no es un sub-estado de ninguno).
        elems.tabs.style.display = enDetalle ? "none" : "";
        elems.btnResultados.className = "lr-tab" + (enResultados ? " on" : "");
        elems.btnResultados.setAttribute("aria-selected", enResultados ? "true" : "false");
        elems.btnEvolucion.className = "lr-tab" + (enEvolucion ? " on" : "");
        elems.btnEvolucion.setAttribute("aria-selected", enEvolucion ? "true" : "false");
        elems.panelResultados.style.display = enResultados ? "" : "none";
        elems.panelEvolucion.style.display = enEvolucion ? "" : "none";
        elems.panelDetalle.style.display = enDetalle ? "" : "none";
        // Los gráficos se arman recién cuando el paciente abre la vista —
        // en código, a partir de valores ya extraídos y cacheados, nunca
        // dibujados por la IA (docs/architecture/laboratorio-paciente.md).
        if (enEvolucion && !evolucionLista) {
          evolucionLista = true;
          pintarEvolucion(elems.panelEvolucion, data.series || []);
        }
        if (enDetalle) {
          var inf = informes[idx];
          if (inf) {
            inf._clinicoHref = clinicoHref;
            pintarDetalle(elems.panelDetalle, inf);
          } else {
            // Índice fuera de rango (bookmark viejo, informe que se cayó):
            // caer a la lista en vez de romper.
            mostrar("resultados", undefined, "replace");
            return;
          }
        }
        // Reflejar la vista en el hash sin ensuciar el historial: la entrada
        // de detalle SÍ va al historial (para que "atrás" la deshaga y vuelva
        // a la lista); los cambios entre lista/evolución sólo reemplazan.
        try {
          if (historyMode === "push" && enDetalle) {
            history.pushState(null, "", "#labs&r=" + idx);
          } else if (historyMode === "push" && !enDetalle && location.hash.indexOf("#labs&r=") === 0) {
            history.pushState(null, "", "#labs");
          } else if (historyMode === "replace" && !enDetalle && location.hash.indexOf("#labs&r=") === 0) {
            history.replaceState(null, "", "#labs");
          }
        } catch (e) {}
      }

      elems.btnResultados.addEventListener("click", function () { mostrar("resultados", undefined, "push"); });
      elems.btnEvolucion.addEventListener("click", function () { mostrar("evolucion", undefined, "replace"); });

      // "Ver todos los resultados" desde una tarjeta de la lista.
      pintarLista(elems.panelResultados, informes, clinicoHref, function (i) {
        mostrar("detalle", i, "push");
        // Llevar el scroll al tope de la pestaña — si no, el paciente abre el
        // detalle y sigue viendo la mitad de la tarjeta de abajo.
        try { host.scrollIntoView({ block: "start", behavior: "smooth" }); } catch (e) {}
      });
      montarProveedor(elems.panelResultados, data);
      montarSubida(elems.panelResultados);
      pintarNota(clinicoHref);
      // Deep-link por hash (#labs&r=<idx>) — compartible, y sobrevive al
      // refresh. Resuelve el índice antes del primer mostrar().
      var hashIdx;
      var hm = /^#labs&r=(\d+)$/.exec(location.hash || "");
      if (hm && Number(hm[1]) < informes.length) hashIdx = Number(hm[1]);
      // popstate: el botón atrás del navegador sale del detalle y vuelve a
      // la lista — actualiza VISTA sin recargar.
      popstateHandler = function () {
        if (!alive()) return;
        var pm = /^#labs&r=(\d+)$/.exec(location.hash || "");
        if (pm && Number(pm[1]) < informes.length) {
          if (VISTA !== "detalle" || detalleIdx !== Number(pm[1])) mostrar("detalle", Number(pm[1]), "silent");
        } else if (VISTA === "detalle") {
          mostrar("resultados", undefined, "silent");
        }
      };
      window.addEventListener("popstate", popstateHandler);
      if (hashIdx != null) mostrar("detalle", hashIdx, "silent"); else mostrar(VISTA, undefined, "silent");
    })();
  }

  function cargar() {
    if (!alive()) return;
    request = typeof AbortController === "undefined" ? null : new AbortController();
    fetch("/api/account/lab-results", { credentials: "same-origin", signal: request && request.signal })
      .then(function (r) {
        if (!r.ok) {
          var error = new Error("Lab results request failed");
          error.status = r.status;
          throw error;
        }
        return r.json();
      })
      .then(function (j) {
        if (!alive()) return;
        var data = j || {};
        render(data);
        // Reintento único ~8s después si quedan informes sin leer todavía —
        // el pase de IA corre en fondo; nunca un polling loop (spec:
        // "single retry, no polling loop").
        if (Number(data.analisis_pendiente) > 0 && !reintentoHecho) {
          reintentoHecho = true;
          retryTimer = setTimeout(cargar, 8000);
        }
      }).catch(function (error) {
        if (!alive()) return;
        if ((error.status === 401 || error.status === 403) && typeof options.onUnauthorized === "function") {
          options.onUnauthorized();
          return;
        }
        showLoadError();
      });
  }

  function showLoadError() {
    if (!alive()) return;
    host.innerHTML = '<p class="dcard-p" role="alert">' + T("account.mine.lab.load_error", null,
      "No pudimos mostrar tus resultados ahora. Volvé a intentar en un rato.") + '</p>';
  }
  ready.then(function () {
    if (!alive()) return;
    host.innerHTML = '<p style="font-size:16px;color:var(--text);">' + T("ai.notice.lab", null, "Estos resultados se leen con inteligencia artificial. Consultalos con tu médico. Si subís una imagen, se envía completa al proveedor.") + '</p><p class="dcard-p">' + T("account.mine.lab.loading", null, "Cargando…") + '</p>';
    cargar();
  }).catch(showLoadError);
  mounts.set(host, dispose);
  return dispose;
}
