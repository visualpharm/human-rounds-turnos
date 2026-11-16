/* admin-table.mjs — anchos de columna medidos y arrastrables, para TODAS las
   tablas del admin (the product owner, 2026-07-24: "this table is nice and uses the
   draggable columns… apply this to all the other tables in the admin section.
   Use exactly the same code to show different tables").

   Es EL MISMO motor que estrenó Pacientes de hoy (admin-queue.js, ronda 3 del
   2026-07-24), sacado de ahí y parametrizado: cola ahora lo consume igual que
   el resto, así no quedan dos implementaciones que se van separando con el
   tiempo. Qué hace, en orden:

     1. MIDE el ancho natural de cada columna (pasada con table-layout:auto y
        las celdas sin recortar) y toma el MÁXIMO sobre todas las tablas del
        mismo grupo — los días del calendario o los grupos por médico quedan
        alineados entre sí.
     2. Lo hace CABER en el contenedor: el sobrante o el faltante se reparte
        sólo entre las columnas de texto (`flex`), con un piso por columna. El
        navegador decide los anchos; nosotros sólo los igualamos y los
        encajamos.
     3. Cuando ni con todo en su mínimo entra, CAEN columnas por prioridad
        (`drop`) en vez de mandar la tabla a un scroll horizontal — lo primero
        que quedaría tapado es siempre la última columna, que suele ser el
        control de la pantalla. Lo que cae sigue estando a un click.
     4. Cada th trae una manija (`.tbl-res`): el ancho elegido a mano GANA
        sobre la medición, se guarda por persona en el servidor de
        preferencias y no se toca al repartir. Doble click en la manija
        devuelve esa columna al automático.

   Una tabla que no declara columnas igual funciona: `derivarCols()` arma la
   metadata leyendo los encabezados (piso por largo del título, elástica si su
   contenido es largo). Por eso una sección nueva no necesita configurar nada.

   Módulo ES canónico (fila 4.1): los consumidores lo importan por nombre a
   través de window.SANA_STATIC / sanaImportStatic y esperan esa promesa en el
   punto donde llaman a la API. Sin global de navegador, sin gemelo clásico,
   sin copia. La evaluación del módulo instala los listeners compartidos y el
   MutationObserver que engancha las tablas sin dueño.

   Uso:
       var tableReady = window.sanaImportStatic("platform/admin-table.mjs");
       tableReady.then(function (Table) {
         var anchos = Table.crear({ clave:"pacientes", host:fn, ... });
         anchos.programarFit();   // después de cada render: ajusta al final de la misma tarea, antes de pintar
       });
*/
"use strict";

var Prefs = null;
var preferencesLoaded = false;
// Stories import this module directly, outside the admin shell. The normal
// path still uses the fingerprint-aware shell resolver; the relative ESM
// fallback makes the module self-contained in that isolated runtime.
var loadStatic = typeof window.sanaImportStatic === "function"
  ? window.sanaImportStatic.bind(window)
  : function () { return import("./admin-preferences.mjs"); };
var preferencesReady = loadStatic("platform/admin-preferences.mjs").then(function (m) {
  Prefs = m;
  return m.load();
}).then(function (values) {
  preferencesLoaded = true;
  return values;
});

function afterPreferencesLoaded(callback) {
  // Always cross a microtask boundary so constructors can finish publishing
  // their API before a hydration callback invokes it.
  preferencesReady.then(callback);
}

function setPreference(key, value) {
  function currentValue() { return typeof value === "function" ? value() : value; }
  if (preferencesLoaded) Prefs.set(key, currentValue());
  else preferencesReady.then(function () { Prefs.set(key, currentValue()); });
}

var I18n = null;
// Browser consumers publish the fingerprinted URL before this module loads.
// A standalone story has no browser manifest, so only that environment uses
// the checked-in relative module.  Do not invent a /static fallback in a
// served browser: a missing manifest is a broken shell, not an alternate URL.
var i18nReady = (typeof window !== "undefined")
  ? ((window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
    ? import(window.SANA_STATIC["platform/i18n.mjs"])
    : Promise.reject(new Error("[static] platform/i18n.mjs is not in window.SANA_STATIC")))
  : import("./i18n.mjs");
i18nReady.then(function (m) { I18n = m; retitle(); }, function () { return null; });
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
function resizeTitle() {
  return T("admin.reports.table.resize_title", null, "Arrastrá para cambiar el ancho (doble clic = automático)");
}
function gearLabel() {
  return T("admin.reports.table.elegir_columnas", null, "Elegir columnas");
}
// A table mounted before the catalog import settled carries the Spanish
// fallbacks in its handles and gear: relabel them once the catalog is here.
function retitle() {
  if (typeof document === "undefined") return;
  document.querySelectorAll("[data-tbl-res]").forEach(function (h) { h.title = resizeTitle(); });
  document.querySelectorAll("[data-tblcol-gear]").forEach(function (b) {
    b.setAttribute("aria-label", gearLabel());
    b.title = gearLabel();
  });
  document.querySelectorAll("[data-tbl-export]").forEach(function (b) {
    b.setAttribute("aria-label", exportLabel());
    b.title = exportLabel();
  });
}

var LS_BASE = "st_adm_anchos";   // {clave: {columna: px}} — preferencia de quien mira

function leer(almacen) {
  var o = Prefs
    ? Prefs.get("table.widths." + almacen.replace(/[^a-z0-9_.:-]/gi, "-").toLowerCase(), {})
    : {};
  return o && typeof o === "object" ? o : {};
}

function fn(v, def) { return typeof v === "function" ? v : function () { return def; }; }

/* cfg:
     clave      string  — bucket en localStorage (obligatorio)
     almacen    string  — clave de localStorage entera (default LS_BASE + ":" + clave)
     tablas()   -> lista de <table> del grupo (todas se miden juntas)
     medio()    -> el elemento contra el que se hace caber (default: el wrap de la 1ª tabla)
     cols()     -> [{key,min,max,flex}] en el orden de las columnas; sin esto se derivan
     drop       [key] — prioridad de caída
     dropClase(key) -> clase que oculta esa columna (default "tbl-oc-"+key)
     apilado()  -> true cuando la tabla está apilada (móvil): ahí no se mide nada
     medirClase string — clase que se pone al host mientras se mide
     host()     -> contenedor donde vive medirClase (default: padre de la 1ª tabla)
     vacia(key) -> true si todas las celdas de esa columna están vacías: cae primero
     elegida(key) -> true si la persona prendió esa columna a mano (un
                engranaje propio, la cola); sin esto se mira «Elegir columnas»
     fill       false = no repartir el ancho que sobra
     anchosCelda(key) -> [px] lo que pide cada celda de una columna `entero`
                (se llama mientras se mide); con eso la falta de ancho se
                reparte donde tapa menos texto
   col.entero true | número — la columna no se recorta mientras otra pueda ceder;
                el número es el peso de su texto tapado (2 = vale el doble).
                Sin declararlo, una columna de nombres (persona, médico,
                servicio, centro, preparación: ver esNombre) ya es `entero: 2`;
                `entero: false` la saca de ahí.
   col.nombre true — la columna es de nombres aunque su clave no lo diga

   Sin configurar nada, el motor además (pinamar-turnos 07.11, «nombres mal
   recortados»): mide lo que pide cada celda en una línea; una columna con
   todas sus celdas vacías no se dibuja, en ningún ancho (salvo `always`, sin
   título o prendida a mano: ésa se queda con el ancho de su encabezado); el ancho
   que sobra va primero a las columnas que recortan texto, aunque pasen su
   `max`; y la celda que igual queda recortada lleva el texto completo en
   `title`.
*/
var NOMBRE_RE = /(^|[^a-z])(nombre|name|paciente|persona|medic|doctor|profesional|usuari|servicio|centro|preparaci)/;
function sinAcentos(s) {
  return String(s == null ? "" : s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}
function esNombre(c) {
  if (!c || c.clip || c.entero === false) return false;
  if (c.nombre) return true;
  return NOMBRE_RE.test(sinAcentos(c.key)) || NOMBRE_RE.test(sinAcentos(c.label));
}
// `entero` declarado gana; una columna de nombres sin declarar pesa el doble.
function pesoEntero(c) {
  if (typeof c.entero === "number") return c.entero;
  if (c.entero) return 1;
  return esNombre(c) ? 2 : 0;
}
// Una celda vacía sólo dice «—» (o nada) y no dibuja nada: ni ícono, ni
// botón, ni barras. Lo que dibuja sin texto tiene caja propia.
// Una celda que baja de línea no tapa texto al angostarse. No envuelve si
// ella o algo adentro dice nowrap (la línea del nombre en la cola, los
// médicos de Pacientes).
var ENVUELVE_RE = /^(normal|pre-wrap|break-spaces|pre-line)$/;
function envuelveCelda(td) {
  if (!td || !ENVUELVE_RE.test(getComputedStyle(td).whiteSpace || "")) return false;
  var els = td.getElementsByTagName ? td.getElementsByTagName("*") : [];
  for (var k = 0; k < els.length && k < 40; k++) {
    if (!ENVUELVE_RE.test(getComputedStyle(els[k]).whiteSpace || "")) return false;
  }
  return true;
}
var RELLENO_RE =/^[\s—–\-·]*$/;
function celdaVacia(td) {
  if (!RELLENO_RE.test(td.textContent || "")) return false;
  var els = td.getElementsByTagName ? td.getElementsByTagName("*") : [];
  for (var k = 0; k < els.length; k++) {
    if (!(els[k].textContent || "").trim() && els[k].getBoundingClientRect().width > 0) return false;
  }
  return true;
}
function crearTabla(cfg) {
  cfg = cfg || {};
  var almacen = cfg.almacen || (LS_BASE + ":" + cfg.clave);
  var USER_W = leer(almacen);
  var USER_W_DIRTY = {};
  var ANCHOS = null;            // último set aplicado, por índice
  var COLS = null;              // metadata vigente (declarada o derivada)
  var tablasFn = fn(cfg.tablas, []);
  // Debajo de 1024 la tabla ya no es una grilla que convenga encajar (o está
  // apilada, o la sección la deja scrollear): se devuelve al layout natural.
  var apilado = cfg.apilado || function () {
    try { return window.matchMedia("(max-width:1024px)").matches; } catch (e) { return false; }
  };
  var pend = false;
  var cuadro = null;
  var medioObservado = null;
  var observadorDeMedio = null;
  var disposed = false;
  var ownedTables = new Set();
  if (cfg.selector) RESERVADAS.push(cfg.selector);

  // A module can create its table while the shared preferences request is
  // still in flight (the queue does this during script evaluation). Merge
  // the eventual server snapshot into that instance, but let a drag or a
  // double-click performed meanwhile win over the late response.
  afterPreferencesLoaded(function () {
    if (disposed) return;
    var saved = leer(almacen);
    Object.keys(saved).forEach(function (key) {
      if (!USER_W_DIRTY[key]) USER_W[key] = saved[key];
    });
    programarFit();
  });

  function tablas() {
    var t = tablasFn();
    return t ? Array.prototype.slice.call(t) : [];
  }

  // A column toggle can redraw a table just before the available content
  // width changes (for example, an open chooser while the direct shell
  // crosses its sidebar breakpoint). Watch the actual fitting medium so the
  // table is measured again on the next frame, rather than retaining the
  // previous viewport's inline column widths until the resize debounce.
  // This one path keeps the frame (fitEnCuadro): the observer is delivered
  // after layout, and a fit run inside its delivery would rewrap rows and
  // change the medium's height mid-delivery — the browser's «ResizeObserver
  // loop» error. A redraw never waits for it: programarFit() fits first.
  function observarMedio(medio) {
    if (!medio || medio === medioObservado || !window.ResizeObserver) return;
    if (observadorDeMedio) observadorDeMedio.disconnect();
    medioObservado = medio;
    observadorDeMedio = new window.ResizeObserver(function () { fitEnCuadro(); });
    observadorDeMedio.observe(medio);
  }

  function guardar() {
    setPreference(
      "table.widths." + almacen.replace(/[^a-z0-9_.:-]/gi, "-").toLowerCase(),
      function () { return Object.assign({}, USER_W); }
    );
  }

  // El texto del encabezado es la identidad de la columna cuando no hay
  // metadata: es lo único estable entre renders (no hay ids en el markup).
  function etiqueta(th) {
    return (th.textContent || "").replace(/[↑↓▲▼]/g, "").replace(/\s+/g, " ").trim();
  }

  function ths(tb) {
    var fila = tb.tHead && tb.tHead.rows.length ? tb.tHead.rows[0] : null;
    return fila ? Array.prototype.slice.call(fila.cells) : [];
  }

  // Sin metadata declarada: piso por largo del título (un encabezado nunca
  // se recorta) y elástica si su contenido da para envolver. Alcanza para
  // que una tabla nueva se comporte bien sin configurar nada.
  function derivarCols(tb) {
    return ths(tb).map(function (th, i) {
      var label = etiqueta(th);
      var largo = label.length;
      return {
        key: label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || ("col" + i),
        label: label,
        // el encabezado nunca se recorta: ese es el piso
        min: label ? Math.max(52, Math.min(150, 22 + largo * 7.5)) : 40,
        max: 420,
        // ceden ancho las de texto; las cortas (Edad, Estado, acciones) no
        flex: largo >= 6 ? 1 : 0
      };
    });
  }

  function cols(tb) {
    if (cfg.cols) {
      var c = fn(cfg.cols, null)();
      if (c && c.length) return c;
    }
    return tb ? derivarCols(tb) : [];
  }

  function colgroup(tb, n) {
    // El motor reparte por <col>: si la tabla no lo trae (la mayoría), se
    // agrega una sola vez. Es invisible y no cambia el markup de nadie.
    var cg = tb.querySelector("colgroup");
    if (cg && cg.children.length === n) return cg;
    if (cg) cg.parentNode.removeChild(cg);
    cg = document.createElement("colgroup");
    for (var i = 0; i < n; i++) cg.appendChild(document.createElement("col"));
    tb.insertBefore(cg, tb.firstChild);
    return cg;
  }

  function aplicarAnchos(ws) {
    var total = ws.reduce(function (a, b) { return a + b; }, 0);
    tablas().forEach(function (tb) {
      var cs = colgroup(tb, ws.length).children;
      // A dropped column (width 0) hides its CELLS with display:none, and a
      // hidden cell leaves the grid: its <col> must leave with it, or every
      // <col> after it describes the NEXT visible column (Teléfono, Asistencia
      // and Estado came out 0 px wide at 1100/1280 — the product owner, 2026-10-29).
      for (var i = 0; i < cs.length && i < ws.length; i++) {
        cs[i].style.width = ws[i] + "px";
        cs[i].style.display = ws[i] > 0 ? "" : "none";
      }
      tb.style.tableLayout = "fixed";
      tb.style.width = total + "px";
    });
    ANCHOS = ws;
  }

  function limpiarAnchos() {
    tablas().forEach(function (tb) {
      tb.style.tableLayout = "";
      tb.style.width = "";
      var cg = tb.querySelector("colgroup");
      if (cg) Array.prototype.forEach.call(cg.children, function (c, i) {
        c.style.width = "";
        c.style.display = VACIAS_OC[i] ? "none" : "";
      });
    });
    ANCHOS = null;
  }

  // ---- columnas vacías: no se muestran, en ningún ancho ------------------
  // pinamar-turnos 07.11 (Cómo prepararse a 1440: «En una línea» sin una
  // sola celda en 30 filas, y al lado «Se reconoce por» con «…»). Una columna
  // vacía en TODAS las filas del listado no se dibuja, ni en la notebook ni
  // en el teléfono; vuelve en cuanto una fila trae un valor. No se esconden:
  // las `always` (identifican la fila o son el control de la pantalla), las
  // sin título (acciones, selección, relleno) y las que la persona prendió a
  // mano en «Elegir columnas» (pidió verla: la ve, aunque esté vacía).
  var VACIAS_OC = {};           // índice -> true: columna vacía escondida

  // Las filas que llevan una celda por columna, cabecera incluida: una fila
  // con colspan (sin resultados, el encabezado de un médico) no se toca.
  function filasCompletas(tb) {
    var out = [];
    [tb.tHead].concat(Array.prototype.slice.call(tb.tBodies || []), [tb.tFoot]).forEach(function (sec) {
      if (!sec || !sec.rows) return;
      Array.prototype.forEach.call(sec.rows, function (f) {
        if (f.cells && f.cells.length === COLS.length) out.push(f);
      });
    });
    return out;
  }

  function marcarVacias(oc) {
    VACIAS_OC = oc;
    tablas().forEach(function (tb) {
      filasCompletas(tb).forEach(function (f) {
        for (var i = 0; i < f.cells.length; i++) {
          var td = f.cells[i];
          if (oc[i]) {
            if (!td._tblVacia) { td._tblVacia = true; td.style.display = "none"; }
            if (typeof td.setAttribute === "function") td.setAttribute("data-tbl-vacia", "");
          } else if (td._tblVacia) {
            td._tblVacia = false;
            td.style.display = "";
            if (typeof td.removeAttribute === "function") td.removeAttribute("data-tbl-vacia");
          }
        }
      });
    });
  }

  // ¿Quedó una celda de una columna escondida sin esconder? (la pantalla
  // agregó o redibujó filas sin pedir un fit). Lo mira el barrido del
  // observer, así la columna nunca queda corrida ni vuelve tarde.
  function vaciasDesfasadas() {
    if (disposed || !COLS || !Object.keys(VACIAS_OC).length) return false;
    return tablas().some(function (tb) {
      return filasCompletas(tb).some(function (f) {
        return Object.keys(VACIAS_OC).some(function (i) { return f.cells[i] && !f.cells[i]._tblVacia; });
      });
    });
  }

  function ocultable(c, i, cabeceras) {
    if (!c || c.always || c.relleno) return false;
    var th = cabeceras[i];
    if (!(c.label || (th && etiqueta(th)))) return false;
    if (cfg.elegida) return !cfg.elegida(c.key);
    return !elegidaEnSelector(cfg.clave, c);
  }

  // Mide qué columnas vienen vacías en todas las filas y esconde las que se
  // pueden esconder. Se mide siempre con todo a la vista: una celda
  // escondida no deja ver su ícono y parecería vacía para siempre.
  function vaciasDe(tbs, filas) {
    var vacias = {};
    if (!filas.length) return vacias;
    COLS.forEach(function (c, i) {
      if (filas.every(function (f) { return celdaVacia(f.cells[i]); })) vacias[i] = true;
    });
    return vacias;
  }
  function esconderVacias(tbs, vacias) {
    var cabeceras = ths(tbs[0]);
    var oc = {};
    COLS.forEach(function (c, i) { if (vacias[i] && ocultable(c, i, cabeceras)) oc[i] = true; });
    marcarVacias(oc);
    return oc;
  }

  var dropClase = cfg.dropClase || function (k) { return "tbl-oc-" + k; };

  // Columnas que pueden caerse: las vacías (`cfg.vacia`, sólo las que no son
  // `always`) y las de `drop`. Todas pasan por acá, para que la que cayó en
  // el render anterior vuelva cuando ya no hace falta.
  function caibles() {
    var ks = (cfg.drop || []).slice();
    if (cfg.vacia) {
      (COLS || []).forEach(function (c) { if (!c.always && ks.indexOf(c.key) === -1) ks.push(c.key); });
    }
    return ks;
  }

  function aplicarOcultas(ocultas) {
    var ks = caibles();
    tablas().forEach(function (tb) {
      ks.forEach(function (k) { tb.classList.toggle(dropClase(k), !!ocultas[k]); });
    });
  }

  function autoFit() {
    if (disposed) return;
    var tbs = tablas();
    ownedTables.forEach(function (tb) {
      if (tbs.indexOf(tb) === -1) {
        if (tb._sanaAnchos === api) delete tb._sanaAnchos;
        ownedTables.delete(tb);
      }
    });
    if (!tbs.length) return;
    COLS = cols(tbs[0]);
    if (!COLS.length) return;
    montarManijas(tbs);

    if (apilado()) {
      VACIAS_OC = {};
      limpiarAnchos();
      aplicarOcultas({});
      marcarVacias({});
      esconderVacias(tbs, vaciasDe(tbs, filasDeDatos(tbs)));
      limpiarAnchos();
      return;
    }
    var medio = (cfg.medio && cfg.medio()) || tbs[0].closest(".cnt-table-wrap") || tbs[0].parentNode;
    observarMedio(medio);
    var disp = medio ? medio.clientWidth : 0;
    if (!disp) return;   // sección todavía oculta: no hay nada que medir

    var host = (cfg.host && cfg.host()) || tbs[0].parentNode;
    if (cfg.medirClase && host) host.classList.add(cfg.medirClase);
    VACIAS_OC = {};
    limpiarAnchos();
    aplicarOcultas({});   // medir SIEMPRE con todas: al agrandar, vuelven
    marcarVacias({});
    var nat = COLS.map(function () { return 0; });
    tbs.forEach(function (tb) {
      var hs = ths(tb);
      for (var i = 0; i < hs.length && i < COLS.length; i++) {
        var w = Math.ceil(hs[i].getBoundingClientRect().width);
        if (w > nat[i]) nat[i] = w;
      }
    });
    // Lo que pide cada celda de una columna `entero`, ordenado: con eso se
    // sabe cuántas celdas quedan cortadas a cada ancho.
    var celdas = {};
    if (cfg.anchosCelda) {
      COLS.forEach(function (c) {
        if (!c.entero) return;
        var cw = cfg.anchosCelda(c.key);
        if (cw && cw.length !== undefined) {
          celdas[c.key] = Array.prototype.map.call(cw, function (x) { return Math.ceil(x); })
            .sort(function (a, b) { return a - b; });
        }
      });
    }
    if (cfg.medirClase && host) host.classList.remove(cfg.medirClase);

    // Las demás columnas, sin configurar nada: lo que pide cada celda en UNA
    // línea, qué columnas vienen todas vacías y cuáles envuelven (ésas no
    // tapan texto al angostarse, sólo bajan de línea). Se mide con la tabla
    // en `tbl-midiendo` (nowrap, admin-direct.css) y vuelve en el mismo frame.
    var filas = filasDeDatos(tbs);
    var vacias = vaciasDe(tbs, filas), envuelve = {}, cabecera = {};
    if (filas.length && typeof document.createRange === "function") {
      COLS.forEach(function (c, i) {
        if (!vacias[i] && envuelveCelda(filas[0].cells[i])) envuelve[i] = true;
      });
      tbs.forEach(function (tb) { tb.classList.add("tbl-midiendo"); });
      var rango = document.createRange();
      COLS.forEach(function (c, i) {
        if (vacias[i]) {
          tbs.forEach(function (tb) {
            var th = ths(tb)[i];
            if (th) cabecera[i] = Math.max(cabecera[i] || 0, Math.ceil(th.getBoundingClientRect().width));
          });
          return;
        }
        if (celdas[c.key] || envuelve[i]) return;
        var out = [];
        filas.forEach(function (f) {
          var td = f.cells[i];
          rango.selectNodeContents(td);
          var rr = rango.getBoundingClientRect();
          if (!rr.width) { out.push(0); return; }
          var st = getComputedStyle(td);
          out.push(Math.ceil(rr.right - td.getBoundingClientRect().left + (parseFloat(st.paddingRight) || 0)));
        });
        celdas[c.key] = out.sort(function (a, b) { return a - b; });
      });
      tbs.forEach(function (tb) { tb.classList.remove("tbl-midiendo"); });
    }
    // Una columna de nombres que envuelve no tapa nada: no es `entero`.
    function peso(i) { return envuelve[i] || vacias[i] ? 0 : pesoEntero(COLS[i]); }

    // `entero: true` (Paciente y Servicio en la cola, y toda columna de
    // nombres): antes de recortar esa columna por debajo de lo que mide su
    // contenido, cae primero una columna vacía o una de `drop`. Para el piso
    // cuenta su ancho natural, no su min. Una columna toda vacía ocupa lo que
    // su encabezado (Cómo prepararse a 1440: «En una línea», sin una sola
    // celda, se llevaba 220 px y los nombres de al lado quedaban cortados).
    function anchoDe(c, i, minimo) {
      if (USER_W[c.key] > 0) return USER_W[c.key];
      if (vacias[i]) return Math.max(40, cabecera[i] || nat[i] || 0);
      // Para caer columnas cuenta sólo `entero` declarado: un nombre largo de
      // una fila no saca a Médico de Pacientes; lo recorta el reparto, con
      // elipsis y el nombre entero en el globo.
      if (minimo && c.flex && (!c.entero || !peso(i))) return c.min;
      return Math.max(c.min, Math.min(nat[i] || c.min, c.max || 9999));
    }
    // Las vacías que se pueden esconder ya no ocupan nada: su ancho va a las
    // de al lado por el mismo reparto de siempre.
    var ocultas = {};
    var vaciasOc = esconderVacias(tbs, vacias);
    COLS.forEach(function (c, i) { if (vaciasOc[i]) ocultas[c.key] = true; });
    var drop = cfg.drop || [];
    // Primero las columnas cuyas celdas vienen todas vacías (sólo dicen «—»),
    // después la prioridad declarada.
    var orden = [];
    if (cfg.vacia) {
      COLS.forEach(function (c) { if (!c.always && cfg.vacia(c.key)) orden.push(c.key); });
    }
    // Lo mismo sin que la pantalla lo diga: una columna que el motor midio
    // vacia en todas las filas cae antes que una con datos (Como prepararse a
    // 390: caia «Se reconoce por» y quedaba «En una linea», sin una celda,
    // comiendole 110 px a los nombres).
    COLS.forEach(function (c, i) { if (!c.always && vacias[i] && orden.indexOf(c.key) === -1) orden.push(c.key); });
    drop.forEach(function (k) { if (orden.indexOf(k) === -1) orden.push(k); });
    for (var d = 0; d < orden.length; d++) {
      var piso = COLS.reduce(function (a, c, i) {
        return ocultas[c.key] ? a : a + anchoDe(c, i, true);
      }, 0);
      if (piso <= disp) break;
      ocultas[orden[d]] = true;
    }
    aplicarOcultas(ocultas);

    var ws = COLS.map(function (c, i) { return ocultas[c.key] ? 0 : anchoDe(c, i, false); });
    var elasticas = [];
    COLS.forEach(function (c, i) { if (c.flex && !ocultas[c.key] && !vacias[i] && !(USER_W[c.key] > 0)) elasticas.push(i); });
    var total = function () { return ws.reduce(function (a, b) { return a + b; }, 0); };
    // Cuántas celdas de una columna quedan cortadas a un ancho `w`.
    var cortadas = function (ord, w) {
      var lo = 0, hi = ord.length;
      while (lo < hi) { var mid = (lo + hi) >> 1; if (ord[mid] > w) hi = mid; else lo = mid + 1; }
      return ord.length - lo;
    };
    // El ancho que sobra va primero, pixel a pixel, a la columna que con ese
    // pixel deja de tapar más texto (los nombres pesan el doble), aunque pase
    // su `max`: el tope es para repartir, no para cortar un nombre con lugar
    // al lado (cola a 1440: «María de los Ángeles Fernández de la To» con
    // 170 px vacíos a la derecha). Lo que queda, por `flex` como siempre.
    var crecerRecortadas = function (extra) {
      var dado = 0;
      var cand = [];
      COLS.forEach(function (c, i) {
        if (!ocultas[c.key] && !vacias[i] && !envuelve[i] && !(USER_W[c.key] > 0) && celdas[c.key]) cand.push(i);
      });
      while (dado < extra) {
        var mejor = -1, valor = 0;
        cand.forEach(function (i) {
          var v = (COLS[i].clip ? 0.25 : Math.max(1, peso(i))) * cortadas(celdas[COLS[i].key], ws[i]);
          if (v > valor) { mejor = i; valor = v; }
        });
        if (mejor < 0) break;
        ws[mejor] += 1;
        dado += 1;
      }
      return dado;
    };

    if (elasticas.length) {
      var diff = disp - total();
      if (diff > 0) diff -= crecerRecortadas(diff);
      if (diff > 0 && cfg.fill !== false) {
        var pesoFlex = elasticas.reduce(function (a, i) { return a + COLS[i].flex; }, 0);
        elasticas.forEach(function (i) { ws[i] += Math.floor(diff * COLS[i].flex / pesoFlex); });
      } else if (diff < 0) {
        // Achicar en pasadas: lo que una columna no puede ceder (llegó a su
        // min) lo absorben las demás en la vuelta siguiente. Las `entero`
        // ceden sólo después de que las otras elásticas llegaron a su min.
        var achicar = function (grupo) {
          for (var pasada = 0; pasada < 5 && total() > disp; pasada++) {
            var falta = total() - disp;
            var margen = grupo.reduce(function (a, i) { return a + Math.max(0, ws[i] - COLS[i].min); }, 0);
            if (margen <= 0) break;
            var quitar = Math.min(falta, margen);
            grupo.forEach(function (i) {
              var puede = Math.max(0, ws[i] - COLS[i].min);
              if (!puede) return;
              ws[i] -= Math.min(puede, Math.round(quitar * puede / margen));
            });
          }
        };
        // Las `entero` con lo que pide cada celda (cfg.anchosCelda) ceden
        // pixel a pixel donde ese pixel tapa menos texto: cuántas celdas
        // quedan cortadas a ese ancho, por el peso de la columna. Un nombre
        // largo ya no recorta el servicio de las otras 30 filas (pinamar-
        // turnos 05.11: a 1440, «Maximiliano Domínguez 14 años» dejaba
        // «Ginecología y Obstetri…» en toda la cola). Tapar texto cuesta más
        // cuanto más se tapa, así que elegir el pixel más barato cada vez da
        // el reparto que menos texto tapa en total. Una columna de nombres
        // que nadie declaró `entero` entra igual, con peso 2.
        var recortarEnteras = function (grupo) {
          while (total() > disp) {
            var mejor = -1, costo = Infinity;
            grupo.forEach(function (i) {
              if (ws[i] <= COLS[i].min) return;
              var c = peso(i) * cortadas(celdas[COLS[i].key], ws[i] - 1);
              if (c < costo || (c === costo && ws[i] > ws[mejor])) { mejor = i; costo = c; }
            });
            if (mejor < 0) break;
            ws[mejor] -= 1;
          }
        };
        achicar(elasticas.filter(function (i) { return !peso(i); }));
        var enteras = elasticas.filter(function (i) { return peso(i) > 0; });
        if (enteras.length && enteras.every(function (i) { return celdas[COLS[i].key]; })) {
          recortarEnteras(enteras);
        } else {
          achicar(elasticas);
        }
      }
      // Cuadre final: el redondeo del reparto deja 1-2px de más o de menos y
      // 1px de más ya dibuja una barra de scroll. Se ajusta contra la columna
      // elástica más ancha (nunca por debajo de su min).
      var resto = disp - total();
      if (resto && (resto < 0 || cfg.fill !== false)) {
        var ancha = elasticas.reduce(function (m, i) { return ws[i] > ws[m] ? i : m; }, elasticas[0]);
        ws[ancha] = Math.max(COLS[ancha].min, ws[ancha] + resto);
      }
    }

    // Hacer entrar a lo bruto una tabla que NO declara prioridad de caída
    // recorta todas las columnas a la vez: "Administr…", "Hospital…",
    // "CAPS Va…". Eso es peor que la barra de scroll que había antes. Así
    // que si el ajuste dejaría a alguna columna con menos del 60% de lo que
    // el contenido pide, se abandona el encaje y se vuelve al ancho natural
    // (las manijas siguen ahí: quien quiera prioriza a mano). Una tabla con
    // `drop` declarado —la cola— sí sabe qué sacrificar y no pasa por acá.
    if (!(cfg.drop && cfg.drop.length)) {
      var aplastada = elasticas.some(function (i) {
        // `clip: true` = esta columna ESTÁ para recortarse (el resumen de
        // una entrevista previa es un párrafo y pide 900px): no cuenta como
        // aplastada, o una sola celda larga manda al ancho natural a la
        // tabla entera —y a todas las del grupo, cada una con sus columnas
        // en otra vertical—. Para el resto se compara contra lo que la
        // columna dice que necesita (`max`), no contra lo que mide.
        if (COLS[i].clip) return false;
        var techo = Math.min(nat[i], COLS[i].max || 9999);
        return !(USER_W[COLS[i].key] > 0) && techo && ws[i] < techo * 0.6;
      });
      if (aplastada) { limpiarAnchos(); return; }
    }
    aplicarAnchos(ws);
    titularRecortes(filas);
  }

  // Filas de datos del cuerpo: las que traen una celda por columna (una fila
  // de «sin resultados» o de detalle con colspan no cuenta). Para medir alcanza
  // con las primeras 300.
  function filasDeDatos(tbs) {
    var out = [];
    tbs.forEach(function (tb) {
      Array.prototype.forEach.call(tb.tBodies || [], function (body) {
        Array.prototype.forEach.call(body.rows || [], function (f) {
          if (out.length < 300 && f.cells && f.cells.length === COLS.length) out.push(f);
        });
      });
    });
    return out;
  }

  // Lo que quedó cortado igual (no había lugar) lleva el texto completo en
  // `title`, en el elemento que corta: la celda o su línea de bloque. El globo
  // del recorte (más abajo) lo usa y el lector de pantalla lo lee. Un title
  // propio de la pantalla no se toca; el puesto acá se saca cuando ya entra.
  // El texto que se ve de una celda: lo oculto (el triage que en el teléfono
  // cierra la línea del nombre y en la tabla ancha no se dibuja) no entra en
  // el globo de una celda cortada.
  function textoVisible(el) {
    var doc = el.ownerDocument;
    if (!doc || !doc.createTreeWalker) return el.textContent || "";
    var w = doc.createTreeWalker(el, 4), s = "", n;
    while ((n = w.nextNode())) {
      var p = n.parentElement;
      if (p && p !== el && !p.getClientRects().length) continue;
      s += n.nodeValue;
    }
    return s;
  }

  function titularRecortes(filas) {
    filas.forEach(function (f) {
      Array.prototype.forEach.call(f.cells, function (td) {
        var propio = td.hasAttribute("title") && !td.hasAttribute("data-tbl-title");
        var corta = function (el) {
          return el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1
            && !el.querySelector("button,input,select,textarea");
        };
        var poner = function (el, si) {
          // Una línea de bloque adentro de la celda (el nombre en la cola y en
          // Pacientes) la corta la celda sin elipsis: «…de la To». Se la marca
          // sólo mientras corta, así nada más cambia de alineación.
          if (el !== td) {
            var bloque = si && /^(block|flow-root|list-item)$/.test(getComputedStyle(el).display);
            el.classList.toggle("tbl-corta", bloque);
          }
          if (propio) return;
          var auto = el.hasAttribute("data-tbl-title");
          if (!auto && el.hasAttribute("title")) return;
          if (si) {
            el.setAttribute("title", textoVisible(el).replace(/\s+/g, " ").trim());
            el.setAttribute("data-tbl-title", "");
          } else if (auto) {
            el.removeAttribute("title");
            el.removeAttribute("data-tbl-title");
          }
        };
        // el más profundo que corta lleva el title (un title en la celda y
        // otro en su línea serían dos globos)
        var hijo = false;
        Array.prototype.forEach.call(td.children, function (ch) {
          var si = corta(ch);
          if (si) hijo = true;
          poner(ch, si);
        });
        poner(td, !hijo && corta(td));
      });
    });
  }

  // La manija se agrega desde acá (no la pinta cada módulo en su HTML): así
  // una tabla nueva la tiene por existir, que es todo el punto de compartir
  // el motor. `data-tbl-res` guarda el índice, no la clave: la tabla puede
  // no tener metadata declarada.
  function montarManijas(tbs) {
    tbs.forEach(function (tb) {
      var cabeceras = ths(tb);
      cabeceras.forEach(function (th, i) {
        if ((COLS[i] && COLS[i].resizable === false) || th.querySelector("[data-tbl-res]") || th.querySelector("[data-cola-res]")) return;
        // La última columna sin título (acciones) no se ajusta: su manija
        // sólo dibujaba una raya suelta al final de la cabecera.
        if (i === cabeceras.length - 1 && !th.textContent.trim()) return;
        if (getComputedStyle(th).position === "static") th.style.position = "relative";
        var h = document.createElement("span");
        h.className = "tbl-res";
        h.setAttribute("data-tbl-res", String(i));
        h.title = resizeTitle();
        th.appendChild(h);
      });
      tb.classList.add("tbl-anchos");
      tb._sanaAnchos = api;
      ownedTables.add(tb);
    });
  }

  // The fit runs at the end of the task that asked for it — a microtask, not
  // the next animation frame (2026-11-10). A frame later, a redraw that lands
  // late in a frame is painted once at the table's natural width and then
  // jumps when the fit arrives (the queue's doctor column moved 296 px at
  // 1280, CLS 0.053 per redraw). A microtask still runs before any paint, also
  // when it is asked for from inside a frame (the automatic sweep), and
  // several calls in one task still make one fit. autoFit() itself refuses
  // once disposed.
  function programarFit() {
    if (disposed || pend) return;
    pend = true;
    queueMicrotask(function () { pend = false; autoFit(); });
  }

  // The medium observer's refit only (see observarMedio): a frame later.
  function fitEnCuadro() {
    if (disposed || cuadro !== null) return;
    cuadro = window.requestAnimationFrame(function () { cuadro = null; autoFit(); });
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (cuadro !== null) { window.cancelAnimationFrame(cuadro); cuadro = null; }
    if (observadorDeMedio) observadorDeMedio.disconnect();
    observadorDeMedio = null;
    medioObservado = null;
    var index = INSTANCIAS.indexOf(api);
    if (index !== -1) INSTANCIAS.splice(index, 1);
    if (cfg.selector) {
      var reservation = RESERVADAS.indexOf(cfg.selector);
      if (reservation !== -1) RESERVADAS.splice(reservation, 1);
    }
    Object.keys(AUTO).forEach(function (key) { if (AUTO[key] === api) delete AUTO[key]; });
    ownedTables.forEach(function (tb) { if (tb._sanaAnchos === api) delete tb._sanaAnchos; });
    ownedTables.clear();
    if (DRAG && DRAG.inst === api) {
      DRAG = null;
      document.body.classList.remove("tbl-resizing");
    }
  }

  var api = {
    dispose: dispose,
    autoFit: autoFit,
    programarFit: programarFit,
    _vaciasDesfasadas: vaciasDesfasadas,
    // los usa el arrastre global, que no sabe de qué instancia es la tabla
    _drag: function (i) { return { w0: ANCHOS ? ANCHOS[i] : 0, ok: !disposed && !!ANCHOS && !apilado() }; },
    _mover: function (i, w) {
      if (disposed || !ANCHOS || !COLS[i]) return;
      var ws = ANCHOS.slice();
      ws[i] = Math.max(COLS[i].min, w);
      USER_W_DIRTY[COLS[i].key] = true;
      USER_W[COLS[i].key] = ws[i];
      aplicarAnchos(ws);
    },
    _soltar: function () { if (!disposed) { guardar(); autoFit(); } },
    _auto: function (i) {
      if (disposed || !COLS || !COLS[i]) return;
      USER_W_DIRTY[COLS[i].key] = true;
      delete USER_W[COLS[i].key];
      guardar();
      autoFit();
    }
  };
  return api;
}

// ---- arrastre: un solo par de listeners para todas las instancias ---------

var DRAG = null;

function manija(e) {
  var h = e.target && e.target.closest ? e.target.closest("[data-tbl-res]") : null;
  if (!h) return null;
  var tb = h.closest("table");
  return tb && tb._sanaAnchos ? { i: +h.getAttribute("data-tbl-res"), inst: tb._sanaAnchos } : null;
}

document.addEventListener("pointerdown", function (e) {
  var m = manija(e);
  if (!m) return;
  var st = m.inst._drag(m.i);
  if (!st.ok) return;
  e.preventDefault();
  DRAG = { i: m.i, inst: m.inst, x0: e.clientX, w0: st.w0 };
  document.body.classList.add("tbl-resizing");
});

document.addEventListener("pointermove", function (e) {
  if (!DRAG) return;
  DRAG.inst._mover(DRAG.i, Math.round(DRAG.w0 + (e.clientX - DRAG.x0)));
});

document.addEventListener("pointerup", function () {
  if (!DRAG) return;
  var d = DRAG;
  DRAG = null;
  document.body.classList.remove("tbl-resizing");
  d.inst._soltar();
});

document.addEventListener("dblclick", function (e) {
  var m = manija(e);
  if (m) m.inst._auto(m.i);
});

var resizeT = null;
window.addEventListener("resize", function () {
  if (resizeT) clearTimeout(resizeT);
  resizeT = setTimeout(function () {
    resizeT = null;
    INSTANCIAS.forEach(function (i) { i.programarFit(); });
  }, 150);
});

// The columns are fitted to whatever face is painting. A web font that lands after the
// fit can be a hair wider than its metric-matched fallback (static/fonts/*.css), and a
// column fitted to the last pixel would rewrap for a frame: refit when each face lands,
// not only when the whole set settles (a slow mono or latin-ext cut would hold that).
//
// Between the moment a face is applied and the refit there can be a painted frame in the
// new, wider face with the old column widths. While the page's face is pending, `html.tbl-fuentes`
// keeps the rows that would wrap on that hair (admin-queue.css) on one line; the class goes
// in the microtask right after the refit, when the widths fit the face that landed, or
// after FUENTES_TOPE_MS at the latest when the face never reports (see marcarFuentes).
function fuenteSinCargar() {
  try {
    if (document.fonts.status === "loading") return true;
    // Idle but the page's face not requested yet: pending, unless a face failed (then it is settled).
    var fallida = false;
    document.fonts.forEach(function (face) { if (face.status === "error") fallida = true; });
    if (fallida) return false;
    var cs = getComputedStyle(document.body);
    return !document.fonts.check(cs.fontSize + " " + cs.fontFamily, "Ag");
  } catch (e) { return false; }
}
// The class never outlives a few seconds: a face that never reports (blocked, offline, no
// loadingdone) must not leave the rows unwrappable for good.
var FUENTES_TOPE_MS = 3000;
var fuentesDesde = 0;
function marcarFuentes() {
  if (!document.documentElement) return;
  var pendiente = fuenteSinCargar();
  if (!pendiente) fuentesDesde = 0; // each pending period gets its own budget and timer
  else if (!fuentesDesde) {
    fuentesDesde = Date.now();
    setTimeout(marcarFuentes, FUENTES_TOPE_MS + 50);
  } else if (Date.now() - fuentesDesde > FUENTES_TOPE_MS) pendiente = false;
  document.documentElement.classList.toggle("tbl-fuentes", pendiente);
}
function refitAlCargar() {
  INSTANCIAS.forEach(function (i) { i.programarFit(); });
  queueMicrotask(marcarFuentes);
}
function seguirCara(face) {
  if (face && face.loaded && face.status === "loading") face.loaded.then(refitAlCargar, function () {});
}
if (document.fonts && document.fonts.addEventListener) {
  if (document.fonts.forEach) document.fonts.forEach(seguirCara);
  document.fonts.addEventListener("loading", function (e) { (e.fontfaces || []).forEach(seguirCara); });
  document.fonts.addEventListener("loading", marcarFuentes);
  document.fonts.addEventListener("loadingdone", refitAlCargar);
  document.fonts.addEventListener("loadingerror", refitAlCargar);
  if (document.body) marcarFuentes(); else document.addEventListener("DOMContentLoaded", marcarFuentes);
}

// ---- enganche automático --------------------------------------------------
// Las secciones que no declaran columnas no llaman a nada: el observer ve la
// tabla nueva y le monta una instancia con metadata derivada. Así "todas las
// tablas del admin" es literal y no una lista que hay que mantener.

var INSTANCIAS = [];
var AUTO = {};          // clave -> instancia (las tablas iguales comparten)
// Selectores de tablas que ya tienen dueño declarado (cola): el enganche
// automático no las toca ni siquiera antes del primer fit.
var RESERVADAS = [];

function claveDe(tb) {
  var sec = tb.closest("section[id], div[id^='sec-']");
  var labels = [];
  var fila = tb.tHead && tb.tHead.rows.length ? tb.tHead.rows[0] : null;
  if (!fila) return null;
  Array.prototype.forEach.call(fila.cells, function (c, i) {
    var t = (c.textContent || "").replace(/[↑↓▲▼]/g, "").replace(/\s+/g, " ").trim();
    // Una columna sin título (la de acciones, al final) igual cuenta: se
    // nombra por su posición. Descartarla dejaba tablas enteras afuera.
    labels.push(t || ("col" + i));
  });
  var conNombre = labels.filter(function (l) { return !/^col\d+$/.test(l); }).length;
  if (labels.length < 3 || conNombre < 2) return null;
  // El conjunto ordenado alfabéticamente: dos tablas con las mismas columnas
  // (los días del calendario del médico) comparten ancho, que es lo que uno
  // espera al arrastrar una.
  return (sec ? sec.id : "adm") + ":" + labels.slice().sort().join("|");
}

function registrar(inst) { INSTANCIAS.push(inst); return inst; }

var barridoPend = false;
function barrer() {
  barridoPend = false;
  // Filas nuevas en una tabla con una columna vacía escondida: se vuelve a
  // medir ya, antes de pintar (la celda nueva puede traer el valor que la
  // devuelve, o quedar corrida debajo de otra cabecera).
  INSTANCIAS.forEach(function (inst) { if (inst._vaciasDesfasadas()) inst.autoFit(); });
  var vistas = {};
  Array.prototype.forEach.call(document.querySelectorAll("table"), function (tb) {
    // las que ya tienen dueño (cola, o cualquier módulo que llame a crear)
    if (tb._sanaAnchos || tb.closest("[data-sin-anchos]")) return;
    if (RESERVADAS.some(function (s) { return tb.matches(s); })) return;
    var k = claveDe(tb);
    if (!k) return;
    vistas[k] = vistas[k] || [];
    vistas[k].push(tb);
  });
  Object.keys(vistas).forEach(function (k) {
    if (!AUTO[k]) {
      AUTO[k] = registrar(crearTabla({
        clave: k,
        tablas: function () {
          return Array.prototype.slice.call(document.querySelectorAll("table"))
            .filter(function (tb) {
              return claveDe(tb) === k
                && !RESERVADAS.some(function (s) { return tb.matches(s); });
            });
        }
      }));
    }
    AUTO[k].programarFit();
  });
}
function pedirBarrido() {
  if (barridoPend) return;
  barridoPend = true;
  requestAnimationFrame(barrer);
}

// =====================================================================
// ELEGIR COLUMNAS (engranaje) — el mismo motor para todas las tablas
// =====================================================================
// Product owner, 2026-07-28: "add it to the customizable columns, and do it for all
// the tables actually". Pacientes de hoy y Médicos ya tenían un selector de
// columnas cada uno, escrito dos veces; el resto de las tablas no tenía
// ninguno. Acá vive UNA implementación y cada sección declara nada más su
// lista de columnas.
//
// Una columna: {key, label, default:true (viene prendida), always:true (no
// se puede apagar — es la que identifica la fila o el control de la
// pantalla)}. Todo lo demás (min/max/flex) lo lee el motor de anchos y acá
// se ignora, así una sección declara sus columnas UNA vez y las dos cosas
// funcionan.

// El dibujo es el archivo Icons8 (sf-regular, `table-1`) que admin-direct.css
// pinta como máscara con la tinta del botón — nunca un glifo dibujado ni una
// ligadura de fuente (regla 04.10, «no dibujamos íconos»).
var GEAR_ICON = '<span class="adm-icon" aria-hidden="true"></span>';

function columnas(cfg) {
  cfg = cfg || {};
  var groupKey = String(cfg.clave);
  var disposed = false;
  var todas = cfg.cols || [];
  var pref = "table.columns." + String(cfg.clave || "tabla");

  function porDefecto() {
    return todas.filter(function (c) { return c["default"]; }).map(function (c) { return c.key; });
  }
  function leer() {
    var k = Prefs ? Prefs.get(pref, null) : null;
    // Se guarda la lista ELEGIDA, no un diff contra el default: si mañana
    // una columna cambia de default, quien ya eligió no ve aparecer nada.
    if (Array.isArray(k)) return k;
    // Una pantalla que tenía su propio selector (`cfg.legado`, la clave
    // vieja — el catálogo guardaba en `catalog.columns.<tipo>`) no le hace
    // perder a nadie su elección: se lee una vez, pasa por la migración de
    // la pantalla (`cfg.migrar`) y queda copiada en la clave compartida.
    // Una lista vieja vacía valía «las de siempre», como la leía el catálogo.
    var viejo = cfg.legado && Prefs ? Prefs.get(cfg.legado, null) : null;
    if (Array.isArray(viejo) && viejo.length) {
      var migrada = cfg.migrar ? cfg.migrar(viejo.slice()) : viejo.slice();
      setPreference(pref, migrada.slice());
      return migrada;
    }
    return porDefecto();
  }
  var visibles = leer();
  var changedBeforeLoad = false;

  var api = {
    dispose: function () {
      if (disposed) return;
      disposed = true;
      if (GRUPOS[groupKey] === api) delete GRUPOS[groupKey];
    },
    visibles: function () { return visibles.slice(); },
    activas: function () {
      return todas.filter(function (c) {
        return c.always || visibles.indexOf(c.key) !== -1;
      });
    },
    tiene: function (key) {
      return todas.some(function (c) {
        return c.key === key && (c.always || visibles.indexOf(c.key) !== -1);
      });
    },
    gearHtml: function (clase) {
      // ONE place on every list (the product owner 05.09: «the gear in one place»):
      // the right end of the screen's toolbar row, first in `.cnt-toolbar-right`,
      // just before the screen's create action — where Pacientes, Usuarios,
      // Médicos, Servicios, Centros and Solicitudes (`.adm-sol-tools-r`, next
      // to «Crear turno») already had it. A list with no create action
      // (Mis pacientes, Mis turnos) keeps it at the right end of its band.
      var opts = todas.filter(function (c) { return !c.always && c.label; }).map(function (c) {
        return '<label class="cnt-gear-opt"><input type="checkbox" data-tblcol="' + escAttr(c.key)
          + '"' + (visibles.indexOf(c.key) !== -1 ? " checked" : "") + "><span>"
          + escAttr(c.label) + "</span></label>";
      }).join("");
      return '<div class="cnt-gear-wrap' + (clase ? " " + clase : "") + '" data-tblcol-grupo="'
        + escAttr(cfg.clave) + '">'
        + '<button type="button" class="cnt-gear-btn" data-tblcol-gear aria-label="'
        + escAttr(gearLabel()) + '"'
        + ' title="' + escAttr(gearLabel())
        + '" aria-haspopup="true" aria-expanded="false">' + GEAR_ICON + "</button>"
        + '<div class="cnt-gear-pop" data-tblcol-pop>' + opts + "</div></div>";
    },
    // ¿La persona prendió esta columna a mano? Sólo una que no viene de
    // fábrica puede estar prendida por elección; ésa se muestra aunque esté
    // vacía. La tabla puede no declarar columnas: se reconoce por el título.
    _elegida: function (c) {
      var lab = sinAcentos(c && c.label).trim();
      return todas.some(function (x) {
        return !x.always && !x["default"] && visibles.indexOf(x.key) !== -1
          && (x.key === c.key || (lab && sinAcentos(x.label).trim() === lab));
      });
    },
    _set: function (keys) {
      if (disposed) return;
      if (!preferencesLoaded) changedBeforeLoad = true;
      visibles = keys;
      setPreference(pref, visibles.slice());
      if (cfg.onChange) cfg.onChange(api.activas());
    }
  };
  if (GRUPOS[groupKey]) GRUPOS[groupKey].dispose();
  GRUPOS[groupKey] = api;
  // Owners may reconstruct their picker in onChange. Once loaded, leer()
  // already returned the final value: scheduling another notification
  // would create an endless promise/repaint loop.
  if (!preferencesLoaded) afterPreferencesLoaded(function () {
    if (disposed || changedBeforeLoad) return;
    var hydrated = leer();
    var changed = hydrated.length !== visibles.length || hydrated.some(function (key) {
      return visibles.indexOf(key) === -1;
    });
    visibles = hydrated;
    if (changed && cfg.onChange) cfg.onChange(api.activas());
  });
  return api;
}

var GRUPOS = {};

// El selector de la misma tabla manda (misma `clave`); una tabla que no lo
// nombra (las de enganche automático, Pacientes) se busca en todos.
function elegidaEnSelector(clave, c) {
  var claves = Object.keys(GRUPOS);
  if (GRUPOS[clave]) claves = [clave];
  return claves.some(function (k) { return GRUPOS[k]._elegida(c); });
}

function escAttr(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

// Un solo par de listeners para todos los engranajes de la consola: el que
// se abre cierra a los demás, y un click en cualquier otro lado los cierra
// todos (si no, quedan dos popovers abiertos tapando la tabla).
document.addEventListener("click", function (e) {
  // Un click DENTRO del popover es elegir una columna, no cerrarlo: se
  // prenden varias de una sentada y cerrarse en la primera obliga a
  // reabrirlo por cada una.
  if (e.target.closest && e.target.closest("[data-tblcol-pop]")) return;
  var btn = e.target.closest ? e.target.closest("[data-tblcol-gear]") : null;
  Array.prototype.forEach.call(document.querySelectorAll("[data-tblcol-grupo]"), function (w) {
    var b = w.querySelector("[data-tblcol-gear]"), pop = w.querySelector("[data-tblcol-pop]");
    if (!pop) return;
    var abrir = b === btn && pop.style.display !== "flex";
    pop.style.display = abrir ? "flex" : "none";
    if (b) b.setAttribute("aria-expanded", abrir ? "true" : "false");
  });
});

document.addEventListener("change", function (e) {
  var chk = e.target.closest ? e.target.closest("[data-tblcol]") : null;
  if (!chk) return;
  var wrap = chk.closest("[data-tblcol-grupo]");
  var grupo = wrap && GRUPOS[wrap.getAttribute("data-tblcol-grupo")];
  if (!grupo) return;
  grupo._set(Array.prototype.slice.call(wrap.querySelectorAll("[data-tblcol]:checked"))
    .map(function (x) { return x.getAttribute("data-tblcol"); }));
});

// =====================================================================
// DESCARGAR — la tabla que se ve, como archivo para Excel
// =====================================================================
// Product owner, 2026-11-07: an export for every small part (reports, the patients
// of the day...), never for the big places. ONE button for every
// small table, next to the gear: it reads the tables the screen shows right
// now — the columns in their order, the rows of the current filter and date —
// and asks the server (`POST /api/admin/table-export`, admin_panel/
// table_export.py) for the file. The server decides who may download which
// screen, refuses past the row limit with a sentence, and records each
// patient of a clinical screen in the access log. A big list (all of
// Pacientes, the logs) simply has no button, and the server has no entry for it.
//
// What a screen may declare (all optional):
//   on the button   data-tbl-export="<selector of the area>" (default: the
//                   screen's main area), data-tbl-export-title,
//                   data-tbl-export-title-part (added to the screen's title),
//                   data-tbl-export-day
//   on a <table>    data-export-skip (not part of the file),
//                   data-export-group="<label>" (grouped tables: the label
//                   becomes a first column, its header data-export-group-head)
//   on a <th>       data-export-skip (an action column), data-export="<text>"
//                   (the header's words; by default its visible text)
//   on a <td>       data-export="<text>" (the value of an icon-only cell),
//                   data-export-skip (its whole row is not data: a free slot)
//   inside a cell   data-export-omit (buttons of an action), data-export="<text>"
//                   (the full value of a part the screen cuts)
//   on a <tr>       data-export-dni (the patient, for the access log),
//                   data-export-skip (an empty-state row)
//   in the area     data-export-day="<YYYY-MM-DD>" (the day shown, when the
//                   button is not redrawn with it), data-export-title-part
//                   (a heading added to the file name)

var EXPORT_ICON = '<span class="adm-icon adm-icon-download" aria-hidden="true"></span>';

function exportLabel() {
  return T("admin.shell.export.button", null, "Descargar");
}

function exportHtml(opts) {
  opts = opts || {};
  var attrs = ' data-tbl-export="' + escAttr(opts.scope || "") + '"';
  if (opts.title) attrs += ' data-tbl-export-title="' + escAttr(opts.title) + '"';
  if (opts.titlePart) attrs += ' data-tbl-export-title-part="' + escAttr(opts.titlePart) + '"';
  if (opts.day) attrs += ' data-tbl-export-day="' + escAttr(opts.day) + '"';
  return '<button type="button" class="cnt-gear-btn tbl-export-btn' + (opts.clase ? " " + opts.clase : "")
    + '"' + attrs + ' aria-label="' + escAttr(exportLabel()) + '" title="' + escAttr(exportLabel())
    + '">' + EXPORT_ICON + "</button>";
}

function exportClean(text) {
  return String(text || "").replace(/[↑↓▲▼]/g, "").replace(/[ \t ]+/g, " ")
    .replace(/\s*\n\s*/g, "\n").trim().replace(/\n+/g, " · ");
}

// A cell that marks its parts: the text of each part in order, without the
// parts marked data-export-omit (the buttons of an action) and with the value
// of the parts that carry data-export (the whole summary behind a cut one).
function exportWalk(node, shown) {
  var out = [];
  Array.prototype.forEach.call(node.childNodes, function (n) {
    if (n.nodeType === 3) { if (n.nodeValue.trim()) out.push(n.nodeValue.trim()); return; }
    if (n.nodeType !== 1 || n.hasAttribute("data-export-omit")) return;
    if (n.hasAttribute("data-export")) { out.push(n.getAttribute("data-export")); return; }
    if (shown && hiddenEl(n)) return;
    var inner = exportWalk(n, shown);
    if (inner) out.push(inner);
  });
  return out.join(" ").trim();
}

function exportText(cell) {
  if (cell.hasAttribute("data-export")) return exportClean(cell.getAttribute("data-export"));
  if (cell.querySelector("[data-export], [data-export-omit]")) return exportClean(exportWalk(cell, !hiddenEl(cell)));
  var tel = cell.querySelector('a[href^="tel:"]');
  // innerText keeps the line break between stacked parts (name / DNI); a
  // hidden cell (a column the width engine dropped) answers its textContent.
  var text = (cell.innerText != null ? cell.innerText : cell.textContent) || "";
  text = text.replace(/[↑↓▲▼]/g, "").replace(/[ \t ]+/g, " ").replace(/\s*\n\s*/g, "\n").trim();
  // An icon-only contact (phone, WhatsApp, mail): the values it copies, once
  // each — the phone and its WhatsApp icon carry the same number.
  if (!text) {
    var seen = [];
    Array.prototype.forEach.call(cell.querySelectorAll("[data-copy]"), function (el) {
      var v = (el.getAttribute("data-copy") || "").trim();
      if (v && seen.indexOf(v) === -1) seen.push(v);
    });
    text = seen.join(" · ");
  }
  if (!text && tel) text = decodeURIComponent(tel.getAttribute("href").slice(4));
  if (!text) {
    var named = cell.querySelector("[aria-label], [title]");
    if (named) text = (named.getAttribute("aria-label") || named.getAttribute("title") || "").trim();
  }
  return text.replace(/\n+/g, " · ");
}

function hiddenEl(el) {
  if (el.hidden) return true;
  var cs = window.getComputedStyle ? window.getComputedStyle(el) : null;
  return !!(cs && cs.display === "none" && !el.closest("thead"));
}

function exportableTables(scope) {
  return Array.prototype.slice.call(scope.querySelectorAll("table")).filter(function (tb) {
    if (tb.hasAttribute("data-export-skip") || tb.closest("[data-export-skip]")) return false;
    if (tb.closest("[hidden]")) return false;
    return !!(tb.tHead && tb.tHead.rows.length);
  });
}

// The columns of one table: [{index, label}] without action columns (no
// header text, or marked) and without columns the screen hid on purpose
// (the gear or the width engine keep the cell in the DOM with a class).
// A header's words as shown: without its sort arrow (aria-hidden), its own
// buttons (Historial's «i») and the variant the width hides (Servicio /
// Servicio y práctica).
function exportHeadText(node) {
  var out = [];
  Array.prototype.forEach.call(node.childNodes, function (n) {
    if (n.nodeType === 3) { out.push(n.nodeValue); return; }
    if (n.nodeType !== 1 || n.tagName === "BUTTON" || n.hasAttribute("data-export-omit")
      || n.getAttribute("aria-hidden") === "true" || n.hidden) return;
    var cs = window.getComputedStyle ? window.getComputedStyle(n) : null;
    if (cs && cs.display === "none") return;
    out.push(exportHeadText(n));
  });
  return out.join(" ");
}

// A column the screen hid: its own heading is display:none and so is its cell
// in the first data row (the engine's tbl-oc- classes hide both). The phone's
// cards hide the whole thead instead: the headings keep their own display,
// and those columns stay.
function columnHidden(tb, th, i) {
  if (th.hidden) return true;
  var cs = window.getComputedStyle ? window.getComputedStyle(th) : null;
  if (!cs || cs.display !== "none") return false;
  var sample = null;
  Array.prototype.some.call(tb.tBodies, function (body) {
    return Array.prototype.some.call(body.rows, function (tr) {
      if (tr.cells.length > i && tr.cells[i].colSpan === 1) { sample = tr.cells[i]; return true; }
      return false;
    });
  });
  if (!sample) return true;
  var sc = window.getComputedStyle(sample);
  return sample.hidden || sc.display === "none";
}

function exportColumns(tb, withHidden) {
  var cols = [];
  var head = tb.tHead.rows[tb.tHead.rows.length - 1];
  Array.prototype.forEach.call(head.cells, function (th, i) {
    if (th.hasAttribute("data-export-skip") || (!withHidden && columnHidden(tb, th, i))) return;
    var label = (th.getAttribute("data-export") || exportHeadText(th) || "")
      .replace(/[↑↓▲▼]/g, "").replace(/\s+/g, " ").trim();
    if (!label) return;
    cols.push({ index: i, label: label });
  });
  return cols;
}

function collectExport(scope) {
  var columns = null, rows = [], patients = [], grouped = false, groupHead = "";
  exportableTables(scope).forEach(function (tb) {
    var cols = exportColumns(tb);
    if (!cols.length) return;
    var group = tb.getAttribute("data-export-group");
    if (group != null) { grouped = true; groupHead = groupHead || tb.getAttribute("data-export-group-head") || ""; }
    if (!columns) columns = cols.map(function (c) { return c.label; });
    else {
      // The next day's table may hide a different empty column: its cells go
      // under the first table's headings by name, a heading it lacks is blank.
      var all = exportColumns(tb, true);
      cols = columns.map(function (label) {
        var hit = all.filter(function (c) { return c.label === label; })[0];
        return { index: hit ? hit.index : -1, label: label };
      });
    }
    Array.prototype.forEach.call(tb.tBodies, function (body) {
      Array.prototype.forEach.call(body.rows, function (tr) {
        if (tr.hasAttribute("data-export-skip") || hiddenEl(tr)) return;
        if (Array.prototype.some.call(tr.cells, function (td) { return td.hasAttribute("data-export-skip"); })) return;
        if (tr.cells.length === 1 && tr.cells[0].colSpan > 1) return;   // empty state / band
        var row = cols.map(function (c) { var td = tr.cells[c.index]; return td ? exportText(td) : ""; });
        if (!row.some(function (v) { return v; })) return;
        rows.push(group != null ? { group: group, cells: row } : { cells: row });
        patients.push(tr.getAttribute("data-export-dni") || "");
      });
    });
  });
  if (!columns) return null;
  return {
    columns: grouped ? [groupHead].concat(columns) : columns,
    rows: rows.map(function (r) { return grouped ? [r.group || ""].concat(r.cells) : r.cells; }),
    patients: patients
  };
}

function exportScope(btn) {
  var sel = btn.getAttribute("data-tbl-export");
  var scope = sel ? document.querySelector(sel) : null;
  return scope || btn.closest("[data-tbl-export-area]") || document.querySelector(".adm5-main") || document.body;
}

// The file's name: the screen's title, plus what the area says it shows when
// that changes without the button being redrawn (the doctor picked in
// Pacientes de hoy: [data-export-title-part]).
function exportTitle(btn, scope) {
  var t = btn.getAttribute("data-tbl-export-title");
  if (!t) {
    var h = document.querySelector(".adm5-main h1") || document.querySelector("h1");
    t = h ? h.textContent : "";
  }
  var own = btn.getAttribute("data-tbl-export-title-part");
  var part = scope && scope.querySelector("[data-export-title-part]");
  if (own) t += " · " + own;
  else if (part && !hiddenEl(part)) t += " · " + part.textContent;
  return t.replace(/\s+/g, " ").trim();
}

function exportDay(btn, scope) {
  var holder = scope && scope.querySelector("[data-export-day]");
  return btn.getAttribute("data-tbl-export-day") || (holder && holder.getAttribute("data-export-day")) || "";
}

function exportFilename(res, fallback) {
  var cd = res.headers.get("Content-Disposition") || "";
  var m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  if (m) { try { return decodeURIComponent(m[1]); } catch (e) { /* fall through */ } }
  m = /filename="([^"]+)"/i.exec(cd);
  return m ? m[1] : fallback;
}

function exportToast(msg) {
  if (typeof window !== "undefined" && window.toast) window.toast(msg);
}

function exportFailed() {
  return T("admin.shell.export.error.invalid", null, "No se pudo armar el archivo. Recargá la página y volvé a descargar.");
}

function exportTable(btn) {
  var scope = exportScope(btn);
  var data = collectExport(scope);
  if (!data || !data.rows.length) {
    exportToast(T("admin.shell.export.empty", null, "No hay filas para descargar."));
    return Promise.resolve(null);
  }
  var body = {
    screen: window.location.pathname,
    title: exportTitle(btn, scope),
    day: exportDay(btn, scope),
    columns: data.columns, rows: data.rows, patients: data.patients
  };
  btn.disabled = true;
  return fetch("/api/admin/table-export", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (res) {
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (err) {
        exportToast(err && typeof err.error === "string" && err.code ? err.error : exportFailed());
        return null;
      });
    }
    return res.blob().then(function (blob) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = exportFilename(res, "export.csv");
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
      return a.download;
    });
  }, function () {
    exportToast(exportFailed());
    return null;
  }).then(function (name) {
    btn.disabled = false;
    return name;
  });
}

document.addEventListener("click", function (e) {
  var btn = e.target.closest ? e.target.closest("[data-tbl-export]") : null;
  if (!btn || btn.disabled) return;
  e.preventDefault();
  exportTable(btn);
});

// =====================================================================
// TEXTO RECORTADO — el completo, en el mismo lugar
// =====================================================================
// Product owner, 2026-08-05: "make the unclipped summary show on hover and tap.
// always show clipped data this way. the tooltip should preserve the
// unclipped text at the same place."
//
// Una celda recortada no se lee, y el `title` nativo la muestra ABAJO Y A LA
// DERECHA del puntero, en otra tipografía: hay que buscar dónde sigue la
// frase. Acá el texto completo se pinta ENCIMA de la celda, arrancando en el
// mismo píxel y con la misma letra, así que las palabras que ya se veían no
// se mueven — sólo aparecen las que faltaban.
//
// Vale para toda la consola sin que ninguna tabla declare nada: se detecta
// el recorte midiendo (scrollWidth/scrollHeight), no por una clase. Un `title`
// más rico que el texto (Historial: puntaje + motivos) gana, y mientras el
// globo propio está a la vista se le saca el title al elemento para que el
// navegador no dibuje el suyo encima.

var CLIP_PAD_X = 8, CLIP_PAD_Y = 5;
var clipPop = null, clipEl = null, clipTitle = null;

// Un contenedor cualquiera puede tener scrollHeight > clientHeight sin estar
// "recortado" en este sentido —una pantalla larga adentro de un wrap con
// overflow— y ahí el globo terminaba mostrando el texto ENTERO de la
// pantalla. Lo que califica es lo que el CSS declaró como recorte: una celda
// con text-overflow o un bloque con line-clamp, y nada más.
var INLINE = { SPAN: 1, B: 1, I: 1, EM: 1, STRONG: 1, A: 1, CODE: 1, SMALL: 1, ABBR: 1, TIME: 1 };

function recortado(el) {
  if (!el || el.nodeType !== 1) return false;
  var t = (el.textContent || "").trim();
  if (!t || t.length > 600) return false;
  // nada de bloques adentro: eso ya es un contenedor, no una celda de texto
  for (var i = 0; i < el.children.length; i++) {
    if (!INLINE[el.children[i].tagName]) return false;
  }
  var cs = window.getComputedStyle(el);
  var clip = cs.textOverflow === "ellipsis"
    || (cs.webkitLineClamp && cs.webkitLineClamp !== "none");
  if (!clip) return false;
  return el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
}

// El candidato es el elemento recortado más profundo bajo el puntero, sin
// salir de la fila: se mira el target y hasta 3 padres.
function candidato(target) {
  var el = target, n = 0;
  while (el && el.nodeType === 1 && n++ < 4) {
    if (el.hasAttribute && el.hasAttribute("data-no-clip-pop")) return null;
    if (el.classList && el.classList.contains("tbl-clip-pop")) return null;
    if (recortado(el)) return el;
    if (el.tagName === "TR" || el.tagName === "TABLE" || el.tagName === "BODY") return null;
    el = el.parentElement;
  }
  return null;
}

function ocultarClip() {
  if (clipEl && clipTitle != null) { clipEl.setAttribute("title", clipTitle); }
  clipTitle = null;
  clipEl = null;
  if (clipPop) clipPop.style.display = "none";
}

function mostrarClip(el) {
  if (el === clipEl) return;
  ocultarClip();
  if (!clipPop) {
    clipPop = document.createElement("div");
    clipPop.className = "tbl-clip-pop";
    clipPop.setAttribute("role", "tooltip");
    document.body.appendChild(clipPop);
  }
  var texto = (el.getAttribute("title") || el.textContent || "").trim();
  if (!texto) return;
  clipEl = el;
  // el title nativo dibujaría un segundo globo encima del propio
  if (el.hasAttribute("title")) { clipTitle = el.getAttribute("title"); el.removeAttribute("title"); }

  var cs = window.getComputedStyle(el);
  var r = el.getBoundingClientRect();
  clipPop.textContent = texto;
  clipPop.style.font = cs.font;
  clipPop.style.letterSpacing = cs.letterSpacing;
  clipPop.style.color = cs.color;
  clipPop.style.textAlign = cs.textAlign;
  clipPop.style.display = "block";
  // Arranca en el MISMO píxel que la celda: se descuenta el padding propio
  // del globo y el que ya tenía la celda.
  var left = r.left + parseFloat(cs.paddingLeft || 0) - CLIP_PAD_X;
  var top = r.top + parseFloat(cs.paddingTop || 0) - CLIP_PAD_Y;
  var anchoUtil = Math.max(160, window.innerWidth - left - 16);
  clipPop.style.maxWidth = Math.min(520, anchoUtil) + "px";
  clipPop.style.minWidth = Math.min(r.width, anchoUtil) + "px";
  clipPop.style.left = Math.max(6, left) + "px";
  clipPop.style.top = top + "px";
  // Si se pasa de la pantalla por abajo, sube lo justo.
  var alto = clipPop.getBoundingClientRect().height;
  if (top + alto > window.innerHeight - 8) {
    clipPop.style.top = Math.max(6, window.innerHeight - alto - 8) + "px";
  }
}

document.addEventListener("mouseover", function (e) {
  var el = candidato(e.target);
  if (el) mostrarClip(el);
  else if (clipEl && !clipEl.contains(e.target)) ocultarClip();
});
// Al salir de la ventana (relatedTarget nulo) se cierra; adentro lo resuelve
// el mouseover de arriba — un mouseleave por descendiente lo haría titilar.
document.addEventListener("mouseout", function (e) { if (!e.relatedTarget) ocultarClip(); });
// Tap (y click con mouse): el mismo globo, que en un teléfono no hay hover.
// Un click sobre un link/botón sigue siendo ese click y nada más.
document.addEventListener("click", function (e) {
  if (e.target.closest && e.target.closest("a,button,input,select,textarea,label,summary")) return;
  var el = candidato(e.target);
  if (el) { if (el === clipEl && clipPop && clipPop.style.display === "block") ocultarClip(); else { clipEl = null; mostrarClip(el); } }
  else ocultarClip();
});
window.addEventListener("scroll", function () { ocultarClip(); }, true);
window.addEventListener("resize", ocultarClip);
document.addEventListener("keydown", function (e) { if (e.key === "Escape") ocultarClip(); });

function init() {
  new MutationObserver(pedirBarrido).observe(document.body, { childList: true, subtree: true });
  barrer();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();

// ---- API exportada ---------------------------------------------------------
// `crear` registra la instancia para que el arrastre global y el observer
// la encuentren; `columnas` y `barrer` completan el contrato que publicaba
// el antiguo global. Los consumidores importan el módulo.
export function crear(cfg) { return registrar(crearTabla(cfg)); }
export { columnas };
export { barrer };
export { exportHtml, collectExport, exportTable };
