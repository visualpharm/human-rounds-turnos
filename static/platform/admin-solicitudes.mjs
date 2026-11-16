/* static/platform/admin-solicitudes.mjs — native requests queue renderer.

   Rediseño 2026-08-05 (design-loop, "Pinamar Admin Solicitudes Linear"): la
   pantalla dejó de ser una pila de tarjetas de ~400px de alto —diez por
   página, con paginador— y pasó a ser lo que la mesa de entrada realmente
   hace: una cola densa a la izquierda, agrupada por lo que la IA sugiere
   hacer, y UNA solicitud abierta a la derecha.

   Los tres cambios de fondo:

     1. Agrupado por sugerencia. Los dos grupos inequívocos («aprobar según
        sugerencia» y «rechazar según sugerencia») se despachan en lote desde
        la barra de selección; la atención queda para el grupo del medio, que
        es el único que pide leer la orden.
     2. Split lista + detalle. Antes había que scrollear una tarjeta entera
        para ver la siguiente; ahora entran ~20 solicitudes de un vistazo y la
        que se está resolviendo tiene su panel fijo a la derecha.
     3. Load-more en vez de paginador. Una cola no tiene páginas: tiene fondo.

   El módulo es dueño de su markup Y de su CSS (misma forma que
   calendar-grid.mjs y admin-hours-editor.js), así
   que la story de Storybook monta el código que se sirve, no una copia.

   Lo que NO vive acá: el estado, los filtros, el guardado y el cableado de
   eventos siguen en admin.html. Y sobre todo, el panel de detalle se dibuja
   como `<div class="card" data-id="…">` con los MISMOS hooks internos que la
   tarjeta vieja (.pt-select, .svc-sel, .med-sel, .turno, .turno-sel,
   .motivo-sel, [data-act]) — executePending(), poblarTurnoSel() y los
   listeners de aprobar/rechazar leen ese contrato y no se enteran del
   rediseño. Cambia la forma, no el contrato.

   Extensión 2026-08-06 (feedback del responsable del producto sobre el primer pull): Aprobadas,
   Rechazadas y Todas dejaron la tabla vieja (resolvedTable/pagerHtml, en
   admin.html) y pasan por ESTE mismo motor. Cuatro columnas nuevas —
   `estado`, `turno`, `hsi`, `resuelta_por`— cubren lo que esa tabla mostraba
   (el turno asignado, el chip de HSI, quién resolvió); `estado` reusa
   exactamente el render de `veredicto` (mismo punto, mismo texto), así que
   una fila nunca lleva las dos a la vez — el contexto (admin.html) elige cuál
   entra en `cols`. Lo que la tabla resolvía con un click DENTRO de la celda
   (marcar asistencia, ver el historial de visitas) se mudó al panel de
   detalle: `detalleResHtml()` es el equivalente de `detalleHtml()` para una
   fila ya resuelta — mismo `<aside class="sol-det card">`, sin la sección
   «Resolver» porque ya no hay nada que resolver. `render()` elige entre
   ambos con `o.detalleTipo` ('resuelta' | lo que sea → pendiente, el default
   de siempre). Los grupos de resueltas pueden plegarse (`g.colapsable`,
   `g.colapsado`, chevron en el encabezado) — los de pendientes nunca, porque
   son los que piden atención. */
function importStatic(name, nodeUrl) {
  if (typeof window === "undefined") return import(nodeUrl);
  const url = window.SANA_STATIC && window.SANA_STATIC[name];
  if (!url) throw new Error("[static] " + name + " is not in window.SANA_STATIC");
  return import(url);
}

const preferencesReady = typeof window === "undefined"
  ? Promise.resolve(null)
  : importStatic("platform/admin-preferences.mjs", new URL("./admin-preferences.mjs", import.meta.url))
    .then((module) => module.load());
const [I18n, Format] = await Promise.all([
  importStatic("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url)),
  importStatic("platform/format.mjs", new URL("./format.mjs", import.meta.url)),
]);
await preferencesReady;

function T(key, vars, fallback) {
  return I18n.t(key, vars, fallback);
}
// Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
function Tn(key, n, vars, one, other) {
  if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
  var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
  return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
}

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Mismo criterio que admin-queue.js: nunca gritar un nombre que llegó en
  // MAYÚSCULAS del padrón/HSI (Format.nombre lo normaliza; sin ese
  // módulo cargado — Storybook, tests — se devuelve tal cual).
  function nombrePropio(v) {
    return Format.nombre(v);
  }

  // Las columnas de la cola. `always` = la que identifica la fila. El resto
  // se prende y apaga desde el engranaje de SanaTable.columnas — el control
  // que el resto del panel ya usa para lo mismo (no un botón «Columnas»
  // nuevo al lado de uno viejo que hace lo mismo).
  //
  // Derive labels when requested, after the caller awaits readiness.
  // Script order cannot make an asynchronous module import synchronous.
  // El orden es el de la pantalla (handoff 2026-08-06, comentario del responsable del producto
  // «status fields first, dni - hide by default»): lo primero que se lee de
  // una fila es qué dijo la IA, porque es lo que decide si hay que abrirla.
  // El documento sigue existiendo como columna, apagado: quien lo necesita lo
  // prende desde el engranaje y su elección queda guardada.
  // `estado`, `turno`, `hsi` y `resuelta_por` son las cuatro nuevas —
  // Aprobadas/Rechazadas/Todas las usan en vez de `veredicto`/`cuando`
  // (admin.html arma el `cols` de cada contexto; nunca conviven las dos
  // familias en la misma fila). `estado` comparte el render de `veredicto`
  // punto por punto (mismo `.sol-dot` — el responsable del producto, 2026-08-06: reusar, no
  // inventar un componente nuevo) así que no hace falta un caso propio en
  // filaHtml() para pintarlo.
  function columns() { return [
    { key: "veredicto", label: T("admin.queue.solicitudes.col_lectura_ia", null, "Lectura IA"), "default": true },
    { key: "estado", label: T("admin.queue.toolbar.estado_aria", null, "Estado"), "default": true },
    { key: "paciente", label: T("admin.queue.table.paciente", null, "Paciente"), always: true },
    { key: "dni", label: T("admin.queue.solicitudes.col_documento", null, "Documento"), "default": false },
    { key: "servicio", label: T("admin.queue.table.servicio", null, "Servicio"), "default": true },
    { key: "centro", label: T("admin.queue.toolbar.centro", null, "Centro"), "default": true },
    { key: "cuando", label: T("admin.queue.solicitudes.col_recibida", null, "Recibida"), "default": true, right: true },
    { key: "turno", label: T("admin.queue.solicitudes.col_turno_motivo", null, "Turno / motivo"), "default": true },
    { key: "hsi", label: "HSI", "default": true },
    { key: "resuelta_por", label: T("admin.queue.solicitudes.col_resuelta_por", null, "Resuelta por"), "default": false }
  ]; }

  // Ancho de cada columna dentro de la grilla de la fila. La casilla y el
  // paciente son fijos-flexibles; el resto entra por su contenido real
  // (un DNI son 10 caracteres, una hora son 12).
  var ANCHO = {
    paciente: "minmax(120px,1fr)",
    dni: "96px",
    // El servicio es flexible: a 124px fijos «Ginecología y Obstetricia» se
    // cortaba a 1440px. Toma el ancho que sobra junto con el paciente.
    servicio: "minmax(160px,1.2fr)",
    // «Sampleton Hospital», «Diagnostic Center» on ONE line at 16px: at 128px
    // every second row grew to two lines. A longer name still wraps, and on a
    // narrow laptop the track gives the 48px back before the list overflows.
    centro: "minmax(128px,176px)",
    // «Orden de otra persona» (165px at 16px) plus the dot, on one line.
    // A grouped pending queue has no such column at all: the group header
    // names the reason and the paperclip follows the name (queueHtml).
    veredicto: "176px",
    estado: "128px",
    // «Dom 20 sep, 11:00» en UN renglón a 16px: con 112px se cortaba en
    // «Dom 20 sep, 11:…»; 140px es el ancho real del formato más largo
    // («Ddd DD mmm, HH:MM») a 16px.
    cuando: "140px",
    // «Sobreturno autorizado» agregado a la fecha es lo más largo que entra
    // en esta columna; un motivo más largo corta con el ellipsis del resto
    // de las celdas (.sol-cell), como cualquier otra columna de la fila.
    turno: "184px",
    // «Envío automático a HSI» with its turning icon, the longest label the HSI
    // control takes, in one line (design review 2026-11-04, C1: 108px cut it).
    hsi: "208px",
    resuelta_por: "120px"
  };

  function gridFor(cols) {
    return "44px " + cols.map(function (c) { return ANCHO[c.key] || "auto"; }).join(" ");
  }

  var css =
    // ---- la pantalla: cabecera + barra de filtros + split, sin tarjetas ----
    // El wrap del contenido pone padding y ancho máximo para formularios; la
    // cola quiere el ancho entero y sus propias líneas de pelo.
    ".adm-content-wrap:has(> #sec-solicitudes:not([style*=\"none\"]))" +
      "{max-width:none;padding:0;flex:1;min-height:0;display:flex;flex-direction:column;}" +
    "#sec-solicitudes{display:flex;flex-direction:column;flex:1;min-height:0;}" +
    "#sec-solicitudes #list{display:flex;flex-direction:column;flex:1;min-height:0;}" +
    // El split ocupa lo que queda de pantalla y scrollea por dentro: la cola y
    // el detalle son dos columnas independientes, no una página larga. Sólo
    // en pantallas donde el split existe — abajo de 1000px el panel es una
    // hoja encima y la página scrollea como cualquier otra sección.
    "@media (min-width:1001px){" +
      ".adm-shell:has(#sec-solicitudes:not([style*=\"none\"])){height:100vh;min-height:0;overflow:hidden;}" +
      ".adm-main:has(> .adm-content-wrap > #sec-solicitudes:not([style*=\"none\"])){min-height:0;}" +
      "}" +

    // The desktop controls fit on one row in the normal case, but this band
    // must remain ordinary content flow. A fixed 40px flex basis clipped the
    // now-readable filters as soon as a label wrapped or touch sizing won.
    // The list simply receives the remaining height.
    // 16px lateral: el mismo inset que .adm-topbar ahora pegada (2026-09-16),
    // para que búsqueda y filtros alineen con el título columna a columna.
    "@media (min-width:1001px){" +
      "#sec-solicitudes .adm-band{gap:8px;padding:6px 16px;min-height:40px;box-sizing:border-box;flex:0 0 auto;" +
        "flex-wrap:wrap;border-bottom:1px solid var(--border);margin:0;}" +
      "}" +
    // The filter toggle carries no rule of its own here: it is the shared
    // bare 44px icon of admin-direct.css (one button system, 2026-11-08).
    // The old #sec-solicitudes override (1-1-0) beat that 0-2-0 rule and drew
    // the only framed filter funnel of the panel.
    "@media (max-width:1000px){" +
      "#sec-solicitudes .adm-band{padding:10px 16px;margin:0;border-bottom:1px solid var(--border);}" +
      "}" +

    // ---- lo que la cola le agrega a la banda de la pantalla ----
    // El contenedor absorbe el espacio libre para que el `space-between` de
    // .adm-h1row deje «Crear turno» contra el borde derecho.
    "#adm-sol-tools{flex:1;min-width:0;display:flex;align-items:center;gap:8px;}" +
    ".adm-sol-tools-r{margin-left:auto;display:flex;align-items:center;gap:8px;}" +
    // The gear is the shared bare 44px icon of the screen header
    // (admin-direct.css, one screen header 2026-11-08).
    // El estado "on" de Agrupar ahora es el quiet-secondary compartido —
    // `.adm5-main :is(.btn.on,...)` en admin-direct.css — nunca un tinte
    // propio: la regla vieja pintaba un durazno con --accent-soft que le
    // ganaba por ID a esa regla compartida y era la única acción que se
    // veía distinta de cualquier otro botón "activo" del panel (responsable del producto,
    // 2026-09-15).

    // Derivar a otro servicio: el botón vive en la tercera columna de la fila
    // «Servicio»; el combo se despliega debajo del bloque, a ancho completo.
    ".sol-kv-a{display:flex;align-items:center;justify-content:flex-end;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" +
    ".sol-kv-a .svc-toggle{margin:0;}" +
    // Do not depend on Material Symbols for this one small action: offline
    // and privacy-restricted installations otherwise print `swap_horiz`.
    ".sol-kv-a .svc-toggle .adm-icon{position:relative;display:block;width:18px;height:14px;font-size:0;}" +
    ".sol-kv-a .svc-toggle .adm-icon:before,.sol-kv-a .svc-toggle .adm-icon:after{content:'';position:absolute;width:13px;height:2px;background:currentColor;}" +
    ".sol-kv-a .svc-toggle .adm-icon:before{top:3px;left:1px;clip-path:polygon(0 50%,4px 0,4px 2px,100% 2px,100% 0,13px 3px,100% 6px,100% 4px,4px 4px,4px 6px);}" +
    ".sol-kv-a .svc-toggle .adm-icon:after{top:9px;left:4px;clip-path:polygon(0 0,0 2px,9px 2px,9px 0,13px 3px,9px 6px,9px 4px,0 4px,0 6px,0 6px);}" +
    ".sol-deriv{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:9px;}" +
    // Plegado y sin nota no tiene nada que mostrar: sin esto deja 9px de aire.
    ".sol-deriv:has(> .svc-edit[hidden]):not(:has(.svc-nota)){display:none;}" +
    ".sol-deriv .svc-edit,.sol-deriv .cmb{flex:1;min-width:0;max-width:none;}" +
    ".sol-deriv .svc-nota{font-size:13px;color:var(--hint-ink);flex-basis:100%;}" +

    // Los campos de la IA entran en una sola columna: el panel tiene 428px,
    // y `columns:2` los partía en dos tiras de 190px.
    ".sol-det .campos{columns:1;margin-top:0;}" +
    // «Pide» (.sol-kv) y «Lo que dice la orden» (.campos) son dos sistemas de
    // etiqueta/valor distintos que caen uno debajo del otro en el mismo panel:
    // traían la etiqueta a 12,5 y a 11,5 px y la columna a 92 y a 88 px, así
    // que ni el tamaño ni el margen izquierdo de los valores coincidían.
    ".sol-det .campos .k{font-size:13px;width:92px;}" +
    ".sol-det .fixes{margin:0;}" +
    ".sol-det .ai-resumen{margin:0;font-size:13px;color:var(--hint-ink);}" +

    // ---- barra de selección: aparece SÓLO con filas tildadas ----
    // En flujo, nunca fija: una barra permanente con tres botones apagados
    // era chrome que ocupaba lugar sin decir nada.
    ".sol-selbar{display:flex;align-items:center;gap:8px;height:36px;flex:0 0 36px;" +
      "padding:0 var(--adm-gutter,16px);background:var(--accent-soft);border-bottom:1px solid var(--accent-bd);" +
      "font-size:13px;color:var(--accent-hover);}" +
    ".sol-selbar-n{font-weight:700;}" +
    ".sol-selbar .btn{height:28px;padding:0 10px;font-size:var(--adm-btn-font,14px);}" +

    // ---- "Turnos de hoy": panorama del día arriba de la cola (2026-08-27) ----
    // Tabla chica de dos columnas, sin el motor de anchos de SanaTable — es
    // un panorama de acompañamiento, no la grilla principal de la pantalla.
    ".sol-hoy{border-bottom:1px solid var(--border-soft);padding:4px 16px 14px;}" +
    ".sol-hoy-tabla{width:100%;border-collapse:collapse;font-size:13px;}" +
    ".sol-hoy-tabla td{padding:3px 10px 3px 0;color:var(--hint-ink);white-space:nowrap;}" +
    ".sol-hoy-tabla td:first-child{color:var(--text);font-weight:500;width:1%;}" +
    ".sol-hoy-tabla .cnt-numeric{text-align:right;}" +
    ".sol-hoy-link{display:inline-block;margin-top:8px;font-size:13px;color:var(--accent);text-decoration:none;}" +
    ".sol-hoy-link:hover{color:var(--accent-hover);}" +
    ".sol-hoy-vacio{margin:0;}" +

    // ---- el split ----
    ".sol-split{display:flex;flex:1;min-height:0;}" +
    // overflow-x:hidden a propósito (responsable del producto, 2026-09-15): sin esto, con el
    // panel de detalle abierto a 1440px las columnas más angostas de la
    // grilla (ancho fijo en px, no se encogen) seguían de largo por debajo
    // del panel — texto tapado a la mitad, no una elipsis. Con el eje X
    // recortado ACÁ, la elipsis de cada celda (.sol-cell/.sol-when/.sol-ch)
    // corta donde corresponde.
    ".sol-queue{flex:1;min-width:0;overflow-y:auto;overflow-x:hidden;}" +

    // ---- encabezado de grupo ----
    // Grid, no flex, y el mismo primer casillero de 44px que la fila y el
    // encabezado de columnas: el título del grupo arranca donde arranca el
    // nombre de cada fila (responsable del producto, 2026-08-06). El chevron (cuando el grupo se
    // puede plegar) vive en ESE casillero, no como ícono suelto antes del
    // título.
    // One left edge (2026-11-10): the list's side padding is the panel's
    // gutter, and what each first slot draws — the row's checkbox, the
    // group's arrow — sits on it, under the title and the search's magnifier.
    ".sol-grp{display:grid;grid-template-columns:44px 1fr auto;align-items:center;gap:10px;" +
      "height:34px;padding:0 var(--adm-gutter,16px);background:var(--bg);}" +
    // No divider lines between groups or rows (responsable del producto, 2026-11-08: "don't split
    // them with dividers"): a group is set apart by space, a row by hover.
    ".sol-row + .sol-grp,.sol-grp-more + .sol-grp{margin-top:12px;}" +
    ".sol-grp-more{display:grid;grid-template-columns:44px 1fr;gap:10px;padding:4px var(--adm-gutter,16px) 8px;}" +
    ".sol-grp-mas{justify-self:start;}" +
    ".sol-grp-tc{display:flex;align-items:center;gap:8px;min-width:0;overflow:hidden;}" +
    // El nombre del grupo se lee: 16px en el color del texto, en una línea
    // (sin contador, responsable del producto 2026-10-14). En el teléfono el nombre baja de
    // línea al lado del botón del grupo (ver el bloque de 760px).
    ".sol-grp-n{font-size:16px;font-weight:600;color:var(--text);white-space:nowrap;" +
      "overflow:hidden;text-overflow:ellipsis;}" +
    ".sol-grp-a{display:flex;align-items:center;gap:6px;}" +
    // The arrow is centred in its 24px button; the button reaches 8px outside
    // the edge so the arrow itself starts on it (the hover fill opens outward,
    // like the search's frame).
    ".sol-grp-chev{width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;" +
      "margin-left:-8px;background:none;border:0;border-radius:4px;padding:0;cursor:pointer;}" +
    ".sol-grp-chev:hover{background:var(--hover-bg);}" +
    ".sol-grp-car{width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;" +
      "border-top:5px solid var(--text-2);transition:transform .12s ease;}" +
    ".sol-grp-chev-closed .sol-grp-car{transform:rotate(-90deg);}" +
    // El botón de lote del grupo despacha las dos pilas inequívocas de un
    // click. Copia el control que ya hace ese trabajo en la barra de
    // selección — «Resolver según sugerencia» es accent lleno, «Rechazar» es
    // el contorno gris que se pone rojo bajo el puntero — con la altura, la letra y las esquinas del
    // botón del panel (un solo sistema de botones, 2026-11-08: antes 28px y
    // esquinas de 4px junto a botones de 36).
    ".sol-grp-btn.btn-ok:hover{background-image:var(--hover-fill);}" +

    // ---- encabezado de columnas y filas: UNA regla de grilla, así no pueden
    //      desalinearse (misma disciplina que admin-hours-editor.js) ----
    ".sol-cols,.sol-row{display:grid;align-items:center;gap:10px;padding:0 var(--adm-gutter,16px);}" +
    ".sol-cols{height:26px;border-bottom:1px solid var(--border-soft);font-size:13px;" +
      "color:var(--hint-ink);letter-spacing:.02em;position:sticky;top:0;background:var(--panel);z-index:1;}" +
    ".sol-ch{display:flex;align-items:center;gap:4px;min-height:24px;margin:0 -5px;padding:0 5px;" +
      "border-radius:4px;cursor:pointer;background:none;border:0;font:inherit;font-size:var(--adm-btn-font,14px);font-weight:500;letter-spacing:inherit;" +
      "color:var(--hint-ink,var(--text-2));min-width:0;max-width:100%;overflow:hidden;}" +
    ".sol-ch-t{min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}" +
    // Column names are controls (they sort): dark --hint-ink, never the
    // translucent scale; the sorted one and the hovered one go to --text.
    ".sol-ch:hover{background:var(--hover-bg);color:var(--text);}" +
    ".sol-ch-on{color:var(--text);font-weight:500;}" +
    ".sol-ch-r{justify-content:flex-end;}" +
    ".sol-sar{width:0;height:0;flex:0 0 5px;border-left:3px solid transparent;border-right:3px solid transparent;}" +
    ".sol-sar-d{border-top:4px solid currentColor;}" +
    ".sol-sar-u{border-bottom:4px solid currentColor;}" +
    ".sol-sar-off{border-top:4px solid var(--border-strong);opacity:0;}" +
    ".sol-ch:hover .sol-sar-off{opacity:1;}" +

    // Every value in a row is read, so the row is 16px in the text color (the
    // AI reading, service, center and date were 13px light gray). A center
    // name may use two lines, so the row has a minimum rather than a
    // clipping height.
    ".sol-row{min-height:40px;border:0;cursor:pointer;" +
      "background:none;width:100%;" +
      "font:inherit;font-size:16px;line-height:1.4;text-align:left;color:var(--text);}" +
    ".sol-row:hover{background:var(--hover-bg);}" +
    ".sol-row-on{background:var(--accent-soft);box-shadow:inset 2px 0 0 var(--accent);}" +
    ".sol-row-on:hover{background:var(--hover-selected);}" +
    ".sol-open{display:block;width:100%;min-width:0;min-height:24px;border:0;background:none;padding:0;text-align:left;" +
      "color:inherit;font:inherit;font-size:16px;font-weight:500;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
    ".sol-open:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}" +
    ".sol-cell{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
    // The center is the one contextual field that must remain legible in a
    // compact row; patient and service stay one line to keep scanning stable.
    ".sol-cell-centro{white-space:normal;overflow:visible;text-overflow:clip;line-height:1.25;padding:4px 0;}" +
    // A pending request with an attachment shows a paperclip, as any mail
    // inbox does; one without shows nothing (the product owner, 2026-11-08: "nobody in
    // Gmail writes 'no attachment'"). Icons8 SF Regular, drawn in the text color.
    ".sol-clip{display:inline-block;flex:0 0 20px;width:20px;height:20px;background:var(--text);" +
      "-webkit-mask:url(/static/icons/chat-attach-sf-regular.svg) center/contain no-repeat;" +
      "mask:url(/static/icons/chat-attach-sf-regular.svg) center/contain no-repeat;}" +
    // The grouped queue has no reading track: the name sits by the checkbox
    // and the fixed layouts below drop their second track (--sol-head).
    ".sol-q-clip{--sol-head:44px;}" +
    ".sol-nm .sol-clip{margin-left:8px;vertical-align:-4px;}" +
    // Con el panel de detalle abierto la cola pierde ~420px; a 1440px ya no
    // entran las 8 columnas + el panel sin recortar texto. Centro es la
    // menos necesaria en ese momento (el panel ya la repite en el bloque de
    // «Lo que dice la orden») — se apaga por CSS sólo mientras hay una
    // solicitud abierta, nunca en la elección de columnas del operador
    // (the product owner, 2026-09-15). Above 1200px the open list is as narrow as a
    // 761-1200px window, so it takes that layout: the same five tracks, DNI,
    // Centro and «Resuelta por» hidden. Hiding Centro alone left its track in
    // the inline grid: every later cell slid one track left and «Cargar en
    // HSI» ended past the list's edge (design review 2026-11-04, C1). The HSI
    // column stays, in a sixth track: it is what the desk clicks after an
    // approval, and its label wraps instead of being cut (blind review
    // 2026-11-05, C1).
    "@media (min-width:1201px){" +
      ".sol-split:has(>.sol-det.card) .sol-cols,.sol-split:has(>.sol-det.card) .sol-row{" +
        "grid-template-columns:var(--sol-head,44px 140px) minmax(150px,1fr) minmax(160px,1.2fr) 140px !important;}" +
      ".sol-split:has(>.sol-det.card):has(.sol-ch-hsi) .sol-cols,.sol-split:has(>.sol-det.card):has(.sol-ch-hsi) .sol-row{" +
        "grid-template-columns:var(--sol-head,44px 140px) minmax(0,1fr) minmax(0,1.2fr) 140px 160px !important;}" +
      ".sol-split:has(>.sol-det.card) :is(.sol-cell-dni,.sol-cell-centro,.sol-cell-resuelta_por," +
        ".sol-ch-dni,.sol-ch-centro,.sol-ch-resuelta_por){display:none;}" +
      ".sol-split:has(>.sol-det.card) .sol-cell-hsi{white-space:normal;}" +
      // Two whole lines at most: whole-pixel line boxes, and the button grows
      // with them instead of holding its one-line height.
      ".sol-split:has(>.sol-det.card) .sol-cell-hsi .hsi-sync-btn{white-space:normal;text-align:left;" +
        "line-height:18px;padding-block:5px;height:auto;}" +
    "}" +
    // The extension's hint under «Cargar en HSI» is a popover below the button:
    // the HSI cell lets it out (the column already fits the longest label).
    ".sol-cell-hsi{overflow:visible;}" +
    // El nombre y el DNI son los datos que se leen — nunca en gris claro.
    ".sol-nm{font-weight:500;color:var(--text);}" +
    ".sol-mut{color:var(--text);}" +
    ".sol-num{color:var(--text);}" +
    ".sol-when{color:var(--text);font-size:16px;text-align:right;" +
      "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
    ".sol-ck-hit{width:44px;height:44px;display:flex;align-items:center;justify-content:flex-start;}" +
    ".sol-ck{width:44px;height:44px;margin:0;accent-color:var(--accent);cursor:pointer;}" +

    // Un veredicto es un punto y una palabra: una pastilla por fila rayaría
    // la tabla entera. El margen negativo achica SÓLO el hueco entre el
    // checkbox y el punto (responsable del producto, 2026-08-06: "decrease the spacing between
    // the checkbox and the status dot") — el `gap:10px` de la grilla sigue
    // separando bien al resto de las columnas, que nadie marcó como apretadas.
    ".sol-vd{display:flex;align-items:center;gap:6px;min-width:0;margin-left:-5px;}" +
    ".sol-dot{width:7px;height:7px;border-radius:50%;flex:0 0 7px;}" +
    ".sol-d-ok{background:var(--done);}" +
    ".sol-d-warn{background:var(--warn);}" +
    ".sol-d-bad{background:var(--danger);}" +
    ".sol-d-none{background:var(--border-strong);}" +
    ".sol-vd-t{font-size:16px;color:var(--text);min-width:0;white-space:normal;line-height:1.25;overflow-wrap:break-word;}" +

    // ---- pie: cuántas se ven, y cargar más ----
    ".sol-foot{display:flex;align-items:center;gap:10px;padding:14px var(--adm-gutter,16px);}" +
    ".sol-foot-n{font-size:13px;color:var(--hint-ink);}" +

    // ---- el panel de detalle ----
    ".sol-det{width:428px;flex:0 0 428px;border-left:1px solid var(--border);overflow-y:auto;" +
      "display:flex;flex-direction:column;background:var(--panel);}" +
    // Es una .card por contrato con executePending(), pero acá no tiene nada
    // de tarjeta: ni borde, ni sombra, ni radio — y sobre todo NO es la grilla
    // de dos columnas (información | tira de la orden) que `.card:not(.card--solo)`
    // le impone, que repartía la cabecera y «Pide» en dos columnas de 428px y
    // dejaba un hueco vertical antes de «Resolver».
    ".sol-det.card{display:flex;flex-direction:column;margin:0;border:0;" +
      "border-left:1px solid var(--border);border-radius:0;box-shadow:none;padding:0;" +
      "grid-template-columns:none;}" +
    ".sol-det.card > *{grid-column:auto;grid-row:auto;}" +
    // On a desktop the page scrolls, not the queue: a request picked far
    // down the list painted its detail at the top of the page, out of view,
    // and the right column looked empty (TASK-53, 26.10, 1280 px). The
    // column stays in view under the sticky top bar (admin-direct.css,
    // 44px) and scrolls on its own when taller than the window.
    "@media (min-width:1201px){" +
      ".sol-det{position:sticky;top:44px;align-self:flex-start;max-height:calc(100vh - 44px);}" +
    "}" +
    ".sol-det-h{padding:14px 16px 12px;border-bottom:1px solid var(--border-soft);}" +
    // The name row: «Name, 73», the contact as icons (Format.contactoIconoHtml,
    // as in Pacientes) and the ✕ that closes the request, at the right end —
    // no separate «Volver a la lista» band (critical review 2026-11-08).
    ".sol-det-top{display:flex;align-items:center;gap:4px;min-width:0;}" +
    ".sol-det-n{flex:1 1 auto;min-width:0;font-size:18px;font-weight:700;color:var(--text);letter-spacing:-.01em;}" +
    ".sol-det.card .sol-det-x{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;" +
      "width:32px;height:32px;min-width:0;min-height:0;margin:-4px -6px -4px 4px;padding:0;border:0;border-radius:8px;" +
      "background:none;color:var(--hint-ink);cursor:pointer;}" +
    // The icon is the shared close button .hr-x (human-rounds.css).
    ".sol-det.card .sol-det-x:hover{background:var(--hover-bg);color:var(--text);}" +
    "@media (max-width:640px),(pointer:coarse){.sol-det.card .sol-det-x{width:44px;height:44px;margin:-10px -12px -10px 0;}}" +
    // DNI, age, phone; the center under the service; the field names of a
    // resolved request: key information, dark --hint-ink, never the
    // translucent scale the humanrounds theme paints light grey (2026-11-07).
    ".sol-det-m{margin-top:3px;font-size:16px;color:var(--hint-ink,var(--text-2));}" +
    ".sol-det-s{display:flex;align-items:center;gap:6px;margin-top:9px;}" +
    // The AI notice right under the reading it labels (2026-11-08, A3).
    ".sol-det .sol-det-ai{margin:8px 0 0;}" +
    ".sol-b{padding:14px 16px;border-bottom:1px solid var(--border-soft);}" +
    ".sol-lbl{font-size:16px;font-weight:600;color:var(--hint-ink,var(--text-2));letter-spacing:0;margin-bottom:7px;}" +
    // 8px entre renglones (el diseño), no 5px: con 5 los pares etiqueta/valor
    // se leían como un bloque compacto en vez de como filas separadas.
    ".sol-kv{display:grid;grid-template-columns:92px minmax(0,1fr) auto;gap:8px 10px;font-size:16px;align-items:baseline;}" +
    ".sol-kv-k{color:var(--hint-ink,var(--text-2));}" +
    ".sol-kv-v{color:var(--text);min-width:0;overflow-wrap:anywhere;white-space:normal;}" +
    ".sol-kv-free{color:var(--text);line-height:1.5;white-space:normal;}" +
    ".sol-pide-line1{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;font-size:16px;font-weight:600;color:var(--text);}" +
    ".sol-pide-line2{margin-top:3px;font-size:16px;color:var(--hint-ink,var(--text-2));}" +
    ".sol-pide-line3{margin-top:3px;font-size:15px;color:var(--text);}" +
    ".sol-caso{white-space:pre-line;overflow-wrap:anywhere;}" +
    // «Su caso» como conversación (2026-11-07): la urgencia con su punto,
    // qué quiere en negrita, el porqué y los datos; el chat se abre debajo.
    ".sol-caso-box{font-size:16px;color:var(--text);line-height:1.45;overflow-wrap:anywhere;}" +
    ".sol-caso-box p{margin:0 0 6px;}" +
    ".sol-caso-urg{display:flex;align-items:center;gap:6px;font-weight:600;}" +
    ".sol-caso-quiere{font-weight:600;}" +
    ".sol-caso-hechos{margin:0 0 8px;padding-left:20px;}" +
    ".sol-caso-hechos li{margin:0 0 2px;}" +
    ".sol-caso-ver{margin-top:4px;}" +
    ".sol-caso-chat{margin-top:10px;padding-top:10px;border-top:1px solid var(--border-soft);}" +
    ".sol-caso-msg{margin:0 0 8px;white-space:pre-line;}" +
    ".sol-caso-msg b{display:block;font-size:14px;color:var(--hint-ink,var(--text-2));font-weight:600;}" +

    // La foto de la orden ocupa el ancho del panel; el zoom sigue siendo el
    // mismo botón de siempre (.orden-btn).
    ".sol-shot{position:relative;width:100%;height:172px;border:1px solid var(--border);" +
      "border-radius:5px;overflow:hidden;background:var(--bg);}" +
    ".sol-shot .orden-prev{position:absolute;inset:0;}" +

    // ---- resolver: al final del panel, en flujo, nunca fijo ----
    ".sol-res{padding:14px 16px 24px;}" +
    ".sol-res-row{display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap;}" +
    // Los controles se reparten el ancho REAL del panel. Sin esto heredan los
    // 160/170px fijos que .act-approve-controls les daba en la tarjeta vieja
    // —pensados para una fila de 1240px— y el botón «Aprobar» se iba fuera de
    // la pantalla.
    ".sol-res-row > .cmb,.sol-res-row > .adm-sel,.sol-res-row > .turno-sel," +
      ".sol-res-row > .motivo-sel{flex:1 1 96px;width:auto;min-width:0;max-width:none;}" +
    ".sol-res-row > .motivo-otro,.sol-res-row > .turno-free{flex:1 1 100%;width:100%;min-width:0;}" +
    ".sol-res-sep{margin-top:14px;}" +
    // «Contactar»: the third answer the reference-deployment counterpart's desk gives a request (2026-10:
    // «nos contactábamos con las personas»). There is no staff-to-patient
    // channel in the product, so it opens the patient's WhatsApp (or a call,
    // or mail) with the request named; the request stays pending.
    ".sol-res-contactar{margin-top:14px;}" +
    ".sol-res-contactar .sol-contactar{min-height:40px;display:inline-flex;align-items:center;}" +
    "@media(pointer:coarse){.sol-res-contactar .sol-contactar{min-height:44px;}}" +
    ".sol-res .btn{flex:0 0 auto;font-size:var(--adm-btn-font,14px);min-height:var(--adm-btn-h,36px);padding:0 12px;}" +
    "@media(pointer:coarse){.sol-res .btn{min-height:44px;}}" +

    // Los cuatro controles de Resolver son UN set y tienen que verse igual: el
    // combo de médico, los dos selects (turno y motivo) y los dos campos
    // libres que destapan «Otro horario…» / «Otro motivo…». Traían tres
    // recetas distintas —el combo a 13px, radio 9, borde suave y fondo bg-2;
    // los selects a 12.5px, radio 8, borde fuerte— y los campos libres NINGUNA:
    // su única regla vivía en `.acts`, la tarjeta que ya no se renderiza, así
    // que salían con la fuente y el borde gris del navegador. Gana la receta
    // del select, que es el control que más se repite en el panel.
    ".sol-res-row .cmb-in{font-size:16px;min-height:40px;padding:8px 32px 8px 10px;" +
      "line-height:normal;border-color:var(--border-strong);border-radius:8px;" +
      "background-color:var(--panel);}" +
    ".sol-res-row .adm-sel{font-size:16px;min-height:40px;padding-top:8px;padding-bottom:8px;line-height:normal;}" +
    ".sol-res-row input[type=datetime-local],.sol-res-row .motivo-otro{" +
      "font:inherit;font-size:16px;min-height:40px;padding:8px 10px;box-sizing:border-box;" +
      "border:1px solid var(--border-strong);border-radius:8px;" +
      "background-color:var(--panel);color:var(--text);}" +
    ".sol-res-row input[type=datetime-local]:focus,.sol-res-row .motivo-otro:focus{" +
      "outline:2px solid var(--accent-soft);border-color:var(--accent);}" +
    "@media(pointer:coarse){.sol-res-row .cmb-in,.sol-res-row .adm-sel,.sol-res-row input[type=datetime-local],.sol-res-row .motivo-otro{min-height:44px;}}" +
    // The detail pane stays 428px wide even on a desktop, so approving uses two
    // deliberate rows at every viewport: doctor first, then preference, slot and
    // action. The slot receives the flexible column instead of being one of three
    // equally squeezed controls.
    // One left edge (2026-11-07): the doctor and the slot start at the same
    // x. The slot used to sit in a second column behind the patient's time-of-
    // day preference, and with no preference an empty 24px placeholder shifted
    // it right; the preference now reads as its own line above the slot.
    ".sol-res-aprobar{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;}" +
    ".sol-res-aprobar>.cmb{grid-column:1/-1;}" +
    ".sol-res-aprobar>.pref-slot{display:none;}" +
    ".sol-res-aprobar>.pref-txt{grid-column:1/-1;font-size:16px;color:var(--hint-ink,var(--text-2));white-space:normal;}" +
    // Sin médicos del servicio: la pista va debajo del combo vacío (2026-10-23).
    ".sol-res-aprobar>.med-vacio{grid-column:1/-1;margin:0;font-size:16px;color:var(--hint-ink,var(--text-2));}" +
    ".sol-res-aprobar>.med-vacio[hidden]{display:none;}" +
    ".sol-res-aprobar>.turno-sel{grid-column:1;min-width:0;width:100%;}" +
    ".sol-res-aprobar>.btn{grid-column:2;}" +
    ".sol-res-aprobar>.btn:disabled{opacity:.45;cursor:not-allowed;}" +
    ".sol-res-aprobar>.turno-free{grid-column:1/-1;}" +
    ".sol-act-err{grid-column:1/-1;margin:2px 0 0;color:var(--danger);}" +

    // Aprobar/Rechazar: por defecto OUTLINE, sólido SÓLO en la decisión que la
    // IA sugiere (handoff 2026-07-17: «nunca un botón negro sólido por
    // default»). Estas reglas también estaban escopeadas a `.acts`, así que al
    // morir la tarjeta «Aprobar» volvía al `.btn-ok` genérico —negro, el de
    // Guardar— y `.btn-suggest` dejaba de significar nada. Rechazar no
    // sugerido va gris, no rojo (responsable del producto, 2026-07-20).
    ".sol-res .btn-ok{background:var(--panel);color:var(--text);border:1px solid var(--border-strong);}" +
    ".sol-res .btn-ok:hover{background:var(--hover-bg-soft);}" +
    ".sol-res .btn-ok.btn-suggest{background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);border-color:var(--btn-bg,var(--accent));}" +
    ".sol-res .btn-ok.btn-suggest:hover{background-image:var(--hover-fill);}" +
    // Written to outrank the panel's own `.adm5-main .btn.btn-no` (red ink,
    // red outline): with the bare `.sol-res .btn-no` the grey lost, and
    // «Rechazar», the group's «Rechazar todas» and the selection bar's
    // «Rechazar» wore red. Red is for the suggested rejection only.
    // The bare `.sol-res` twin serves a card mounted outside the panel's main.
    ".sol-res .btn-no,.adm5-main .sol-res .btn.btn-no,.adm5-main .btn.sol-grp-btn.btn-no,.adm5-main .sol-selbar .btn.btn-no{" +
      "background:var(--panel);color:var(--hint-ink);border:1px solid var(--border-strong);}" +
    ".sol-res .btn-no:hover,.adm5-main .sol-res .btn.btn-no:hover,.adm5-main .btn.sol-grp-btn.btn-no:hover,.adm5-main .sol-selbar .btn.btn-no:hover{" +
      "background:var(--danger-soft);color:var(--danger);border-color:var(--danger-soft);}" +
    ".sol-res .btn-no.btn-suggest,.adm5-main .sol-res .btn.btn-no.btn-suggest{background:var(--danger);color:#fff;border-color:var(--danger);}" +
    ".sol-res .btn-no.btn-suggest:hover,.adm5-main .sol-res .btn.btn-no.btn-suggest:hover{background-image:var(--hover-fill);color:#fff;}" +
    ".sol-res .overturn-ctl{display:flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;color:var(--hint-ink,var(--text-2));}" +
    ".sol-res .overturn-ctl[hidden]{display:none;}" +
    // Las dos ramas vienen completas y la que la IA NO sugiere se apaga —
    // nunca se esconde, y nunca se pinta un bloque de fondo debajo (responsable del producto,
    // 2026-07-22). Misma decisión que en la tarjeta vieja, en la forma nueva.
    ".card--sug-aprobar .sol-res-sep,.card--sug-rechazar .sol-res-aprobar{opacity:.58;}" +
    ".card--sug-aprobar .sol-res-sep:hover,.card--sug-aprobar .sol-res-sep:focus-within," +
      ".card--sug-rechazar .sol-res-aprobar:hover,.card--sug-rechazar .sol-res-aprobar:focus-within{opacity:1;}" +

    // Vacíos: la cola sin nada, y el panel sin solicitud elegida.
    ".sol-empty{padding:24px 16px;color:var(--text);font-size:16px;}" +
    ".sol-queue-empty .adm-empty-state{padding:24px 16px;}" +
    ".sol-det-empty{padding:28px 16px;color:var(--hint-ink);font-size:13px;}" +
    // No request open: no empty 428px column saying "pick one" — the list takes
    // the full width, so its last column is never cut (the product owner, 2026-09-15).
    ".sol-det.sol-det-vacia{display:none;}" +

    // ---- angosto: el detalle deja de ser una columna y pasa a ser una hoja
    //      encima de la cola. Una columna de 428px al lado de otra no entra
    //      en un teléfono, y partir la fila en dos líneas la vuelve ilegible.
    "@media (max-width:1200px){" +
      ".sol-det{position:fixed;inset:0;width:auto;flex:none;z-index:40;border-left:0;" +
        "box-shadow:var(--shadow-pop);}" +
      ".sol-det.card{border-left:0;}" +
      // Under the open request's sheet the page itself does not scroll: the
      // sheet is the page (the product owner, 2026-11-08: the open request dragged the
      // whole list along).
      "html:has(.sol-det.card[data-id]),body:has(.sol-det.card[data-id]){overflow:hidden;}" +
      ".sol-det-vacia{display:none;}" +
      // El agrupado y el engranaje de columnas no entran en la línea del
      // título con el botón del menú, y en un teléfono la cola ya muestra
      // sólo dos columnas.
      ".adm-sol-tools-r{display:none;}" +
      "#adm-sol-tools{display:none;}" +
      // The sheet scrolls; the name row with its ✕ stays on top.
      ".sol-det.card .sol-det-h{position:sticky;top:0;z-index:1;background:var(--panel);}" +
      "}" +
    // En un teléfono la cola muestra lectura de la IA + paciente y nada más:
    // el resto vive en el detalle, a un toque. Las columnas ocultas se apagan
    // por CSS y no por el registro de columnas: la elección del operador para
    // el escritorio no se pisa por haber abierto el panel en el celular.
    //
    // El orden de las dos que quedan es el mismo que en el escritorio (la
    // lectura primero), así que la grilla le da a la lectura un ancho fijo y
    // deja el resto para el nombre: la grilla es posicional, y con el ancho
    // flexible en la primera ranura el nombre —que es lo que identifica la
    // fila— terminaba exprimido en 108px.
    //
    // La fecha y hora (cuándo llegó una pendiente, qué turno recibió una
    // aprobada) se ve a TODO ancho y en un solo renglón (TASK-45, el responsable del producto
    // 25.10: «on the phone the time of the turno is not visible… date and time on one
    // line»): en la laptop es la última ranura de la grilla, con el ancho
    // del formato más largo; en el teléfono baja a un segundo renglón de la
    // fila, debajo de la lectura y el nombre, con todo el ancho que queda.
    "@media (max-width:1200px) and (min-width:761px){" +
      ".sol-cols,.sol-row{grid-template-columns:var(--sol-head,44px 140px) minmax(150px,1fr) minmax(160px,1.2fr) 140px !important;}" +
      ".sol-cell-dni,.sol-cell-centro,.sol-cell-hsi,.sol-cell-resuelta_por," +
        ".sol-ch-dni,.sol-ch-centro,.sol-ch-hsi,.sol-ch-resuelta_por{display:none;}" +
    "}" +
    "@media (max-width:760px){" +
      ".sol-cols,.sol-row{grid-template-columns:var(--sol-head,44px 140px) minmax(0,1fr) !important;gap:8px;}" +
      ".sol-grp,.sol-grp-more{gap:8px;}" +
      // In the GROUPED queue the only column left on a phone is the name: a
      // header saying «Paciente» over a list of names tells nothing, so it
      // goes. The flat and resolved lists keep theirs: it is where they are
      // sorted. The group's title wraps instead of ending in dots.
      ".sol-q-clip .sol-cols{display:none;}" +
      ".sol-grp{height:auto;min-height:44px;}" +
      ".sol-grp-tc{overflow:visible;}" +
      ".sol-grp-n{white-space:normal;overflow:visible;line-height:1.25;}" +
      ".sol-row{min-height:44px;row-gap:0;}" +
      ".sol-row>:first-child{grid-row:1 / span 2;align-self:start;}" +
      ".sol-row>.sol-cell-cuando,.sol-row>.sol-cell-turno{grid-column:2 / -1;grid-row:2;text-align:left;" +
        "margin:-8px 0 0 8px;padding-bottom:8px;line-height:1.4;}" +
      ".sol-row>.sol-cell-cuando:empty,.sol-row>.sol-cell-turno:empty{display:none;}" +
      // In the grouped queue the second track is the name: the date starts where it starts.
      ".sol-q-clip .sol-row>.sol-cell-cuando{margin-left:0;}" +
      ".sol-ch,.sol-grp-btn,.sol-open{min-height:44px;}" +
      ".sol-ck-hit{width:44px;height:44px;}" +
      "#q{min-height:44px;}" +
      ".sol-cell-dni,.sol-cell-servicio,.sol-cell-centro,.sol-cell-hsi,.sol-cell-resuelta_por," +
        ".sol-ch-dni,.sol-ch-servicio,.sol-ch-centro,.sol-ch-cuando," +
        ".sol-ch-turno,.sol-ch-hsi,.sol-ch-resuelta_por{display:none;}" +
      ".sol-selbar{flex-wrap:wrap;height:auto;flex:0 0 auto;padding:8px var(--adm-gutter,16px);gap:6px;}" +
      "}";

  // ---- encabezado de columnas -------------------------------------------

  function colsHtml(cols, sort) {
    var out = '<div class="sol-cols" style="grid-template-columns:' + gridFor(cols) + '"><div></div>';
    cols.forEach(function (c) {
      var on = sort && sort.key === c.key;
      var arrow = "sol-sar " + (on ? (sort.dir > 0 ? "sol-sar-u" : "sol-sar-d") : "sol-sar-off");
      var cls = "sol-ch sol-ch-" + c.key + (c.right ? " sol-ch-r" : "") + (on ? " sol-ch-on" : "");
      var label = esc(c.label);
      var ariaLabel = esc(T("admin.queue.table.ordenar_por", { label: c.label }, "Ordenar por " + c.label));
      var caret = '<span class="' + arrow + '"></span>';
      var labelSpan = '<span class="sol-ch-t">' + label + "</span>";
      out += '<button type="button" class="' + cls + '" data-sol-sort="' + esc(c.key) + '"'
        + ' aria-label="' + ariaLabel + '">'
        + (c.right ? caret + labelSpan : labelSpan + caret) + "</button>";
    });
    return out + "</div>";
  }

  // ---- una fila ----------------------------------------------------------

  function filaHtml(r, cols) {
    var cls = "sol-row" + (r.abierta ? " sol-row-on" : "");
    var sinLectura = !cols.some(function (c) { return c.key === "veredicto" || c.key === "estado"; });
    var out = '<div class="' + cls + '" data-sol-row="' + esc(r.id) + '"'
      + ' style="grid-template-columns:' + gridFor(cols) + '">';
    // El primer casillero SIEMPRE existe, tenga o no checkbox adentro — es lo
    // que mantiene alineados verticalmente el encabezado de grupo, el
    // encabezado de columnas y cada fila (responsable del producto, 2026-08-06). Una fila
    // resuelta no se tilda (el lote sólo despacha pendientes), así que ahí
    // el casillero queda vacío en vez de faltar.
    out += r.checkbox === false
      ? "<div></div>"
      : '<label class="sol-ck-hit"><input type="checkbox" class="sol-ck" data-pending-select="' + esc(r.id) + '"'
        + (r.tildada ? " checked" : "") + ' aria-label="'
        + esc(T("admin.queue.solicitudes.aria_seleccionar", { who: r.paciente }, "Seleccionar la solicitud de " + r.paciente))
        + '"></label>';
    cols.forEach(function (c) {
      var k = c.key;
      if ((k === "veredicto" || k === "estado") && r.adjunto != null) {
        // A pending row: a paperclip when the patient attached something,
        // like any mail inbox, and the AI's reason only where it changes the
        // decision (the product owner, 2026-11-08). No dot: the group already says it.
        out += '<div class="sol-vd sol-cell-' + k + '">'
          + (r.adjunto ? '<span class="sol-clip" role="img" aria-label="'
            + esc(T("admin.queue.solicitudes.con_adjunto", null, "Con orden adjunta")) + '"></span>' : "")
          + (r.veredicto ? '<span class="sol-vd-t">' + esc(r.veredicto) + "</span>" : "") + "</div>";
      } else if (k === "veredicto" || k === "estado") {
        // Mismo punto, mismo texto — la columna que corresponda (nunca las
        // dos juntas) lee el mismo par r.dot/r.veredicto.
        out += '<div class="sol-vd sol-cell-' + k + '"><span class="sol-dot ' + esc(r.dot) + '"></span>'
          + '<span class="sol-vd-t">' + esc(r.veredicto) + "</span></div>";
      } else if (k === "cuando") {
        out += '<div class="sol-when sol-cell-cuando">' + esc(r.cuando) + "</div>";
      } else if (k === "turno") {
        out += '<div class="sol-cell sol-cell-turno sol-mut">' + esc(r.turno || "") + "</div>";
      } else if (k === "hsi") {
        out += '<div class="sol-cell sol-cell-hsi">' + (r.hsiHtml || "—") + "</div>";
      } else if (k === "paciente") {
        // No reading column (grouped queue): the paperclip follows the name.
        var clip = r.adjunto && sinLectura ? T("admin.queue.solicitudes.con_adjunto", null, "Con orden adjunta") : "";
        out += '<button type="button" class="sol-cell sol-cell-paciente sol-nm sol-open"'
          + ' aria-label="' + esc(T("admin.queue.solicitudes.aria_abrir", { who: r.paciente },
            "Abrir la solicitud de " + r.paciente) + (clip ? " · " + clip : "")) + '">' + esc(r.paciente || "")
          + (clip ? '<span class="sol-clip" aria-hidden="true"></span>' : "") + "</button>";
      } else {
        var extra = k === "paciente" ? " sol-nm" : k === "dni" ? " sol-num" : " sol-mut";
        out += '<div class="sol-cell sol-cell-' + k + extra + '">' + esc(r[k] || "") + "</div>";
      }
    });
    return out + "</div>";
  }

  // ---- un grupo ----------------------------------------------------------

  // `g.bulk` ('aprobar' | 'rechazar') convierte el costado derecho del grupo
  // en el botón que despacha la pila entera. El grupo del medio no lo tiene:
  // es el que hay que leer a mano, y ahí no existe un lote correcto.
  //
  // Antes esa esquina era una frase de estado del grupo («La orden coincide
  // con el pedido»), que decía UNA vez lo que la fila ya dice en cada
  // renglón desde que «Lectura de la IA» es la primera columna.

  function grupoHtml(g, cols) {
    var accion = "";
    if (g.bulk) {
      var cls = "btn sol-grp-btn " + (g.bulk === "rechazar" ? "btn-no" : "btn-ok");
      accion = '<button type="button" class="' + cls + '" data-sol-grupo="' + esc(g.bulk) + '"'
        + (g.motivo ? ' data-sol-motivo="' + esc(g.motivo) + '"' : "") + ">"
        + esc(g.bulkLabel) + "</button>";
    }
    // El plegado es SÓLO para los grupos de resueltas dentro de «Todas»
    // (Aprobadas/Rechazadas/Canceladas, responsable del producto 2026-08-06): pendientes nunca se
    // pliega, es lo que pide atención. El chevron ocupa el mismo casillero de
    // 24px que el checkbox de la fila —no un ícono suelto a la izquierda del
    // título— así el título arranca en la MISMA columna en la que arranca el
    // contenido de cada fila (el bug que el responsable del producto marcó: el encabezado de grupo
    // no alineaba con nada).
    var chevron = g.colapsable
      ? '<button type="button" class="sol-grp-chev' + (g.colapsado ? " sol-grp-chev-closed" : "") + '"'
        + ' data-sol-grp-toggle="' + esc(g.id) + '" aria-expanded="' + (g.colapsado ? "false" : "true") + '"'
        + ' aria-label="' + esc(T("admin.queue.solicitudes.grupo_toggle", { titulo: g.titulo },
            "Mostrar u ocultar " + g.titulo)) + '"><span class="sol-grp-car"></span></button>'
      : "<div></div>";
    var filas = g.colapsado ? "" : g.filas.map(function (r) { return filaHtml(r, cols); }).join("");
    // The rest of a long group is one click away, inside the group, on the
    // same left edge as the rows' content (the product owner, 2026-11-08).
    if (!g.colapsado && g.mas > 0) {
      var lote = g.masLote || g.mas;
      filas += '<div class="sol-grp-more"><div></div><button type="button" class="btn sol-grp-mas" data-sol-grp-mas="'
        + esc(g.id) + '">' + esc(T("admin.queue.solicitudes.cargar_mas", { lote: lote }, "Cargar " + lote + " más"))
        + "</button></div>";
    }
    return '<div class="sol-grp">' + chevron
      + '<span class="sol-grp-tc"><span class="sol-grp-n">' + esc(g.titulo) + "</span></span>"
      + '<span class="sol-grp-a">' + accion + "</span></div>"
      + filas;
  }

  // ---- la cola entera ----------------------------------------------------
  //
  // `pie`: {mostrando, total} — sin páginas, sólo cuántas se ven de cuántas y
  // el botón que trae el próximo lote.

  function queueHtml(o) {
    var cols = o.cols || columns();
    // The grouped pending queue names the reason in each group's title, so
    // it has no reading column: the name sits by the checkbox and the
    // paperclip follows it (ui review 2026-11-08: an empty track before
    // every name without an order).
    if (o.soloClip) cols = cols.filter(function (c) { return c.key !== "veredicto" && c.key !== "estado"; });
    var vacio = o.vacio || T("admin.queue.solicitudes.vacio", null, "No hay solicitudes pendientes.");
    // Nothing listed → no header row to sort by and no columns to name
    // (progressive disclosure, product owner 2026-10-26): only the empty state.
    if (!o.grupos.length) {
      return '<div class="sol-queue sol-queue-empty">'
        + (o.vacioHtml || '<p class="sol-empty">' + esc(vacio) + "</p>") + "</div>";
    }
    var cuerpo = o.grupos.map(function (g) { return grupoHtml(g, cols); }).join("");
    var pie = "";
    if (o.pie && o.pie.total && o.pie.mostrando < o.pie.total) {
      var lote = o.pie.lote || 100;
      var mostrandoTxt = esc(T("admin.queue.solicitudes.mostrando",
        { mostrando: o.pie.mostrando, total: o.pie.total },
        "Mostrando " + o.pie.mostrando + " de " + o.pie.total));
      var masTxt = esc(T("admin.queue.solicitudes.cargar_mas", { lote: lote }, "Cargar " + lote + " más"));
      pie = '<div class="sol-foot"><span class="sol-foot-n">' + mostrandoTxt + "</span>"
        + (o.pie.mostrando < o.pie.total
          ? '<button type="button" class="btn" data-sol-mas>' + masTxt + "</button>"
          : "") + "</div>";
    }
    return '<div class="sol-queue' + (o.soloClip ? " sol-q-clip" : "") + '">'
      + colsHtml(cols, o.sort) + cuerpo + pie + "</div>";
  }

  // ---- barra de selección ------------------------------------------------

  function selbarHtml(n, conSugerencia) {
    if (!n) return "";
    var contado = Tn("admin.queue.solicitudes.seleccionada", n, null, "{n} seleccionada", "{n} seleccionadas");
    var hint = T("admin.queue.solicitudes.resolver_hint", null,
      "Aprueba las sugeridas para aprobar y rechaza las sugeridas para rechazar"
      + " (con su motivo pre-elegido); las dudosas quedan como están.");
    return '<div class="sol-selbar" data-bulk-bar>'
      + '<span class="sol-selbar-n">' + esc(contado) + "</span>"
      + '<button class="btn btn-ok" type="button" data-bulk-action="sugerencia"'
      + (conSugerencia ? "" : " disabled")
      + ' title="' + esc(hint) + '">'
      + esc(T("admin.queue.solicitudes.resolver_boton", null, "Resolver según sugerencia")) + "</button>"
      + '<button class="btn btn-ok" type="button" data-bulk-action="aprobar">'
      + esc(T("admin.queue.solicitudes.aprobar", null, "Aprobar")) + "</button>"
      + '<button class="btn btn-no" type="button" data-bulk-action="rechazar">'
      + esc(T("admin.queue.solicitudes.rechazar", null, "Rechazar")) + "</button>"
      + "</div>";
  }

  // ---- el panel de detalle ----------------------------------------------
  //
  // Recibe fragmentos ya armados por admin.html (la orden, los campos de la
  // IA, los arreglos, los controles de aprobar y rechazar): esa lógica es la
  // misma que antes y no se mudó. Acá se decide dónde va cada cosa.

  // «Name, 73», the contact icons (already escaped HTML from Format) and the ✕.
  function filaNombreHtml(nombre, contactoHtml) {
    var cerrar = T("admin.queue.overlay.cerrar", null, "Cerrar");
    return '<div class="sol-det-top"><div class="sol-det-n">' + esc(nombre) + "</div>"
      + (contactoHtml || "")
      + '<button type="button" class="sol-det-x hr-x" data-sol-cerrar aria-label="' + esc(cerrar)
      + '" title="' + esc(cerrar) + '"></button></div>';
  }

  // The AI notice (ai.notice.clinical, Decreto 742/2026) rides with the AI
  // reading of an open request — right under its verdict — and only when
  // the request has one (the reading, «Su caso», what the order says), the
  // way the day queue does (2026-11-08 review A3). The list's own caption
  // stays behind the sheet on a phone.
  function avisoIAHtml(d) {
    if (!(d.veredicto || d.casoHtml || d.camposHtml)) return "";
    return '<p class="adm-ai-notice sol-det-ai" data-ai-notice-inline="clinical">'
      + esc(T("ai.notice.clinical", null, "Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención."))
      + "</p>";
  }

  function detalleHtml(d) {
    if (!d) {
      // El panel vacío sólo tiene sentido como columna al costado de la cola.
      // En un teléfono es una hoja a pantalla completa, y una hoja vacía tapa
      // la lista entera: ahí directamente no existe.
      return '<aside class="sol-det sol-det-vacia"><p class="sol-det-empty">'
        + esc(T("admin.queue.solicitudes.detalle_vacio", null, "Elegí una solicitud de la lista para verla acá."))
        + "</p></aside>";
    }
    var h = '<aside class="sol-det card card--sug-' + esc(d.sug) + '" data-id="' + esc(d.id)
      + '" data-sugerencia="' + esc(d.sug) + '">';

    h += '<div class="sol-det-h">'
      + filaNombreHtml(d.nombre, d.contactoHtml)
      + (d.meta ? '<div class="sol-det-m">' + d.meta + "</div>" : "")
      + (d.veredicto
        ? '<div class="sol-det-s"><span class="sol-dot ' + esc(d.dot) + '"></span>'
          + '<span class="sol-vd-t">' + esc(d.veredicto) + "</span></div>"
        : "")
      + avisoIAHtml(d)
      + "</div>";

    // Business card: line 1 = service + derivar button, line 2 = center (the received date lives only in the list)
    // No labels, no "Pide" title block
    h += '<div class="sol-b">'
      + '<div class="sol-pide-line1">' + (d.pide[0] ? d.pide[0].v : '') + (d.pide[0] && d.pide[0].aside ? ' ' + d.pide[0].aside : '') + '</div>'
      + (d.pide[1] && d.pide[1].v ? '<div class="sol-pide-line2">' + d.pide[1].v + '</div>' : '')
      // Every extra line: the slot the patient asked for and/or their own
      // «Contanos tu caso» text (the reference-deployment counterpart, 2026-10-17) — each its own line.
      + d.pide.slice(2).map(function (p) { return p ? '<div class="sol-pide-line3">' + p.v + '</div>' : ''; }).join('')
      + (d.derivacion || "") + "</div>";
    h += casoBlock(d);

    // La foto va DEBAJO de lo que la IA extrajo (responsable del producto, handoff 2026-08-06:
    // «this must go below extracted properties»). Quien resuelve lee primero
    // los campos ya leídos y sólo baja a la imagen cuando algo no cierra; con
    // la foto arriba, 172px de orden empujaban los datos fuera de la vista.
    if (d.camposHtml) {
      h += '<div class="sol-b"><div class="sol-lbl">'
        + esc(T("admin.queue.solicitudes.lo_que_dice_la_orden", null, "Lo que dice la orden"))
        + '</div>' + d.camposHtml + "</div>";
    }
    if (d.practicas) {
      h += '<div class="sol-b"><div class="sol-lbl">'
        + esc(T("admin.queue.solicitudes.practicas_pedidas", null, "Prácticas pedidas")) + '</div>'
        + '<div class="sol-kv-v sol-kv-free">' + esc(d.practicas) + "</div></div>";
    }
    if (d.ordenHtml) {
      h += '<div class="sol-b sol-shot-block">'
        + '<div class="sol-shot">' + d.ordenHtml + "</div></div>";
    }
    if (d.arreglosHtml) {
      h += '<div class="sol-b"><div class="sol-lbl">'
        + esc(T("admin.queue.solicitudes.que_le_podes_pedir", null, "Qué le podés pedir"))
        + '</div>' + d.arreglosHtml + "</div>";
    }

    h += '<div class="sol-res">'
      + '<div class="sol-res-row sol-res-aprobar">' + d.aprobarHtml + "</div>"
      + (d.sobreturnoHtml || "")
      + '<div class="sol-res-row sol-res-sep">' + d.rechazarHtml + "</div>"
      + (d.contactarHtml ? '<div class="sol-res-row sol-res-contactar">' + d.contactarHtml + "</div>" : "")
      + "</div>";

    return h + "</aside>";
  }

  // ---- el panel de detalle de una fila YA RESUELTA -----------------------
  //
  // El equivalente de detalleHtml() para Aprobadas/Rechazadas/Todas: mismo
  // `<aside class="sol-det card">`, sin la sección «Resolver» (ya se
  // resolvió) y sin los bloques de lectura de la IA (ya no hace falta
  // releerla para decidir). Lo que la tabla vieja resolvía adentro de una
  // celda —marcar asistencia, abrir el historial de visitas— admin.html lo
  // arma en `d.asistenciaHtml` con las MISMAS funciones de siempre
  // (asistenciaCell/visitasCell): acá sólo se ubica.

  // «Su caso» (2026-11-07): la síntesis de la conversación con el paciente y
  // el botón que abre el chat entero; admin-app.js arma `d.casoHtml`.
  function casoBlock(d) {
    if (!d.casoHtml) return "";
    return '<div class="sol-b sol-caso-b"><div class="sol-lbl">'
      + esc(T("admin.queue.caso.titulo", null, "Su caso")) + '</div>'
      + d.casoHtml + "</div>";
  }

  function detalleResHtml(d) {
    if (!d) return detalleHtml(null);
    var h = '<aside class="sol-det card" data-id="' + esc(d.id) + '">';
    h += '<div class="sol-det-h">'
      + filaNombreHtml(d.nombre, "")
      + '<div class="sol-det-m">' + esc(d.meta) + "</div>"
      + '<div class="sol-det-s"><span class="sol-dot ' + esc(d.estadoDot) + '"></span>'
      + '<span class="sol-vd-t">' + esc(d.estadoTexto) + "</span></div>"
      + "</div>";
    h += '<div class="sol-b"><div class="sol-kv">'
      + (d.kv || []).map(function (kv) {
        return '<div class="sol-kv-k">' + esc(kv.k) + '</div><div class="sol-kv-v">' + kv.v
          + '</div><div class="sol-kv-a"></div>';
      }).join("")
      + "</div></div>";
    h += casoBlock(d);
    if (d.asistenciaHtml) {
      h += '<div class="sol-b"><div class="sol-lbl">'
        + esc(T("admin.queue.solicitudes.col_visita", null, "Visita")) + '</div>'
        + d.asistenciaHtml + "</div>";
    }
    return h + "</aside>";
  }

  // ---- "Turnos de hoy": el panorama del día, al lado de la cola ----------
  //
  // Reunión hospital 2026-08-27 (contraparte técnica, probando el panel): aprobar o
  // redirigir una solicitud a ciegas de cómo viene el día es peor decisión
  // que con la agenda a la vista — "acá en pacientes hoy este listado
  // aparezca acá… sí, es útil". Mismos datos que «Pacientes de hoy»
  // (`/api/admin/day-queue`, ya scoped por rol y por centro — no es un
  // endpoint nuevo), resumidos: un médico por fila, cuántos turnos tiene y
  // cuántos huecos libres le quedan. Sin asistencia ni overlay — para eso
  // ya está #cola, esto es sólo el panorama para decidir.
  function hoyResumenHtml(grupos) {
    var conTurnos = (grupos || []).filter(function (g) { return g.turnos && g.turnos.length; });
    if (!conTurnos.length) {
      return '<p class="cnt-hint sol-hoy-vacio">'
        + esc(T("admin.queue.solicitudes.today_empty", null, "No hay turnos aprobados para hoy.")) + "</p>";
    }
    var filas = conTurnos.slice().sort(function (a, b) {
      return (nombrePropio(a.medico) || "").localeCompare(nombrePropio(b.medico) || "");
    }).map(function (g) {
      var n = g.turnos.length;
      var libres = (g.libres || []).length;
      var nombre = esc(nombrePropio(g.medico) || T("admin.queue.group.sin_medico", null, "Sin médico asignado"));
      var totalTxt = Tn("admin.queue.group.turnos_count", n, null, "{n} turno", "{n} turnos");
      var libresTxt = libres
        ? " · " + Tn("admin.queue.solicitudes.today_libres", libres, null, "{n} libre", "{n} libres")
        : "";
      return "<tr><td>" + nombre + "</td><td class=\"cnt-numeric\">" + esc(totalTxt + libresTxt) + "</td></tr>";
    }).join("");
    return '<table class="sol-hoy-tabla"><tbody>' + filas + "</tbody></table>"
      + '<a class="sol-hoy-link" href="/admin/cola">'
      + esc(T("admin.queue.solicitudes.today_ver_cola", null, "Ver el detalle en Pacientes de hoy")) + "</a>";
  }

  // ---- la pantalla entera ------------------------------------------------
  //
  // `o.detalleTipo === 'resuelta'` dibuja detalleResHtml(); cualquier otro
  // valor (incluido "no vino") dibuja detalleHtml(), el de siempre — así una
  // story vieja que no conoce `detalleTipo` sigue funcionando igual.

  function render(o) {
    var det = o.detalleTipo === "resuelta" ? detalleResHtml(o.detalle) : detalleHtml(o.detalle);
    return selbarHtml(o.seleccionadas, o.conSugerencia)
      + '<div class="sol-split">' + queueHtml(o) + det + "</div>";
  }

  // In the panel CSS is injected by the real module; the Storybook story
  // mounts this exported string itself.
  if (typeof document !== "undefined" && document.head && !document.getElementById("admin-solicitudes-css")) {
    var tag = document.createElement("style");
    tag.id = "admin-solicitudes-css";
    tag.textContent = css;
    document.head.appendChild(tag);
  }

export { css, columns, render, queueHtml, colsHtml, grupoHtml, filaHtml,
  selbarHtml, detalleHtml, detalleResHtml, hoyResumenHtml };
