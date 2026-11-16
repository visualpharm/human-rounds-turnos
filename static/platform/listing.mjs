/* static/platform/listing.mjs — unique listing/table engine for the patient
   site. Pages pass {cols, rows, options}; this module draws the columns.

   Canonical ES module (audit rows 1.1/4.1). Named exports; no browser
   global, no classic twin, no CommonJS wrapper. Lives under
   static/platform/ and is loaded through window.SANA_STATIC /
   sanaImportStatic. Top-level await on platform/i18n.mjs, hours.mjs,
   dates.mjs and platform/search.mjs so a consumer that awaits this
   module also awaits those dependencies — T() is live, walk-in enrichment
   uses Hours, popularity ranking uses Search, and date labels use Dates
   from the first render. Never fall back to unsorted rows or raw
   translation keys.

   Reglas (skills table-design + patient-flow-design + ui-rules):
   - un dato por celda; valores cortos en una sola línea;
   - orden canónico entidad-primero (responsable del producto, 2026-07-12): entidad · contexto ·
     centro · Orden · Próxima disponibilidad (la pastilla ES el botón cuando
     hay turno reservable; texto verde plano "Abre..." cuando es walk-in);
   - reservable SIEMPRE antes que walk-in en el sort por próxima (tiers ORDEN);
   - columna uniforme (todas las filas dicen el mismo texto genérico) colapsa
     a UNA nota al pie de la lista (responsable del producto, 2026-07-12) — ver colapsoUniforme;
   - encabezados SIEMPRE ordenables (responsable del producto, 2026-07-11); >15 filas → búsqueda; filtros perfilados
     (público/sexo/facetas) pre-cargados desde la cuenta;
   - links con estilo de link, pills sólo para estado/tags, texto ≥16px.

   Uso servidor: <div data-listado><div class="lst-slot"></div>
     <script type="application/json" class="lst-data">{…}</script></div>
     + <script type="module" src="…/listing.<hash>.mjs"></script>
   Uso cliente:  import { render } from the fingerprinted module URL */
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

var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url));
var Hours = await importDep("platform/hours.mjs", new URL("./hours.mjs", import.meta.url));
var Dates = await importDep("platform/dates.mjs", new URL("./dates.mjs", import.meta.url));
var Search = await importDep("platform/search.mjs", new URL("./search.mjs", import.meta.url));
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function norm(s) {
    return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  // Ancho real de un texto en la tipografía de un control (canvas: no toca el
  // DOM ni fuerza reflow por medición).
  var _mctx = null;
  function textWidth(txt, font) {
    if (!_mctx) _mctx = document.createElement("canvas").getContext("2d");
    _mctx.font = font;
    return _mctx.measureText(String(txt || "")).width;
  }
  function ageFrom(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return null;
    var b = new Date(+m[1], +m[2] - 1, +m[3]), n = new Date();
    var a = n.getFullYear() - b.getFullYear();
    if (n.getMonth() < b.getMonth() || (n.getMonth() === b.getMonth() && n.getDate() < b.getDate())) a--;
    return a >= 0 && a < 130 ? a : null;
  }
  // "hoy" por el reloj de la instalación (el mismo que usa Dates.corta para Hoy/Mañana),
  // como Date local a medianoche; sin dates.mjs, el del navegador.
  function hoyInstalacion() {
    var h = /^(\d{4})-(\d{2})-(\d{2})$/.exec(
      (Dates && Dates.hoyIsoInstalacion && Dates.hoyIsoInstalacion()) || "");
    return h ? new Date(+h[1], +h[2] - 1, +h[3]) : new Date();
  }
  // días de espera hasta una fecha ISO (0 = hoy, 1 = mañana); null si no parsea
  function diasHasta(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return null;
    var n = hoyInstalacion();
    return Math.round((new Date(+m[1], +m[2] - 1, +m[3]) -
      new Date(n.getFullYear(), n.getMonth(), n.getDate())) / 864e5);
  }
  // A Dates.corta label placed after a verb («Sacar», «Abre»): Spanish lowers
  // the weekday («Sacar jue 19 nov»), English keeps «Book Thu 19 Nov»; «Hoy» /
  // «Mañana» go lower in every language — same criterion as withWeekdayArticle.
  function enFrase(label) {
    label = String(label || "");
    var hoy = T("date.today", null, "Hoy"), man = T("date.tomorrow", null, "Mañana");
    var rel = label === hoy || label.indexOf(hoy + ",") === 0 ||
      label === man || label.indexOf(man + ",") === 0;
    return rel || T("date.article", null, "el") ? label.charAt(0).toLowerCase() + label.slice(1) : label;
  }
  function fechaCorta(iso, hora) {
    if (!Dates || !Dates.corta) return "";
    var label = Dates.corta(iso, hora);
    if (!label) return "";
    return T("listing.book_verb", null, "Sacar") + " " + enFrase(label);
  }
  // The same short date inside a sentence: «mañana, 08:00» / «el mié 2 dic,
  // 08:00» (es), «Wed 2 Dec, 08:00» (en). Twin of
  // site_schedule._short_date_in_phrase (review 2026-11-15, P21).
  function fechaEnFrase(iso, hora) {
    if (!Dates || !Dates.corta) return "";
    var label = Dates.corta(iso, hora);
    if (!label || !T("date.article", null, "el")) return label || "";
    var hoy = T("date.today", null, "Hoy"), man = T("date.tomorrow", null, "Mañana");
    var low = label.charAt(0).toLowerCase() + label.slice(1);
    if (label === hoy || label.indexOf(hoy + ",") === 0 || label === man || label.indexOf(man + ",") === 0) return low;
    return T("date.weekday_with_article", { day: low }, "el " + low);
  }

  // Icons are Icons8 files from /static/icons (same SF-regular pack as the
  // rest of the site), painted through a mask so they take the text colour —
  // never an inline <svg> or a letter in a CSS circle (review 2026-11-15).
  function icon(name, cls) {
    return '<span class="lst-ico lst-ico--' + name + (cls ? " " + cls : "") + '" aria-hidden="true"></span>';
  }
  var SEARCH_ICON = icon("search");

  // Two initials of a person's name, skipping the title («Dra. Ana Pérez» →
  // «AP»); the server's avatar_ini wins when it sends one.
  function iniciales(nombre) {
    var partes = String(nombre || "").replace(/^\s*(dra?|lic|prof)\.?\s+/i, "").trim().split(/\s+/)
      .filter(function (w) { return /^\p{L}/u.test(w); });
    if (!partes.length) return "";
    var a = partes[0].charAt(0), b = partes.length > 1 ? partes[partes.length - 1].charAt(0) : "";
    return (a + b).toUpperCase();
  }

  // kind del estado/próximo -> clase de color (flexible: sirve a próximo y a estado)
  // walkin -> muted (responsable del producto, 2026-07-11 12:27): lo reservable es lo prominente;
  // todo walk-in va atenuado, también su texto genérico sin pastilla.
  var KIND = {
    turno: "strong", ok: "ok", green: "ok", verde: "ok",
    solicitud: "muted", walkin: "muted", gray: "muted", muted: "muted",
    amber: "warn", warn: "warn", accent: "accent", strong: "strong"
  };
  function kindCls(k) { return KIND[k] || "muted"; }

  // ---- calibración de orden y color (responsable del producto, 2026-07-11 12:27) ----------------
  // ORDEN: el tier "0" es EXCLUSIVO de los turnos reservables (kind=turno, por
  // fecha) — "con turno" va SIEMPRE antes que cualquier walk-in. TODO walk-in
  // vive en el tier "1": abierto-ahora primero ("1!"), después la apertura más
  // próxima por fecha ("1"+iso), después los genéricos sin horario parseable
  // ("1a" vení en el día / "1b" por orden de llegada); "2" = te confirma el
  // centro. (Revoca "abierto gana a cualquier fecha", que mezclaba con/sin
  // turno en el orden.)
  // COLOR (responsable del producto, 2026-10-19, llamada con la contraparte técnica): TODOS los turnos son verde, sin
  // dorado ni rojo — para no confundir. La lejanía sólo vira el verde de a poco
  // hacia un azul ("el aire no es transparente"): 11 pasos de 3 días, w0 = hoy o
  // mañana, w10 = 30 días o más (o fecha desconocida). Las pastillas walk-in
  // usan una única variante atenuada. Mismo criterio en site_studies._cta_bucket.
  var ORDEN = {
    abierto: function (base) { return "1!" + (base || ""); }, // "!" < dígitos/letras
    abre: function (iso, hora) { return "1" + (iso || "") + (hora || ""); },
    bucket: function (dd) {
      return "w" + (dd == null ? 10 : Math.max(0, Math.min(10, Math.round(dd / 3))));
    }
  };

  // CSS de los 11 pasos w0..w10: --slot-c = verde del tema mezclado con el azul
  // lejano en P% (0..100). Botón (<a>) = fondo lleno; pastilla de estado (<span>)
  // = la misma tinta, tenue. Blanco sobre cualquier paso da contraste AA.
  function waitCss() {
    var css = "";
    for (var i = 0; i <= 10; i++) {
      var c = ".lst-time--w" + i;
      css += c + "{--slot-c:color-mix(in srgb,#2c5f9e " + (i * 10) + "%,var(--accent,#0d7d48));}" +
        "span" + c + "{color:color-mix(in srgb,var(--slot-c),#000 18%);" +
          "border-color:color-mix(in srgb,var(--slot-c),#fff 65%);" +
          "background:color-mix(in srgb,var(--slot-c),#fff 90%);}";
    }
    return css;
  }

  // ---- registro canónico de columnas (single source of truth, responsable del producto 2026-07-11) --
  // Cada superficie de listado del sitio (home, /doctors, /center/<slug>, /d/<cluster>,
  // /study/<cid>/<slug>, my-requests) le pasa a listing.js su lista de columnas
  // como CLAVES de este registro (string) — el spec completo (tipo, dato, href, label,
  // avatar, hideSm, right) vive UNA sola vez acá. Un objeto {preset:"clave", ...} pisa
  // puntualmente algún campo (p.ej. hideSm sólo en una tabla); un objeto crudo sin
  // `preset` se usa tal cual (compat con columnas ad-hoc como "enviada"/"detalle" en
  // my-requests, que no tienen equivalente canónico). El ORDEN de salida es
  // SIEMPRE el canónico de abajo, sin importar en qué orden vengan las claves.
  // Orden canónico entidad-primero (responsable del producto, 2026-07-12 — revoca la columna "Turno"
  // separada): entidad (Servicio/Estudio/Médico) · contexto (Especialidad/Área) ·
  // Centro · Orden · Próxima disponibilidad. La pastilla de Próxima disponibilidad
  // ES el botón cuando hay turno reservable (link directo, con un link secundario
  // "más horarios" a la página informativa); walk-in es texto verde plano, nunca
  // clickeable — no hay sistema de turnos que accionar.
  function presets() {
    return {
    servicio:     { t: "link", key: "nombre", href: "href", label: T("listing.col.servicio", null, "Servicio") },
    estudio:      { t: "link", key: "nombre", href: "href", label: T("listing.col.estudio", null, "Estudio") },
    medico:       { t: "link", key: "nombre", href: "href", label: T("listing.col.medico", null, "Médico"), avatar: true },
    especialidad: { t: "link", key: "especialidad", href: "esp_href", label: T("listing.col.especialidad", null, "Especialidad"), hideSm: true, sub: true },
    area:         { t: "text", key: "area", label: T("listing.col.area", null, "Área"), hideSm: true, sub: true },
    centro:       { t: "link", key: "centro", href: "centro_href", label: T("listing.col.centro", null, "Centro"), sub: true },
    // Estudios (2026-07-18): link al centro con más horas semanales + "+N" a
    // los demás (popover "También se hace en") — mismo lenguaje que
    // Especialidad/Centro en Médicos, ya no texto plano ni "3 centros".
    lugar:        { t: "lugar", key: "lugar", href: "lugar_href", extra: "lugar_mas", label: T("listing.col.centro", null, "Centro"), sub: true },
    estado:       { t: "estado", key: "estado", label: T("listing.col.estado", null, "Estado") },
    proximo:      { t: "proximo", key: "proximo", href: "turno_href", more: "info_href", label: T("listing.col.proximo", null, "Próxima disponibilidad") },
    // Orden stays on a phone (review 2026-11-15, P6/D17): a compact line
    // under the name with Centro — a patient must know BEFORE tapping that
    // the visit needs a referral.
    orden:        { t: "orden", key: "orden", label: T("listing.col.orden", null, "Orden") },
    accion:       { t: "action", key: "accion", href: "accion_href", label: T("listing.col.action", null, "Acción"), right: true, sortable: false }
    };
  }
  var COL_ORDER = ["servicio", "estudio", "medico", "especialidad", "area",
    "centro", "lugar", "estado", "orden", "proximo", "accion"];

  // ---- colapso de columnas uniformes (responsable del producto, 2026-07-12) --------------------
  // Si TODAS las filas visibles dicen exactamente lo mismo en una columna
  // informativa, repetirlo fila por fila es ruido (ui-rules: cada dato aparece
  // UNA vez): la columna se esconde y la explicación sale una sola vez como
  // nota al pie, redactada como oración completa que además aclara QUÉ
  // confirma el centro — el fragmento "Te confirma el centro" repetido no lo
  // decía. Sólo colapsan los textos genéricos conocidos (sin fecha ni
  // apertura); una celda con dato real (pastilla) nunca colapsa. La columna
  // Próxima disponibilidad colapsa cuando TODA la lista es walk-in genérico
  // ("Por orden de llegada" / "Vení en el día" n veces) o el mismo texto de
  // "te confirma el centro" — la pastilla-botón reservable jamás colapsa.
  // Which generic "next availability" texts collapse into a single footnote.
  // Keyed on `kind`, the wire value the server sends, NOT on the rendered
  // text: the text is translated now, and a Spanish lookup table would stop
  // matching the moment a visitor switched language (2026-08-04). Rows still
  // have to say the SAME thing to collapse — `p.v === p0.v` below — so
  // "Vení en el día" and "Por orden de llegada" never merge into one note
  // just because both are walk-in.
  var COLAPSA = { solicitud: "solicitud", walkin: "walkin" };

  // "el martes"/"Tuesday": castellano antepone artículo y baja el día a
  // minúscula mid-frase; inglés capitaliza el día SIEMPRE y no antepone nada
  // (mismo criterio que hours.mjs:frase() y week.js:bloques() — el responsable del producto,
  // 2026-08-04/05). `d` ya viene con la mayúscula correcta del catálogo
  // (date.day.*), así que sólo se toca si el idioma activo lo pide: bajarla
  // siempre (como hacía antes) rompía el inglés ("Book saturday" en vez de
  // "Book Saturday").
  function withWeekdayArticle(d) {
    var esGram = !!T("date.article", null, "el");
    if (!esGram) return T("date.weekday_with_article", { day: d }, "" + d);
    var lowered = d.charAt(0).toLowerCase() + d.slice(1);
    return T("date.weekday_with_article", { day: lowered }, "el " + lowered);
  }

  function esPlano(p) { return !!p && !p.dia && !p.open && !p.abre && !p.habilita; }
  // `all` (optional): every row of the page, filter or search aside — a
  // context column hides only when the PAGE is uniform, never because a
  // search happened to leave two rows of one specialty.
  function colapsoUniforme(cols, rows, all) {
    var skip = {}, tipos = [];
    var pagina = all && all.length ? all : rows;
    if (rows && rows.length) cols.forEach(function (c, i) {
      var tipo = null;
      if (c.t === "proximo") {
        var p0 = rows[0][c.key] || {};
        var t0 = COLAPSA[p0.kind];
        if (t0 && rows.every(function (r) {
          var p = r[c.key] || {};
          return esPlano(p) && p.v === p0.v;
        })) tipo = t0;
      }
      // A context column (specialty, center, area) naming the same place on
      // every row says what the page title already says: hidden, no note.
      // Same place = same href (gendered labels share one); a row listing
      // several («_todas») is never uniform.
      if (c.sub && (c.t === "link" || c.t === "text") && pagina.length > 1) {
        var id0 = String(pagina[0][c.href || ""] || pagina[0][c.key] || "");
        if (id0 && pagina.every(function (r) {
          var todas = r[c.key + "_todas"];
          return !(todas && todas.length > 1) &&
            String(r[c.href || ""] || r[c.key] || "") === id0;
        })) { skip[i] = 1; return; }
      }
      // «Sin orden» on every row: one sentence above the table instead. Only
      // an explicit false counts — a row that doesn't say is never «sin orden».
      if (c.t === "orden" && pagina.length > 1 && pagina.every(function (r) { return r[c.key] === false; })) {
        tipo = "sin_orden";
      }
      if (tipo) { skip[i] = 1; if (tipos.indexOf(tipo) < 0) tipos.push(tipo); }
    });
    return { skip: skip, tipos: tipos };
  }
  // nota única al pie: con varias filas y columna de centro invita a elegir
  // ("apply to a medical center and they will confirm and assign the time").
  function notaColapso(tipo, n, elegir) {
    if (tipo === "sin_orden")
      return T("listing.note.no_order", null, "No hace falta orden médica.");
    if (tipo === "solicitud")
      return n > 1 && elegir
        ? T("listing.note.request_choose", null,
            "Elegí dónde atenderte: el centro te confirma la fecha y la hora del turno.")
        : T("listing.note.request_one", null,
            "El centro te confirma la fecha y la hora del turno.");
    return n > 1
      ? T("listing.note.walkin_many", null, "Se atienden sin turno, por llegada.")
      : T("listing.note.walkin_one", null, "Se atiende sin turno, por llegada.");
  }
  var COL_RANK = {};
  COL_ORDER.forEach(function (k, i) { COL_RANK[k] = i; });
  function cloneCol(o) { var c = {}; for (var k in o) c[k] = o[k]; return c; }
  // expande UNA columna del payload (string preset | {preset,...override} | objeto
  // crudo) a su spec completa + el rank canónico que decide su posición.
  function expandCol(item, idx) {
    var col, rank;
    if (typeof item === "string") {
      col = cloneCol(presets()[item] || { t: "text", key: item, label: item });
      rank = COL_RANK[item] != null ? COL_RANK[item] : COL_ORDER.length + idx;
    } else if (item && item.preset) {
      col = cloneCol(presets()[item.preset] || {});
      for (var k in item) if (k !== "preset") col[k] = item[k];
      rank = COL_RANK[item.preset] != null ? COL_RANK[item.preset] : COL_ORDER.length + idx;
    } else {
      col = item; // objeto crudo sin equivalente canónico: al final, orden de llegada
      rank = COL_ORDER.length + idx;
    }
    return { col: col, rank: rank };
  }
  // ordena SIEMPRE por rank canónico (estable: dos columnas sin preset conocido
  // conservan el orden en que llegaron entre sí).
  function expandCols(list) {
    return (list || [])
      .map(function (item, idx) { return expandCol(item, idx); })
      .sort(function (a, b) { return a.rank - b.rank; })
      .map(function (x) { return x.col; });
  }

  // ---- pastillas puras (responsable del producto, 2026-07-12) — sin estado, testeables directo ---
  // botón reservable: LA pastilla ES el botón — <a> con el href de la acción de
  // la fila, y dice el VERBO (mockup el responsable del producto 2026-07-14): "Sacar mañana, 13:00" /
  // "Sacar el martes 14 de julio, 15:00" — un botón dice lo que hace, no sólo
  // la fecha. Partes en spans para separadores correctos; sin nota (la nota
  // walk-in vive en openText).
  function timePill(bucket, dow, fecha, hora, href, shortLabel) {
    var d = String(dow || "");
    // "el martes" / "Tuesday": UNA frase de catálogo (date.weekday_with_article),
    // no el artículo pegado al día a mano — un idioma sin artículo (inglés)
    // deja ese key vacío, y pegar "" + " " + día a mano deja un espacio
    // colgando (2026-08-05: PR13 lo hacía así, chocó con date.article de PR14
    // y el listado en inglés salía con doble espacio, "Sacar  Tuesday").
    // "Hoy"/"Mañana" no llevan artículo en ningún idioma; la minúscula de
    // "hoy"/"today" es la MISMA en los dos idiomas (no es gramática del
    // castellano), así que esa rama sigue bajando la inicial sin condición
    // — sólo el día de semana necesita el criterio de withWeekdayArticle
    // (2026-08-05: bajaba SIEMPRE y el inglés salía "Book saturday").
    var relative = d === T("date.today", null, "Hoy") || d === T("date.tomorrow", null, "Mañana");
    var dl = d ? d.charAt(0).toLowerCase() + d.slice(1) : d;
    var when = relative ? dl : (d ? withWeekdayArticle(d) : d);
    var full = T("listing.book_verb", null, "Sacar") + " " + when
      + (fecha ? " " + fecha : "") + (hora ? ", " + hora : "");
    // ONE date format at every width (review 2026-11-15, D20/P21): the short
    // label «Sacar jue 19 nov, 08:00» — the long «el miércoles 18 de
    // noviembre» desktop form is gone, so the column never mixes two shapes.
    var label = shortLabel || full;
    return '<a class="lst-time lst-time--' + bucket + '" href="' + esc(href || "#") + '">' +
      esc(label) + "</a>";
  }

  // Servicio/turno «con orden» (contraparte técnica, llamada 19/10): el botón dice la acción
  // real — «Subí tu orden» — y el primer turno queda como nota al lado. Lleva
  // el mismo href que el turno (el asistente arranca por la orden y ya trae el
  // horario elegido). bucket "pedir" = sin fecha conocida (tinta).
  function orderPill(bucket, href, when) {
    return '<a class="lst-time lst-time--' + bucket + '" href="' + esc(href || "#") + '">' +
      esc(T("listing.upload_order", null, "Subí tu orden")) + "</a>" +
      // the date alone: the column header already says «Próxima
      // disponibilidad», a «Primer turno:» prefix repeated it (review D9)
      (when ? '<span class="lst-time-note lst-time-note--when">' + esc(when) + "</span>" : "");
  }

  // Track of each column in the 901–1120 doctors' grid (tableHtml writes
  // the row template into --lst-av-grid from the columns a table shows).
  var AV_TRACK = {
    nombre: "minmax(210px,1.25fr)",
    centro: "minmax(120px,.78fr)",
    orden: "minmax(88px,.55fr)",
    proximo: "minmax(238px,1.45fr)",
    _: "minmax(120px,.78fr)"
  };

  // Servicio reservable que todavía no abrió turnos (contraparte técnica, llamada 19/10):
  // en vez de un «Sacar turno» que termina en «escribinos», el mensaje —
  // cuándo se habilitan, o que la agenda no tiene fechas nuevas. `ap` viene del
  // server (site_availability._habilita). Texto plano: no hay nada que accionar acá.
  function habilitaCell(ap, row) {
    var cuando = "";
    if (ap && ap.estado === "fecha" && ap.dia) {
      var w = fechaEnFrase(ap.dia, ap.hora || "") || ap.dia;
      cuando = T("service.noslots.opens_from_when", { when: w }, "Se habilitan desde " + w);
    }
    // sin fecha de apertura: una sola frase, no la misma idea dos veces
    return '<span class="lst-closed">' +
      esc(T("service.noslots.when_label", null, "No hay turnos disponibles")) +
      // the space keeps the two sentences apart in textContent (screen
      // readers, copy-paste); the block span still starts its own line
      (cuando ? ' <span class="lst-closed-when">' + esc(cuando) + "</span>" : "") +
      remedioHtml(ap, row) + "</span>";
  }

  // The patient gets the next step, never only «no hay turnos» (remedies
  // rule; review 2026-11-15 item 9). The server's own remedy wins
  // (`ap.remedio` = {href, texto}); without one, the row's specialty — or its
  // center, when the page already IS that specialty — is the way on.
  function remedioHtml(ap, row) {
    var rm = ap && ap.remedio;
    if (rm && rm.href && rm.texto) {
      return ' <a class="lst-closed-next" href="' + esc(rm.href) + '">' + esc(rm.texto) + "</a>";
    }
    if (!row) return "";
    var here = "";
    try { here = (typeof location !== "undefined" && location.pathname) || ""; } catch (e) { here = ""; }
    // never the row's title in the sentence: `especialidad` is the doctor's
    // gendered title («Cardióloga»), sometimes with what the row books
    if (row.esp_href && row.esp_href !== here) {
      return ' <a class="lst-closed-next" href="' + esc(row.esp_href) + '">' +
        esc(T("listing.noslots.other_doctors", null, "Ver otros médicos de la especialidad")) + "</a>";
    }
    if (row.centro_href && row.centro_href !== here && row.centro) {
      return ' <a class="lst-closed-next" href="' + esc(row.centro_href) + '">' +
        esc(T("listing.noslots.center", { centro: row.centro }, "Ver los turnos de " + row.centro)) + "</a>";
    }
    return "";
  }

  // texto walk-in: verde plano, SIN pastilla — nunca clickeable, no hay turno
  // que sacar. `nota` = sufijo ("sin turno, por llegada").
  function openText(texto, nota) {
    return '<span class="lst-open">' + esc(texto) + "</span>" +
      (nota ? '<span class="lst-open-note"> ' + esc(nota) + "</span>" : "");
  }

  // link secundario "más horarios" — suprimido si no hay destino propio o es
  // igual al href de la acción (nada nuevo que mostrar).
  // An outline secondary button in ink next to the one filled primary
  // (review 2026-11-15; replaces 2026-07-14's «same colour as the button»).
  function moreLink(infoHref, href, bucket) {
    if (!infoHref || infoHref === href) return "";
    return '<a class="lst-more' + (bucket ? " lst-more--" + bucket : "") +
      '" href="' + esc(infoHref) + '">'
      + esc(T("listing.more_times", null, "Más horarios")) + "</a>";
  }

  // Búsqueda sin resultados en la página de un centro: el paso siguiente es la
  // misma consulta en la búsqueda de todos los centros (regla remedies: al
  // paciente se le da la solución, no una caja vacía).
  function searchAllHtml(q, href) {
    q = String(q || "").trim();
    if (!q || !href) return "";
    return '<p class="lst-empty"><a href="' + esc(href + "?q=" + encodeURIComponent(q)) + '">' +
      esc(T("listing.search_all", { q: q }, "Buscar «{q}» en todos los centros")) + "</a></p>";
  }

  // Fila de grupo (responsable del producto, 2026-08-05 — «Resultados de laboratorio»): un
  // encabezado de sección DENTRO del <tbody> (fecha + laboratorio de un
  // informe), no una columna más. Genérico: cualquier row puede traer
  // `grupo:{id,primary,secondary}` — tableHtml() abre una fila nueva cada
  // vez que el `id` cambia respecto de la anterior; una tabla cuyas filas
  // nunca traen `grupo` (todas las de hoy) no dibuja ni un solo
  // <tr class="lst-grp"> y su CSS no cambia (las reglas nuevas están todas
  // bajo `:has(tr.lst-grp)`, ver injectStyle).
  function grupoRowHtml(g, colspan) {
    return '<tr class="lst-grp"><th colspan="' + colspan + '"><span class="lst-grp-d">' +
      esc(g.primary || "") + "</span>" +
      (g.secondary ? '<span class="lst-grp-l"> · ' + esc(g.secondary) + "</span>" : "") +
      "</th></tr>";
  }

  // ---- una instancia por mount -------------------------------------------
  // Registro de grupos de búsqueda (opts.searchGroup): group -> [{mount, apply}].
  // El driver difunde su query a los demás miembros del grupo. Vive a nivel de
  // módulo para que un listado alcance a sus hermanos de la misma página.
  var GROUPS = {};

  function initMount(mount, payload) {
    var opts = payload.options || {};
    var COLS = expandCols(payload.cols);
    var ROWS = payload.rows || [];
    // Ranking por popularidad = horas semanales de atención (responsable del producto, 2026-07-14:
    // "a las páginas de listado también"). MISMA implementación que la búsqueda
    // de la home — platform/search.mjs, una sola vez — cuando opts.rank ===
    // "popularidad": agrupa por nombre, grupos por horas totales desc, filas
    // por horas desc. Native Search is required: never fall back to the
    // unsorted input if the module is missing.
    // Deja el orden por defecto en "ninguno" para respetarlo; los encabezados
    // siguen reordenando al click (p.ej. por próxima disponibilidad). Las
    // superficies "elegí el centro que te atiende antes" (estudio-centros,
    // cluster-centros) NO lo piden: ahí manda la espera, no la popularidad.
    var RANKED = opts.rank === "popularidad";
    if (RANKED) {
      if (!Search || typeof Search.ordenarPorPopularidad !== "function") {
        throw new Error("[listing] popularity ranking requires native Search");
      }
      ROWS = Search.ordenarPorPopularidad(ROWS);
    }
    // ¿la lista compara centros? decide la redacción de la nota de colapso
    var ELEGIR = COLS.some(function (c) { return c.key === "centro" || c.key === "lugar"; });
    // El host se limpia SIEMPRE antes de dibujar: un caller de api.render()
    // puede haber dejado adentro un "Cargando…" (o un render anterior) — sin
    // esto, initMount APPENDEABA su .lst-slot al lado de lo que ya hubiera,
    // así que el spinner viejo quedaba flotando bajo la tabla nueva para
    // siempre (2026-08-05: bug real en prod, lab-results.js). El camino de
    // auto-init server-side (boot(), [data-listado]) ya leyó su `.lst-data`
    // en `payload` antes de llegar acá, así que perder ese <script> es seguro.
    // The server's slot carries the height reserve (--lst-reserva*, see
    // .lst-slot:empty in site_shell.py). The new slot keeps it: with profile
    // filters the first draw waits for /api/account/me, and an empty slot
    // without the reserve collapsed the page for that whole wait — the footer
    // jumped into view and back (CLS 0.26-0.37 on /doctors, 2026-11-10).
    // Only the server's slot (--ssr) is copied: a client re-render draws a
    // new payload, and the previous one's reserve would be the wrong height.
    var prev = mount.querySelector(".lst-slot--ssr");
    var reserva = prev ? prev.getAttribute("style") : "";
    mount.innerHTML = "";
    var slot = document.createElement("div");
    slot.className = "lst-slot";
    if (reserva) slot.setAttribute("style", reserva);
    mount.appendChild(slot);

    // walk-in con horario del cartel → estado real (horarios.js): abierto ahora
    // u hora de la próxima apertura. El sort queda SIEMPRE en el tier walk-in
    // ("1…", ver ORDEN): un walk-in nunca desplaza a un turno reservable.
    (function enrichWalkins() {
      if (!Hours) return;
      // Nombres de día con artículo ("el domingo"): reusa date.day.* +
      // date.weekday_with_article (catálogo compartido con dates.mjs) en vez de
      // mantener una segunda tabla de días acá — mismo índice que
      // Date.getDay() (0=domingo). date.weekday_with_article ES la frase
      // completa ("el {day}" / "{day}"), no el artículo pegado a mano: un
      // idioma sin artículo (inglés) deja date.article vacío, y "" + " " +
      // día a mano deja un espacio colgando que "Abre {when}" duplica
      // (2026-08-05, ver timePill arriba). Cada T() con clave y fallback
      // LITERALES (no una clave armada por concatenación) para que
      // i18n-fallbacks.test.mjs valide también este call site.
      // La minúscula ("el domingo") es gramática del castellano, no
      // universal: withWeekdayArticle() (definida arriba, mismo criterio que
      // timePill) es la que decide según el idioma activo — bajarla acá
      // SIEMPRE hacía que un walk-in en inglés dijera "Opens tuesday" en vez
      // de "Opens Tuesday" (bug real, demo, 2026-08-05).
      function diaConArticulo(i) {
        var d = [
          T("date.day.sun", null, "Domingo"),
          T("date.day.mon", null, "Lunes"),
          T("date.day.tue", null, "Martes"),
          T("date.day.wed", null, "Miércoles"),
          T("date.day.thu", null, "Jueves"),
          T("date.day.fri", null, "Viernes"),
          T("date.day.sat", null, "Sábado")
        ][i];
        return withWeekdayArticle(d);
      }
      // "Abre {when}" es UNA frase (design note 2026-08-04: nunca pegar un
      // fragmento traducido a mano con "Abre" + día en el call site).
      function abreLabel(when) {
        return T("listing.walkin.opens", { when: when }, "Abre " + when);
      }
      ROWS.forEach(function (r) {
        COLS.forEach(function (c) {
          if (c.t !== "proximo" && c.t !== "estado") return;
          var p = r[c.key];
          if (!p || p.dia || !p.horario) return;
          var e = Hours.estado(p.horario);
          if (!e) return;
          var hoy = hoyInstalacion();
          if (e.abierto) {
            p.open = true;
            if (e.hasta != null) {
              p.hasta = String(Math.floor(e.hasta / 60)).padStart(2, "0") + ":" +
                String(e.hasta % 60).padStart(2, "0");
            }
            p.sort = ORDEN.abierto(p.sort); // primero entre los walk-in, nunca sobre un turno
          } else if (e.dia != null) {
            // e.fecha viene de las recurrencias mensuales ("el primer día hábil
            // de cada mes") y puede caer en otro mes; lo semanal se deriva de off.
            var iso = e.fecha;
            if (!iso) {
              var d = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() + e.off);
              iso = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
                "-" + String(d.getDate()).padStart(2, "0");
            }
            var hora = e.min == null ? "" : String(Math.floor(e.min / 60)).padStart(2, "0") +
              ":" + String(e.min % 60).padStart(2, "0");
            // ONE way to say a walk-in opening (review 2026-11-15, D35): «Abre»
            // + the same short date the booking buttons use — «Abre mañana,
            // 08:00» / «Abre jue 19 nov, 08:00» — never «el jueves» on one row
            // and «Lunes 3 de agosto» on the next.
            var corta = (Dates && Dates.corta && Dates.corta(iso, hora)) || "";
            p.abre = {
              off: e.off, hora: "", fecha: "",
              dow: corta ? abreLabel(enFrase(corta)) : abreLabel(e.off === 0 ? T("date.today_lower", null, "hoy")
                : e.off === 1 ? T("date.tomorrow_lower", null, "mañana")
                : diaConArticulo(e.dia)) + (hora ? ", " + hora : "")
            };
            // apertura por fecha dentro del tier walk-in: antes que los
            // genéricos "1a"/"1b" (el año "2…" ordena antes que la letra),
            // después de "abierto ahora", y NUNCA sobre un turno reservable.
            p.sort = ORDEN.abre(iso, hora);
          }
        });
      });
    })();
    if (!ROWS.length) {
      slot.innerHTML = (opts.title ? '<h2 class="lst-h">' + esc(opts.title) + "</h2>" : "") +
        '<p class="lst-empty">'
        + esc(opts.empty || T("listing.empty", null, "No hay resultados.")) + "</p>";
      return;
    }

    var filters = opts.filters || [];
    function hasF(t) { return filters.some(function (f) { return f.t === t; }); }
    var FACETS = filters.filter(function (f) { return f.t === "facet"; });

    var WANT_SORT = opts.sort !== false;
    var WANT_SEARCH = opts.search === true || (opts.search !== false && ROWS.length > 15);
    var WANT_FILTERS = filters.length > 0;
    // On a phone the table has no header to tap: a long list gets a sort
    // select instead (review D34). Short lists don't need one.
    var WANT_PHONE_SORT = WANT_SORT && ROWS.length > 5;

    // Grupo de búsqueda (responsable del producto, 2026-07-23): en una página con dos tablas
    // (cluster = servicios + médicos; centro = médicos + servicios) UN solo
    // buscador filtra AMBAS. El listado con el buscador (WANT_SEARCH) es el
    // "driver"; los otros del mismo opts.searchGroup son "listeners" sin caja
    // propia. Al tipear, el driver difunde la query a los listeners; cada
    // listener sin resultados con query activa se OCULTA entero (título
    // incluido), así "Echegaray" muestra solo la tabla de médicos y "Urgencias"
    // solo la de servicios — nunca una tabla vacía debajo del buscador.
    var GROUP = opts.searchGroup || null;
    var IS_DRIVER = !!(GROUP && WANT_SEARCH);
    var IS_LISTENER = !!(GROUP && !WANT_SEARCH);

    // identidad de orden de una columna: dos columnas pueden leer el MISMO
    // dato — `id` distingue dos columnas que leyeran el mismo campo, para el sort.
    function sid(col) { return col.id || col.key; }

    // orden por defecto: la columna próximo si existe, si no la primera.
    // opts.defaultSort === false (responsable del producto, 2026-07-12) desactiva el auto-sort por
    // completo — deja defKey en false, que no matchea ninguna columna, así
    // sortVal(null, r) devuelve "" para todas las filas y el sort estable de
    // JS conserva el orden de entrada (usado por la búsqueda de la home para
    // respeta Search.ordenarPorPopularidad).
    var defKey = opts.defaultSort;
    // rankeado por popularidad → sin auto-sort por columna, se respeta el orden
    // que dejó Search (igual que defaultSort:false en la búsqueda de la home).
    if (defKey == null && RANKED) defKey = false;
    if (defKey == null) {
      var pc = COLS.filter(function (c) { return c.t === "proximo"; })[0];
      defKey = pc ? sid(pc) : (COLS[0] && sid(COLS[0]));
    }
    var state = { q: "", publico: "todos", sexo: null,
                  facet: {}, sortKey: defKey, sortDir: 1, filtrosAbiertos: false };
    FACETS.forEach(function (f) { state.facet[f.key] = ""; });
    // La clave de columna interna (facet.key, p.ej. "especialidad") puede diferir
    // del nombre del param en la URL (p.ej. "specialty") — sólo el lado URL se
    // tradujo al inglés (2026-07-17); el key interno queda igual.
    var FACET_URL_KEY = { especialidad: "specialty" };
    function facetUrlKey(f) { return FACET_URL_KEY[f.key] || f.key; }
    // preselección por query param (p.ej. /doctors?specialty=Medicina%20general
    // desde el subtítulo de la página de un médico) — sólo si el valor existe
    // entre las filas, para no dejar el filtro en un estado sin resultados.
    try {
      var qp = new URLSearchParams(location.search);
      FACETS.forEach(function (f) {
        var v = qp.get(facetUrlKey(f));
        if (v && ROWS.some(function (r) { return rowHasFacet(r, f.key, v); })) state.facet[f.key] = v;
      });
      // opts.urlState (responsable del producto, 2026-07-17): cada búsqueda/filtro de un listado es
      // una URL propia, compartible y bookmarkeable. Leemos ?q= al montar (las
      // facetas ya se leían arriba) y lo REESCRIBIMOS al cambiar — ver writeUrl.
      // Sólo se activa en páginas de UN listado (medicos/estudios); en páginas
      // con dos tablas (centro) pisaría el ?q= de la otra.
      if (opts.urlState) { var qq = qp.get("q"); if (qq) state.q = qq; }
    } catch (e) {}

    // Escribe el estado propio de ESTE listado (q + facetas) a la URL, sin tocar
    // otros params ni el hash. replaceState: la URL siempre queda compartible sin
    // llenar el historial tecla por tecla.
    function writeUrl() {
      if (!opts.urlState) return;
      try {
        var p = new URLSearchParams(location.search);
        if (state.q) p.set("q", state.q); else p.delete("q");
        FACETS.forEach(function (f) {
          var uk = facetUrlKey(f);
          if (state.facet[f.key]) p.set(uk, state.facet[f.key]); else p.delete(uk);
        });
        var qs = p.toString();
        history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
      } catch (e) {}
    }

    // perfil desde la cuenta: pre-carga público (edad) y sexo (ocultar el otro sexo)
    if (opts.profile !== false && (hasF("publico") || hasF("sexo"))) {
      fetch("/api/account/me", { credentials: "include" })
        .then(function (r) { return r.ok ? r.json().catch(function () { return null; }) : null; })
        .then(function (j) {
          var u = j && (j.usuario || j.user || (j.dni ? j : null));
          if (u) {
            var edad = ageFrom(u.nacimiento);
            if (edad != null && hasF("publico")) state.publico = edad < 18 ? "ninos" : "adultos";
            var sx = String(u.sexo || "").toUpperCase();
            if ((sx === "M" || sx === "F") && hasF("sexo")) state.sexo = sx;
          }
          render();
        }).catch(render);
    } else {
      render();
    }

    function colByKey(k) { for (var i = 0; i < COLS.length; i++) if (sid(COLS[i]) === k) return COLS[i]; return null; }

    function searchText(r) {
      var parts = [];
      COLS.forEach(function (c) {
        if (c.t === "link" || c.t === "text") parts.push(r[c.key]);
      });
      FACETS.forEach(function (f) { parts.push(r[f.key]); });
      // Patient-language aliases are searchable without becoming another
      // visible table column.
      if (Array.isArray(r.palabras)) parts = parts.concat(r.palabras);
      return norm(parts.filter(Boolean).join(" "));
    }

    // Los valores de una faceta en una fila: casi siempre uno (r[key]); una
    // fila con varios (un médico con dos especialidades) trae la lista en
    // r[key + "_todas"] y entra en el filtro por cualquiera de ellos.
    function rowFacetVals(r, key) {
      var all = r[key + "_todas"];
      if (Array.isArray(all) && all.length) return all;
      return r[key] ? [r[key]] : [];
    }
    function rowHasFacet(r, key, v) { return rowFacetVals(r, key).indexOf(v) >= 0; }

    function facetVals(key) {
      var seen = {}, out = [];
      ROWS.forEach(function (r) {
        rowFacetVals(r, key).forEach(function (v) { if (v && !seen[v]) { seen[v] = 1; out.push(v); } });
      });
      return out.sort();
    }

    function visible() {
      var q = norm(state.q);
      var rows = ROWS.filter(function (r) {
        if (hasF("sexo") && state.sexo && r.sexo && r.sexo !== state.sexo) return false;
        if (hasF("publico") && state.publico !== "todos" && r.publico && r.publico !== "todos" && r.publico !== state.publico) return false;
        for (var k in state.facet) if (state.facet[k] && !rowHasFacet(r, k, state.facet[k])) return false;
        if (q && !RANKED && searchText(r).indexOf(q) < 0) return false;
        return true;
      });
      if (q && RANKED) {
        // Public entity catalogs (/doctors, specialties and centers) use the
        // same name/initials matcher as home. Apply facets first, so matching
        // never brings back rows excluded by the person's chosen filters.
        // Keep literal phrase matches too (including stopword-only queries)
        // that the former substring filter accepted. Other tables retain
        // their existing filtering and ordering contract.
        var matched = Search.buscar(rows, q, function (r) {
          return { principal: [r.nombre], resto: [searchText(r)] };
        });
        var seen = new Set(matched);
        rows = rows.filter(function (r) {
          return !seen.has(r) && searchText(r).indexOf(q) >= 0;
        }).concat(matched);
      }
      var col = colByKey(state.sortKey);
      rows.sort(function (a, b) {
        var av = sortVal(col, a), bv = sortVal(col, b);
        return (av < bv ? -1 : av > bv ? 1 : 0) * state.sortDir;
      });
      return rows;
    }
    function sortVal(col, r) {
      if (!col) return "";
      if (col.t === "proximo") return (r[col.key] || {}).sort || "";
      if (col.t === "orden") return r[col.key] ? 1 : 0;
      return norm(r[col.key] || "");
    }

    // ---- render ----------------------------------------------------------
    function render() {
      var rows = visible();
      // Driver de un grupo sin resultados y con query activa: NO dibuja "no hay
      // resultados" (el match puede estar en la tabla hermana) — sólo la caja.
      var vacioDriver = IS_DRIVER && state.q && rows.length === 0;
      slot.innerHTML =
        (opts.title ? '<h2 class="lst-h">' + esc(opts.title) + "</h2>" : "") +
        ((WANT_SEARCH || WANT_FILTERS || WANT_PHONE_SORT) ? toolsHtml() : "") +
        (vacioDriver ? driverEmptyBody() : tableHtml(rows)) +
        (opts.note ? '<p class="lst-note">' + esc(opts.note) + "</p>" : "");
      wire(rows);
      fitSelects();
      // Listener sin resultados con query activa: se oculta entero.
      if (IS_LISTENER) mount.style.display = (state.q && rows.length === 0) ? "none" : "";
    }

    // Un <select> nativo se dimensiona por su opción MÁS LARGA, no por la que
    // está elegida: el filtro "Área" ocupaba 129px para mostrar la palabra
    // "Área". Con tres filtros eso desperdiciaba espacio suficiente para mandar
    // el tercero a otra línea aun cuando los tres valores visibles entraban.
    // Medimos el texto REALMENTE visible y mantenemos el grupo en una sola fila
    // hasta que esos tres valores ya no entren; recién entonces apilamos TODO el
    // grupo, nunca 2+1 por un breakpoint arbitrario.
    function fitSelects() {
      var box = slot.querySelector(".lst-selects");
      if (!box) return;
      var sels = [].slice.call(box.querySelectorAll(".lst-select"));
      if (!sels.length) return;
      box.classList.remove("lst-selects--stacked");
      var total = 0, quiere = [];
      sels.forEach(function (s) {
        s.style.width = "";
        var cs = getComputedStyle(s);
        var opt = s.options[s.selectedIndex];
        var w = Math.ceil(
          textWidth(opt ? opt.text : "", cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily) +
          parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) +
          parseFloat(cs.borderLeftWidth) + parseFloat(cs.borderRightWidth) + 2);
        quiere.push(w);
        s.style.width = w + "px";
        total += w;
      });
      // En el layout angosto los filtros ocupan una fila propia. Se compara
      // contra el padre porque un flex child con contenido intrínseco puede
      // reportar un ancho mayor al espacio real y esconder el desborde.
      var gaps = (parseFloat(getComputedStyle(box).columnGap) || 0) * (sels.length - 1);
      var avail = box.parentNode.clientWidth;
      if (window.matchMedia("(max-width:900px)").matches && total + gaps > avail + 1) {
        box.classList.add("lst-selects--stacked");
        sels.forEach(function (s) { s.style.width = ""; });
      }
    }
    // Un listener aplica la query difundida por el driver (misma caja lógica).
    function applyGroupQuery(q) { state.q = q || ""; render(); }
    if (GROUP) {
      (GROUPS[GROUP] = GROUPS[GROUP] || []).push({
        mount: mount, apply: applyGroupQuery, count: function () { return visible().length; }
      });
    }
    // Driver sin match propio: el resultado puede estar en la tabla hermana;
    // sólo si NINGUNA del grupo tiene filas se ofrece la búsqueda general.
    function groupAllEmpty() {
      return (GROUPS[GROUP] || []).every(function (g) { return g.count() === 0; });
    }
    function driverEmptyInner() {
      return groupAllEmpty() ? searchAllHtml(state.q, opts.searchAllHref) : "";
    }
    function driverEmptyBody() { return '<div class="lst-body">' + driverEmptyInner() + "</div>"; }
    // El breakpoint cambia tipografía y padding de los selects: hay que volver
    // a medirlos o el ancho queda calculado para el ancho anterior.
    window.addEventListener("resize", fitSelects);

    function toolsHtml() {
      var search = WANT_SEARCH
        ? '<div class="lst-searchgroup"><div class="lst-searchbox">' + SEARCH_ICON +
          '<input class="lst-search" type="search" inputmode="search" aria-label="' +
          esc(opts.searchLabel || opts.searchPlaceholder
              || T("listing.search.aria", null, "Buscar")) + '" placeholder="' +
          esc(opts.searchPlaceholder
              || T("listing.search.placeholder", null, "Servicio, estudio o especialidad"))
          + '" value="' +
          esc(state.q) + '" autocomplete="off"></div>' +
          ((opts.searchExamples || []).length
            ? '<p class="lst-examples"><span>'
              + esc(T("listing.examples.label", null, "Probá:")) + "</span>" +
              opts.searchExamples.map(function (q) {
                return '<a href="?q=' + encodeURIComponent(q) + '" data-search-example="' +
                  esc(q) + '">' + esc(q) + "</a>";
              }).join("") + "</p>"
            : "") + "</div>"
        : "";
      var blocks = "";
      // Filtros de selección única = dropdowns, NUNCA pills (ui-rules 2026-07-14:
      // "cada vez que dibujás una pill, confirmá que DEBE ser pill"; una elección
      // mutuamente exclusiva es un <select>). El valor por defecto del dropdown ES
      // la etiqueta del campo — "Área", "Para" — no un "Todos" genérico ni el viejo
      // "Área · todos" (the product owner, 2026-07-15).
      filters.forEach(function (f) {
        if (f.t === "publico") {
          // "Todas las edades" ES el valor por defecto (no un "Para" genérico,
          // Responsable del producto 2026-07-18: dos dropdowns leyendo "Para…" confundía cuál era
          // cuál) — pre-cargado desde la cuenta (niño/adulto) cuando se puede.
          var popts = [["todos", T("listing.age.all", null, "Todas las edades")],
                       ["adultos", T("listing.age.adults", null, "Adultos")],
                       ["ninos", T("listing.age.children", null, "Niños")]].map(function (o) {
            return '<option value="' + o[0] + '"' + (state.publico === o[0] ? " selected" : "") + ">" + esc(o[1]) + "</option>";
          }).join("");
          blocks += '<select aria-label="' + esc(T("listing.age.aria", null, "Edad del paciente"))
            + '" class="lst-select' + (state.publico !== "todos" ? " on" : "") + '" data-pubk>' + popts + "</select>";
        } else if (f.t === "sexo") {
          // Dropdown, no checkbox (the product owner, 2026-07-18: "gender must be an
          // unambiguous dropdown") — dice a QUIÉN atiende el servicio/médico,
          // no el sexo del profesional ("Para mujeres" = atiende mujeres,
          // p.ej. ginecología). Siempre visible cuando el filtro está
          // declarado; pre-cargado desde la cuenta (F/M) igual que antes.
          var sopts = [["", T("listing.audience.all", null, "Para todos")],
                       ["F", T("listing.audience.women", null, "Para mujeres")],
                       ["M", T("listing.audience.men", null, "Para varones")]].map(function (o) {
            return '<option value="' + o[0] + '"' + ((state.sexo || "") === o[0] ? " selected" : "") + ">" + esc(o[1]) + "</option>";
          }).join("");
          blocks += '<select aria-label="' + esc(T("listing.audience.aria", null, "A quién atiende"))
            + '" class="lst-select' + (state.sexo ? " on" : "") + '" data-sexok>' + sopts + "</select>";
        } else if (f.t === "facet") {
          var vals = facetVals(f.key);
          if (vals.length < 2) return;
          var optionsH = '<option value="">' + esc(f.label || "Todos") + "</option>" +
            vals.map(function (v) {
              return '<option value="' + esc(v) + '"' + (state.facet[f.key] === v ? " selected" : "") + ">" + esc(v) + "</option>";
            }).join("");
          blocks += '<select aria-label="' + esc(f.label || "Filtrar") + '" class="lst-select' + (state.facet[f.key] ? " on" : "") + '" data-facetk="' + esc(f.key) + '">' + optionsH + "</select>";
        }
      });
      // Los filtros van en su propio contenedor para poder tratarlos como UN
      // grupo (hook de layout de controles relacionados): una fila entera o
      // nada — nunca dos en una línea y el tercero colgando abajo.
      // Phone (≤900px): the filters fold behind ONE «Filtros» control, open
      // while any of them is set, and the sort select sits next to it.
      var activos = filtrosActivos();
      var abiertos = state.filtrosAbiertos || activos > 0;
      var toggle = blocks
        ? '<button type="button" class="lst-filtros' + (activos ? " on" : "") + '" aria-expanded="' +
          (abiertos ? "true" : "false") + '" data-filtros>' + icon("filter") +
          esc(activos ? T("listing.filters.count", { n: activos }, "Filtros (" + activos + ")")
                      : T("listing.filters.toggle", null, "Filtros")) + "</button>"
        : "";
      var sortSel = WANT_PHONE_SORT ? sortSelectHtml() : "";
      var phonebar = toggle || sortSel ? '<div class="lst-phonebar">' + toggle + sortSel + "</div>" : "";
      return '<div class="lst-tools' + (blocks ? " lst-tools--filters" : "") +
        (abiertos ? " lst-tools--open" : "") + '">' + search + phonebar +
        (blocks ? '<div class="lst-selects">' + blocks + "</div>" : "") + "</div>";
    }
    function filtrosActivos() {
      var n = 0;
      if (hasF("publico") && state.publico !== "todos") n++;
      if (hasF("sexo") && state.sexo) n++;
      for (var k in state.facet) if (state.facet[k]) n++;
      return n;
    }
    // «Ordenar por …»: the sortable columns the table shows, plus the
    // suggested order of a ranked list.
    function sortSelectHtml() {
      var u = colapsoUniforme(COLS, ROWS, ROWS);
      var cols = COLS.filter(function (c, i) { return !u.skip[i] && c.sortable !== false && c.label; });
      if (!cols.length) return "";
      var opciones = (defKey === false
        ? '<option value=""' + (state.sortKey === false ? " selected" : "") + ">" +
          esc(T("listing.sort.suggested", null, "Ordenar: sugerido")) + "</option>"
        : "") + cols.map(function (c) {
          var k = sid(c), lbl = String(c.label);
          lbl = lbl.charAt(0).toLowerCase() + lbl.slice(1);
          return '<option value="' + esc(k) + '"' + (state.sortKey === k ? " selected" : "") + ">" +
            esc(T("listing.sort.by", { col: lbl }, "Ordenar por " + lbl)) + "</option>";
        }).join("");
      return '<select class="lst-select lst-sortsel" aria-label="' +
        esc(T("listing.sort.aria", null, "Ordenar la lista")) + '" data-sortsel>' + opciones + "</select>";
    }
    function colCls(col) {
      // lst-td-<tipo>/lst-td-sub quedan como hooks de estilo aunque mobile
      // (<640px, responsable del producto 2026-07-12) ya no arma tarjeta — es tabla real con
      // scroll horizontal, así que sólo lst-hide-sm/lst-r importan ahí.
      var c = [
        "lst-td-" + (col.t === "estado" ? "proximo" : col.t),
        "lst-col-" + sid(col)
      ];
      if (col.sub) c.push("lst-td-sub");
      if (col.right) c.push("lst-r");
      if (col.hideSm) c.push("lst-hide-sm");
      return ' class="' + c.join(" ") + '"';
    }
    function th(col) {
      var cls = colCls(col);
      if (!WANT_SORT || col.sortable === false)
        return "<th" + cls + ">" + esc(col.label || "") + "</th>";
      var on = state.sortKey === sid(col);
      var caret = on ? (state.sortDir > 0 ? " ↑" : " ↓") : "";
      return "<th" + cls + '><button type="button" class="lst-sort' + (on ? " on" : "") +
        '" data-sort="' + esc(sid(col)) + '">' + esc(col.label || "") + caret + "</button></th>";
    }

    // pastilla vieja usada SÓLO por la columna "estado" (my-requests.js:
    // status de una SOLICITUD, no de un servicio/médico) — se mantiene
    // byte-idéntica al comportamiento anterior, sin <a> ni link secundario.
    function estadoPill(bucket, dow, fecha, hora, nota) {
      var h = '<span class="lst-time lst-time--' + bucket + '">' +
        '<span class="lst-time-d">' + esc(dow) + "</span>" +
        (fecha ? '<span class="lst-time-sep"> </span><span class="lst-time-f">' + esc(fecha) + "</span>" : "") +
        (hora ? '<span class="lst-time-sep">, </span><span class="lst-time-h">' + esc(hora) + "</span>" : "") +
        "</span>";
      if (nota) h += '<span class="lst-time-note">' + esc(nota) + "</span>";
      return '<span class="lst-timecell">' + h + "</span>";
    }

    function cellHtml(col, r, idx, personSubCol) {
      if (col.t === "action") {
        var actionHref = r[col.href || "accion_href"] || "";
        var actionLabel = r[col.key] || "";
        if (!actionHref || !actionLabel) return "";
        return '<a class="lst-action" href="' + esc(actionHref) + '">' +
          esc(actionLabel) + "</a>";
      }
      if (col.t === "estado") {
        var p = r[col.key] || {};
        if (p.dia) {
          var diaTxt = (Dates && Dates.dia(p.dia)) || "";
          if (!diaTxt) {
            diaTxt = String(p.v || "");
            var ci = diaTxt.lastIndexOf(", ");
            if (ci > 0 && p.hora) diaTxt = diaTxt.slice(0, ci);
            // El servidor escribe «Mañana, sábado 11»: la pastilla usa la palabra sola.
            var manana = T("date.tomorrow", null, "Mañana");
            if (diaTxt.indexOf(manana + ", ") === 0) diaTxt = manana;
          }
          var bucket = ORDEN.bucket(diasHasta(p.dia));
          var sp = diaTxt.indexOf(" ");
          return estadoPill(bucket, sp > 0 ? diaTxt.slice(0, sp) : diaTxt,
            sp > 0 ? diaTxt.slice(sp + 1) : "", p.hora || "", "");
        }
        var nota0 = p.nota || "";
        if (p.open) return estadoPill("walkin",
          T("listing.walkin.open_now", null, "Atiende ahora"),
          p.hasta ? T("listing.walkin.until", { hora: p.hasta }, "hasta las " + p.hasta) : "",
          "", nota0);
        if (p.abre) return estadoPill("walkin",
          p.abre.dow, p.abre.fecha || "", p.abre.hora, nota0);
        return '<span class="lst-when lst-when--' + kindCls(p.kind) + '">' + esc(p.v || "") + "</span>";
      }
      if (col.t === "proximo") {
        var p = r[col.key] || {};
        // slot_href (si la fila lo trae) lleva ESE turno preseleccionado al
        // wizard (?slot=…): clic en la hora = hora elegida, sin re-mostrar la
        // grilla (responsable del producto, 2026-07-14). El href pelado queda para el nombre/fila.
        var href = r.slot_href || r[col.href || "href"] || "";
        var infoHref = r[col.more || "info_href"] || "";
        // Servicios de acceso directo (telemedicina, zoonosis, donación):
        // ocupan la misma columna de acción que un turno, con la promesa de
        // valor como etiqueta y una disponibilidad breve al lado.
        if (p.action && href) {
          return '<span class="lst-timecell">' +
            '<a class="lst-time lst-time--ya" href="' + esc(href) + '">' +
            esc(p.action) + '</a>' +
            (p.nota ? '<span class="lst-time-note">' + esc(p.nota) + '</span>' : '') +
            '</span>';
        }
        // Turno con fecha real → la pastilla ES el botón, en formato amigable
        // ("Hoy, 14:40" / "Martes 14 de julio, 15:00") color-codeada por
        // cercanía calibrada a hospital público (responsable del producto, 2026-07-11): ≤7 días
        // verde, 8-30 ámbar, >30 gris (ver ORDEN.bucket). + link "más horarios".
        if (p.dia) {
          var diaTxt = (Dates && Dates.dia(p.dia)) || "";
          if (!diaTxt) {
            diaTxt = String(p.v || "");
            var ci = diaTxt.lastIndexOf(", ");
            if (ci > 0 && p.hora) diaTxt = diaTxt.slice(0, ci);
            // El servidor escribe «Mañana, sábado 11»: la pastilla usa la palabra sola.
            var manana = T("date.tomorrow", null, "Mañana");
            if (diaTxt.indexOf(manana + ", ") === 0) diaTxt = manana;
          }
          var bucket = ORDEN.bucket(diasHasta(p.dia));
          var sp = diaTxt.indexOf(" ");
          var dow = sp > 0 ? diaTxt.slice(0, sp) : diaTxt;
          var fecha = sp > 0 ? diaTxt.slice(sp + 1) : "";
          if (r.orden && href) {
            return '<span class="lst-timecell">' +
              orderPill(bucket, href, (Dates && Dates.corta && Dates.corta(p.dia, p.hora || "")) || diaTxt) +
              moreLink(infoHref, href, bucket) + "</span>";
          }
          return '<span class="lst-timecell">' +
            timePill(bucket, dow, fecha, p.hora || "", href, fechaCorta(p.dia, p.hora || "")) +
            moreLink(infoHref, href, bucket) + "</span>";
        }
        // Walk-in con horario del cartel → texto verde plano SIN pastilla,
        // "Atiende ahora hasta las X" (abierto) / "Abre..." (futuro) + sufijo
        // muted fijo "por orden de llegada" (responsable del producto, 2026-07-12: la info de modo
        // que vivía en la columna Turno ahora vive acá).
        if (p.open) {
          var t1 = T("listing.walkin.open_now", null, "Atiende ahora") +
            (p.hasta ? " " + T("listing.walkin.until", { hora: p.hasta }, "hasta las " + p.hasta) : "");
          return openText(t1, T("listing.walkin.order_note", null, "sin turno, por llegada"));
        }
        if (p.abre) {
          var t2 = p.abre.dow + (p.abre.fecha ? " " + p.abre.fecha : "") + (p.abre.hora ? ", " + p.abre.hora : "");
          return openText(t2, T("listing.walkin.order_note", null, "sin turno, por llegada"));
        }
        // Sin fecha ni horario parseable pero RESERVABLE → botón en TINTA
        // "Sacar turno" con la aclaración al lado como texto plano ("La fecha
        // la confirma el centro") — mockup el responsable del producto 2026-07-14: el botón dice la
        // acción, la nota es información y no viste chrome de botón.
        // Sin "más horarios": no hay horarios que mostrar.
        // the server's next step rides on `proximo.remedio`, beside `habilita`
        if (p.kind === "solicitud" && p.habilita) {
          return habilitaCell(p.remedio ? Object.assign({}, p.habilita, { remedio: p.remedio }) : p.habilita, r);
        }
        if (p.kind === "solicitud" && href) {
          if (r.orden) {
            return '<span class="lst-timecell">' + orderPill("pedir", href, "") +
              '<span class="lst-time-note">'
              + esc(T("listing.date_confirmed", null, "La fecha la confirma el centro"))
              + "</span></span>";
          }
          return '<span class="lst-timecell">' +
            '<a class="lst-time lst-time--pedir" href="' + esc(href) + '">'
            + esc(T("listing.book", null, "Sacar turno")) + "</a>" +
            '<span class="lst-time-note">'
            + esc(T("listing.date_confirmed", null, "La fecha la confirma el centro"))
            + "</span></span>";
        }
        // Walk-in genérico sin horario → texto de siempre.
        return '<span class="lst-when lst-when--' + kindCls(p.kind) + '">' + esc(p.v || "") + "</span>";
      }
      if (col.t === "link") {
        var href = r[col.href] || "";
        // Jerarquía (responsable del producto, 2026-07-11): la ENTIDAD (servicio/médico/estudio) es
        // el link prominente en accent; el contexto secundario (especialidad,
        // centro) se atenúa con `sub` — link gris con subrayado punteado, menos
        // visible que la entidad y la acción "Sacar turno", sin dejar de leerse
        // como link (accent-is-link: subrayado = afordancia de link a reposo).
        var nameCls = "lst-name" + (col.sub ? " lst-name--sub" : "");
        // médico/agenda demo (responsable del producto, 2026-07-10): tag chico junto al NOMBRE (la
        // celda con avatar), dentro del mismo link — no es un link aparte.
        var demoTag = r.demo && col.avatar ? '<span class="lst-tag lst-tag--demo">demo</span>' : "";
        if (col.avatar) {
          // avatar + nombre = UN solo link (accent-is-link: un link por celda, sin
          // anchors anidados); el avatar clickeable es parte del mismo <a>.
          // En filas de profesionales, la especialidad forma una sola identidad
          // visual con el nombre. Se mantiene dentro de esta celda para que el
          // retrato centre contra el bloque completo, no contra una línea suelta.
          var av = "";
          if (r.avatar) av = '<span class="lst-av lst-av--ph" style="' + esc(r.avatar) + '"></span>';
          // no photo: the SAME neutral initials for every doctor (review P37),
          // derived from the name when a payload did not send them
          else {
            var ini = r.avatar_ini != null && r.avatar_ini !== "" ? r.avatar_ini : iniciales(r[col.key]);
            if (ini) av = '<span class="lst-av lst-av--' + (r.avatar_c || 0) + '">' + esc(ini) + "</span>";
          }
          var personSub = personSubCol && r[personSubCol.key]
            ? '<span class="lst-person-specialty">' + esc(r[personSubCol.key]) + "</span>"
            : "";
          return '<a class="lst-linkcell lst-linkcell--av" href="' + esc(href || "#") + '">' + av +
            '<span class="lst-person-copy"><span class="' + nameCls + '">' + esc(r[col.key] || "") +
            "</span>" + personSub + "</span>" + demoTag + "</a>";
        }
        if (!href) return '<span class="lst-txt' + (col.sub ? " lst-txt--sub" : "") + '">' + esc(r[col.key] || "") + "</span>";
        // `<href>_ext`: a link out of the site (the address → Google Maps), new tab.
        var ext = r[col.href + "_ext"] ? ' target="_blank" rel="noopener noreferrer"' : "";
        return '<span class="lst-linkcell"><a class="' + nameCls + '" href="' + esc(href) + '"' + ext + ">" + esc(r[col.key] || "") + "</a></span>";
      }
      if (col.t === "orden") {
        // "Con orden" ya no es pill (ui-rules 2026-07-14: pill sólo si DEBE ser
        // pill): texto en tinta + icono ⓘ, y el conjunto sigue siendo el botón
        // que abre el popover que explica la orden (mismo data-orden). "Sin
        // orden" es sólo información → texto plano atenuado.
        var ov = r[col.key];
        if (ov == null) return "";
        return ov
          ? '<button type="button" class="lst-orden" data-orden="' + idx + '">'
            + esc(T("listing.order.with", null, "Con orden")) + icon("info", "lst-orden-i") + "</button>"
          : '<span class="lst-orden-sin">'
            + esc(T("listing.order.without", null, "Sin orden")) + "</span>";
      }
      if (col.t === "lugar") {
        // Estudios (responsable del producto, 2026-07-18): el centro con más horas, linkeado igual
        // que Especialidad/Centro (dashed-underline .lst-name--sub) — ya no
        // "3 centros" en texto plano. Cuando hay más de un centro, un botón de
        // texto "+N" al lado abre el popover "También se hace en" con el
        // resto (también linkeados). El "+N" es <button>, así que el click
        // nunca dispara el link de la fila (tr[data-href] ya ignora a/button).
        var lhref = r[col.href] || "";
        var lnombre = r[col.key] || "";
        var extra = r[col.extra] || [];
        var lhtml = lhref
          ? '<a class="lst-name lst-name--sub" href="' + esc(lhref) + '">' + esc(lnombre) + "</a>"
          : '<span class="lst-txt lst-txt--sub">' + esc(lnombre) + "</span>";
        if (extra.length) {
          lhtml += ' <button type="button" class="lst-masbtn" data-lugarmas="' + idx + '">+' + extra.length + "</button>";
        }
        return '<span class="lst-linkcell">' + lhtml + "</span>";
      }
      if (col.t === "dot") {
        // valor booleano genérico + punto de estado: r[col.key] = {ok, label}
        // (label ya viene traducido por el caller — el motor no conoce
        // vocabulario de ninguna pantalla puntual). Usado hoy por
        // account.mine.lab (En rango/Fuera de rango); cualquier otra tabla
        // que necesite "punto + texto de estado" reusa el mismo tipo.
        var dv = r[col.key] || {};
        return '<span class="lst-dot lst-dot--' + (dv.ok ? "ok" : "out") + '">' +
          '<span class="lst-dot-i"></span>' + esc(dv.label || "") + "</span>";
      }
      // outKey (genérico, responsable del producto 2026-08-05): un valor de texto que se pinta en
      // el color de alerta cuando el dato que acompaña está fuera de lo
      // esperado (r[col.outKey] truthy) — sin acoplar el motor a NINGÚN
      // vocabulario de pantalla puntual (hoy: el resultado de un análisis
      // fuera de rango). Ninguna columna existente pasa outKey, así que esto
      // no mueve un solo píxel de una tabla que no lo pida.
      var outCls = (col.outKey && r[col.outKey]) ? " lst-txt--out" : "";
      return '<span class="lst-txt' + (col.sub ? " lst-txt--sub" : "") + outCls + '">' + esc(r[col.key] || "") + "</span>";
    }

    function tableHtml(rows) {
      if (!rows.length)
        return '<div class="lst-body"><p class="lst-empty">' +
          esc(opts.emptyFilter || T("listing.empty_filtered", null,
            "No hay resultados. Probá quitar un filtro.")) + "</p>" +
          searchAllHtml(state.q, opts.searchAllHref) + "</div>";
      // columnas uniformes (todas las filas dicen lo mismo) colapsan a una
      // nota única al pie — ver colapsoUniforme arriba.
      var u = colapsoUniforme(COLS, rows, ROWS);
      var RCOLS = COLS.filter(function (c, i) { return !u.skip[i]; });
      // lst-row-av: la fila tiene columna con avatar (médico) → en mobile el botón
      // y el centro se alinean DEBAJO del nombre, no del avatar (responsable del producto, 2026-07-15).
      var hasAv = RCOLS.some(function (c) { return c.avatar; });
      // En pantallas angostas la especialidad forma parte de la identidad visual
      // del profesional: nombre y especialidad comparten celda y el avatar se
      // centra contra ambas líneas. En escritorio ancho, la misma especialidad
      // vuelve a su columna canónica para que la tabla sea más fácil de escanear.
      // CSS muestra una sola de las dos representaciones en cada breakpoint.
      var personSubCol = hasAv
        ? RCOLS.find(function (c) { return c.key === "especialidad"; })
        : null;
      var TCOLS = RCOLS;
      // Etiqueta de campo en mobile (data-l -> content:attr(data-l), sólo
      // consumida por el CSS de :has(tr.lst-grp)): la PRIMERA columna es la
      // identidad de la fila (el mockup la agranda y no la etiqueta, igual
      // que .lst-name en toda otra tabla del sitio) — las demás sí llevan
      // su etiqueta, porque en una tabla agrupada no hay encabezado visible
      // que las distinga (mobile esconde el <thead> entero).
      var hasGrupos = rows.some(function (r) { return r.grupo; });
      var lastGrupoId;
      var body = rows.map(function (r, i) {
        var grpHtml = "";
        if (r.grupo && r.grupo.id !== lastGrupoId) {
          lastGrupoId = r.grupo.id;
          grpHtml = grupoRowHtml(r.grupo, TCOLS.length);
        }
        // fila-link (opts.rowHref): toda la línea lleva al destino de la fila
        // (sacar turno); los links internos (especialidad) siguen siendo suyos.
        var rh = opts.rowHref && r[opts.rowHref];
        var trc = ((rh ? "lst-rowlink " : "") +
          (hasAv ? "lst-row-av" : "")).trim();
        return grpHtml + "<tr" + (trc ? ' class="' + trc + '"' : "") + (rh ? ' data-href="' + esc(rh) + '"' : "") + ">" +
          TCOLS.map(function (c, ci) {
            var label = (hasGrupos && ci > 0) ? ' data-l="' + esc(c.label || "") + '"' : "";
            return "<td" + colCls(c) + label + ">" + cellHtml(c, r, i, personSubCol) + "</td>";
          }).join("") + "</tr>";
      }).join("");
      var notas = u.tipos.map(function (t) {
        return '<p class="lst-note">' + esc(notaColapso(t, rows.length, ELEGIR)) + "</p>";
      }).join("");
      // 901–1120 a doctors' table is one grid per row (CSS). Its tracks are
      // the columns this table actually shows — the specialty sits inside the
      // name cell there — so a column the page dropped (no Centro on a
      // center's page) or a uniform one collapsed into a note leaves no empty
      // track. Both CSS copies (here and chat-widget.mjs) read --lst-av-grid.
      var avGrid = hasAv ? TCOLS.filter(function (c) { return sid(c) !== "especialidad"; })
        .map(function (c) { return AV_TRACK[sid(c)] || AV_TRACK._; }).join(" ") : "";
      return '<div class="lst-body"><div class="lst-scroll"><table class="lst-table"' +
        (avGrid ? ' style="--lst-av-grid:' + avGrid + '"' : "") + "><thead><tr>" +
        TCOLS.map(th).join("") + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
        notas + "</div>";
    }

    // ---- interacción -----------------------------------------------------
    function wire(rows) {
      var s = slot.querySelector(".lst-search");
      if (s) {
        s.addEventListener("input", function () {
          state.q = s.value;
          writeUrl();
          var rows2 = visible();
          var wrap = slot.querySelector(".lst-body");
          // Driver de grupo sin match propio: cuerpo vacío (el resultado puede
          // estar en la tabla hermana) — no el cartel "no hay resultados".
          var driverVacio = IS_DRIVER && state.q && rows2.length === 0;
          var tmp = document.createElement("div");
          tmp.innerHTML = driverVacio ? '<div class="lst-body"></div>' : tableHtml(rows2);
          if (wrap) wrap.replaceWith(tmp.firstChild);
          bindTable(rows2);
          // Difundir a las tablas hermanas del grupo (filtran y se ocultan si
          // quedan vacías) — la caja de ESTE listado conserva foco y cursor.
          if (IS_DRIVER) (GROUPS[GROUP] || []).forEach(function (g) { if (g.mount !== mount) g.apply(s.value); });
          // Con las hermanas ya filtradas: si nadie del grupo tiene filas, el link.
          if (driverVacio) {
            var vacio = slot.querySelector(".lst-body");
            if (vacio) vacio.innerHTML = driverEmptyInner();
          }
          logSearch(s.value, rows2.length);
        });
        s.addEventListener("blur", function () { flushSearchLog(s.value, visible().length); });
        s.addEventListener("keydown", function (e) {
          if (e.key === "Enter") flushSearchLog(s.value, visible().length);
        });
        slot.querySelectorAll("[data-search-example]").forEach(function (a) {
          a.addEventListener("click", function (e) {
            e.preventDefault();
            s.value = a.getAttribute("data-search-example") || "";
            s.dispatchEvent(new Event("input", { bubbles: true }));
            s.focus();
          });
        });
      }
      var pubSel = slot.querySelector("[data-pubk]");
      if (pubSel) pubSel.addEventListener("change", function () { state.publico = pubSel.value; writeUrl(); render(); });
      var sexSel = slot.querySelector("[data-sexok]");
      if (sexSel) sexSel.addEventListener("change", function () { state.sexo = sexSel.value || null; writeUrl(); render(); });
      FACETS.forEach(function (f) {
        slot.querySelectorAll("[data-facet-" + f.key + "]").forEach(function (b) {
          b.addEventListener("click", function () { state.facet[f.key] = b.getAttribute("data-facet-" + f.key); writeUrl(); render(); });
        });
      });
      slot.querySelectorAll("[data-facetk]").forEach(function (sel) {
        sel.addEventListener("change", function () { state.facet[sel.getAttribute("data-facetk")] = sel.value; writeUrl(); render(); });
      });
      var filtrosBtn = slot.querySelector("[data-filtros]");
      if (filtrosBtn) filtrosBtn.addEventListener("click", function () {
        var tools = slot.querySelector(".lst-tools");
        var abrir = !tools.classList.contains("lst-tools--open");
        state.filtrosAbiertos = abrir;
        tools.classList.toggle("lst-tools--open", abrir);
        filtrosBtn.setAttribute("aria-expanded", abrir ? "true" : "false");
        if (abrir) fitSelects();
      });
      var sortSel = slot.querySelector("[data-sortsel]");
      if (sortSel) sortSel.addEventListener("change", function () {
        state.sortKey = sortSel.value || defKey;
        state.sortDir = 1;
        render();
      });
      bindTable(rows);
    }
    function bindTable(rows) {
      slot.querySelectorAll("[data-sort]").forEach(function (b) {
        b.addEventListener("click", function () {
          var k = b.getAttribute("data-sort");
          if (state.sortKey === k) state.sortDir *= -1; else { state.sortKey = k; state.sortDir = 1; }
          render();
        });
      });
      slot.querySelectorAll("[data-orden]").forEach(function (b) {
        b.addEventListener("click", function (e) { e.preventDefault(); openOrden(b); });
      });
      slot.querySelectorAll("[data-lugarmas]").forEach(function (b) {
        b.addEventListener("click", function (e) {
          e.preventDefault();
          var i = parseInt(b.getAttribute("data-lugarmas"), 10);
          openLugarMas(b, rows[i]);
        });
      });
      slot.querySelectorAll("tr[data-href]").forEach(function (tr) {
        tr.addEventListener("click", function (e) {
          if (e.target.closest("a,button")) return;
          location.href = tr.getAttribute("data-href");
        });
      });
    }

    // popover de la orden médica
    var openPop = null;
    function openOrden(anchor) {
      closePop();
      var link = opts.orden_link || "/d/clinica";
      var p = document.createElement("div");
      p.className = "lst-pop";
      p.innerHTML =
        "<p>" + T("listing.order.what", null,
          "Necesitás una <b>orden médica de derivación</b> (con PAMI, la OME — Orden Médica Electrónica).")
        + "</p><p>" + T("listing.order.how", null,
          "La hace tu médico de cabecera o un clínico. Subís la foto de la orden cuando pedís el turno.")
        + "</p>" +
        '<a class="lst-poplink" href="' + esc(link) + '">'
        + esc(T("listing.order.get", null, "Conseguir la orden en Medicina General")) + "</a>";
      document.body.appendChild(p);
      var r = anchor.getBoundingClientRect();
      p.style.top = (window.scrollY + r.bottom + 6) + "px";
      p.style.left = Math.max(10, window.scrollX + r.left) + "px";
      openPop = p;
      setTimeout(function () { document.addEventListener("click", onDoc, true); }, 0);
      function onDoc(e) { if (!p.contains(e.target) && e.target !== anchor) closePop(); }
      p._onDoc = onDoc;
    }
    function closePop() {
      if (openPop) { document.removeEventListener("click", openPop._onDoc, true); openPop.remove(); openPop = null; }
    }

    // popover "También se hace en" (columna lugar, estudios en más de un
    // centro) — mismo mecanismo que openOrden (un solo popover a la vez,
    // cierra con click afuera). 2+ centros = lista real, un link por línea
    // (ui-rules: nunca una sola línea unida por ' · ').
    function openLugarMas(anchor, r) {
      closePop();
      var extra = (r && r.lugar_mas) || [];
      if (!extra.length) return;
      var p = document.createElement("div");
      p.className = "lst-pop";
      p.innerHTML = '<p class="lst-pop-title">'
        + esc(T("listing.also_at", null, "También se hace en")) + "</p>" +
        '<ul class="lst-pop-list">' + extra.map(function (c) {
          return '<li><a class="lst-poplink" href="' + esc(c.href) + '">' + esc(c.nombre) + "</a></li>";
        }).join("") + "</ul>";
      document.body.appendChild(p);
      var rr = anchor.getBoundingClientRect();
      p.style.top = (window.scrollY + rr.bottom + 6) + "px";
      p.style.left = Math.max(10, window.scrollX + rr.left) + "px";
      openPop = p;
      setTimeout(function () { document.addEventListener("click", onDoc, true); }, 0);
      function onDoc(e) { if (!p.contains(e.target) && e.target !== anchor) closePop(); }
      p._onDoc = onDoc;
    }
  }

  // ---- estilos (una vez) ----------------------------------------------------
  function injectStyle() {
    if (document.getElementById("lst-style")) return;
    var css =
      ".lst-h{font-size:16px;font-weight:600;color:var(--text);margin:26px 0 12px;}" +
      ".lst-tools{display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center;margin:0 0 14px;}" +
      ".lst-searchgroup{flex:1 1 100%;min-width:0;}" +
      /* En tablet y escritorio, búsqueda + filtros forman una sola barra
         compacta. Los ejemplos quedan para mobile, donde ayudan sin robarle
         ancho a los controles principales. */
      ".lst-tools--filters{flex-wrap:nowrap;gap:8px;align-items:flex-start;}" +
      ".lst-tools--filters .lst-searchgroup{flex:1 1 300px;}" +
      ".lst-tools--filters .lst-examples{display:none;}" +
      /* Los filtros son UN grupo, no tres controles sueltos: viven en su propia
         fila y no se rompen (hook de controles relacionados). El ancho de cada
         select lo fija fitSelects() midiendo la opción elegida. */
      ".lst-selects{display:flex;flex-wrap:nowrap;gap:8px;flex-shrink:0;}" +
      ".lst-selects .lst-select{flex-shrink:0;}" +
      ".lst-searchbox{display:flex;align-items:center;gap:13px;background:var(--panel);border:1px solid var(--border);" +
        "border-radius:14px;padding:15px 20px;box-shadow:var(--shadow-card);transition:border-color .12s,box-shadow .12s;}" +
      ".lst-ico{display:inline-block;flex-shrink:0;width:22px;height:22px;background:currentColor;" +
        "-webkit-mask:var(--ico) center/contain no-repeat;mask:var(--ico) center/contain no-repeat;}" +
      ".lst-ico--search{--ico:url(/static/icons/search-sf-regular.svg);}" +
      ".lst-ico--info{--ico:url(/static/icons/info-sf-regular.svg);}" +
      ".lst-ico--filter{--ico:url(/static/icons/filter-sf-regular.svg);}" +
      ".lst-searchbox .lst-ico{color:var(--text-2);}" +
      ".lst-searchbox:focus-within{border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring),var(--shadow-card);}" +
      /* Piso de 44px para el input suelto (F04, QA 2026-09-10): en la
         variante sin filtros el campo medía 27px de alto — el área tocable
         real era una línea de texto. La variante con filtros ya usa 44px
         (regla de abajo); el input grande llega al mismo piso. */
      ".lst-search{flex:1;min-width:0;min-height:44px;padding:0;font:inherit;font-size:18px;color:var(--text);background:none;border:0;outline:none;}" +
      ".lst-search::placeholder{color:var(--text-3);}" +
      // En una barra con filtros, búsqueda y selects son controles pares:
      // misma altura, tipografía, radio y borde. La búsqueda grande conserva
      // su tratamiento de portada cuando aparece sola.
      ".lst-tools--filters .lst-searchbox,.lst-tools--filters .lst-select{height:44px;box-sizing:border-box;}" +
      ".lst-tools--filters .lst-searchbox{gap:10px;padding:0 12px;border-color:var(--border-strong);border-radius:10px;box-shadow:none;}" +
      ".lst-tools--filters .lst-searchbox .lst-ico{width:20px;height:20px;}" +
      // Phone-only bar (≤900px): the «Filtros» control and the sort select.
      ".lst-phonebar{display:none;}" +
      ".lst-filtros{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 14px;box-sizing:border-box;" +
        "font:inherit;font-size:16px;color:var(--text);background:var(--panel);border:1px solid var(--border-strong);" +
        "border-radius:10px;cursor:pointer;white-space:nowrap;}" +
      ".lst-filtros .lst-ico{width:20px;height:20px;}" +
      ".lst-filtros.on{border-color:var(--accent);}" +
      // Piso de 16px para todo control enfocable: con menos, iOS Safari hace
      // zoom automático al enfocar y la página queda recortada a los costados.
      // min-height:0 — acá el piso lo pone la caja de 44px de arriba, y el
      // input no tiene que forzar su propia altura dentro de ella.
      ".lst-tools--filters .lst-search{font-size:16px;min-height:0;}" +
      /* Los ejemplos WRAPean, nunca se recortan (F07/F09, QA 2026-09-10):
         nowrap+overflow:hidden escondía el último chip más allá del viewport
         en teléfonos (ES360 +66px). Cada link es su propia línea potencial;
         el piso de 24px (WCAG 2.5.8) va en el mismo slice que el wrap para
         que la fila envuelta siga siendo un target tocable. */
      ".lst-examples{display:flex;flex-wrap:wrap;align-items:center;gap:2px 12px;margin:10px 2px 0;" +
        "font-size:16px;color:var(--text);}" +
      // Link de texto: color y subrayado los pone el tema (a{} en
      // human-rounds.css) — acá no se apaga nada (responsable del producto, 2026-09-15: "do
      // transparent underlines for all the links").
      ".lst-examples a{color:var(--link,var(--accent));display:inline-flex;align-items:center;min-height:44px;}" +
      ".lst-examples a:hover{color:var(--link-hover,var(--accent-hover));}" +
      // Un solo sistema de <select> para todo el sitio (responsable del producto, 2026-07-18):
      // caret propio (SVG inline, nunca el nativo del navegador), foco en
      // accent, y — la señal importante — cuando el valor YA NO es el default
      // el borde pasa a accent + el texto a semibold, así un filtro activo se
      // ve de un vistazo sin tener que leer el texto.
      ".lst-select{appearance:none;-webkit-appearance:none;-moz-appearance:none;" +
        "padding:10px 34px 10px 12px;font:inherit;font-size:16px;color:var(--text);background-color:var(--panel);" +
        "border:1px solid var(--border-strong);border-radius:10px;cursor:pointer;transition:border-color .12s;" +
        // the chevron is the Icons8 file, never a hand-drawn data-URI svg
        "background-image:url(/static/icons/expand-arrow-sf-regular.svg);background-size:14px 14px;" +
        "background-repeat:no-repeat;background-position:right 12px center;}" +
      ".lst-select:focus{outline:none;border-color:var(--accent);}" +
      ".lst-select.on{border-color:var(--accent);font-weight:600;}" +
      ".lst-scroll{overflow-x:auto;}" +
      ".lst-table{width:100%;border-collapse:collapse;font-size:16px;}" +
      // Encabezados y orden son controles, no letra chica descriptiva
      // (2026-11-08): sin elegir van en --hint-ink, gris oscuro opaco en los
      // dos temas; la escala translúcida --text-2..4 queda para el texto.
      // Tamaño de lectura y sin mayúsculas (2026-11-15, revisión pública:
      // 13,5px en versalitas no se leía): 15px, 16px en escritorio.
      ".lst-table th{text-align:left;padding:9px 12px;border-bottom:1px solid var(--border-strong);white-space:nowrap;" +
        "font-size:15px;font-weight:600;color:var(--hint-ink,var(--text-3));}" +
      ".lst-table th.lst-r{text-align:right;}" +
      ".lst-sort{background:none;border:0;padding:0;font:inherit;font-size:15px;font-weight:600;color:var(--hint-ink,var(--text-3));" +
        "cursor:pointer;}" +
      ".lst-sort:hover{color:var(--hover-ink);}" +
      ".lst-sort.on{color:var(--accent);}" +
      // vertical-align:middle (responsable del producto, 2026-07-14): todos los elementos de la
      // fila — avatar, nombre, tag, pill, botón — comparten el eje horizontal.
      ".lst-table td{padding:13px 12px;border-bottom:1px solid var(--border-soft);vertical-align:middle;font-size:16px;white-space:nowrap;}" +
      ".lst-table td.lst-r{text-align:right;}" +
      // Fila de grupo (responsable del producto, 2026-08-05): un <th colspan> intercalado en el
      // <tbody>, no un encabezado más — texto normal (nunca mayúsculas, a
      // diferencia del thead) con la fecha en tinta fuerte y el laboratorio
      // atenuado al lado. Sólo existe cuando alguna fila trae `grupo`.
      ".lst-grp th{padding:24px 12px 9px;border-bottom:1px solid var(--border-soft);" +
        "font-size:16px;font-weight:500;color:var(--text);text-transform:none;letter-spacing:0;white-space:normal;}" +
      ".lst-grp:first-child th{padding-top:0;}" +
      ".lst-grp-d{font-weight:800;color:var(--text);}" +
      ".lst-grp-l{color:var(--text-2);}" +
      // Punto + texto de estado (t:"dot") — salvia "ok"/bronce "out", los
      // mismos tokens que ya colorean turnos/pastillas en el resto del sitio.
      ".lst-dot{display:inline-flex;align-items:center;gap:8px;}" +
      ".lst-dot-i{width:9px;height:9px;border-radius:50%;flex-shrink:0;}" +
      ".lst-dot--ok{color:var(--text-2);}.lst-dot--ok .lst-dot-i{background:var(--done);}" +
      ".lst-dot--out{color:var(--warn);font-weight:700;}.lst-dot--out .lst-dot-i{background:var(--warn);}" +
      // Valor fuera de lo esperado (outKey) — mismo tratamiento que el punto
      // "out" de arriba, para que el número y su estado se lean del mismo
      // color. Selector COMPUESTO (.lst-txt.lst-txt--out, no sólo
      // .lst-txt--out): el span siempre lleva las dos clases juntas y la
      // base ".lst-txt{color:var(--text-2)}" vive más abajo en esta misma
      // hoja — con la misma especificidad (0,1,0) la última declarada gana,
      // así que un selector de una sola clase perdía contra la base pese a
      // estar "encima" en el CSS (bug real, encontrado en el pull de
      // «Resultados de laboratorio», 2026-08-05: el valor fuera de rango no
      // se pintaba). El compuesto (0,2,0) gana siempre, sin depender del orden.
      ".lst-txt.lst-txt--out{color:var(--warn);font-weight:700;}" +
      /* Con columna de lugar/sede (centro con sedes: 5 columnas) la tabla no
         entra en el shell de 1120 y el scroll interno escondía justo la
         columna del turno (la regla 2026-07-15: TODO tiene que entrar). Sólo
         en esas tablas los textos de contexto pueden envolver a 2 líneas, y
         la fila se alinea arriba (hook de alineación: fila que puede
         envolver → top). */
      "@media(min-width:1041px){" +
        ".lst-table:has(td.lst-td-lugar) td.lst-td-text,.lst-table:has(td.lst-td-lugar) td.lst-td-lugar," +
        ".lst-table:has(td.lst-td-lugar) td.lst-td-link{white-space:normal;}" +
        ".lst-table:has(td.lst-td-lugar) td{vertical-align:middle;}" +
      "}" +
      // Names and centers are data, not fixed-width controls.  Let their
      // cells wrap at every non-phone width so an unusually long value cannot
      // consume the appointment column inside the table's own scroll box.
      "@media(min-width:901px){" +
        // TODAS las filas miden lo mismo, con botón o sin él (responsable del producto, 2026-09-15:
        // "the table should have the same row height, no matter if it has a
        // button or not"): la celda fija el alto de la fila al de la fila más
        // alta posible (botón de turno: 33px + 2×14 de padding en desktop) y
        // todo se centra en vertical — una fila de texto ya no queda más baja
        // que la de al lado. Una celda que envuelve a dos líneas sigue
        // creciendo (height es piso, no techo).
        ".lst-table tbody td{height:56px;}" +
        // balance: a wrapped name splits into two even lines («Medicina
        // general - / Salud de la mujer»), never one word left alone below.
        ".lst-table td.lst-td-text,.lst-table td.lst-td-link,.lst-table td.lst-td-lugar{white-space:normal;vertical-align:middle;text-wrap:balance;}" +
        ".lst-table td.lst-td-link .lst-linkcell{display:block;max-width:100%;}" +
        // The avatar identity is a media object, not a text-only link.  Keep
        // its circle beside the wrapping name rather than letting the generic
        // long-name rule turn it into a block above the name.
        ".lst-table td.lst-td-link .lst-linkcell--av{display:inline-flex;}" +
        ".lst-table td.lst-td-link .lst-name,.lst-table td.lst-td-link .lst-txt{white-space:normal;overflow-wrap:anywhere;}" +
      "}" +
      ".lst-table tr:last-child td{border-bottom:0;}" +
      // No tabular digits anywhere in a row (review D26): Manrope's tabular
      // «1» is as wide as a «0», so «12:30» read «1 2:30».
      ".lst-when{white-space:nowrap;}" +
      ".lst-when--strong{color:var(--text);font-weight:600;}" +
      ".lst-when--ok{color:var(--done);font-weight:500;}" +
      ".lst-when--warn{color:var(--warn,#8a5b12);font-weight:500;}" +
      ".lst-when--accent{color:var(--accent);font-weight:500;}" +
      ".lst-when--muted{color:var(--text);}" +
      // pastilla del próximo turno / apertura en formato amigable. Turnos
      // reservables: un verde que vira al azul con la lejanía (ver ORDEN.bucket).
      // Walk-in: variante única atenuada (--walkin) — menos visible que
      // cualquier turno reservable, sin dejar de leerse (contraste AA).
      // Una sola línea por celda (responsable del producto, 2026-07-12): el turno y su "más horarios"
      // van uno al lado del otro, no apilados — apilarlos hacía filas de dos
      // alturas distintas y la tabla dejaba de escanearse de un vistazo.
      ".lst-timecell{display:inline-flex;align-items:center;gap:10px;white-space:nowrap;}" +
      ".lst-time{display:inline-flex;align-items:center;box-sizing:border-box;padding:6px 11px;border:1px solid;border-radius:9px;text-decoration:none;" +
        "font-size:16px;font-weight:400;line-height:1.3;white-space:nowrap;}" +
      // Reservable (<a>) = BOTÓN de verdad (the product owner, 2026-07-14): fondo lleno,
      // texto blanco de UN solo color — nada de mezclar tintas dentro de la
      // pastilla. La cercanía sigue codificada, ahora en el fondo (blanco
      // sobre estos tres da contraste AA). El span de estado (my-requests,
      // no clickeable) conserva el look tenue de pastilla.
      // El verde de --done en pinamar viene del design system (handoff
      // Claude Design: --ok #6f9959 original), oscurecido un paso para AA con
      // texto blanco. El tema sana usa su propia paleta bronce/terracota, sin
      // verde (ver human-rounds.css) — --done se lee siempre desde el token, sin
      // fallback literal verde.
      // ONE primary colour for every booking button (review 2026-11-15): the
      // wait no longer tints it — the date on it says how far it is.
      "a.lst-time{min-height:44px;border:none;padding:0 16px;color:var(--btn-fg,#fff);background:var(--btn-bg,var(--accent,#0d7d48));" +
        "transition:filter .12s;cursor:pointer;}" +
      "a.lst-time:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}" +
      // El tier gris usa el taupe CÁLIDO del sitio (--text-2), no un gris
      // azulado — el frío desentona con la paleta crema y lee "deshabilitado"
      // (the product owner, 2026-07-14).
      "a.lst-time--ya{background:var(--btn-bg,var(--accent,#0d7d48));}" +
      waitCss() +
      // "Sacar turno" sin fecha conocida: botón en tinta (mockup 2026-07-14),
      // la nota "La fecha la confirma el centro" va al lado como texto plano.
      "a.lst-time--pedir{background:var(--btn-bg,var(--accent,#0d7d48));}" +
      "a.lst-time--pedir:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}" +
      "span.lst-time--ya{color:var(--accent-hover,#0b6e33);border-color:var(--accent-bd,#cde7d5);background:var(--accent-soft,#eaf4ec);}" +
      ".lst-time-h{font-weight:400;}" +
      ".lst-time-note{font-size:16px;color:var(--text);white-space:nowrap;}" +
      // Two facts, two lines: «No hay turnos disponibles» over «Se habilitan
      // a partir de: …». Held on one line (the td's nowrap) the sentence's
      // min-content equalled its whole length, so auto layout handed this
      // column all the spare width while Servicio/Atención broke into three
      // lines (product owner, 2026-11-13). Stacked, the cell is no wider than a
      // booking button and the date line still wraps if a column is narrow.
      // min-width: a floor so a squeezed column never breaks the date into
      // a word per line (about the width of «No hay turnos disponibles»).
      ".lst-closed{display:inline-block;min-width:min(13em,100%);font-size:16px;color:var(--text);line-height:1.4;white-space:normal;text-wrap:pretty;}" +
      // The opening date is the fact the patient needs: ink, not the
      // humanrounds theme's 50% --text-2 tint (same call as .lst-open-note).
      ".lst-closed-when{display:block;color:var(--text);}" +
      // the way on (remedies): a full-height tap target, the theme's link look
      ".lst-closed-next{display:flex;align-items:center;min-height:44px;}" +
      ".lst-action{display:inline-flex;align-items:center;justify-content:center;min-height:44px;" +
        "padding:8px 15px;border-radius:var(--btn-radius,9px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);text-decoration:none;" +
        "font-size:16px;font-weight:650;line-height:1.2;white-space:nowrap;transition:filter .12s,transform .1s,background .12s;}" +
      ".lst-action:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}.lst-action:active{transform:translateY(1px);}" +
      // Green belongs to the one thing you can press: the booking pill. A
      // walk-in opening is information, so it reads in ink (on-site tester,
      // 2026-10-21: green pills next to green walk-in text read as "some
      // rows green, some white" with no visible reason). Its "por orden de
      // llegada" is smaller but in ink too: the humanrounds theme makes
      // --text-2 a 50% tint, too faint for the cell's main fact.
      ".lst-open{color:var(--text);font-weight:500;white-space:nowrap;}" +
      ".lst-open-note{margin-left:6px;font-size:16px;color:var(--text);white-space:normal;}" +
      // "Más horarios" del MISMO color que el botón al que acompaña (responsable del producto,
      // 2026-07-14): son la misma noticia, no dos señales distintas. Peso
      // regular para que la fila no lea como una sucesión de titulares.
      // "Más horarios" es un link de texto como cualquier otro del sitio
      // (responsable del producto, 2026-09-15): color de link del tema + el subrayado tenue.
      // Secondary action = outline button next to the primary one (review
      // 2026-11-15): 44px tall like it, never a second filled colour.
      ".lst-more{display:inline-flex;align-items:center;box-sizing:border-box;min-height:44px;padding:0 14px;" +
        "border:1px solid var(--border-strong);border-radius:9px;background:var(--panel);" +
        "font-size:16px;font-weight:400;color:var(--text);text-decoration:none;white-space:nowrap;cursor:pointer;}" +
      ".lst-more:hover{background:var(--hover-bg);color:var(--text);}" +
      // La celda-link (avatar + nombre) NO apaga el subrayado: se propaga al
      // nombre y no entra en el avatar (inline-flex = inline atómico). El
      // nombre de la entidad es uno de los pocos acentos que quedan
      // (the product owner, 2026-09-15: "accents only a few, such as the name of the exam").
      ".lst-linkcell{display:inline-flex;align-items:center;gap:12px;min-width:0;}" +
      ".lst-name{color:var(--accent);font-weight:500;font-size:16px;}" +
      // Contexto secundario (especialidad, centro): link atenuado — gris, más
      // liviano que la entidad en accent, con subrayado punteado tenue que lo
      // mantiene legible como link a reposo (accent-is-link). En hover pasa a
      // un gris más oscuro — sin subrayado (responsable del producto, 2026-08-07: ningún link se
      // subraya, ni en hover).
      // Context (specialty, center) is still a fact the patient reads: dark
      // and 16px, the entity name alone carries the accent.
      ".lst-name--sub{color:var(--text);font-weight:400;font-size:16px;}" +
      ".lst-name--sub:hover{color:var(--hover-ink,var(--text));}" +
      ".lst-txt--sub{color:var(--text);}" +
      // Names in the avatar cell stay compact for ordinary rows, but wrap
      // when a real long name needs that room so the appointment remains
      // reachable in its own column.
      // vertical-align:middle: el inline-flex del avatar, alineado a la baseline
      // por defecto, sumaba ~6px de hueco de descendente y engordaba la fila —
      // centrarlo la iguala a la fila de servicios (responsable del producto, 2026-07-16).
      ".lst-linkcell--av{vertical-align:middle;align-items:center;}" +
      ".lst-linkcell--av .lst-name{white-space:normal;overflow-wrap:anywhere;}" +
      ".lst-person-copy{display:flex;min-width:0;flex-direction:column;justify-content:center;gap:2px;line-height:1.18;}" +
      ".lst-person-specialty{display:none;color:var(--text);font-size:16px;font-weight:400;white-space:normal;overflow-wrap:anywhere;}" +
      ".lst-name:hover,.lst-linkcell--av:hover .lst-name{color:var(--link,var(--accent));}" +
      // fila-link: toda la línea es clickeable (lleva a sacar turno)
      "tr.lst-rowlink{cursor:pointer;}" +
      "tr.lst-rowlink:hover td{background:var(--hover-bg);}" +
      "tr.lst-rowlink:hover a.lst-time{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}" +
      ".lst-txt{color:var(--text);}" +
      // Avatar más grande SIN estirar la fila (responsable del producto, 2026-07-16): el círculo se
      // dibuja a 40px pero su caja de layout mide 32px gracias al margin vertical
      // negativo (40 − 4 − 4 = 32), así la fila de un médico sigue midiendo EXACTO
      // lo mismo que la de un servicio (59px) — el retrato se agranda hacia el
      // padding de la celda, que sobra (13px), sin recortarse. Antes a 46px sin
      // este truco el retrato era el más alto y estiraba las filas ~24%.
      ".lst-av{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;margin:-4px 0;" +
        "border-radius:50%;font:600 15px var(--font);flex-shrink:0;}" +
      ".lst-av--ph{background:var(--av-ph,#eef0ee) center/cover no-repeat;}" +
      // ONE neutral initials avatar in both themes (review 2026-11-15, P37):
      // the colour slot no longer paints a doctor green and the next blue.
      ".lst-av:not(.lst-av--ph){background:var(--av-ph,#eef0ee);color:var(--text);}" +
      // Columna Orden (mockup 2026-07-14): "Con orden" texto en tinta + ⓘ, el
      // conjunto abre el popover explicativo; "Sin orden" texto atenuado plano.
      ".lst-orden{display:inline-flex;align-items:center;gap:6px;min-height:44px;background:none;border:0;padding:0;" +
        "font:inherit;font-size:16px;font-weight:400;color:var(--text);cursor:pointer;white-space:nowrap;}" +
      ".lst-orden-i{width:20px;height:20px;color:var(--text);}" +
      ".lst-orden:hover .lst-orden-i{color:var(--accent);}" +
      ".lst-orden-sin{font-size:16px;font-weight:400;color:var(--text);white-space:nowrap;}" +
      // "+N" (columna lugar, Estudios en más de un centro): texto, no chrome
      // de botón — es un link a más información, en accent (accent-is-link).
      ".lst-masbtn{display:inline-flex;align-items:center;min-height:44px;background:none;border:0;padding:0;font:inherit;font-size:16px;" +
        "font-weight:700;color:var(--accent);cursor:pointer;white-space:nowrap;}" +
      ".lst-masbtn:hover{color:var(--accent-hover);}" +
      ".lst-tag{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:6px;font-size:13px;" +
        "font-weight:600;letter-spacing:.04em;text-transform:uppercase;vertical-align:middle;}" +
      ".lst-tag--demo{background:var(--bg-2);color:var(--text-3);}" +
      ".lst-note,.lst-empty{font-size:16px;color:var(--text);margin:14px 2px 0;line-height:1.5;}" +
      ".lst-pop{position:absolute;z-index:50;max-width:320px;background:var(--panel);border:1px solid var(--border-strong);" +
        "border-radius:12px;padding:14px 15px;box-shadow:0 8px 30px rgba(0,0,0,.14);font-size:16px;line-height:1.5;color:var(--text);}" +
      ".lst-pop p{margin:0 0 9px;}.lst-pop b{color:var(--text);}" +
      ".lst-poplink{display:inline-block;margin-top:2px;color:var(--accent);font-weight:600;}" +
      ".lst-poplink:hover{color:var(--accent-hover);}" +
      ".lst-pop-title{margin:0 0 8px;font-weight:600;color:var(--text);}" +
      ".lst-pop-list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:7px;}" +
      ".lst-pop-list li{margin:0;}" +
      ".lst-pop-list .lst-poplink{margin-top:0;}" +
      // Desktop: the listing stays in the content column, one left edge with
      // the title (review 2026-11-15, D19/P26 — revokes the 2026-07-23
      // full-viewport breakout, which stretched rows to 1392px).
      "@media(min-width:1121px){" +
        ".lst-table th,.lst-sort{font-size:16px;}" +
        ".lst-table td{padding:14px 13px;}" +
        // Doctor table: a name and a center fit on one line (2026-11-15,
        // public review — «Juan / González» broke while the row had room).
        // At 1072px name, specialty, center and a one-line «button · date ·
        // Más horarios» cell do not all fit: a floor on name and center alone
        // squeezed the specialty until a word broke mid-letter. So «Más
        // horarios» goes under the button, as on the phone, and the freed
        // width becomes floors for all three text columns (the specialty floor
        // fits its longest word, so a word never breaks inside). Every table
        // does the same, not only the doctors': on /d/<specialty> the services
        // table above kept «Más horarios» beside the button (ui review
        // 2026-11-15).
        ".lst-table:has(.lst-linkcell--av) td{padding-left:10px;padding-right:10px;}" +
        ".lst-table td.lst-col-proximo .lst-timecell{flex-wrap:wrap;max-width:270px;row-gap:4px;}" +
        ".lst-table:has(.lst-linkcell--av) td.lst-col-nombre{min-width:13em;}" +
        ".lst-table:has(.lst-linkcell--av) td.lst-col-especialidad{min-width:9em;}" +
        ".lst-table:has(.lst-linkcell--av) td.lst-col-centro{min-width:11em;}" +
        ".lst-table tbody td{height:66px;}" +
        ".lst-name{font-size:17px;}" +
      "}" +
      // Tablet/narrow window: the identity cell owns name + specialty. The
      // avatar is centered against that two-line stack while the remaining
      // facts keep their columns.
      "@media(min-width:901px) and (max-width:1120px){" +
        ".lst-scroll{overflow-x:auto;}" +
        ".lst-table th{padding:8px 7px;font-size:15px;}" +
        ".lst-sort{font-size:15px;}" +
        ".lst-table td{padding:11px 7px;}" +
        ".lst-name{font-size:17px;}" +
        // «Subí tu orden» + «Primer turno: …» + «Más horarios» (00e14ad5) is
        // wider than the doctor grid's appointment column once the chat panel
        // takes its share of a 1024px window: under pressure the note and the
        // link wrap below the button — same remedy as the phone layout —
        // instead of pushing the row past the listing box. Each piece stays
        // on one line; a cell that fits keeps its single line.
        ".lst-timecell{gap:4px 7px;flex-wrap:wrap;max-width:100%;}" +
        "a.lst-time{padding:0 12px;}" +
        ".lst-more{padding:0 12px;}" +
        ".lst-av{width:34px;height:34px;margin:-2px 0;font-size:13px;}" +
        // In a row, the base column layout's vertical centring would centre
        // a name that wraps above its specialty horizontally: start at the
        // avatar like every other name (2026-11-13; same at ≤900).
        ".lst-person-copy{flex-direction:row;justify-content:flex-start;align-items:baseline;flex-wrap:wrap;column-gap:8px;row-gap:1px;}" +
        ".lst-person-specialty{display:inline;}" +
        ".lst-table:has(.lst-row-av) .lst-col-especialidad{display:none;}" +
        ".lst-table:has(.lst-row-av),.lst-table:has(.lst-row-av) thead,.lst-table:has(.lst-row-av) tbody{display:block;}" +
        // --lst-av-grid (tableHtml): one track per column the table shows, so
        // a center's page (no Centro), a study's «Quién atiende» (no Centro,
        // no Orden) or a collapsed column leaves no empty track mid-row.
        ".lst-table:has(.lst-row-av) tr{display:grid;grid-template-columns:var(--lst-av-grid,minmax(210px,1.25fr) minmax(120px,.78fr) minmax(88px,.55fr) minmax(238px,1.45fr));align-items:center;border-bottom:1px solid var(--border-soft);}" +
        // A grid cell is a block: its height:56px stopped being a floor and
        // became a cap, so a wrapped «Más horarios» spilled onto the next row.
        // The row keeps the uniform height, the cell grows with its content.
        ".lst-table:has(.lst-row-av) tbody tr{min-height:56px;}" +
        ".lst-table:has(.lst-row-av) tbody td{height:auto;min-width:0;}" +
        ".lst-table:has(.lst-row-av) tbody tr:last-child{border-bottom:0;}" +
        ".lst-table:has(.lst-row-av) td{border-bottom:0;}" +
        // The other cells take the next tracks in order.
        ".lst-table:has(.lst-row-av) .lst-col-nombre{grid-column:1;grid-row:1;align-self:center;}" +
      "}" +
      "@media(min-width:701px) and (max-width:1120px){" +
        ".lst-timecell{gap:8px;}" +
      "}" +
      // Mobile: la fila se APILA compacta. Servicio/estudio (responsable del producto, img 5-6):
      // entidad + centro en la MISMA línea separados por ' · ' (el centro baja
      // ENTERO a la línea 2 sólo si no hay lugar), y el botón de turno debajo con
      // poco aire. Médico (img 7): avatar + nombre en la línea 1, botón alineado
      // DEBAJO del nombre (no del avatar). Se esconden las columnas hideSm
      // (especialidad/área/orden). "Más horarios" se QUEDA (responsable del producto, 2026-07-16:
      // esconderlo era un bug, no diseño — en el teléfono elegir otro horario
      // importa igual; el botón con ?slot= saltea la grilla, así que sin este
      // link no había camino visible a los demás horarios). Sin scroll
      // horizontal: nada se recorta.
      "@media(max-width:900px){" +
        ".lst-tools--filters{flex-wrap:wrap;gap:10px;}" +
        ".lst-tools--filters .lst-searchgroup{flex:1 1 100%;}" +
        ".lst-tools--filters .lst-examples{display:flex;}" +
        // One «Filtros» control; the selects open under it (review P29).
        ".lst-phonebar{display:flex;flex:1 1 100%;gap:8px;min-width:0;}" +
        ".lst-phonebar .lst-sortsel{flex:1 1 auto;min-width:0;height:44px;}" +
        ".lst-tools--filters:not(.lst-tools--open) .lst-selects{display:none;}" +
        // El ancho medido de cada valor sostiene los tres filtros en UNA fila
        // mientras entren. fitSelects() apila el grupo completo si dejan de
        // entrar; nunca queda un 2+1 desperdiciando media fila.
        ".lst-selects{display:flex;flex-wrap:nowrap;flex:1 1 100%;gap:8px;min-width:0;}" +
        ".lst-selects>.lst-select{flex:1 1 auto;}" +
        ".lst-selects.lst-selects--stacked{display:grid;grid-template-columns:minmax(0,1fr);}" +
        ".lst-selects.lst-selects--stacked .lst-select{width:100%!important;}" +
        // 16px también acá: un <select> más chico dispara el mismo zoom de
        // iOS al abrirlo. fitSelects() ya apila el grupo si no entra en fila.
        ".lst-tools--filters .lst-select{font-size:16px;padding:10px 26px 10px 8px;" +
          "background-position:right 7px center;min-width:0;}" +
        ".lst-scroll{overflow-x:visible;margin:0;}" +
        ".lst-table,.lst-table tbody,.lst-table tr,.lst-table td{display:block;}" +
        ".lst-table thead{display:none;}" +
        // Fila apilada: sin aire lateral propio, salvo que el contenedor ponga la
        // tabla a los dos bordes de su tarjeta (margen negativo) — entonces fija
        // --lst-row-pad-x con el padding que canceló y el texto vuelve al borde
        // de lectura de la tarjeta mientras los divisores llegan a los bordes.
        ".lst-table tr{padding:14px var(--lst-row-pad-x,0);border-bottom:1px solid var(--border-soft);}" +
        ".lst-table tr:last-child{border-bottom:0;}" +
        ".lst-table td{padding:0;border:0;white-space:normal;}" +
        ".lst-table td.lst-hide-sm,.lst-hide-sm{display:none;}" +
        // Centro and Orden: ONE compact line under the name (review P6/D17),
        // «CAPS Norte · Con orden», instead of hiding Orden on a phone.
        ".lst-table tr.lst-row-av td.lst-col-centro,.lst-table tr.lst-row-av td.lst-col-orden{display:inline-block;vertical-align:middle;}" +
        ".lst-table tr.lst-row-av td.lst-col-centro+td.lst-col-orden{margin-left:0;}" +
        ".lst-table tr.lst-row-av td.lst-col-centro+td.lst-col-orden::before{content:\"·\";margin:0 8px;color:var(--text);}" +
        ".lst-table tr:not(.lst-row-av) td.lst-col-orden{display:inline-block;margin-left:10px;vertical-align:middle;}" +
        // servicio/estudio: entidad + centro son campos distintos y se separan
        // sólo con aire. La coma queda reservada para una celda que enumera dos
        // ubicaciones (p.ej. "CAPS Ostende, Centro Cultural").
        ".lst-table tr:not(.lst-row-av) td.lst-td-link,.lst-table tr:not(.lst-row-av) .lst-linkcell{display:inline;}" +
        ".lst-table tr:not(.lst-row-av) td.lst-td-sub{margin-top:0;margin-left:10px;white-space:normal;}" +
        // médico = objeto-media dentro de UNA celda: foto + bloque compacto de
        // nombre/especialidad. Los demás datos arrancan bajo el bloque de texto,
        // no bajo el avatar.
        ".lst-table tr.lst-row-av{padding-left:var(--lst-row-pad-x,0);}" +
        ".lst-table tr.lst-row-av td.lst-col-nombre{padding-left:0;}" +
        ".lst-table tr.lst-row-av td:not(.lst-col-nombre){margin-left:56px;}" +
        ".lst-person-copy{flex-direction:row;justify-content:flex-start;align-items:baseline;flex-wrap:wrap;column-gap:8px;row-gap:1px;}" +
        ".lst-person-specialty{display:inline;line-height:1.2;}" +
        ".lst-table tr.lst-row-av .lst-name{font-size:17px;line-height:1.2;}" +
        ".lst-td-sub{margin-top:3px;}" +
        ".lst-td-proximo{margin-top:2px;}" +
        // Keep actions in normal flow: wrapped names/specialties can be
        // taller than the avatar, so a fixed negative offset obscures them.
        // Keep each action intact, but let the alternative wrap when the
        // avatar indent leaves too little room for both links on a phone.
        // «Más horarios» sits in the SAME place in every row (review D17):
        // its own line under the button, never beside it on one row and
        // below it on the next. An order's date stays next to its button.
        ".lst-timecell{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px 10px;max-width:100%;white-space:nowrap;}" +
        ".lst-timecell>.lst-time{justify-self:start;}" +
        ".lst-timecell>.lst-more{grid-column:1/-1;justify-self:start;margin:0;}" +
        ".lst-time-note{white-space:normal;}" +
        ".lst-searchbox{gap:11px;padding:10px 16px;}" +
        ".lst-search{font-size:16px;}" +
        ".lst-examples a:nth-of-type(n+4){display:none;}" +
        ".lst-av{width:44px;height:44px;margin:0;font-size:15px;}" +
        // Tabla agrupada (responsable del producto, 2026-08-05 — «Resultados de laboratorio»):
        // cada determinación se apila CON su etiqueta — antes "92 mg/dL"
        // quedaba arriba de "70–110" sin decir cuál era cuál (el peor
        // defecto de la pantalla vieja). Todo bajo :has(tr.lst-grp): una
        // tabla que nunca agrupa (todas las demás del sitio hoy) no mueve
        // un solo píxel acá.
        ".lst-table:has(tr.lst-grp) tr.lst-grp{padding:22px var(--lst-row-pad-x,0) 6px;}" +
        ".lst-table:has(tr.lst-grp) tr.lst-grp th{padding:0;white-space:normal;font-size:16px;}" +
        ".lst-table:has(tr.lst-grp) tr:not(.lst-grp){padding:11px var(--lst-row-pad-x,0);}" +
        ".lst-table:has(tr.lst-grp) td{display:flex;gap:10px;align-items:baseline;}" +
        ".lst-table:has(tr.lst-grp) td:first-child{display:block;font-size:18px;font-weight:800;color:var(--text);margin-bottom:6px;}" +
        ".lst-table:has(tr.lst-grp) td[data-l]::before{content:attr(data-l);flex:0 0 128px;font-size:16px;font-weight:500;color:var(--text-2);}" +
      "}" +
      "@media(max-width:360px){.lst-person-copy{flex-direction:column;align-items:flex-start;gap:2px;}" +
        ".lst-person-specialty{display:block;}}";
    var st = document.createElement("style"); st.id = "lst-style"; st.textContent = css;
    document.head.appendChild(st);
  }

  // ---- logging de búsquedas (analytics para un futuro reporte admin) --------
  // Compartido por TODO buscador del sitio que use listado.js (el .lst-search
  // que se dibuja solo con >15 filas, en médicos/estudios/centros/etc.). Loguea
  // la query FINAL asentada — 1500ms sin tipear — nunca cada tecla, y una sola
  // vez por valor distinto (dedup contra la última logueada). blur/Enter la
  // asientan de una. Cero impacto en la búsqueda si el beacon falla: el envío
  // va en try/catch silencioso y nunca bloquea ni demora el render.
  var _logTimer = null;
  var _logLastQ = null;
  // Random id of this page load (kept nowhere): lets the report glue the
  // prefixes of one typing session into the longest query.
  var SEARCH_SID = Math.random().toString(36).slice(2, 12) + Date.now().toString(36);

  function _sendSearchLog(q, resultados) {
    var origen = (typeof location !== "undefined" && location.pathname) || "";
    var jid = "";
    try { jid = (typeof sessionStorage !== "undefined" && sessionStorage.getItem("hr_jid")) || ""; } catch (e) { /* sin sesión */ }
    var payload = JSON.stringify({ q: q, resultados: resultados, origen: origen, sid: SEARCH_SID, jid: jid });
    try {
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        navigator.sendBeacon("/api/search-log", new Blob([payload], { type: "application/json" }));
        return;
      }
    } catch (e) { /* sigue al fallback */ }
    try {
      if (typeof fetch === "function") {
        fetch("/api/search-log", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: payload, keepalive: true
        }).catch(function () {});
      }
    } catch (e) { /* analytics: nunca debe romper la búsqueda */ }
  }

  function logSearch(q, resultados) {
    q = String(q || "").trim();
    if (_logTimer) { clearTimeout(_logTimer); _logTimer = null; }
    if (q.length < 2 || q === _logLastQ) return;
    _logTimer = setTimeout(function () {
      _logTimer = null;
      _logLastQ = q;
      _sendSearchLog(q, resultados);
    }, 1500);
  }

  function flushSearchLog(q, resultados) {
    q = String(q || "").trim();
    if (_logTimer) { clearTimeout(_logTimer); _logTimer = null; }
    if (q.length < 2 || q === _logLastQ) return;
    _logLastQ = q;
    _sendSearchLog(q, resultados);
  }

  // ---- buscador arriba con el teclado abierto -------------------------------
  // En el teléfono, el teclado en pantalla se come la mitad de abajo y iOS
  // centra el input enfocado: la caja queda al medio y los resultados, abajo
  // del teclado, invisibles mientras se tipea (responsable del producto, 2026-09-02). Al enfocar
  // CUALQUIER input[type=search] del sitio paciente (los .lst-search y el #q
  // de la portada — por eso vive acá y no en wire(): un listener global cubre
  // también los buscadores propios de las páginas que cargan listado.js), la
  // página se desplaza para dejar la caja pegada abajo de la nav, con los
  // resultados a la vista en el espacio que el teclado deja libre. El retardo
  // deja terminar el auto-scroll + teclado de iOS, que si no pisa el nuestro.
  function pinSearchTop(el) {
    if (typeof window === "undefined" || !window.matchMedia) return;
    if (!window.matchMedia("(max-width:900px)").matches) return;
    var box = (el.closest && el.closest(".lst-searchbox,.searchbox")) || el;
    setTimeout(function () {
      if (document.activeElement !== el) return;
      var nav = parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue("--stznav-h")) || 0;
      var y = window.scrollY + box.getBoundingClientRect().top - nav - 10;
      window.scrollTo({ top: Math.max(y, 0), behavior: "smooth" });
    }, 350);
  }

  // ---- boot -----------------------------------------------------------------
  function boot() {
    injectStyle();
    document.querySelectorAll("[data-listado]").forEach(function (m) {
      if (m.__lst) return; m.__lst = 1;
      var raw = m.querySelector(".lst-data");
      if (!raw) return;
      var p; try { p = JSON.parse(raw.textContent); } catch (e) { return; }
      initMount(m, p);
    });
  }
  if (typeof document !== "undefined") {
    var startBoot = function () { boot(); };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startBoot);
    else startBoot();
    document.addEventListener("focusin", function (e) {
      var t = e.target;
      if (t && t.matches && t.matches('input[type="search"]')) pinSearchTop(t);
    });
  }

  function render(el, payload) {
    injectStyle();
    el.setAttribute("data-listado", "");
    el.__lst = 1;
    initMount(el, payload);
  }

export {
  render,
  ORDEN as orden,
  expandCols,
  colapsoUniforme as colapso,
  notaColapso as nota,
  timePill,
  orderPill,
  habilitaCell,
  iniciales,
  withWeekdayArticle,
  openText,
  moreLink,
  searchAllHtml,
  grupoRowHtml as grupoRow,
  logSearch,
  flushSearchLog,
  pinSearchTop
};
