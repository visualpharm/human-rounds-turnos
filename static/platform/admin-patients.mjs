/* admin-patients.mjs — native lifecycle-owned controller for the direct
 * /admin/pacientes roster (list, "Nuevo paciente", patient detail, archive),
 * the general-staff twin of the doctor-scoped collection API consumers.
 *
 * Import is inert. The template (owned by root) boots an explicit host and
 * owns page-exit disposal. Every mount owns its reads, mutations, listeners,
 * timers and table owners; disposing or mounting over the same host aborts
 * in-flight work and retires every callback.
 *
 * Route ownership is tracked independently of component lifetime: list,
 * detail and new-patient each take a fresh route generation, and a fetch,
 * body parse, session confirmation, error or render/mutation continuation
 * from an older route can never toast, redirect, repaint or mutate a newer
 * one (the three reproduced races of the classic controller: stale list
 * success over a rendered detail; a late detail failure over the new-patient
 * form; a late detail failure over another patient's rendered detail).
 * Pending search/list/detail/load-more/mutation continuations are all
 * invalidated by a route switch, and an old request's completion can never
 * unlock a newer owner's busy state.
 *
 * Dependencies are the real shared modules (session/i18n/dates/format/
 * preferences/table/account-form), awaited before the first route.
 * The birth-date parser is account-form's parseFlexibleDate — the single
 * shared implementation; this module ships no second parser.
 *
 * Data contract (unchanged, admin_panel/admin_patients_router.py):
 *   GET    /api/admin/patients[?q=&offset=&limit=]
 *          -> {items:[…], total, offset, limit, modo?, medicos?}
 *   GET    /api/admin/patients/<dni> -> {item, cobertura_principal, ranking,
 *          dependientes, a_cargo_de, ver_accesos}
 *   GET    /api/admin/patients/<dni>/access-log -> {items:[{dia, hora, usuario,
 *          rol, accion, objeto, permitido}], retencion_dias}  (admin only)
 *   POST   /api/admin/patients            -> {ok, item, password_temporal?}
 *   PUT    /api/admin/patients/<dni>       -> {ok, item}
 *   DELETE /api/admin/patients/<dni>       -> {ok, item}  (PUT activo:true restores)
 *   POST   /api/admin/patients/<dni>/dependents            -> {ok, …}
 *   DELETE /api/admin/patients/<dni>/dependents/<dni_dep>  -> {ok, …}
 * HTTP 200 is not itself an acknowledgement: a mutation body must carry
 * {ok:true, item} (plus password_temporal when it exists) before it toasts,
 * redirects or repaints. The solo_admin clinical-history endpoint stays
 * untouched server-side; this screen has never called it and does not start.
 */

const mounts = new WeakMap();

export function mount(host, options) {
  if (!host) throw new Error("Patients host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};

  var disposed = false;
  var routeGen = 0;          // bumps on every list/detail/new switch
  var searchTimer = null;    // 300ms search debounce
  var listReq = null;        // current list request owner (route + supersede)
  var primary = null;        // current route's primary fetch controller
  var loadingMore = null;    // in-flight "Cargar más" owner
  var flight = null;         // in-flight mutation owner (single-flight)
  var dependentRefresh = null;
  var ANCHOS = null;         // SanaTable.crear — column widths owner
  var COLPICK = null;        // SanaTable.columnas — visible columns owner
  var ownedStyle = null;

  var Session = null;
  var I18n = null;
  var Dates = null;
  var Format = null;
  var Prefs = null;
  var Table = null;
  var Account = null;
  var Filters = null;        // admin-filter-controls — the shared disclosure rule

  // Per-mount list state (the classic controller's module globals, scoped to
  // one mounted screen so a remount starts clean).
  var Q = "";
  var MODO = "recientes";
  var ITEMS = [];
  var TOTAL = 0;
  // How long the unsearched list is (the server's total for the registry
  // with no search): the size Filters.FILTER_MIN_ROWS is judged
  // on, so typing a search never makes the filters jump away.
  var LIST_ROWS = null;
  var PAGE = 200;
  var ORDEN = { key: "nombre", dir: 1 };
  var FILTROS = { estado: "", banda: "", cobertura: "" };
  var PREFS_READY = false;
  var TURNOS_FICHA = [];     // los turnos de la ficha abierta (acciones por fila)
  var DEPS = [];             // dependientes of the open detail
  var depEditando = null;    // DNI del dependiente en edición (null = alta)
  var formActivo = true;     // ¿la ficha abierta está activa? (archivo/restaura)
  var fichaDni = null;       // DNI de la ficha abierta (registro de accesos)
  var FICHA_PACIENTE = null; // quién es la ficha abierta (Reprogramar lo lleva al turno nuevo)

  function alive() { return !disposed && host.isConnected !== false; }
  function ownsRoute(gen) { return alive() && gen === routeGen; }

  function doc() { return (typeof window !== "undefined" && window.document) || globalThis.document; }
  function loc() { return (typeof window !== "undefined" && window.location) || globalThis.location; }
  function hist() { return (typeof window !== "undefined" && window.history) || globalThis.history; }
  function ask(msg) {
    var fn = (typeof window !== "undefined" && window.confirm) || globalThis.confirm;
    return typeof fn === "function" ? fn(msg) : false;
  }
  function toast(msg) { var fn = (typeof window !== "undefined" && window.toast) || globalThis.toast; if (fn) fn(msg); }

  // The toolbar's one 44px height is the shared list row rule in
  // admin-direct.css (2026-11-06); only this screen's widths stay here.
  function installStyle() {
    ownedStyle = doc().createElement('style');
    ownedStyle.textContent = `
      #sec-pacientes .claims{margin:0 0 16px;}
      #sec-pacientes .claims h2{margin:0 0 8px;font-size:1rem;}
      #sec-pacientes .claims ul{list-style:none;margin:0;padding:0;}
      #sec-pacientes .claims .claim-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 0;}
      #sec-pacientes .claims .claim-row>span{flex:1 1 24ch;}
      #sec-pacientes .cnt-toolbar-filters{flex-wrap:wrap;max-width:100%;}
      #sec-pacientes .cnt-toolbar-filters .cnt-search{flex:0 0 27ch;min-width:27ch;max-width:100%;}
      #sec-pacientes .pat-table th{white-space:normal;text-overflow:clip;}
      /* Sin overflow-wrap:anywhere en desktop: achica el min-content de la
         celda, la medición natural de la columna colapsa y el motor la fija
         en su piso — "Sin cobertura" quedaba en dos renglones. El anywhere
         sólo en la pila (<640px), donde la tarjeta envuelve libre.
         Email NO entra en este wrap (responsable del producto, 2026-09-15): una dirección normal
         no tiene dónde partir (sin espacio ni guión) y terminaba cortada a
         lo bruto sin puntos suspensivos. Se deja en la elipsis+globo por
         defecto de .tbl-anchos — table-design: recorte real + tooltip.
         El servicio tampoco envuelve en la compu (2026-11-05): «Ginecología
         y Obstetricia» en dos o tres renglones hacía una fila más alta que
         las demás; recorta con elipsis y el globo. */
      /* A column the engine drops (width 0, see DROP) leaves the grid: its
         cells go too, so nothing of it is drawn or measured past the edge. */
      @media(min-width:641px){
        /* The engine measures each column's content with the table at its
           natural width (anchos(): medirClase); at 100% every column came out
           wider than its text and Paciente took 344 px for 221 of name. */
        #pat-table.pat-measuring .pat-table{table-layout:auto!important;width:auto!important;}
        /* One right edge (the product owner 04.11): the last column ends where the title
           button and the column gear end, as the first starts on the left one. */
        #sec-pacientes .pat-table :is(th,td):last-child,#sec-pacientes .pat-appts td:last-child{padding-right:0;}
        /* With no padding there, the last header's resize grip drew its line
           across the last letter («Turnos»); the table's edge is no column
           boundary, the previous grip sizes that column (as the queue's
           filler column does). */
        #sec-pacientes .pat-table th:last-child .tbl-res{display:none;}
        #sec-pacientes .pat-table.pat-hide-estado [data-col="estado"],#sec-pacientes .pat-table.pat-hide-servicio [data-col="servicio"],#sec-pacientes .pat-table.pat-hide-turnos [data-col="turnos"],#sec-pacientes .pat-table.pat-hide-medico [data-col="medico"],#sec-pacientes .pat-table.pat-hide-dni [data-col="dni"],#sec-pacientes .pat-table.pat-hide-creado [data-col="creado"],#sec-pacientes .pat-table.pat-hide-localidad [data-col="localidad"],#sec-pacientes .pat-table.pat-hide-ultimo [data-col="ultimo"],#sec-pacientes .pat-table.pat-hide-sexo [data-col="sexo"],#sec-pacientes .pat-table.pat-hide-nacimiento [data-col="nacimiento"]{display:none;}
      }
      #sec-pacientes .pat-table td[data-label]::before{color:var(--hint-ink,var(--text-2));text-transform:none;letter-spacing:normal;}
      #sec-pacientes .pat-table tbody td{border-bottom:0;}
      /* La persona es UNA línea, «Nombre Apellido, 48» (responsable del producto, 2026-11-02 y
         03): el DNI es su propia columna. */
      #sec-pacientes .pat-table .pat-person,#sec-pacientes #pat-deps .pat-person{display:block;}
      #sec-pacientes .pat-table td[data-col="nombre"]::before{display:none;}
      /* Asistencia: tres barras que crecen, llenas según el nivel. */
      /* El responsable del producto 2026-11-04: chicas, del tamaño de los demás iconos, pegadas al
         final del nombre y con el pie sobre la línea del texto: sin texto
         propio, la línea base de un flex es el borde de abajo de su primer
         hijo, que es el de todas (align-items:flex-end). */
      .pat-bars{display:inline-flex;align-items:flex-end;gap:2px;height:14px;vertical-align:baseline;margin-left:8px;}
      .pat-bars i{display:block;width:4px;border-radius:1px;background:var(--border-strong,#c9ccd1);}
      .pat-bars i:nth-child(1){height:6px;}.pat-bars i:nth-child(2){height:10px;}.pat-bars i:nth-child(3){height:14px;}
      #sec-pacientes .pat-table td[data-col="ranking"] .pat-bars{margin-left:0;}
      #sec-pacientes .pat-nameline{display:block;}
      #sec-pacientes .pat-docs{display:flex;white-space:nowrap;overflow:hidden;}
      #sec-pacientes .pat-doc,#sec-pacientes .pat-docs-more{flex:none;}
      #sec-pacientes .pat-docs-more{padding-left:.25em;}
      #sec-pacientes .pat-doc:first-child{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;}
      #sec-pacientes .pat-docs-mide .pat-doc:first-child{flex-shrink:0;}
      #sec-pacientes .pat-table .pat-nameline .pat-person{display:inline;}
      #sec-pacientes .pat-head-name .pat-bars{margin-left:10px;}
      #sec-pacientes .pat-table .pat-nameline .pat-bars{vertical-align:-1px;}
      /* El contador de turnos es una pastilla en la primera fila del paciente. */
      #sec-pacientes .pat-turnos-n{display:inline-block;min-width:24px;padding:1px 8px;border-radius:999px;background:var(--chip-bg,#eef0ee);color:var(--text);font-weight:600;font-size:14px;line-height:1.4;text-align:center;box-sizing:border-box;}
      .pat-bars--good i.on{background:var(--done);}.pat-bars--warn i.on{background:var(--warn);}.pat-bars--bad i.on{background:var(--danger);}
      /* Acciones de la ficha («Agregar persona a cargo», «Ver el registro»,
         «Descargar sus datos»): botones de tamaño normal, no links de 13px.
         Las dos de «Quién abrió» van en una fila, también a 390px. */
      #sec-pacientes .pat-act{min-height:44px;font-size:var(--adm-btn-font,14px);white-space:nowrap;}
      #sec-pacientes .pat-acc-acts{gap:10px;}
      #sec-pacientes #pac-dep-add{margin:12px 0 0;}
      #sec-pacientes .pat-deps--bare{margin-top:24px;}
      #sec-pacientes .pat-deps--bare #pac-dep-add{margin-top:0;}
      #sec-pacientes .pat-deps-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:0 0 10px;}
      #sec-pacientes .pat-deps-head .pat-deps-h{flex:1 1 auto;margin:0;}
      #sec-pacientes .pat-deps-head #pac-dep-add{margin:0;}
      /* Progressive disclosure: while the new-person form is open it replaces
         the button that opened it. */
      #sec-pacientes #pat-deps[data-form="new"] #pac-dep-add{display:none;}
      /* «Quién abrió sus datos» a 390px: sin envolver, un usuario largo y
         «Resumen del paciente» empujaban el «Negado» fuera de la pantalla. */
      @media(max-width:640px){
        #sec-pacientes #pat-accesos .cnt-table td{white-space:normal;overflow-wrap:anywhere;}
      }
      @media(max-width:768px){
        #sec-pacientes .cnt-toolbar,#sec-pacientes>#pat-modo-hint{margin-left:var(--adm-gutter);margin-right:var(--adm-gutter);}
        #sec-pacientes .pat-table tr{padding-left:var(--adm-gutter);padding-right:var(--adm-gutter);box-sizing:border-box;}
      }
      /* Tablet (2026-11-05): the filters keep the width of what they say and
         share the search's line; the search takes what is left. They used to
         grow from an 11rem base, so a filter that wrapped alone stretched
         across the whole line (602-760 px for «Cobertura») and with three the
         column gear fell to a third line. The box grows from 0, so the gear
         always stays on the first line. The phone keeps the shared «Filtros»
         toggle (admin-direct.css, max-width:640px). */
      @media(min-width:641px) and (max-width:1024px){
        #sec-pacientes .cnt-toolbar{align-items:flex-start;}
        #sec-pacientes .cnt-toolbar-filters{display:flex;flex-wrap:wrap;flex:1 1 0;min-width:0;width:auto;container:pat-filtros / inline-size;}
        #sec-pacientes .cnt-toolbar-filters .cnt-search{flex:1 1 240px;width:auto;min-width:0;}
        #sec-pacientes .cnt-toolbar-filters .adm-sel{flex:0 0 auto;width:var(--control-fit-width,auto);min-width:var(--control-fit-width,max-content);grid-column:auto;}
      }
      /* Three filters beside a 240 px search need about 600 px; in a
         narrower box the search takes its own line and the three the next,
         not two and one. */
      @container pat-filtros (max-width:599px){
        #sec-pacientes .cnt-toolbar-filters:has(.adm-sel:not([hidden]) ~ .adm-sel:not([hidden]) ~ .adm-sel:not([hidden])) .cnt-search{flex-basis:100%;}
      }
      @media(max-width:640px){
        #sec-pacientes .pat-table td[data-col="email"]{overflow-wrap:anywhere;}
        #sec-pacientes .pat-table td{text-align:left;}
        #sec-pacientes .pat-table td:empty{display:none;}
      }
      /* Phone card density (the product owner review, 2026-09-15): the stacked card keeps
         the person (name, age) and — on a further line — the facts the desk
         actually acts on: the contact icons, the doctor, the attendance bars
         when they are not full. A normal coverage/status reads as noise and
         disappears. */
      @media(max-width:640px){
        #sec-pacientes .pat-table .usr-badge{font-size:16px;}
        #sec-pacientes .pat-table td[data-label]::before{font-size:16px;}
        #sec-pacientes .pat-table td[data-col="estado"]:has(.chip-activo){display:none;}
        /* The facts line is spaced, not dotted: the shared " · " separator is
           keyed on a hidden earlier cell (phone, email), so the first visible
           fact started with a stray dot. Same size for every fact. */
        #sec-pacientes .pat-table td[data-col]:not([data-col="nombre"]){font-size:14px;line-height:1.4;color:var(--text);white-space:nowrap;display:inline-block;vertical-align:middle;}
        /* The card is a wrapping row: each fact is one unit that moves to the next
           line whole (a cell that overflowed was clipped at the edge, "Turnos 3"). */
        #sec-pacientes .pat-table tbody tr{display:flex;flex-wrap:wrap;align-items:center;column-gap:14px;}
        /* Line 1: the name (with its attendance bars) and, at the right edge,
           the Turnos pill (the product owner, 2026-11-04: no «Turnos N» repeated in every
           card). */
        #sec-pacientes .pat-table td[data-col="nombre"]{flex:1 1 calc(100% - 64px);min-width:0;}
        #sec-pacientes .pat-table td[data-col="turnos"]{order:0;margin-left:auto;}
        /* Line 2: DNI, contact icons, attendance column; the doctor (and
           service) take a line of their own below. */
        #sec-pacientes .pat-table td[data-col="dni"]{order:1;}
        #sec-pacientes .pat-table td[data-col="contacto"]{order:2;}
        #sec-pacientes .pat-table td[data-col="ranking"]{order:3;}
        /* Line 3 (2026-11-05): service and doctor share ONE line, spaced like
           line 2 — each on its own line, and a long service wrapping, made
           cards of 73, 92, 93 and 113 px in one list. The service is cut
           with an ellipsis past 60 %; the doctor cell shows the names that
           fit and «+N» (ajustarMedicos). */
        #sec-pacientes .pat-table tbody tr::after{content:"";order:4;flex:0 0 100%;height:0;}
        #sec-pacientes .pat-table td[data-col="servicio"]{order:5;flex:0 1 auto;min-width:0;max-width:60%;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;}
        #sec-pacientes .pat-table td[data-col="medico"]{order:6;flex:1 1 0;min-width:0;box-sizing:border-box;}
        #sec-pacientes .pat-table td[data-col]:not([data-col="nombre"])::before{content:none;}
        /* An empty fact is no item: the inline-block above outweighed the
           td:empty rule, so a patient without a service kept a 0 px cell and
           its column-gap pushed the doctors 14 px off the card's left edge. */
        #sec-pacientes .pat-table td[data-col]:not([data-col="nombre"]):empty{display:none;}
      }
      /* Toolbar (phone, the product owner review 2026-09-15): the column-gear has nothing
         to configure in stacked-card mode, and "Nuevo paciente" moves next
         to the title (templates/admin/pacientes.html's title_band, class
         .pat-title-new) instead of sharing a row with the gear. */
      /* a.btn.pat-title-new (element+2 classes) outweighs admin-direct.css's
         ".adm5-main :is(.btn,…)" rule (2 classes) that sets display:inline-
         flex on every .btn — a plain ".pat-title-new{display:none}" lost
         that specificity fight and the button stayed visible on desktop
         too (caught in the 1440px screenshot, 2026-09-15). */
      /* The product owner 2026-11-02: the title and "Nuevo paciente" share ONE row at
         every width (button at the right edge, like "Crear turno" in
         Solicitudes); the toolbar keeps no copy of it. */
      a.btn.pat-title-new{display:inline-flex;margin-left:auto;}
      /* Two auto margins in one flex row split the free space and leave the
         button floating mid-row: the bell that follows it keeps none, and the
         phone's full-width title (admin-direct.css) shares the row instead. */
      a.btn.pat-title-new + .alert-bell-wrap{margin-left:0;}
      .adm-h1row>.adm-h1:has(+ a.btn.pat-title-new){flex:1 1 0;min-width:0;}
      /* The product owner 2026-11-04: the title row aligns on the text baseline (the title
         and the button label share one line of type), the bell stays centred. */
      .adm-h1row:has(> a.btn.pat-title-new){align-items:baseline;}
      .adm-h1row:has(> a.btn.pat-title-new) > .alert-bell-wrap{align-self:center;}
      /* The ficha / new-patient form hides the shared h1 and brings its own;
         the title-row button goes with it. */
      #adm-h1[style*="none"] ~ a.btn.pat-title-new{display:none;}
      /* The product owner 2026-11-04: an open ficha has no «Pacientes» line above it (the
         breadcrumb already says it): with the title gone the band is an empty
         strip, so it goes too — unless the alert bell lives in it. */
      .adm-topbar:has(#adm-h1[style*="none"]):not(:has(.alert-bell:not([hidden]))){display:none;}
      @media(max-width:640px){
        #sec-pacientes .cnt-toolbar-right .cnt-gear-btn{display:none;}
        /* With the gear gone the right group is empty: it is a flex item and its
           gap was the empty band between the search and the first card. */
        #sec-pacientes .cnt-toolbar-right{display:none;}
      }
      /* Turnos de la ficha en el teléfono: cada turno es un bloque (cuándo,
         servicio, dónde, estado) en vez de cuatro columnas que se salen. */
      @media(max-width:640px){
        #sec-pacientes .pat-appts thead{display:none;}
        #sec-pacientes .pat-appts,
        #sec-pacientes .pat-appts tbody{display:block;}
        #sec-pacientes .pat-appts tr{display:block;padding:10px 0;border-bottom:1px solid var(--line, rgba(0,0,0,.12));}
        #sec-pacientes .pat-appts td{display:block;padding:1px 0;border:0;}
        /* Servicio y centro largos bajan de renglón en vez de salirse. */
        #sec-pacientes .pat-appts td.pat-appt-main{white-space:normal;min-width:0;overflow-wrap:anywhere;}
        /* Personas a cargo en el teléfono: una tarjeta por persona que envuelve,
           no una tabla de cinco columnas que se sale del borde derecho. */
        #sec-pacientes #pat-deps .cnt-table-wrap{overflow:visible;}
        #sec-pacientes #pat-deps .cnt-table,#sec-pacientes #pat-deps .cnt-table tbody{display:block;}
        #sec-pacientes #pat-deps .cnt-table thead{display:none;}
        #sec-pacientes #pat-deps .cnt-table tr{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 14px;padding:10px 0;border-bottom:1px solid var(--line, rgba(0,0,0,.12));}
        #sec-pacientes #pat-deps .cnt-table td{display:block;padding:0;border:0;white-space:normal;min-width:0;overflow-wrap:anywhere;}
        #sec-pacientes #pat-deps .cnt-table td:first-child{flex:1 0 100%;}
        #sec-pacientes #pat-deps .cnt-table td:empty,#sec-pacientes #pat-deps .cnt-table td:has(>.cnt-muted){display:none;}
        /* The actions line keeps its height with or without buttons, so every
           card of the list is the same height (the product owner 2026-11-04). */
        #sec-pacientes .pat-appts td.pat-appt-acc{padding-top:8px;min-height:52px;box-sizing:border-box;}
      }
      /* Desktop: a row with Confirmar / Cancelar / Atendió / No vino is no taller
         than one without them — every row is as high as the buttons need. */
      @media(min-width:641px){
        #sec-pacientes .pat-appts td{padding-top:8px;padding-bottom:7px;height:60px;box-sizing:border-box;vertical-align:middle;}
        /* The cell's own line (the strut) would add a pixel under the buttons: it has none, the buttons carry their line. */
        #sec-pacientes .pat-appts td.pat-appt-acc{line-height:0;white-space:nowrap;}
        #sec-pacientes .pat-appts td.pat-appt-acc .btn{min-height:44px;vertical-align:middle;line-height:1.25;}
        /* The buttons stand next to the turno they act on (2026-11-05): the
           text column is as wide as its widest first line and the buttons
           follow it, one column for every row, instead of 600-850 px away at
           the right end of a 1440 screen. The second line (service · centre)
           does not widen the column: it is cut with an ellipsis, whole in its
           title. */
        #sec-pacientes .pat-appts td.pat-appt-main{width:1%;}
        #sec-pacientes .pat-appts .pat-appt-acc{text-align:left;}
        #sec-pacientes .pat-appts .pat-appt-l2{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;width:0;min-width:100%;}
      }
      /* Ficha: la persona en UNA línea (Nombre Apellido, 48 · DNI chico ·
         iconos · barras · médico) y «Editar datos» pegado a la derecha. En el
         teléfono el botón queda a la derecha del nombre y el resto baja. */
      #sec-pacientes .pat-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin:0 0 16px;}
      #sec-pacientes .pat-head-name{margin:0;flex:0 1 auto;min-width:0;font-size:24px;line-height:1.25;font-weight:600;color:var(--text);}
      #sec-pacientes .pat-head-dni{font-size:15px;color:var(--text);}
      #sec-pacientes .pat-head-meta{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;min-width:0;}
      #sec-pacientes .pat-head-info{display:inline-flex;align-items:center;align-self:center;gap:0;}
      /* The icons' touch area (44 on the phone, 32 here) is empty around a
         20 px glyph: without this the DNI stood 28-40 px from the phone icon
         while the rest of the line keeps 16 (2026-11-05). */
      #sec-pacientes .pat-head-meta>.pat-head-dni+.pat-head-info{margin-left:-12px;}
      /* Un turno: «Hoy 9:00» y el médico, grandes y oscuros, en una línea; el
         estado al principio (punto de color + palabra) y comparte la línea si
         entra; debajo, servicio y centro en gris oscuro. */
      #sec-pacientes .pat-appt-l1{display:flex;flex-wrap:nowrap;align-items:baseline;gap:2px 14px;min-width:0;overflow:hidden;}
      #sec-pacientes .pat-appt-l1 .pat-appt-who{flex:0 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
      #sec-pacientes .pat-appt-l1.f1{gap:2px 10px;}
      #sec-pacientes .pat-appt-l1.f2 .pat-appt-who{font-size:16px;}
      #sec-pacientes .pat-appt-l1.f2 .pat-appt-st{font-size:13px;}
      #sec-pacientes .pat-appt-l1.f2{gap:2px 8px;}
      #sec-pacientes .pat-appt-st{display:inline-flex;align-items:baseline;gap:6px;font-size:14px;font-weight:600;color:var(--text);white-space:nowrap;}
      #sec-pacientes .pat-appt-st::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--border-strong,#9aa09a);align-self:center;flex:0 0 auto;}
      #sec-pacientes .pat-appt-st--ok::before{background:var(--done);}
      #sec-pacientes .pat-appt-st--warn::before{background:var(--warn);}
      #sec-pacientes .pat-appt-st--bad::before{background:var(--danger);}
      #sec-pacientes .pat-appt-who{font-size:17px;line-height:1.35;font-weight:600;color:var(--text);}
      #sec-pacientes .pat-appt-when{margin-right:4px;}
      #sec-pacientes .pat-appt-l2{font-size:14px;line-height:1.4;color:var(--hint-ink,#4a443d);margin-top:2px;}
      #sec-pacientes .pat-head-edit{margin-left:auto;}
      #sec-pacientes .pat-appt-acc{text-align:right;}
      #sec-pacientes .pat-appt-acc .btn{white-space:nowrap;}
      #sec-pacientes .pat-appt-acc .btn+.btn{margin-left:8px;}
      #sec-pacientes .pat-appt-ask{display:block;font-weight:600;margin:0 0 6px;}
      #sec-pacientes #pac-datos{margin-top:24px;}
      #sec-pacientes #pac-datos[hidden]{display:none;}
      /* Phone (2026-11-05): the name has the whole first line — next to
         «Editar datos» 31 of 50 names broke in two at 390 —, and the button
         goes to the right end of the DNI line below it. */
      @media(max-width:640px){
        #sec-pacientes .pat-head-name{flex:1 1 100%;}
        #sec-pacientes .pat-head-meta{order:2;flex:0 1 auto;}
        #sec-pacientes .pat-head-edit{margin-left:auto;order:3;}
        /* The last icon's empty touch margin counts as gap: without it an
           8-digit DNI line plus the button came 3 px over 390 and the button
           stood alone on a line of its own. */
        #sec-pacientes .pat-head-meta>.pat-head-info:last-child{margin-right:-12px;}
        #sec-pacientes .pat-appt-acc{white-space:normal;text-align:left;}
        #sec-pacientes .pat-appt-acc .btn{margin:0 8px 0 0;}
      }
    `;
    doc().head.appendChild(ownedStyle);
  }

  function $(sel, root) { return (root || host).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || host).querySelectorAll(sel)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
  // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
  function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
  function isObject(value) { return !!value && typeof value === "object" && !Array.isArray(value); }

  // ---- fetch + session boundary ---------------------------------------------

  function fetchOutcome(path, opts) {
    opts = Object.assign({ credentials: "same-origin" }, opts || {});
    return fetch(path, opts).then(function (r) {
      return r.json().then(
        function (j) { return { ok: r.ok, status: r.status, json: j, response: r }; },
        function () { return { ok: r.ok, status: r.status, json: null, response: r }; }
      );
    }).catch(function (err) {
      if (err && err.name === "AbortError") return { aborted: true, ok: false, status: 0, json: null, response: null };
      return { ok: false, status: 0, json: null, response: null };
    });
  }

  // 401/403 cross the session boundary through Session.confirmar, sharing the
  // route's own currency so an owner that expires mid-confirmation can never
  // navigate or repaint for a route it no longer owns.
  function classify(r, gen, requestCurrent) {
    var current = function () { return ownsRoute(gen) && (!requestCurrent || requestCurrent()); };
    if (!current()) return Promise.resolve(false);
    if (r.aborted) return Promise.resolve(false);
    if (r.status !== 401 && r.status !== 403) return Promise.resolve(current());
    return Session.confirmar(r.response, r.json, current).then(function (transitioned) {
      if (!current()) return false;
      if (transitioned) {
        if (alive()) {
          host.innerHTML = "";
          dispose();
        }
        return false;
      }
      return current();
    });
  }

  function api(path, gen, opts, requestCurrent) {
    return fetchOutcome(path, opts).then(function (r) {
      return classify(r, gen, requestCurrent).then(function (go) {
        if (!go) throw new Error("stale");
        return r;
      });
    });
  }

  function mutationAck(j) { return isObject(j) && j.ok === true && isObject(j.item); }

  // ---- formatters (single shared implementations) -----------------------------

  // El DNI con puntos de miles vive en format.mjs (Format.dni) — la única
  // implementación, compartida con el resto del panel y el sitio.
  function fmtDni(dni) { return Format.dni(dni); }

  // El CAMPO de la ficha muestra dd/mm/aaaa: es lo que se tipea y lo que dice
  // el placeholder (account-form.isoToArg).
  function fechaCorta(iso) {
    if (!iso) return "";
    var arg = Account.isoToArg(iso);
    return arg || String(iso);
  }

  // La COLUMNA, en cambio, va con el mes en letras (responsable del producto, 2026-07-24): en una
  // lista de fechas de nacimiento, 05/05/1980 obliga a decidir cuál número es
  // el mes en cada fila. Con el mes escrito no hay nada que interpretar.
  function fechaLista(iso) {
    if (!iso) return "";
    return Dates.nacimiento ? Dates.nacimiento(iso) : String(iso);
  }

  // La etiqueta se asocia al primer control de la fila (for = su id): un lector
  // de pantalla nombra el campo y tocar la etiqueta lo enfoca (estudio de
  // usabilidad 2026-10-16: 12 campos de «Nuevo paciente» no tenían nombre).
  function row(label, inner) {
    var m = /<(?:input|select|textarea)\b[^>]*\sid="([^"]+)"/.exec(inner);
    return '<div class="cnt-row"><label' + (m ? ' for="' + m[1] + '"' : "") + '>' + esc(label) + '</label><div class="cnt-field">' + inner + "</div></div>";
  }


  // ---- fecha de nacimiento: texto libre día-primero + eco EN PALABRAS -------
  // Regla del responsable del producto (skill form-design): se acepta CUALQUIER formato razonable y
  // se normaliza en código — con EL parser compartido (account-form.
  // parseFlexibleDate), nunca una segunda copia local. El eco dice el mes con
  // letras ("28 de octubre de 1978") para que nadie tenga que adivinar cuál
  // número es el mes.
  function parseFecha(txt) { return Account.parseFlexibleDate(txt); }

  function ecoFecha(iso) {
    return T("admin.queue.patient_form.birth_echo",
      { fecha: fechaLista(iso), edad: Format.edad(iso) },
      "{fecha} · {edad} años");
  }

  function wireFechaEco(selInput, selEco) {
    var inp = $(selInput), eco = $(selEco);
    if (!inp || !eco) return;
    function pintar(mostrarError) {
      var t = String(inp.value || "").trim();
      if (!t) { eco.textContent = ""; eco.className = "cnt-hint"; return; }
      var iso = parseFecha(t);
      if (iso) { eco.textContent = ecoFecha(iso); eco.className = "cnt-hint pat-echo-ok"; }
      else if (mostrarError) {
        eco.textContent = T("admin.queue.common.date_invalid", { ejemplo: "28/10/1978" }, "No entiendo esa fecha. Probá {ejemplo}.");
        eco.className = "cnt-hint pat-echo-error";
      } else { eco.textContent = ""; eco.className = "cnt-hint"; }
    }
    // se valida al salir del campo, no en cada tecla; una vez que hay error,
    // se limpia mientras la corrigen ("premiá temprano, castigá tarde").
    inp.addEventListener("input", function () { pintar(false); });
    inp.addEventListener("blur", function () { pintar(true); });
    pintar(false);
  }

  function wireDniInput(inp) {
    if (!inp) return;   // en edición el DNI es fijo
    inp.addEventListener("input", function () {
      var limpio = Format.digits(inp.value).slice(0, 9);
      if (limpio !== inp.value) inp.value = limpio;
    });
  }

  // ---- cobertura: obras sociales + prepaga, con UNA principal --------------
  // La ficha muestra lo mismo que el paciente cargó en su cuenta y se puede
  // corregir desde acá. Cuál manda la decide el server (cobertura.py): lo
  // marcado gana y, si no hay nada marcado, propone la mejor. Las del padrón
  // (origen 'puco') muestran el nombre FIJO —lo pone el registro nacional—
  // pero el Nº de afiliado sí se edita, porque el padrón no lo trae.

  function obrasDe(cob) {
    var o = (cob || {}).obras_sociales;
    return Array.isArray(o) ? o.filter(function (x) { return x && typeof x.nombre === "string"; }) : [];
  }

  function filaObraHtml(o, i, principal) {
    var esPadron = o.origen === "puco";
    var clave = o.nombre;
    return '<div class="pat-coverage-row" data-cob-idx="' + i + '">'
      + '<label class="pat-coverage-main" title="' + esc(T("admin.queue.coverage.main_title", null, "Es la cobertura que se usa para este paciente")) + '">'
      + '<input type="radio" name="pac-principal" value="' + esc(clave) + '"' + (principal === clave ? " checked" : "") + ">"
      + "<span>" + esc(T("admin.queue.coverage.main", null, "Principal")) + "</span></label>"
      + (esPadron
        ? '<span class="pat-coverage-name pat-coverage-fixed">' + esc(o.nombre) + '<span class="pat-coverage-registry">' + esc(T("admin.queue.coverage.from_registry", null, "según el padrón")) + '</span></span>'
        : '<input class="cnt-in pat-coverage-name" data-cob-nombre value="' + esc(o.nombre) + '" placeholder="' + esc(T("admin.queue.coverage.insurer_placeholder", null, "Obra social")) + '" aria-label="' + esc(T("admin.queue.coverage.insurer_placeholder", null, "Obra social")) + '">')
      + '<input class="cnt-in pat-coverage-member" data-cob-afiliado value="' + esc(o.afiliado || "") + '" placeholder="' + esc(T("admin.queue.coverage.member_no_placeholder", null, "Nº de afiliado")) + '" aria-label="' + esc(T("admin.queue.coverage.member_no_placeholder", null, "Nº de afiliado")) + '">'
      + '<button type="button" class="pat-coverage-remove" data-cob-quitar="' + i + '">' + esc(T("admin.queue.coverage.remove", null, "Quitar")) + "</button>"
      + "</div>";
  }

  // `sugerida` = la clave que eligió el server cuando el paciente todavía no
  // tiene ninguna marcada (cobertura.py). Se muestra marcada desde el
  // principio: es un default visible, no una preferencia oculta.
  function coberturaHtml(cob, sugerida) {
    var obras = obrasDe(cob);
    var pre = (cob || {}).prepaga || {};
    var principal = (cob || {}).principal || sugerida || "";
    var html = '<div id="pac-cob">';
    html += obras.map(function (o, i) { return filaObraHtml(o, i, principal); }).join("");
    if (!obras.length) html += '<p class="cnt-hint pat-coverage-empty">' + esc(T("admin.queue.coverage.empty", null, "Sin obra social cargada.")) + '</p>';
    html += '<button type="button" class="pat-coverage-add" id="pat-coverage-add">' + esc(T("admin.queue.coverage.add", null, "Agregar obra social")) + '</button>';
    html += '<div class="pat-coverage-row pat-coverage-private">'
      + '<label class="pat-coverage-main" title="' + esc(T("admin.queue.coverage.main_title", null, "Es la cobertura que se usa para este paciente")) + '">'
      + '<input type="radio" name="pac-principal" value="prepaga"' + (principal === "prepaga" ? " checked" : "") + ">"
      + "<span>" + esc(T("admin.queue.coverage.main", null, "Principal")) + "</span></label>"
      + '<input class="cnt-in pat-coverage-name" id="pac-prepaga" value="' + esc(pre.nombre || "") + '" placeholder="' + esc(T("admin.queue.coverage.prepaid_placeholder", null, "Prepaga (si tiene)")) + '" aria-label="' + esc(T("admin.queue.coverage.prepaid_placeholder", null, "Prepaga (si tiene)")) + '">'
      + '<input class="cnt-in pat-coverage-member" id="pac-prepaga-af" value="' + esc(pre.afiliado || "") + '" placeholder="' + esc(T("admin.queue.coverage.partner_no_placeholder", null, "Nº de socio")) + '" aria-label="' + esc(T("admin.queue.coverage.partner_no_placeholder", null, "Nº de socio")) + '">'
      + '<input class="cnt-in pat-coverage-plan" id="pac-prepaga-plan" value="' + esc(pre.plan || "") + '" placeholder="' + esc(T("admin.queue.coverage.plan_placeholder", null, "Plan")) + '" aria-label="' + esc(T("admin.queue.coverage.plan_placeholder", null, "Plan")) + '">'
      + "</div>";
    html += "</div>";
    return html;
  }

  function repintarCobertura(cob) {
    var cont = $("#pac-cob");
    if (!cont) return;
    cont.outerHTML = coberturaHtml(cob, cob.principal);
  }

  // Lee del DOM lo que hay cargado. `principal` viaja como la CLAVE (el nombre
  // de la obra, o el literal "prepega") — el server la valida contra lo que
  // realmente quedó guardado. Se manda SIEMPRE (aunque esté vacía): es lo que
  // permite sacar una cobertura en lugar de conservar la guardada.
  function leerCobertura(retainDrafts) {
    var cont = $("#pac-cob");
    var out = { obras_sociales: [], prepaga: null, principal: "" };
    if (!cont) return out;
    $$(".pat-coverage-row[data-cob-idx]", cont).forEach(function (fila) {
      var fijo = $(".pat-coverage-fixed", fila);
      var nombre = fijo ? String(fijo.firstChild.textContent || "").trim()
        : String((($("[data-cob-nombre]", fila) || {}).value) || "").trim();
      if (!nombre && !retainDrafts) return;
      var obra = { nombre: nombre, afiliado: String((($("[data-cob-afiliado]", fila) || {}).value) || "").trim() || null };
      // Only rendering retains registry provenance; the server remains the
      // sole authority when saving. Adding/removing another row cannot make
      // an existing registry name editable or erase an unfinished draft.
      if (retainDrafts && fijo) obra.origen = "puco";
      out.obras_sociales.push(obra);
    });
    var pn = ($("#pac-prepaga") || {}).value;
    if (pn && String(pn).trim()) {
      out.prepaga = {
        nombre: String(pn).trim(),
        afiliado: String((($("#pac-prepaga-af") || {}).value) || "").trim() || null,
        plan: String((($("#pac-prepaga-plan") || {}).value) || "").trim() || null
      };
    }
    var marcada = $('input[name="pac-principal"]:checked', cont);
    if (marcada) out.principal = marcada.value;
    return out;
  }

  // ---- ranking de asistencia (patient_ranking.py) ----------------------------

  function rankDe(it) {
    return it.ranking || { puntaje: 0, etiqueta: "—", nivel: "none", n: 0, motivos: [] };
  }

  // La etiqueta se arma del `banda` del server con las claves del catálogo:
  // la `etiqueta` que manda patient_ranking.py viene fija en castellano y se
  // colaba entera en los otros idiomas (responsable del producto, 2026-09-16, screenshot del
  // panel en italiano con "Viene siempre" sin traducir).
  function bandaLabel(r) {
    var mapa = {
      "viene-siempre": "always_comes",
      "falta-a-veces": "sometimes_misses",
      "falta-seguido": "often_misses",
      "sin-historial": "no_history"
    };
    var clave = mapa[r && r.banda];
    return clave ? T("admin.queue.patients.band." + clave, null, r.etiqueta) : ((r && r.etiqueta) || "—");
  }

  // La asistencia es un nivel, no un texto: tres barritas (responsable del producto, 2026-11-03:
  // «hacela con tres palitos») que se llenan según la banda — viene siempre
  // las tres, falta a veces dos, falta seguido una, sin historial ninguna.
  // El detalle (banda, porcentaje, motivos) va en el globo. El ranking ya sale
  // de los turnos atendidos y ausentes (patient_ranking.py): sin turnos con
  // asistencia registrada no hay nivel que mostrar.
  var BARRAS = { good: 3, warn: 2, bad: 1 };

  function rankTitulo(r) {
    // Sin esta línea un nivel bajo se lee como una sanción, y no lo es: el
    // globo aclara que estima si vendrá al turno y no bloquea nada.
    var nota = T("admin.queue.patients.rank_note", null, "Estima si vendrá al turno; no bloquea nada.");
    if (r.sin_historial) return T("admin.queue.patients.no_attendance_history", null, "Sin turnos con asistencia registrada.") + "\n" + nota;
    var cab = bandaLabel(r);
    if (typeof r.puntaje === "number") cab += " · " + r.puntaje + "%";
    return [cab].concat(r.motivos || [], [nota]).join("\n");
  }

  // Sin nivel (sin turnos con asistencia) no se dibuja nada (2026-11-05):
  // tres barras grises vacías en la mitad de las filas eran un «nada»
  // repetido, lo mismo que el «—» que la lista ya no escribe (02.11).
  function barrasHtml(r) {
    var llenas = BARRAS[r.nivel] || 0;
    if (!llenas) return "";
    var titulo = rankTitulo(r);
    var h = "";
    for (var k = 1; k <= 3; k++) h += "<i" + (k <= llenas ? ' class="on"' : "") + "></i>";
    return '<span class="pat-bars pat-bars--' + esc(r.nivel || "none") + '" role="img" title="' + esc(titulo)
      + '" aria-label="' + esc(titulo.split("\n")[0]) + '">' + h + "</span>";
  }

  function rankCelda(it) { return barrasHtml(rankDe(it)); }
  function rankingActiva() {
    return !!(COLPICK && COLPICK.activas().some(function (c) { return c.key === "ranking"; }));
  }

  // ---- columnas (14, mismas claves/etiquetas/celdas que la clásica) ---------

  // El LINK va en el nombre, no en el DNI (responsable del producto, 2026-07-28: "instead of DNI,
  // we link the name to the customer"). El DNI va debajo del nombre, chico:
  // es con lo que se verifica a alguien en la mesa, pero como dato, no como
  // puerta.

  function otorgados(i) {
    var t = i && i.turnos;
    return t && typeof t.otorgados === "number" ? t.otorgados : (rankDe(i).hechos || 0);
  }

  function colsAll() {
    return [
      // UNA columna para la persona (responsable del producto, 2026-11-02): «Nombre Apellido, 48»
      // como lo escribe la gente, con el DNI debajo, chico. La edad y el DNI
      // ya no son columnas que repiten su etiqueta en cada fila. Se ordena por
      // apellido, que es como se busca en un padrón.
      // La columna no es elástica (2026-11-05): mide lo que pide el nombre más
      // largo (hasta max) y ni crece ni se achica. El espacio que sobra va a
      // «Médico», donde entra un nombre más en vez de un «+N»; el que falta lo
      // ceden médico y servicio, y si no alcanza caen columnas (DROP). Con
      // flex, a 768-1024 se cortaban 10-27 nombres con «…».
      { key: "nombre", label: T("admin.queue.patients.col.person", null, "Paciente"), always: true, min: 156, max: 340,
        orden: function (i) { return [i.apellido, i.nombre].filter(Boolean).join(", ").toLowerCase(); },
        cell: function (i) {
          var persona = Format.personaEdad(i.nombre, i.apellido, i.nacimiento);
          if (!persona) {
            // «Sin nombre» y no un guion (Salud, 30.10: «hay gente registrada
            // sin nombre, es mucha»): el guion parecía una celda rota.
            persona = T("admin.queue.patients.no_name", null, "Sin nombre");
            var e = Format.edadTexto(i.nacimiento);
            if (e) persona += ", " + e;
          }
          // Las tres barras de asistencia van pegadas al nombre, en la misma
          // línea (responsable del producto, 2026-11-04); si la columna «Asistencia» está prendida
          // ya las dibuja ella y acá no se repiten.
          return '<span class="pat-nameline"><a class="pat-person" href="/admin/pacientes?patient=' + encodeURIComponent(i.dni) + '">' + esc(persona) + "</a>"
            + (rankingActiva() ? "" : rankCelda(i)) + "</span>";
        } },
      // El DNI vuelve a ser SU columna (responsable del producto, 2026-11-03: «una línea, y el DNI
      // aparte»): la fila de la persona es una sola línea.
      { key: "dni", label: T("admin.queue.patients.col.dni", null, "DNI"), "default": true, min: 96,
        orden: function (i) { return Format.digits(i.dni); },
        // El archivado se marca junto al DNI (responsable del producto, 2026-11-02).
        cell: function (i) {
          return (Format.dniHtml(i.dni) || esc(fmtDni(i.dni)))
            + (i.activo === false ? " · " + esc(T("admin.queue.patients.status_archived", null, "Archivado")) : "");
        } },
      { key: "nacimiento", label: T("admin.queue.patients.col.birth", null, "Nacimiento"), min: 108,
        orden: function (i) { return i.nacimiento || ""; },
        cell: function (i) { return esc(fechaLista(i.nacimiento)); } },
      { key: "sexo", label: T("admin.queue.patients.col.sex", null, "Sexo"), min: 62,
        orden: function (i) { return i.sexo || ""; },
        cell: function (i) { return esc({ F: T("admin.queue.patients.sex.f", null, "Femenino"), M: T("admin.queue.patients.sex.m", null, "Masculino"), X: T("admin.queue.patients.sex.x", null, "X") }[i.sexo] || ""); } },
      // El teléfono y el mail no se escriben: son dos iconos que abren la
      // llamada y el cliente de correo, y el botón derecho copia el valor
      // (responsable del producto, 2026-11-03). Una sola columna para los dos.
      { key: "contacto", label: T("admin.queue.patients.col.contact", null, "Contacto"), "default": true, ocultaVacia: true, min: 84,
        orden: function (i) { return Format.telNacional(i.contacto) || (i.contacto || "").toLowerCase(); },
        // El campo `contacto` puede guardar el mismo valor que `email` (un
        // solo dato de contacto en la cuenta): el icono no se repite.
        cell: function (i) {
          var contacto = String(i.contacto == null ? "" : i.contacto).trim();
          var email = String(i.email == null ? "" : i.email).trim();
          if (contacto && email && contacto.toLowerCase() === email.toLowerCase()) contacto = "";
          return Format.contactoIconoHtml(contacto) + Format.contactoIconoHtml(email);
        } },
      // El servicio que más consume (responsable del producto, 2026-11-04, en lugar de Cobertura,
      // que sigue en la ficha): el servidor lo manda en `servicio` sólo si
      // lidera con claridad (admin_patients.main_service_id); «todos por
      // poco» llega vacío y la celda no dice nada. El título dice qué es
      // (responsable del producto, 2026-11-05: no «Servicio» a secas, sino el que más usa).
      { key: "servicio", label: T("admin.queue.patients.col.main_service", null, "Servicio más usado"), "default": true, ocultaVacia: true, min: 104, max: 240, flex: 0.01,
        orden: function (i) { return (i.servicio || "").toLowerCase(); },
        cell: function (i) { return esc(i.servicio || ""); } },
      { key: "localidad", label: T("admin.queue.patients.col.locality", null, "Localidad"), min: 110, max: 200, flex: 1,
        orden: function (i) { return (i.localidad || "").toLowerCase(); },
        cell: function (i) { return esc(i.localidad || ""); } },
      // Con qué médico(s) se atiende: el de más turnos y «+N» (responsable del producto,
      // 2026-11-03); los demás, en el globo. Formateador compartido.
      { key: "medico", label: T("admin.queue.patients.col.doctor", null, "Médico"), "default": true, min: 120, max: 260, flex: 1,
        orden: function (i) { return Format.medicosTexto(i.medicos).toLowerCase(); },
        // Todos los nombres van en la celda; ajustarMedicos() esconde los que
        // no entran en la línea y escribe «+N» sólo por ésos (responsable del producto,
        // 2026-11-04: «si caben todos, sin +N»).
        cell: function (i) {
          var nombres = Format.medicosLista(i.medicos);
          if (!nombres.length) return "";
          return '<span class="pat-docs" title="' + esc(Format.medicosTitulo(i.medicos)) + '">'
            // The name in its own span: on paper it stays whole and the list
            // wraps after the comma (human-rounds.css @media print).
            + nombres.map(function (n, k) { return '<span class="pat-doc">' + (k ? ", " : "") + '<span class="pat-doc-n">' + esc(n) + "</span></span>"; }).join("")
            + '<span class="pat-docs-more" hidden></span></span>';
        } },
      { key: "ranking", label: T("admin.queue.patients.col.attendance", null, "Asistencia"), min: 84, max: 120,
        orden: function (i) { return rankDe(i).puntaje; }, cell: rankCelda },
      { key: "turnos", label: T("admin.queue.patients.col.appointments", null, "Turnos"), "default": true, ocultaVacia: true, min: 74, numeric: true,
        // Los turnos OTORGADOS (aprobados, pasados o por venir), con el link a
        // la ficha donde están todos (Salud, 30.10: «no informa qué turnos se
        // le otorgaron»). Antes contaba solo los que dejaron huella de
        // asistencia: alguien con turno mañana figuraba con «—».
        orden: function (i) { return otorgados(i); },
        cell: function (i) {
          var h = otorgados(i);
          if (!h) return "";
          var prox = i.turnos && i.turnos.proximo;
          // Texto simple (responsable del producto, 2026-11-02: «que sea basic»); el detalle
          // aparece al pasar el mouse o mantener el dedo: cuántos y cuál es
          // el próximo. La ficha, con todos, se abre desde el nombre.
          var tip = T("admin.queue.patients.appts_hint", { n: h }, "Turnos otorgados: {n}")
            + (prox ? " · " + T("admin.queue.patients.next_appt", { cuando: Dates.amigable(prox, "") }, "Próximo: {cuando}") : "");
          return '<span class="pat-turnos-n" title="' + esc(tip) + '" aria-label="' + esc(tip) + '">' + esc(h) + "</span>";
        } },
      { key: "ultimo", label: T("admin.queue.patients.col.last_appointment", null, "Último turno"), min: 116,
        orden: function (i) { return rankDe(i).ultimo || ""; },
        cell: function (i) { return esc(fechaLista(rankDe(i).ultimo)); } },
      { key: "creado", label: T("admin.queue.patients.col.registered", null, "Registrado"), min: 110,
        orden: function (i) { return i.created || ""; },
        cell: function (i) { return esc(fechaLista(i.created)); } },
      // Off by default: «Activo» on every row is noise; an archived person is
      // marked next to the DNI, and the state filter still separates them.
      { key: "estado", label: T("admin.queue.patients.col.status", null, "Estado"), min: 92,
        orden: function (i) { return i.activo === false ? 1 : 0; },
        cell: function (i) {
          var activo = i.activo !== false;
          return '<span class="chip ' + (activo ? "chip-activo" : "chip-inactivo") + '">'
            + esc(activo ? T("admin.queue.patients.status_active", null, "Activo") : T("admin.queue.patients.status_archived", null, "Archivado")) + "</span>";
        } },
    ];
  }

  // Columns flagged `ocultaVacia` leave the table (header included) while every
  // visible row has nothing in them, and come back with the first value
  // (ui-expert + the product owner, 2026-11-02: an empty column is a repeated nothing).
  var VACIAS = {};
  function activeCols() {
    var todas = COLPICK ? COLPICK.activas() : colsAll().filter(function (c) { return c.always || c["default"]; });
    calcularVacias(filtradas(ITEMS));
    return todas.filter(function (c) { return !(c.ocultaVacia && VACIAS[c.key]); });
  }
  function calcularVacias(vistas) {
    VACIAS = {};
    if (!vistas.length) return;
    colsAll().forEach(function (c) {
      if (!c.ocultaVacia) return;
      VACIAS[c.key] = vistas.every(function (it) { return String(c.cell(it) || "").trim() === ""; });
    });
  }

  // Prioridad de caída cuando la tabla no entra: primero lo que se puede
  // consultar en la ficha, último lo que la mesa mira para decidir. El
  // teléfono y la asistencia no caen nunca — son las dos razones por las que
  // se abre esta pantalla. Turnos (los otorgados) cae casi al final: es lo que
  // la mesa pidió ver (salud, 30.10) y solo el DNI cae después.
  var DROP = ["creado", "localidad", "ultimo", "sexo", "nacimiento",
    "estado", "servicio", "turnos", "medico", "dni"];

  function ensureOwners() {
    if (!COLPICK) {
      COLPICK = Table.columnas({
        clave: "patients", cols: colsAll(),
        onChange: function (activas) {
          if (disposed) return;
          // La columna por la que se estaba ordenando puede haberse apagado.
          if (!activas.some(function (c) { return c.key === ORDEN.key; })) {
            ORDEN = { key: "nombre", dir: 1 };
          }
          if (host.querySelector("#pat-table")) paintTable();
        }
      });
    }
  }

  function anchos() {
    if (!ANCHOS) {
      ANCHOS = Table.crear({
        clave: "pacientes",
        selector: "#pat-table table",
        tablas: function () { return host.querySelectorAll("#pat-table table"); },
        cols: function () { return activeCols(); },
        drop: DROP,
        dropClase: function (k) { return "pat-hide-" + k; },
        // Measured at the table's natural width, so a column is as wide as
        // its widest cell and not a share of 100% (see installStyle).
        medirClase: "pat-measuring",
        // Pacientes declara `drop`, así que sigue descartando columnas hasta
        // 641px y sólo se apila en tarjetas por debajo de los 640px.
        apilado: function () {
          try { return window.matchMedia("(max-width:640px)").matches; } catch (e) { return false; }
        }
      });
    }
    return ANCHOS;
  }

  // ---- filtros (selects, NUNCA pills; el valor por defecto ES la etiqueta) --

  var BANDAS;
  function prepareFilterLabels() {
  BANDAS = [
    // Tres bandas desde 2026-10-27 (patient_ranking.BANDS): «Suele venir» se
    // fundió en «Viene siempre».
    ["viene-siempre", T("admin.queue.patients.band.always_comes", null, "Viene siempre")],
    ["falta-a-veces", T("admin.queue.patients.band.sometimes_misses", null, "Falta a veces")],
    ["falta-seguido", T("admin.queue.patients.band.often_misses", null, "Falta seguido")],
    ["sin-historial", T("admin.queue.patients.band.no_history", null, "Sin historial")]
  ];
  }

  function filtradas(items) {
    return items.filter(function (i) {
      if (FILTROS.estado === "activo" && i.activo === false) return false;
      if (FILTROS.estado === "archivado" && i.activo !== false) return false;
      if (FILTROS.banda && rankDe(i).banda !== FILTROS.banda) return false;
      if (FILTROS.cobertura) {
        var c = i.cobertura_nombre || "";
        if (FILTROS.cobertura === "__sin__" ? c : c !== FILTROS.cobertura) return false;
      }
      return true;
    });
  }

  function opts(pares, valor, etiqueta) {
    return '<option value="">' + esc(etiqueta) + "</option>"
      + pares.map(function (p) {
        return '<option value="' + esc(p[0]) + '"' + (valor === p[0] ? " selected" : "")
          + ">" + esc(p[1]) + "</option>";
      }).join("");
  }

  function filtrosHtml(items) {
    var coberturas = {};
    items.forEach(function (i) { if (i.cobertura_nombre) coberturas[i.cobertura_nombre] = 1; });
    var lista = Object.keys(coberturas).sort(function (a, b) { return a.localeCompare(b, "es"); })
      .map(function (c) { return [c, c]; });
    // "Sin cobertura" es una respuesta útil (es a quién hay que cargarle la
    // obra social), así que es una opción, no la ausencia de filtro.
    if (items.some(function (i) { return !i.cobertura_nombre; })) lista.push(["__sin__", T("admin.queue.patients.no_coverage", null, "Sin cobertura")]);
    return '<select class="adm-sel" id="pac-f-estado">'
      + opts([["activo", T("admin.queue.patients.filter.active", null, "Activos")], ["archivado", T("admin.queue.patients.filter.archived", null, "Archivados")]], FILTROS.estado, T("admin.queue.patients.col.status", null, "Estado")) + "</select>"
      + '<select class="adm-sel" id="pac-f-banda">' + opts(BANDAS, FILTROS.banda, T("admin.queue.patients.col.attendance", null, "Asistencia")) + "</select>"
      + '<select class="adm-sel" id="pac-f-cobertura">' + opts(lista, FILTROS.cobertura, T("admin.queue.patients.col.coverage", null, "Cobertura")) + "</select>";
  }

  function ordenadas(items) {
    var activas = activeCols();
    var col = activas.filter(function (c) { return c.key === ORDEN.key; })[0] || activas[0];
    return items.slice().sort(function (a, b) {
      var x = col.orden(a), y = col.orden(b);
      if (x === y) return 0;
      return (x > y ? 1 : -1) * ORDEN.dir;
    });
  }

  // La celda «Médico» muestra todos los nombres que entran en su línea y «+N»
  // sólo por los que no entran; si ni uno solo cabe, la elipsis recorta el
  // primero y «+N» queda a la vista (a 768, «Juan Gonzále» sin el «+1»: el
  // recorte se comía el número). Se mide con los nombres puestos y sin
  // encoger (`pat-docs-mide`), de a uno menos hasta que cabe.
  var medFrame = null, medObs = null, medObsEl = null, medObsDocs = null, medObsAncho = "";
  function ajustarMedicos() {
    if (disposed || !host) return;
    $$("#pat-table .pat-docs").forEach(function (el) {
      var nombres = el.querySelectorAll(".pat-doc");
      var mas = el.querySelector(".pat-docs-more");
      var n = nombres.length;
      if (!n || !mas) return;
      var visibles = n;
      function mostrar(k) {
        for (var j = 0; j < n; j++) nombres[j].hidden = j >= k;
        mas.hidden = k >= n;
        mas.textContent = k >= n ? "" : "+" + (n - k);
      }
      if (el.classList) el.classList.add("pat-docs-mide");
      mostrar(n);
      while (visibles > 1 && el.scrollWidth > el.clientWidth + 1) { visibles -= 1; mostrar(visibles); }
      if (el.classList) el.classList.remove("pat-docs-mide");
    });
  }
  function programarMedicos() {
    var w = typeof window !== "undefined" ? window : null;
    if (!w || typeof w.requestAnimationFrame !== "function") { ajustarMedicos(); return; }
    if (medFrame !== null) w.cancelAnimationFrame(medFrame);
    medFrame = w.requestAnimationFrame(function () {
      medFrame = w.requestAnimationFrame(function () { medFrame = null; ajustarMedicos(); });
    });
  }
  // The first doctor cell is watched too (2026-11-05): the engine re-fits the
  // columns after a resize and the web font arrives late — both move the cell
  // without moving the table's box, and «+N» stayed as first measured.
  function observarAncho() {
    var w = typeof window !== "undefined" ? window : null;
    var cont = $("#pat-table");
    if (!cont || !w || typeof w.ResizeObserver !== "function") return;
    var docs = cont.querySelector(".pat-docs");
    if (cont === medObsEl && docs === medObsDocs) return;
    if (medObs) medObs.disconnect();
    medObsEl = cont;
    medObsDocs = docs;
    medObsAncho = "";
    medObs = new w.ResizeObserver(function () {
      var ancho = cont.clientWidth + "x" + (docs ? docs.clientWidth : 0);
      if (ancho === medObsAncho) return;
      medObsAncho = ancho;
      programarMedicos();
    });
    medObs.observe(cont);
    if (docs) medObs.observe(docs);
    if (doc().fonts && doc().fonts.ready) doc().fonts.ready.then(programarMedicos);
  }

  // La primera línea de cada turno de la ficha es UNA sola: estado, «cuándo»
  // y médico (responsable del producto, 2026-11-04, desde el teléfono). Lo que no entra se
  // achica por escalones, de lo que menos se nota a lo que más: 1) la fecha
  // abreviada, 2) letra un punto menor, 3) el médico como «M. Apellido»
  // (el nombre entero queda en el title), 4) elipsis. Se mide con las clases
  // puestas, escalón a escalón, igual que los médicos de la lista.
  var turFrame = null, turObs = null;
  function ajustarTurnos() {
    if (disposed || !host) return;
    $$("#pat-turnos .pat-appt-l1").forEach(function (l1) {
      var st = l1.querySelector(".pat-appt-st");
      var doc_ = l1.querySelector(".pat-appt-doc");
      var cuando = l1.querySelector(".pat-appt-when");
      var who = l1.querySelector(".pat-appt-who");
      function poner(nivel) {
        l1.classList.remove("f1", "f2", "f3", "f4");
        for (var k = 1; k <= nivel; k++) l1.classList.add("f" + k);
        if (cuando) cuando.textContent = nivel >= 1 ? cuando.getAttribute("data-corto") : cuando.getAttribute("data-largo");
        if (doc_) doc_.textContent = nivel >= 3 ? medicoCorto(doc_.getAttribute("data-largo")) : doc_.getAttribute("data-largo");
      }
      function sobra() { return l1.scrollWidth > l1.clientWidth + 1 || (who && who.scrollWidth > who.clientWidth + 1); }
      var nivel = 0;
      poner(0);
      while (nivel < 4 && sobra()) { nivel += 1; poner(nivel); }
    });
  }
  function programarTurnos() {
    var w = typeof window !== "undefined" ? window : null;
    if (!w || typeof w.requestAnimationFrame !== "function") { ajustarTurnos(); return; }
    if (turFrame !== null) w.cancelAnimationFrame(turFrame);
    turFrame = w.requestAnimationFrame(function () { turFrame = null; ajustarTurnos(); });
  }
  function observarTurnos() {
    var w = typeof window !== "undefined" ? window : null;
    if (turObs) { turObs.disconnect(); turObs = null; }
    var cont = $("#pat-turnos");
    ajustarTurnos();
    if (!cont || !w || typeof w.ResizeObserver !== "function") return;
    var ancho = -1;
    turObs = new w.ResizeObserver(function () {
      if (cont.clientWidth === ancho) return;
      ancho = cont.clientWidth;
      programarTurnos();
    });
    turObs.observe(cont);
    if (doc().fonts && doc().fonts.ready) doc().fonts.ready.then(programarTurnos);
  }
  // «Candelaria Larralde» → «C. Larralde»; con cuatro palabras o más se
  // conservan los dos últimos (apellidos compuestos).
  function medicoCorto(nombre) {
    var p = String(nombre || "").trim().split(/\s+/);
    var titulo = /^(dra?|lic|mg)\.?$/i.test(p[0]) ? p.shift() + " " : "";
    if (p.length < 2) return titulo + p.join(" ");
    return titulo + p[0].charAt(0) + ". " + p.slice(p.length >= 4 ? -2 : -1).join(" ");
  }

  function paintTable() {
    var activas = activeCols();
    var thead = "", i;
    for (i = 0; i < activas.length; i++) {
      var c = activas[i], on = c.key === ORDEN.key;
      var aria = on ? (ORDEN.dir === 1 ? "ascending" : "descending") : "none";
      thead += '<th scope="col" data-col="' + esc(c.key) + '" data-sort="' + esc(c.key)
        + '" aria-sort="' + aria + '" tabindex="0" class="cnt-th' + (c.numeric ? " cnt-numeric" : "") + ' cnt-th-sortable' + (on ? " on" : "") + '">'
        + esc(c.label) + (on ? (ORDEN.dir === 1 ? " ▲" : " ▼") : "") + "</th>";
    }
    var vistas = filtradas(ITEMS);
    var body = "";
    var cont = $("#pat-table");
    if (!cont) return;
    if (!vistas.length) {
      // Hay pacientes (o hay una búsqueda/un médico elegido) y lo que se
      // eligió los dejó a todos afuera: los filtros quedan, sin encabezado de
      // tabla vacío, y el camino de vuelta es un botón. La lista SIN nada
      // (ni búsqueda ni filtro) es otro estado y la pinta paintList.
      cont.innerHTML = Filters.noMatchHtml({
        title: !ITEMS.length && Q
          ? T("admin.queue.patients.empty.no_results_for", { q: Q }, "Sin resultados para “{q}”.")
          : T("admin.queue.patients.empty.filtered", null, "Ningún paciente coincide con los filtros."),
        clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros")
      });
    } else {
      ordenadas(vistas).forEach(function (it) {
        body += '<tr class="cnt-tr' + (it.activo === false ? " cnt-tr-borrado" : "") + '">';
        // `data-label` alimenta el ::before de la fila apilada (<640px): cada
        // celda se reconvierte en "ETIQUETA / valor" sin un segundo template.
        activas.forEach(function (c) {
          body += '<td data-col="' + esc(c.key) + '" data-label="' + esc(c.label) + '"'
            + (c.numeric ? ' class="cnt-numeric"' : "") + ">" + c.cell(it) + "</td>";
        });
        body += "</tr>";
      });
      cont.innerHTML = '<table class="cnt-table pat-table"><thead><tr>' + thead + "</tr></thead><tbody>" + body + "</tbody></table>";
      var a = anchos();
      if (a) a.programarFit();
      // Después del ajuste de columnas del motor, que corre al final de esta
      // misma tarea (microtarea, 2026-11-10): recién ahí se sabe cuánto mide
      // la celda del médico, y el «+N» queda puesto antes de pintar.
      queueMicrotask(ajustarMedicos);
      observarAncho();
    }
    Filters.applyDisclosure(host, vistas.length ? "data" : "no-match");
    // Sin contador de la lista (responsable del producto, 2026-11-02: otra vez contadores): la
    // truncación se ve en «Cargar más», que aparece solo si quedan filas.
    var mas = $("#pat-mas");
    if (mas) {
      var hayMas = ITEMS.length < TOTAL;
      var cargando = !!loadingMore;
      mas.hidden = !hayMas;
      mas.disabled = cargando;
      mas.textContent = cargando
        ? T("admin.queue.patients.loading_more", null, "Cargando…")
        : T("admin.queue.patients.load_more", null, "Cargar más");
    }
  }

  // El querystring de la lista: solo q. Al médico se lo busca en el mismo
  // buscador (el server lo reconoce por nombre; responsable del producto, 2026-11-02: habrá
  // muchos médicos, que lo escriban en la búsqueda), no hay filtro aparte.
  function qsPacientes() {
    return Q ? "?q=" + encodeURIComponent(Q) : "";
  }

  function hydratePrefs() {
    if (PREFS_READY) return;
    if (Prefs) {
      Q = Prefs.get("patients.search", Q) || "";
      var o = Prefs.get("patients.sort", null);
      if (o && o.key) ORDEN = { key: o.key, dir: o.dir === -1 ? -1 : 1 };
      var f = Prefs.get("patients.filters", null);
      if (f && typeof f === "object") {
        Object.keys(FILTROS).forEach(function (k) { FILTROS[k] = String(f[k] || ""); });
        // Un filtro guardado con la banda retirada «Suele venir» (2026-10-27)
        // pasa a la banda que la absorbió, no a una lista vacía.
        if (FILTROS.banda === "suele-venir") FILTROS.banda = "viene-siempre";
      }
    }
    PREFS_READY = true;
  }

  function paintRouteError(status) {
    if (!alive()) return;
    host.innerHTML = status === 403
      ? '<p class="adm-empty">' + esc(T("admin.queue.common.no_permission", null, "No tenés permiso para ver esta sección.")) + '</p>'
      : '<p class="adm-empty">' + esc(T("admin.queue.common.load_error", null, "No se pudo cargar. Reintentá.")) + '</p>';
  }

  // ---- lista -----------------------------------------------------------------

  // Cuántos valores distintos trae lo cargado para un filtro de lista fija:
  // con menos de dos no hay nada que elegir y el select no se muestra
  // (showIfChoice lo deja visible si ya está puesto, para poder sacarlo).
  function distintos(valor) {
    var vistos = {};
    ITEMS.forEach(function (i) { vistos[String(valor(i))] = 1; });
    return Object.keys(vistos).length;
  }

  // Filas de la lista sin buscar: con menos de Filters.FILTER_MIN_ROWS no se
  // dibuja ningún filtro (la regla compartida); el buscador no es un filtro.
  function filasLista() {
    return LIST_ROWS != null ? LIST_ROWS : Math.max(TOTAL, ITEMS.length);
  }

  function podarFiltros() {
    var filas = filasLista();
    Filters.showIfChoice($("#pac-f-estado"), distintos(function (i) { return i.activo === false ? "archivado" : "activo"; }), "", filas);
    Filters.showIfChoice($("#pac-f-banda"), distintos(function (i) { return rankDe(i).banda || ""; }), "", filas);
    // Las opciones de Cobertura ya salen de lo cargado: cuenta sus opciones.
    Filters.showIfChoice($("#pac-f-cobertura"), null, "", filas);
  }

  function limpiarFiltros() {
    var delServidor = !!Q;
    if (searchTimer !== null) { clearTimeout(searchTimer); searchTimer = null; }
    Q = "";
    Object.keys(FILTROS).forEach(function (k) { FILTROS[k] = ""; });
    if (Prefs) {
      Prefs.set("patients.search", Q);
      Prefs.set("patients.filters", FILTROS);
    }
    if (delServidor) fetchList(routeGen);
    else paintList();
  }

  function paintList() {
    if (!ITEMS.length && !Q) {
      // Ni un paciente y nada elegido que los esconda: sin buscador, filtros,
      // conteo, engranaje ni encabezado de tabla — el título, «Nuevo
      // paciente» y qué va a aparecer acá. (Sin búsqueda la lista es la de
      // contactos recientes, que recorre el padrón entero ordenado por último
      // contacto: vacía quiere decir padrón vacío, no «nadie reciente».)
      host.innerHTML = '<div class="cnt-toolbar"><div class="cnt-toolbar-filters"></div><div class="cnt-toolbar-right"></div></div>'
        + Filters.emptyStateHtml({
          title: T("admin.queue.patients.empty.none_yet", null, "Todavía no hay pacientes."),
          text: T("admin.queue.patients.empty.none_yet_text", null, "Acá aparece cada persona que pide un turno o que cargás con «Nuevo paciente».")
        });
      Filters.applyDisclosure(host, "empty");
      return;
    }
    host.innerHTML = claimsHtml() + '<div class="cnt-toolbar"><div class="cnt-toolbar-filters">'
      // El placeholder ENTERO tiene que entrar (hook control-content-fits);
      // dice lo mismo que el buscador de Solicitudes y Pacientes de hoy: la
      // misma clave. Las tres buscan por DNI, paciente y medico (aca el medico
      // por q=, responsable del producto 2026-11-02, admin_patients._search_with_doctors).
      // Sin estilos inline: la grilla responsive de admin-content.js manda.
      + '<input class="cnt-in cnt-search" id="pac-q" type="search" placeholder="' + esc(T("admin.queue.filters.buscar_placeholder", null, "Paciente o médico")) + '" value="' + esc(Q) + '">'
      + filtrosHtml(ITEMS)
      // Mismo control que "Cargar más" de la cola de Solicitudes: misma clase,
      // mismo texto. Oculto por default — paintTable lo muestra sólo si
      // quedan filas sin cargar (ITEMS.length < TOTAL).
      + '<button type="button" class="btn" id="pat-mas" data-pat-mas hidden></button>'
      + '</div><div class="cnt-toolbar-right">'
      // El engranaje elige columnas de filas que existen: sin filas cargadas
      // (una búsqueda que no encontró a nadie) no hay nada que configurar.
      + (COLPICK && ITEMS.length ? COLPICK.gearHtml() : "")
      + '</div></div>'
      // Qué lista es esta ahora se dice en una línea corta del conteo
      // ("Recientes · N", paintTable) en vez de una oración bajo la barra
      // (responsable del producto review, 2026-09-15 — antes duplicaba "N de N").
      + '<div class="cnt-table-wrap" id="pat-table"></div>';
    podarFiltros();
    paintTable();
  }

  function renderList(gen) {
    hydratePrefs();
    host.setAttribute("data-list-view", "1");
    ensureOwners();
    fetchClaims(gen);
    return fetchList(gen);
  }

  // Every list read — route boot, debounced search — goes
  // through ONE owner-tokened path: a superseding list request or a route
  // switch aborts and retires the previous one.
  function fetchList(gen) {
    if (listReq && listReq.ctl) listReq.ctl.abort();
    if (loadingMore) {
      if (loadingMore.ctl) loadingMore.ctl.abort();
      loadingMore = null;
    }
    var token = listReq = { gen: gen, ctl: new AbortController() };
    return api("/api/admin/patients" + qsPacientes(), gen, { signal: token.ctl.signal }, function () { return listReq === token; }).then(function (res) {
      if (listReq !== token || !ownsRoute(gen)) return;
      if (!res.ok) { paintRouteError(res.status); return; }
      var j = res.json;
      if (!isObject(j) || !Array.isArray(j.items)) { paintRouteError(0); return; }
      ITEMS = j.items;
      TOTAL = j.total != null ? j.total : ITEMS.length;
      if (!Q) LIST_ROWS = Math.max(TOTAL, ITEMS.length);
      MODO = j.modo || null;
      paintList();
    }).catch(function (error) {
      if (listReq !== token || !ownsRoute(gen) || String(error && error.message) === "stale") return;
      paintRouteError(0);
    });
  }

  // ---- reclamos de documento (TASK-68) ----------------------------------------
  // Quien se registra con un DNI ya tomado queda como reclamo; la mesa confirma
  // (el número pasa a quien lo reclamó) o rechaza (la cuenta se archiva).
  var CLAIMS = [];

  function claimsHtml() {
    if (!CLAIMS.length) return "";
    var rows = CLAIMS.map(function (c) {
      var quien = [c.nombre, c.apellido].filter(Boolean).join(" ") || c.email || c.contacto || "";
      var titular = [c.titular_nombre, c.titular_apellido].filter(Boolean).join(" ");
      var texto = c.estado === "pending"
        ? T("admin.queue.patients.claim.pending", { quien: quien, dni: c.dni, titular: titular || "—" },
          "{quien} pide el DNI {dni}. Hoy es de {titular}.")
        : T("admin.queue.patients.claim.unverified", { quien: quien, dni: c.dni },
          "{quien} se registró con el DNI {dni} sin comprobarlo.");
      return '<li class="claim-row"><span>' + esc(texto) + '</span> '
        + '<button type="button" class="btn" data-claim-resolve="1" data-claim-id="' + esc(c.id) + '">'
        + esc(T("admin.queue.patients.claim.approve", null, "Confirmar")) + '</button> '
        + '<button type="button" class="btn" data-claim-resolve="0" data-claim-id="' + esc(c.id) + '">'
        + esc(T("admin.queue.patients.claim.reject", null, "Rechazar")) + '</button></li>';
    }).join("");
    return '<section class="claims" id="pat-claims"><h2>'
      + esc(Tn("admin.queue.patients.claim.title", CLAIMS.length, { n: CLAIMS.length }, "{n} reclamo de documento", "{n} reclamos de documento"))
      + '</h2><ul>' + rows + '</ul></section>';
  }

  function fetchClaims(gen) {
    return api("/api/admin/dni-claims", gen).then(function (res) {
      if (!ownsRoute(gen)) return;
      var items = res.ok && isObject(res.json) && Array.isArray(res.json.items) ? res.json.items : [];
      var ids = function (list) { return list.map(function (c) { return c.id; }).join(","); };
      var changed = ids(items) !== ids(CLAIMS);
      CLAIMS = items;
      if (changed && host.getAttribute("data-list-view") === "1") paintList();
    }).catch(function () { /* the list works without the claims */ });
  }

  function resolverReclamo(id, aprobar) {
    var msg = aprobar
      ? T("admin.queue.patients.claim.confirm_approve", null, "¿Confirmar? Quien reclama pasa a ser el titular de este DNI y la cuenta que lo tenía se archiva.")
      : T("admin.queue.patients.claim.confirm_reject", null, "¿Rechazar? La cuenta de quien reclama se archiva.");
    if (!ask(msg)) return;
    var token = beginMutation();
    if (!token) return;
    api("/api/admin/dni-claims/" + encodeURIComponent(id) + "/resolve", token.gen, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ aprobar: aprobar }), signal: token.ctl.signal
    }).then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      endMutation(token);
      if (!res.ok) {
        toast(res.json && res.json.error === "conflicto"
          ? T("admin.queue.patients.claim.conflict", null, "Las dos cuentas tienen datos propios; hay que resolverlo a mano.")
          : errText(res.json, T("admin.queue.common.save_error", null, "No se pudo guardar.")));
        fetchClaims(token.gen);
        return;
      }
      CLAIMS = CLAIMS.filter(function (c) { return c.id !== id; });
      fetchList(token.gen);
    }).catch(function () { mutationFailed(token, T("admin.queue.common.save_error", null, "No se pudo guardar.")); });
  }

  function cargarMas() {
    if (!alive() || searchTimer !== null || loadingMore || ITEMS.length >= TOTAL) return;
    var gen = routeGen;
    var token = loadingMore = { gen: gen, ctl: new AbortController() };
    paintTable();
    var qs = "?offset=" + ITEMS.length + "&limit=" + PAGE + (Q ? "&q=" + encodeURIComponent(Q) : "");
    api("/api/admin/patients" + qs, gen, { signal: token.ctl.signal }, function () { return loadingMore === token; }).then(function (res) {
      // Un request viejo NUNCA desbloquea el estado ocupado de un dueño más
      // nuevo: sólo el token vigente puede tocar loadingMore.
      if (loadingMore !== token) return;
      loadingMore = null;
      if (!ownsRoute(token.gen)) return;
      if (!res.ok || !isObject(res.json) || !Array.isArray(res.json.items)) {
        toast(T("admin.queue.patients.load_more_error", null, "No se pudo cargar más pacientes."));
        paintTable();
        return;
      }
      ITEMS = ITEMS.concat(res.json.items || []);
      TOTAL = res.json.total != null ? res.json.total : TOTAL;
      paintTable();
    }).catch(function () {
      if (loadingMore !== token) return;
      loadingMore = null;
    });
  }

  // ---- personas a cargo -------------------------------------------------------
  // The product owner (2026-07-24): "make the kids editable… and kids could be linked to
  // one or two parents". Un chico a cargo de padre Y madre son dos filas con
  // el mismo DNI de chico: por eso cada uno muestra CON QUIÉN MÁS está, y
  // "Quitar" dice de quién lo saca. Sacarlo del último adulto no borra su
  // historial: los turnos ya sacados siguen a nombre del chico.

  function otrosAdultos(dep, dniActual) {
    return (dep.adultos || []).filter(function (a) { return String(a.dni) !== String(dniActual); });
  }

  // Etiqueta visible del vínculo; la VALUE que viaja al server sigue siendo
  // la palabra en castellano (hijo/hija/tutelado/tutelada/otro).
  function vinculoLabel(v) {
    return {
      hijo: T("admin.queue.dependents.relation.son", null, "Hijo"),
      hija: T("admin.queue.dependents.relation.daughter", null, "Hija"),
      tutelado: T("admin.queue.dependents.relation.ward_m", null, "Tutelado"),
      tutelada: T("admin.queue.dependents.relation.ward_f", null, "Tutelada"),
      otro: T("admin.queue.dependents.relation.other", null, "Otro")
    }[v] || (v ? v.charAt(0).toUpperCase() + v.slice(1) : "");
  }

  function depFilaHtml(d, dniActual) {
    var otros = otrosAdultos(d, dniActual);
    var nombre = Format.personaEdad(d.nombre, d.apellido, d.nacimiento);
    // También acá se entra por el nombre: un chico a cargo es un paciente con
    // su propia ficha (y con su propio historial de turnos). Igual que en el
    // listado: una línea «Nombre Apellido, 8» y el DNI en su columna.
    return '<tr class="cnt-tr">'
      + '<td><a class="pat-person" href="/admin/pacientes?patient=' + encodeURIComponent(d.dni) + '">' + esc(nombre || fmtDni(d.dni)) + "</a></td>"
      + "<td>" + (Format.dniHtml(d.dni) || "") + "</td>"
      + "<td>" + esc(fechaLista(d.nacimiento)) + "</td>"
      + "<td>" + esc(vinculoLabel(d.vinculo)) + "</td>"
      + "<td>" + (otros.length
        ? otros.map(function (a) {
          return '<a href="/admin/pacientes?patient=' + encodeURIComponent(a.dni) + '">' + esc([a.apellido, a.nombre].filter(Boolean).join(", ") || fmtDni(a.dni)) + "</a>";
        }).join(", ")
        : '<span class="cnt-muted">—</span>') + "</td>"
      + '<td><span class="pat-dep-acts">'
      + '<button type="button" class="pat-dep-link" data-dep-edit="' + esc(d.dni) + '">' + esc(T("admin.queue.dependents.edit", null, "Editar")) + '</button>'
      + '<button type="button" class="pat-dep-link" data-dep-quitar="' + esc(d.dni) + '">' + esc(T("admin.queue.dependents.remove", null, "Quitar")) + '</button>'
      + "</span></td>"
      + "</tr>";
  }

  // «Turnos» de la ficha (Salud, 30.10: «cuesta mucho encontrar qué turnos
  // tiene cada paciente»): todos los pedidos de esta persona, el más reciente
  // primero, con el estado en palabras. Va arriba del formulario: es lo que
  // el escritorio viene a mirar.
  function turnoEstadoTexto(t) {
    if (t.asistencia === "presente") return T("admin.queue.patient_appts.attended", null, "Vino");
    if (t.asistencia === "ausente") return T("admin.queue.patient_appts.absent", null, "No vino");
    if (t.estado === "aprobada") return T("admin.queue.patient_appts.approved", null, "Confirmado");
    // Una sola palabra en todo ancho (2026-11-04): la de la cola, «Pendiente».
    if (t.estado === "pendiente") return T("admin.queue.patient_appts.pending_short", null, "Pendiente");
    if (t.estado === "cancelada") return T("admin.queue.patient_appts.cancelled", null, "Cancelado");
    if (t.estado === "rechazada") return T("admin.queue.patient_appts.rejected", null, "Rechazado");
    return String(t.estado || "");
  }

  // Qué se puede hacer con cada turno desde la ficha (responsable del producto, 2026-11-03: «ahí
  // hay que cancelar sus turnos, confirmarlos, no corregir al paciente»): los
  // mismos endpoints y las mismas palabras que la cola. Pendiente: Confirmar
  // (fijar el horario es la pantalla de Solicitudes) o Cancelar. Confirmado y
  // por venir: Cancelar, con su pregunta ahí mismo. Confirmado que ya pasó y
  // sin asistencia: Atendió / No vino.
  function turnoAccionesHtml(t, confirmando) {
    var id = esc(String(t.id));
    var cancelar = '<button type="button" class="btn btn-quiet" data-pt-cancel="' + id + '">'
      + esc(T("admin.queue.cancel.boton", null, "Cancelar turno")) + "</button>";
    if (confirmando) {
      return '<span class="pat-appt-ask">' + esc(T("admin.queue.cancel.pregunta", null, "¿Cancelar este turno?")) + "</span>"
        + '<button type="button" class="btn btn-ok" data-pt-cancel-yes="' + id + '">'
        + esc(T("admin.queue.cancel.si", null, "Sí, cancelar el turno")) + "</button>"
        + '<button type="button" class="btn btn-quiet" data-pt-cancel-no="' + id + '">'
        + esc(T("admin.queue.cancel.no", null, "No")) + "</button>";
    }
    if (t.estado === "pendiente") {
      return '<a class="btn btn-accent" href="/admin/solicitudes">' + esc(T("admin.queue.patient_appts.confirm", null, "Confirmar")) + "</a>" + cancelar;
    }
    var atendio = '<button type="button" class="btn btn-quiet" data-pt-asis="' + id + '" data-valor="presente">'
      + esc(T("admin.queue.attendance.presente", null, "Atendió")) + "</button>";
    if (t.estado === "aprobada" && !t.asistencia) {
      if (t.futuro) return cancelar;
      return atendio
        + '<button type="button" class="btn btn-quiet" data-pt-asis="' + id + '" data-valor="ausente">'
        + esc(T("admin.queue.attendance.ausente", null, "No vino")) + "</button>";
    }
    // «No vino» marcado sin querer se corrige ahí mismo (responsable del producto, 2026-11-05):
    // la misma marca de la cola, que reemplaza a la anterior y queda en el
    // registro de cambios.
    if (t.estado === "aprobada" && t.asistencia === "ausente") return atendio;
    // Cancelado: Reprogramar abre el turno nuevo de la mesa con el paciente,
    // el servicio y el médico ya puestos; Restaurar aparece sólo si ese
    // horario sigue libre y no pasó (el servidor lo decide en `restaurable`).
    if (t.estado === "cancelada") {
      var reprog = '<button type="button" class="btn btn-quiet" data-pt-reprog="' + id + '">'
        + esc(T("admin.queue.patient_appts.reschedule", null, "Reprogramar")) + "</button>";
      if (!t.restaurable) return reprog;
      return '<button type="button" class="btn btn-quiet" data-pt-restore="' + id + '">'
        + esc(T("admin.queue.patient_appts.restore", null, "Restaurar")) + "</button>" + reprog;
    }
    return "";
  }

  function turnosHtml(lista) {
    TURNOS_FICHA = Array.isArray(lista) ? lista : [];
    if (!TURNOS_FICHA.length) {
      return '<p class="adm-empty pat-appts-empty" id="pat-turnos">' + esc(T("admin.queue.patient_appts.empty", null, "Todavía no pidió ni tiene turnos.")) + "</p>";
    }
    // Dos líneas por turno (responsable del producto, 2026-11-04): «Hoy, 9:00» y el médico en la
    // MISMA línea, grandes; debajo el servicio y el centro. El estado va al
    // principio y comparte la línea si entra (si no, la suya): un grupo
    // «cuándo + médico» que no baja a medias. Lo que ya pasó lleva la fecha
    // sin hora («2 de octubre») — la hora de un turno viejo no sirve a nadie.
    return '<div class="cnt-table-wrap" id="pat-turnos"><table class="cnt-table pat-appts"><tbody>'
      + TURNOS_FICHA.map(function (t) {
        var cuando;
        // No date (a request rejected before it got one): nothing, not «Sin fecha».
        if (!t.dia) cuando = "";
        else if (t.pedido) {
          // Sin horario asignado todavía: la fecha es la que el paciente pidió.
          cuando = T("admin.queue.patient_appts.asked", { cuando: Dates.amigable(t.dia, "") }, "Pidió {cuando}");
        } else cuando = t.futuro ? Dates.amigable(t.dia, t.hora) : Dates.pasada(t.dia);
        // La misma fecha abreviada («Sáb 14 nov, 09:00»), para cuando la línea no alcanza.
        var cuandoCorto = !t.dia ? cuando : t.pedido
          ? T("admin.queue.patient_appts.asked", { cuando: Dates.corta(t.dia, "") }, "Pidió {cuando}")
          : Dates.corta(t.dia, t.futuro ? t.hora : "");
        var sub = [t.servicio, t.centro].filter(Boolean).join(" · ");
        var kind = t.asistencia === "presente" ? "ok" : t.asistencia === "ausente" ? "bad"
          : t.estado === "aprobada" ? "ok" : t.estado === "pendiente" ? "warn" : "off";
        return '<tr class="cnt-tr" data-pt-fila="' + esc(String(t.id)) + '">'
          + '<td class="pat-appt-main"><div class="pat-appt-l1">'
          + '<span class="pat-appt-st pat-appt-st--' + kind + '">' + esc(turnoEstadoTexto(t)) + "</span>"
          + '<span class="pat-appt-who">' + (cuando ? '<span class="pat-appt-when" data-largo="' + esc(cuando) + '" data-corto="' + esc(cuandoCorto) + '">' + esc(cuando) + "</span>" : "")
          + (t.medico ? ' <span class="pat-appt-doc" data-largo="' + esc(t.medico) + '" title="' + esc(t.medico) + '">' + esc(t.medico) + "</span>" : "") + "</span></div>"
          + (sub ? '<div class="pat-appt-l2" title="' + esc(sub) + '">' + esc(sub) + "</div>" : "")
          + "</td>"
          + '<td class="pat-appt-acc">' + turnoAccionesHtml(t, false) + "</td>"
          + "</tr>";
      }).join("")
      + "</tbody></table></div>";
  }

  // El principio de la ficha: «Nombre Apellido, 48» con las tres barritas de
  // asistencia pegadas al nombre (responsable del producto, 2026-11-04: «después del nombre, no en
  // una línea aparte»), el DNI chico y el teléfono y el mail como iconos, y
  // «Editar datos» a la derecha. Sin la línea de médicos «Nombre +N» (los
  // turnos de abajo ya dicen con quién) y sin contadores.
  function fichaTopHtml(item, data, dni) {
    var persona = Format.personaEdad(item.nombre, item.apellido, item.nacimiento);
    var datos = Format.contactoIconoHtml(item.contacto) + Format.contactoIconoHtml(item.email);
    var barras = data.ranking ? barrasHtml(data.ranking) : "";
    // El número solo, como en la lista (2026-11-05): «DNI 30.111.222» era la
    // única etiqueta de la línea; los puntos ya dicen qué es.
    return '<div class="pat-head">'
      + '<h1 class="pat-head-name">' + esc(persona || fmtDni(dni))
      + (barras ? " " + barras : "") + "</h1>"
      + '<div class="pat-head-meta">'
      + (persona ? '<span class="pat-head-dni">' + (Format.dniHtml(dni) || "") + "</span>" : "")
      + (datos ? '<span class="pat-head-info">' + datos + "</span>" : "")
      + "</div>"
      + '<button type="button" class="btn btn-quiet pat-head-edit" id="pac-edit" aria-expanded="false" aria-controls="pac-datos">'
      + esc(T("admin.queue.patient_form.edit_data", null, "Editar datos")) + "</button>"
      + "</div>";
  }

  // Sin nadie a cargo no hay lista que titular: la sección es sólo el botón
  // (sin encabezado ni «Sin personas a cargo.»). Con gente, el título y la tabla.
  function dependientesHtml(dniActual, aCargoDe) {
    var hayACargoDe = !!(aCargoDe && aCargoDe.length), vacia = !DEPS.length && !hayACargoDe;
    // With people listed, «Agregar persona a cargo» sits at the right end of
    // the section's heading line, not on a line of its own (one screen
    // header, product owner 2026-11-08).
    var addBtn = '<button type="button" class="btn btn-quiet pat-act" id="pac-dep-add">' + esc(T("admin.queue.dependents.add", null, "Agregar persona a cargo")) + '</button>';
    var html = '<section id="pat-deps" class="pat-deps' + (vacia ? " pat-deps--bare" : "") + '">'
      + (vacia ? "" : '<div class="pat-deps-head"><h2 class="pat-deps-h">' + esc(T("admin.queue.dependents.title", null, "Personas a cargo")) + "</h2>" + addBtn + "</div>");
    if (hayACargoDe) {
      html += '<p class="cnt-hint">' + esc(T("admin.queue.dependents.in_charge_of", null, "Esta persona está a cargo de")) + " "
        + aCargoDe.map(function (a) {
          return '<a href="/admin/pacientes?patient=' + encodeURIComponent(a.dni) + '">' + esc([a.apellido, a.nombre].filter(Boolean).join(", ") || fmtDni(a.dni)) + "</a>";
        }).join(", ") + ".</p>";
    }
    if (DEPS.length) {
      html += '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr>'
        + '<th class="cnt-th">' + esc(T("admin.queue.patients.col.person", null, "Paciente")) + '</th>'
        + '<th class="cnt-th">' + esc(T("admin.queue.patients.col.dni", null, "DNI")) + '</th>'
        + '<th class="cnt-th">' + esc(T("admin.queue.patients.col.birth", null, "Nacimiento")) + '</th>'
        + '<th class="cnt-th">' + esc(T("admin.queue.dependents.relation", null, "Vínculo")) + '</th><th class="cnt-th">' + esc(T("admin.queue.dependents.also_in_charge_of", null, "También a cargo de")) + '</th>'
        + '<th class="cnt-th"></th></tr></thead><tbody>'
        + DEPS.map(function (d) { return depFilaHtml(d, dniActual); }).join("")
        + "</tbody></table></div>";
    }
    html += '<div id="pat-dep-form"></div>';
    if (vacia) html += addBtn;
    return html + "</section>";
  }

  // «Quién abrió sus datos» (K10, Ley 26.529 art. 18): el registro de accesos
  // a los datos clínicos de esta persona. Sólo llega `ver_accesos` a quien
  // puede verlo (el admin); se pide recién al tocar el botón, no en cada
  // apertura de la ficha. Mismo esqueleto que «Personas a cargo».
  // Al lado, la copia de todos sus datos (Ley 25.326 art. 14): las mismas
  // manos (permiso `usuarios`), así que viaja con el mismo `ver_accesos`
  // (responsable del producto, q779598). El mismo botón que su vecino; abre el zip, que el
  // servidor manda como adjunto (la página no se va) y deja su fila
  // «exportacion» antes de armarlo.
  function accesosHtml() {
    return '<section id="pat-accesos" class="pat-deps"><h2 class="pat-deps-h">'
      + esc(T("admin.queue.access_log.title", null, "Quién abrió sus datos")) + "</h2>"
      + '<div id="pat-accesos-out"></div>'
      + '<div class="pat-acc-acts">'
      + '<button type="button" class="btn btn-quiet pat-act" id="pac-accesos-ver">'
      + esc(T("admin.queue.access_log.show", null, "Ver el registro")) + "</button>"
      + '<button type="button" class="btn btn-quiet pat-act" id="pac-datos-exportar">'
      + esc(T("admin.queue.data_export.link", null, "Descargar sus datos")) + "</button>"
      + "</div></section>";
  }

  function exportarDatos() {
    if (fichaDni) window.location.assign("/api/admin/patients/" + encodeURIComponent(fichaDni) + "/data-export");
  }

  // Ids de core/access_log.ACTIONS; el castellano es el fallback de cada clave.
  function accionTexto(a) {
    var es = {
      ficha: "Ficha", historia_externa: "Historia de HSI", resumen_ia: "Resumen del paciente",
      entrevista: "Entrevista previa", consulta: "Consulta", orden: "Orden de derivación",
      mis_pacientes: "Mis pacientes", alertas: "Alertas rojas", cola: "Pacientes de hoy",
      mis_turnos: "Mis turnos", solicitudes: "Solicitudes", agenda: "Agenda del médico",
      busqueda: "Búsqueda por DNI", conversaciones: "Lista de conversaciones (entrevista previa)",
      exportacion: "Copia de todos sus datos (pedido de acceso)",
    };
    return Object.prototype.hasOwnProperty.call(es, a)
      ? T("admin.queue.access_log.action." + a, null, es[a]) : String(a || "");
  }

  function plazoTexto(dias) {
    var n = Number(dias) || 0;
    return n && n % 365 === 0
      ? Tn("admin.queue.access_log.years", n / 365, null, "{n} año", "{n} años")
      : Tn("admin.queue.access_log.days", n, null, "{n} día", "{n} días");
  }

  // Tres columnas, no cuatro: a 390px una cuarta quedaba fuera de la vista y
  // el «Negado» —lo que el admin viene a buscar— no se veía. El rechazo va en
  // la misma celda de lo que se intentó abrir.
  function accesosTablaHtml(j) {
    var items = Array.isArray(j.items) ? j.items : [];
    var html = '<p class="cnt-hint">' + esc(T("admin.queue.access_log.hint", { plazo: plazoTexto(j.retencion_dias) },
      "Cada vez que alguien del panel abre la ficha, la entrevista, el resumen, la orden u otro dato clínico de esta persona, queda registrado. El registro no se puede modificar y se guarda {plazo}.")) + "</p>";
    if (!items.length) {
      return html + '<p class="adm-empty">' + esc(T("admin.queue.access_log.empty", null, "Nadie abrió todavía los datos de esta persona.")) + "</p>";
    }
    return html + '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr>'
      + '<th class="cnt-th">' + esc(T("admin.queue.access_log.col.when", null, "Cuándo")) + "</th>"
      + '<th class="cnt-th">' + esc(T("admin.queue.access_log.col.who", null, "Quién")) + "</th>"
      + '<th class="cnt-th">' + esc(T("admin.queue.access_log.col.what", null, "Qué abrió")) + "</th>"
      + "</tr></thead><tbody>"
      + items.map(function (i) {
        return "<tr><td>" + esc(Dates.corta ? Dates.corta(i.dia, i.hora) : (i.dia + " " + i.hora)) + "</td>"
          + "<td>" + esc(i.usuario || "") + "</td>"
          + "<td>" + esc(accionTexto(i.accion)) + (i.permitido ? ""
            : "<br><strong>" + esc(T("admin.queue.access_log.denied", null, "Negado: no es su paciente")) + "</strong>")
          + "</td></tr>";
      }).join("")
      + "</tbody></table></div>";
  }

  function cargarAccesos() {
    var gen = routeGen, dni = fichaDni, out = $("#pat-accesos-out"), btn = $("#pac-accesos-ver");
    if (!dni || !out) return;
    if (btn) btn.disabled = true;
    api("/api/admin/patients/" + encodeURIComponent(dni) + "/access-log", gen).then(function (res) {
      if (!ownsRoute(gen)) return;
      if (!res.ok || !isObject(res.json)) throw new Error(String(res.status));
      out.innerHTML = accesosTablaHtml(res.json);
      if (btn) btn.hidden = true;
    }).catch(function (err) {
      if (!ownsRoute(gen) || (err && err.message === "stale")) return;
      out.innerHTML = '<p class="cnt-hint">' + esc(T("admin.queue.access_log.error", null, "No pudimos cargar el registro. Probá de nuevo.")) + "</p>";
      if (btn) btn.disabled = false;
    });
  }

  function depFormHtml(d) {
    d = d || {};
    var editando = !!d.dni;
    // Alta: el botón «Agregar persona a cargo» ya dijo de qué se trata, así que
    // el formulario no repite el título ni se queda el botón al lado; sólo
    // editar a alguien lleva título (el nombre de a quién).
    return '<div class="pat-dep-form">'
      + (editando
        ? '<h3 class="pat-dep-h3">' + esc(T("admin.queue.dependents.edit_title", { nombre: [d.apellido, d.nombre].filter(Boolean).join(", ") }, "Editar {nombre}")) + "</h3>"
        : "")
      + row(T("admin.queue.patients.col.dni", null, "DNI"), editando
        ? '<span class="pat-dni-fixed">' + (Format.dniHtml(d.dni) || esc(fmtDni(d.dni))) + '</span><p class="cnt-hint">' + esc(T("admin.queue.dependents.dni_hint_existing", null, "Si ya está a cargo de otro adulto, cargá el MISMO DNI desde su ficha y queda a cargo de los dos.")) + '</p>'
        : '<input class="cnt-in pat-dni" id="dep-dni" inputmode="numeric" maxlength="9" value=""><p class="cnt-hint">' + esc(T("admin.queue.dependents.dni_hint_new", null, "Si el chico ya existe, usá su mismo DNI: queda a cargo de los dos adultos.")) + '</p>')
      + row(T("admin.queue.patient_form.first_name", null, "Nombre"), '<input class="cnt-in pat-name" id="dep-nombre" value="' + esc(d.nombre || "") + '">')
      + row(T("admin.queue.patient_form.last_name", null, "Apellido"), '<input class="cnt-in pat-name" id="dep-apellido" value="' + esc(d.apellido || "") + '">')
      + row(T("admin.queue.patients.col.birth", null, "Nacimiento"), '<input class="cnt-in pat-birth" id="dep-nac" inputmode="numeric" placeholder="' + esc(T("admin.queue.patients.birth_placeholder", null, "DD/MM/AAAA")) + '" value="'
        + esc(fechaCorta(d.nacimiento)) + '"><p class="cnt-hint" id="dep-nac-eco"></p>')
      + row(T("admin.queue.patients.col.sex", null, "Sexo"), '<select class="adm-sel pat-sex" id="dep-sexo">'
        // sin opción elegida de antemano: un sexo que nadie eligió no se guarda
        + ["", "F", "M", "X"].map(function (s) {
          var lbl = { "": "—", F: T("admin.queue.patients.sex.f", null, "Femenino"), M: T("admin.queue.patients.sex.m", null, "Masculino"), X: T("admin.queue.patients.sex.x", null, "X") }[s];
          return '<option value="' + s + '"' + ((d.sexo || "") === s ? " selected" : "") + ">" + lbl + "</option>";
        }).join("") + "</select>")
      + row(T("admin.queue.dependents.relation", null, "Vínculo"), '<select class="adm-sel pat-sex" id="dep-vinculo">'
        + ["hijo", "hija", "tutelado", "tutelada", "otro"].map(function (v) {
          return '<option value="' + v + '"' + ((d.vinculo || "hijo") === v ? " selected" : "") + ">" + esc(vinculoLabel(v)) + "</option>";
        }).join("") + "</select>")
      + '<div class="cnt-actions"><button type="button" class="btn btn-accent" id="dep-save">' + esc(T("admin.queue.dependents.save", null, "Guardar")) + '</button>'
      + '<button type="button" class="pat-dep-link" id="dep-cancel">' + esc(T("admin.queue.dependents.cancel", null, "Cancelar")) + "</button></div>"
      + "</div>";
  }

  function abrirDepForm(d) {
    cancelDependentRefresh();
    var caja = $("#pat-dep-form");
    if (!caja) return;
    depEditando = d && d.dni ? d.dni : null;
    var seccion = $("#pat-deps");
    if (seccion) {
      if (depEditando) seccion.removeAttribute("data-form");
      else seccion.setAttribute("data-form", "new");
    }
    caja.innerHTML = depFormHtml(d);
    wireFechaEco("#dep-nac", "#dep-nac-eco");
    wireDniInput($("#dep-dni"));
    var dniIn = $("#dep-dni");
    if (dniIn && dniIn.focus) dniIn.focus();
  }

  function cancelDependentRefresh() {
    if (dependentRefresh) dependentRefresh.ctl.abort();
    dependentRefresh = null;
  }

  function recargarDependientes(dniActual, gen) {
    cancelDependentRefresh();
    var token = dependentRefresh = { ctl: new AbortController() };
    return api("/api/admin/patients/" + encodeURIComponent(dniActual), gen,
      { signal: token.ctl.signal }, function () { return dependentRefresh === token; }).then(function (res) {
      if (!ownsRoute(gen) || dependentRefresh !== token) return;
      if (!res.ok || !isObject(res.json) || !isObject(res.json.item) || !Array.isArray(res.json.dependientes)) {
        toast(T("admin.queue.dependents.refresh_error", null, "No se pudo actualizar las personas a cargo."));
        return;
      }
      var cont = $("#pat-deps");
      if (!cont) return;
      DEPS = res.json.dependientes;
      cont.outerHTML = dependientesHtml(dniActual, Array.isArray(res.json.a_cargo_de) ? res.json.a_cargo_de : []);
    }).catch(function () {
      if (ownsRoute(gen) && dependentRefresh === token) {
        toast(T("admin.queue.dependents.refresh_error", null, "No se pudo actualizar las personas a cargo."));
      }
    }).finally(function () {
      if (dependentRefresh === token) dependentRefresh = null;
    });
  }

  function beginMutation() {
    if (!alive() || flight) return null;
    cancelDependentRefresh();
    var token = { gen: routeGen, ctl: new AbortController() };
    token.buttons = Array.from(host.querySelectorAll('button')).filter(function (button) { return !button.disabled; });
    token.buttons.forEach(function (button) { button.disabled = true; });
    flight = token;
    return token;
  }
  function endMutation(token) {
    if (flight !== token) return;
    flight = null;
    token.buttons.forEach(function (button) { button.disabled = false; });
  }
  function mutationFailed(token, fallback) {
    if (flight !== token || !ownsRoute(token.gen)) return;
    endMutation(token);
    toast(fallback);
  }

  function guardarDependiente(dniActual, editando) {
    // una fecha escrita pero incomprensible se avisa EN SU CAMPO y frena el
    // guardado — mandarla como null borraría el dato en silencio.
    var nacIn = $("#dep-nac");
    var iso = parseFecha(String((nacIn && nacIn.value) || ""));
    if (!iso) {
      var eco = $("#dep-nac-eco");
      if (eco) {
        eco.textContent = T("admin.queue.common.date_invalid", { ejemplo: "28/10/2015" }, "No entiendo esa fecha. Probá {ejemplo}.");
        eco.className = "cnt-hint pat-echo-error";
      }
      if (nacIn && nacIn.focus) nacIn.focus();
      return;
    }
    var token = beginMutation();
    if (!token) return;
    var body = {
      dni: editando ? editando : Format.digits(($("#dep-dni") || {}).value),
      nombre: String(($("#dep-nombre") || {}).value || "").trim(),
      apellido: String(($("#dep-apellido") || {}).value || "").trim(),
      nacimiento: iso,
      sexo: ($("#dep-sexo") || {}).value,
      vinculo: ($("#dep-vinculo") || {}).value
    };
    api("/api/admin/patients/" + encodeURIComponent(dniActual) + "/dependents", token.gen, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: token.ctl.signal
    }).then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      if (!res.ok || !isObject(res.json) || res.json.ok !== true) {
        endMutation(token);
        toast(errText(res.json, T("admin.queue.common.save_error", null, "No se pudo guardar.")));
        return;
      }
      endMutation(token);
      toast(T("admin.queue.common.saved", null, "Guardado."));
      depEditando = null;
      var caja = $("#pat-dep-form");
      if (caja) caja.innerHTML = "";
      var seccionGuardada = $("#pat-deps");
      if (seccionGuardada) seccionGuardada.removeAttribute("data-form");
      recargarDependientes(dniActual, token.gen);
    }).catch(function () { mutationFailed(token, T("admin.queue.common.save_error", null, "No se pudo guardar.")); });
  }

  function quitarDependiente(dniActual, dep) {
    var otros = otrosAdultos(dep, dniActual);
    var msg = otros.length
      ? T("admin.queue.dependents.confirm_remove_shared", { otros: otros.map(function (a) { return [a.apellido, a.nombre].filter(Boolean).join(", "); }).join(", ") }, "¿Sacarlo de esta ficha? Sigue a cargo de {otros}.")
      : T("admin.queue.dependents.confirm_remove_only", null, "¿Sacar a esta persona de la ficha? Los turnos que ya sacó quedan.");
    if (!ask(msg)) return;
    var token = beginMutation();
    if (!token) return;
    api("/api/admin/patients/" + encodeURIComponent(dniActual) + "/dependents/" + encodeURIComponent(dep.dni), token.gen, {
      method: "DELETE", signal: token.ctl.signal
    }).then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      if (!res.ok || !isObject(res.json) || res.json.ok !== true) {
        endMutation(token);
        toast(errText(res.json, T("admin.queue.dependents.remove_error", null, "No se pudo.")));
        return;
      }
      endMutation(token);
      toast(T("admin.queue.dependents.remove_done", null, "Listo."));
      recargarDependientes(dniActual, token.gen);
    }).catch(function () { mutationFailed(token, T("admin.queue.dependents.remove_error", null, "No se pudo.")); });
  }

  // ---- formulario (alta / edición / archivo) ---------------------------------

  function renderForm(gen, dni) {
    var isNew = dni == null;
    host.removeAttribute("data-list-view");
    host.removeAttribute("data-disclosure");
    // La ficha se pide por DNI, no filtrando la lista: la lista NO trae
    // cobertura ni personas a cargo, así que editarla desde ahí guardaba la
    // cobertura vacía.
    var ctl = primary = new AbortController();
    var pre = isNew
      ? Promise.resolve({})
      : api("/api/admin/patients/" + encodeURIComponent(dni), gen, { signal: ctl.signal }).then(function (res) {
        if (res.status === 404) return { item: null };
        if (!res.ok) throw new Error(String(res.status));
        var j = res.json;
        if (!isObject(j)) throw new Error("malformed");
        if (j.item == null) return { item: null };
        if (!isObject(j.item)) throw new Error("malformed");
        return j;
      });

    return pre.then(function (data) {
      if (!ownsRoute(gen)) return;
      var item = isNew ? null : data.item;
      DEPS = (data.dependientes || []);
      depEditando = null;
      if (!isNew && !item) {
        host.innerHTML = '<p class="adm-empty">' + esc(T("admin.queue.patient_form.not_found", null, "No encontramos ese paciente.")) + ' <a href="/admin/pacientes">' + esc(T("admin.queue.patient_form.back_to_list", null, "Volver a la lista")) + '</a>.</p>';
        return;
      }
      item = item || {};
      var activo = item.activo !== false;
      formActivo = activo;

      var titulo = isNew
        ? T("admin.queue.patients.new", null, "Nuevo paciente")
        : (([item.apellido, item.nombre].filter(Boolean).join(", ")) || fmtDni(dni));

      // Mismo esqueleto que los editores de Servicios/Centros/Médicos y
      // Usuarios: crumb + h1 propio + filas .cnt-row apiladas + .cnt-actions.
      // OJO: NO envolver en .card — en este panel `.card` es la tarjeta de
      // una solicitud, así que un formulario adentro caía en columnas.
      var html = '<p class="cnt-crumb"><a href="/admin/pacientes">' + esc(T("admin.queue.patients.title", null, "Pacientes")) + '</a> › </p>';
      // La ficha de alguien que ya existe es SUS TURNOS (cancelar, confirmar,
      // marcar asistencia); los datos se corrigen detrás de «Editar datos».
      // Se abren solos en «Nuevo paciente» y en quien llegó sin nombre: ahí
      // la mesa viene justamente a completarlos.
      var datosAbiertos = isNew || !(item.nombre || item.apellido);
      if (isNew) html += '<h1 class="adm-h1 cnt-h1">' + esc(titulo) + "</h1>";
      else {
        html += fichaTopHtml(item, data, dni);
        html += turnosHtml(data.turnos);
      }
      html += '<div id="pac-datos"' + (datosAbiertos ? "" : " hidden") + ">";
      html += '<form id="pac-form">';
      html += row(T("admin.queue.patients.col.dni", null, "DNI"), isNew
        // ancho = contenido (7-9 dígitos) y la restricción ANTES del campo:
        // se normaliza sola al tipear igual.
        // El lector de la mesa se apoya y listo (dni-scan.mjs lo escucha en todo
        // el formulario); el botón es para sacarle una foto al documento.
        ? '<input class="cnt-in pat-dni" id="pat-dni" inputmode="numeric" autocomplete="off" maxlength="9">'
          + ' <button type="button" class="pat-scan" id="pat-scan">' + esc(T("admin.queue.create_appt.escanear_dni", null, "Escanear el DNI")) + '</button>'
          + '<input type="file" id="pat-scan-file" accept="image/*" hidden>'
          + '<p class="cnt-hint" id="pat-scan-msg" hidden></p>'
        : '<span class="pat-dni-fixed">' + (Format.dniHtml(dni) || esc(fmtDni(dni))) + "</span>");
      html += row(T("admin.queue.patient_form.first_name", null, "Nombre"), '<input class="cnt-in pat-name" id="pat-name" autocomplete="given-name" value="' + esc(item.nombre || "") + '">');
      html += row(T("admin.queue.patient_form.last_name", null, "Apellido"), '<input class="cnt-in pat-name" id="pac-apellido" autocomplete="family-name" value="' + esc(item.apellido || "") + '">');
      html += row(T("admin.queue.patients.col.birth", null, "Nacimiento"), '<input class="cnt-in pat-birth" id="pat-birth" inputmode="numeric" autocomplete="bday" placeholder="' + esc(T("admin.queue.patients.birth_placeholder", null, "DD/MM/AAAA")) + '" value="'
        + esc(fechaCorta(item.nacimiento)) + '"><p class="cnt-hint" id="pat-birth-eco"></p>');
      html += row(T("admin.queue.patients.col.sex", null, "Sexo"), '<select class="adm-sel pat-sex" id="pat-sex">'
        + ["", "F", "M", "X"].map(function (s) {
          var lbl = { "": "—", F: T("admin.queue.patients.sex.f", null, "Femenino"), M: T("admin.queue.patients.sex.m", null, "Masculino"), X: T("admin.queue.patients.sex.x", null, "X") }[s];
          return '<option value="' + s + '"' + ((item.sexo || "") === s ? " selected" : "") + ">" + lbl + "</option>";
        }).join("") + "</select>");
      html += row(T("admin.queue.patients.col.phone", null, "Teléfono"), '<input class="cnt-in pat-phone" id="pac-contacto" type="tel" inputmode="tel" autocomplete="tel" value="' + esc(item.contacto || "") + '">'
        + '<p class="cnt-hint pat-echo-error" id="pat-phone-eco" hidden></p>');
      html += row(T("admin.queue.patients.col.email", null, "Email"), '<input class="cnt-in pat-email" id="pat-email" type="email" autocomplete="email" value="' + esc(item.email || "") + '">');
      html += row(T("admin.queue.patients.col.coverage", null, "Cobertura"), coberturaHtml(item.cobertura, (data.cobertura_principal || {}).clave));
      if (isNew) {
        html += row(T("admin.queue.patient_form.password", null, "Contraseña"), '<p class="cnt-hint">' + esc(T("admin.queue.patient_form.password_hint", null, "Dejala vacía y el sistema genera una temporal, que se muestra una sola vez para dictársela al paciente.")) + '</p>'
          + '<input class="cnt-in cnt-in--password" id="pac-pass" type="password" autocomplete="new-password">');
      } else {
        // TASK-50 (26.10): the way back in for a patient without an email
        // who forgot the password — recovery only goes by email.
        html += row(T("admin.queue.patient_form.password", null, "Contraseña"),
          '<button type="button" class="btn btn-quiet" id="pac-temp-pass">'
          + esc(T("admin.queue.patient_form.new_temp_password", null, "Nueva contraseña temporal")) + "</button>"
          + '<div id="pac-temp-out"></div>');
      }
      html += "</form>";
      fichaDni = isNew ? null : dni;
      FICHA_PACIENTE = isNew ? null : { dni: String(dni), apellido: item.apellido || "", nombre: item.nombre || "" };
      html += '<div id="pac-out"></div>';
      html += '<div class="cnt-actions">'
        + '<button type="button" class="btn btn-accent" id="pac-save">' + esc(isNew ? T("admin.queue.patient_form.create", null, "Crear paciente") : T("admin.queue.dependents.save", null, "Guardar")) + "</button>"
        + (isNew ? "" : '<span class="cnt-actions-danger"><button type="button" class="btn btn-quiet" id="pac-arch">'
          + esc(activo ? T("admin.queue.patient_form.archive", null, "Archivar") : T("admin.queue.patient_form.restore", null, "Restaurar")) + "</button></span>")
        + "</div></div>";
      if (!isNew) html += dependientesHtml(dni, data.a_cargo_de || []);
      if (!isNew && data.ver_accesos === true) html += accesosHtml();
      host.innerHTML = html;
      observarTurnos();
      var botonEditar = $("#pac-edit");
      if (botonEditar && datosAbiertos) botonEditar.setAttribute("aria-expanded", "true");
      var h1c = doc().getElementById("adm-h1");
      // Responsable del producto 2026-11-04: dentro de la ficha ya está la miga «Pacientes ›»; la
      // línea «Pacientes» de arriba sobra. El título de la página es el nombre
      // de la persona (h1 de la ficha) o el de «Nuevo paciente».
      if (h1c) h1c.style.display = "none";
      wireDniInput($("#pat-dni"));
      wireFechaEco("#pat-birth", "#pat-birth-eco");
      wireDocumento();
    });
  }

  // ---- leer el documento: lector de teclado o foto -------------------------
  // Los dos caminos dan lo mismo (dni-scan.mjs) y llenan TODO lo que el
  // documento dice: DNI, apellido, nombre, nacimiento y sexo.
  function scanMsg(txt) {
    var e = $("#pat-scan-msg");
    if (!e) return;
    e.textContent = txt || "";
    e.hidden = !txt;
  }

  function usarDocumento(p, mod) {
    if (!p || !$("#pat-dni")) return;
    var legible = (mod && mod.nombreLegible) || function (v) { return v; };
    var poner = function (sel, v) { var el = $(sel); if (el && v) el.value = v; };
    poner("#pat-dni", p.dni);
    poner("#pac-apellido", legible(p.apellido));
    poner("#pat-name", legible(p.nombres));
    poner("#pat-sex", p.sexo);
    var nac = $("#pat-birth");
    if (nac && p.nacimiento) {
      nac.value = fechaCorta(p.nacimiento);
      if (nac.dispatchEvent && typeof Event === "function") nac.dispatchEvent(new Event("input", { bubbles: true }));
    }
    scanMsg(T("admin.queue.patient_form.document_read", null, "Documento leído. Revisá los datos."));
  }

  var soltarLector = null;
  function wireDocumento() {
    if (soltarLector) { soltarLector(); soltarLector = null; }
    var form = $("#pac-form"), btn = $("#pat-scan"), file = $("#pat-scan-file");
    if (!form || !btn || !file) return;
    var scan = importStatic("platform/dni-scan.mjs").catch(function () { return null; });
    scan.then(function (mod) {
      if (!mod || !mod.escucharLectorEnFormulario || $("#pac-form") !== form) return;
      soltarLector = mod.escucharLectorEnFormulario(form, function (p) { usarDocumento(p, mod); });
    });
    btn.addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () {
      var f = file.files && file.files[0];
      if (!f) return;
      scanMsg(T("admin.queue.create_appt.leyendo_documento", null, "Leyendo el documento…"));
      Promise.all([importStatic("platform/dni-barcode.mjs"), scan]).then(function (mods) {
        return mods[0].readDni(f).then(function (p) {
          if (!p) { scanMsg(T("admin.queue.create_appt.dni_no_leido_codigo", null, "No se pudo leer el código del DNI. Probá de nuevo o escribí el dato.")); return; }
          usarDocumento(p, mods[1]);
        });
      }).catch(function () {
        scanMsg(T("admin.queue.create_appt.dni_no_leido_documento", null, "No se pudo leer el documento. Escribí el dato."));
      }).then(function () { file.value = ""; });
    });
  }

  // Vacío vale (el teléfono no es obligatorio); con letras o «@» no es un
  // número. Misma regla que admin_patients._telefono_valido.
  function telefonoValido(txt) {
    var s = String(txt || "").trim();
    if (!s) return true;
    return !/[\p{L}@]/u.test(s) && s.replace(/\D/g, "").length >= 6;
  }

  function guardarPaciente(isNew, dni) {
    // una fecha escrita pero incomprensible se avisa EN SU CAMPO y frena el
    // guardado — mandarla como null borraría el dato en silencio.
    var nacIn = $("#pat-birth");
    var txtNac = String((nacIn && nacIn.value) || "").trim();
    if (txtNac && !parseFecha(txtNac)) {
      var eco = $("#pat-birth-eco");
      if (eco) {
        eco.textContent = T("admin.queue.common.date_invalid", { ejemplo: "28/10/1978" }, "No entiendo esa fecha. Probá {ejemplo}.");
        eco.className = "cnt-hint pat-echo-error";
      }
      if (nacIn && nacIn.focus) nacIn.focus();
      return;
    }
    // Lo mismo con el teléfono: un email (o cualquier texto con letras) en
    // «Teléfono» se avisa en su campo — el server lo rechaza igual.
    var telIn = $("#pac-contacto");
    var telEco = $("#pat-phone-eco");
    if (telEco) telEco.hidden = true;
    if (!telefonoValido(String((telIn && telIn.value) || ""))) {
      if (telEco) {
        telEco.textContent = T("admin.queue.patient_form.phone_invalid", { ejemplo: "2254 44-5566" }, "Esto no es un teléfono. Escribí el número, por ejemplo {ejemplo}.");
        telEco.hidden = false;
      }
      if (telIn && telIn.focus) telIn.focus();
      return;
    }
    // El alta pide lo que HSI va a pedir para empadronar: nacimiento y sexo.
    if (isNew && (!txtNac || !($("#pat-sex") || {}).value)) {
      var falta = !txtNac ? nacIn : $("#pat-sex");
      toast(T("admin.queue.patient_form.missing_birth_sex", null, "Faltan fecha de nacimiento y sexo."));
      if (falta && falta.focus) falta.focus();
      return;
    }
    var token = beginMutation();
    if (!token) return;
    var body = {
      nombre: String(($("#pat-name") || {}).value || "").trim(),
      apellido: String(($("#pac-apellido") || {}).value || "").trim(),
      // se manda lo que el sistema ENTENDIÓ (ISO), no lo que se tipeó
      nacimiento: parseFecha(String((nacIn && nacIn.value) || "")) || null,
      sexo: ($("#pat-sex") || {}).value || null,
      contacto: String(($("#pac-contacto") || {}).value || "").trim() || null,
      email: String(($("#pat-email") || {}).value || "").trim() || null,
      // mandarla SIEMPRE (aunque esté vacía) es lo que permite sacar una
      // cobertura: el server sólo conserva lo guardado si la key no viene
      cobertura: leerCobertura()
    };
    var req;
    if (isNew) {
      body.dni = Format.digits(($("#pat-dni") || {}).value);
      var pass = ($("#pac-pass") || {}).value;
      if (pass) body.password = pass;
      req = api("/api/admin/patients", token.gen, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: token.ctl.signal });
    } else {
      req = api("/api/admin/patients/" + encodeURIComponent(dni), token.gen, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: token.ctl.signal });
    }
    req.then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      // HTTP 200 no es un acuse: el cuerpo tiene que decir {ok, item}.
      if (!res.ok || !mutationAck(res.json)) {
        endMutation(token);
        toast(errText(res.json, T("admin.queue.common.save_error", null, "No se pudo guardar.")));
        return;
      }
      var temp = res.json.password_temporal;
      if (isNew && typeof temp === "string" && temp) {
        mostrarTemporal(temp);
        endMutation(token);
        toast(T("admin.queue.patient_form.created", null, "Paciente creado."));
        return; // la contraseña queda a la vista; la lista espera un click
      }
      endMutation(token);
      toast(isNew ? T("admin.queue.patient_form.created", null, "Paciente creado.") : T("admin.queue.common.saved", null, "Guardado."));
      loc().assign("/admin/pacientes");
    }).catch(function () { mutationFailed(token, T("admin.queue.common.save_error", null, "No se pudo guardar.")); });
  }

  // The temporary password, printed once to dictate it — the same block for a
  // new patient (at the foot, by «Crear paciente») and for «Nueva contraseña
  // temporal» (right under its button).
  function mostrarTemporal(temp, where) {
    var out = $(where || "#pac-out");
    if (out) out.innerHTML = '<div class="usr-totp"><p><b>' + esc(T("admin.queue.patient_form.temp_password", null, "Contraseña temporal:")) + '</b> '
      + '<code>' + esc(temp) + "</code> — " + esc(T("admin.queue.patient_form.temp_password_note", null, "copiala ahora, no se vuelve a mostrar.")) + "</p></div>";
  }

  // TASK-50: a new temporary password for an existing patient. The old one
  // stops working and their open sessions close, so the desk confirms first.
  function nuevaTemporal(dni) {
    if (!ask(T("admin.queue.patient_form.confirm_temp_password", null,
      "¿Darle una contraseña temporal nueva? La que tiene deja de funcionar."))) return;
    var token = beginMutation();
    if (!token) return;
    api("/api/admin/patients/" + encodeURIComponent(dni) + "/temp-password", token.gen, { method: "POST", signal: token.ctl.signal })
      .then(function (res) {
        if (flight !== token) return;
        if (!ownsRoute(token.gen)) { endMutation(token); return; }
        var temp = res.ok && res.json && res.json.ok === true ? res.json.password_temporal : null;
        endMutation(token);
        if (typeof temp !== "string" || !temp) {
          toast(errText(res.json, T("admin.queue.common.save_error", null, "No se pudo guardar.")));
          return;
        }
        mostrarTemporal(temp, "#pac-temp-out");
      }).catch(function () { mutationFailed(token, T("admin.queue.common.save_error", null, "No se pudo guardar.")); });
  }

  // Con turnos vivos el servidor no archiva: responde 409 con la lista
  // (2026-10-23). La mesa la ve y confirma, y el mismo pedido vuelve con
  // ?cancelar_turnos=1 — se cancelan como con «Cancelar turno» (el paciente
  // recibe el aviso) y recién entonces se archiva.
  function turnosVivosTexto(turnos) {
    return turnos.map(function (t) {
      var cuando = t.dia ? Dates.amigable(t.dia, t.hora) : T("admin.queue.patient_form.archive_pending", null, "pendiente, sin horario");
      return "• " + (t.servicio ? t.servicio + " — " : "") + cuando;
    }).join("\n");
  }

  function archivarPaciente(dni, activo, cancelarTurnos) {
    if (activo && !cancelarTurnos && !ask(T("admin.queue.patient_form.confirm_archive", null, "¿Archivar este paciente? No va a poder entrar más a su cuenta; su historial queda."))) return;
    var token = beginMutation();
    if (!token) return;
    var req = activo
      ? api("/api/admin/patients/" + encodeURIComponent(dni) + (cancelarTurnos ? "?cancelar_turnos=1" : ""), token.gen, { method: "DELETE", signal: token.ctl.signal })
      : api("/api/admin/patients/" + encodeURIComponent(dni), token.gen, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ activo: true }), signal: token.ctl.signal });
    req.then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      if (activo && res.status === 409 && res.json && res.json.error === "turnos_activos" && Array.isArray(res.json.turnos)) {
        endMutation(token);
        // Another center's turnos arrive only as a count: this account
        // cannot see them, so they are not described (and cannot be cancelled).
        var ajenos = Number(res.json.turnos_ajenos) || 0;
        if (cancelarTurnos || !res.json.turnos.length) {
          var hechos = Number(res.json.turnos_cancelados) || 0;
          toast(hechos
            ? Tn("admin.queue.patient_form.archive_left_partial", hechos,
              { m: Tn("admin.queue.patient_form.archive_left_rest", res.json.turnos.length + ajenos, null, "queda {n}", "quedan {n}") },
              "Se canceló {n} turno; {m} que no podés cancelar desde tu cuenta: el paciente no se archivó.",
              "Se cancelaron {n} turnos; {m} que no podés cancelar desde tu cuenta: el paciente no se archivó.")
            : T("admin.queue.patient_form.archive_left", null, "Quedan turnos que no podés cancelar desde tu cuenta: el paciente no se archivó."));
          return;
        }
        var n = res.json.turnos.length, lista = turnosVivosTexto(res.json.turnos);
        var pregunta = Tn("admin.queue.patient_form.confirm_archive_cancel", n, { lista: lista },
          "Tiene {n} turno por venir:\n{lista}\n\n¿Cancelarlo y archivar? Se le avisa al paciente que se canceló.",
          "Tiene {n} turnos por venir:\n{lista}\n\n¿Cancelarlos y archivar? Se le avisa al paciente que se cancelaron.");
        if (ajenos) {
          pregunta += "\n\n" + Tn("admin.queue.patient_form.archive_foreign_note", ajenos, null,
            "Tiene otro en otro centro que no podés cancelar desde tu cuenta: el paciente no se va a archivar.",
            "Tiene otros {n} en otros centros que no podés cancelar desde tu cuenta: el paciente no se va a archivar.");
        }
        if (ask(pregunta)) {
          archivarPaciente(dni, activo, true);
        }
        return;
      }
      if (!res.ok || !mutationAck(res.json)) {
        endMutation(token);
        toast(errText(res.json, T("admin.queue.dependents.remove_error", null, "No se pudo.")));
        return;
      }
      endMutation(token);
      toast(activo ? T("admin.queue.patient_form.archived", null, "Paciente archivado.") : T("admin.queue.patient_form.restored", null, "Paciente restaurado."));
      loc().assign("/admin/pacientes");
    }).catch(function () { mutationFailed(token, T("admin.queue.dependents.remove_error", null, "No se pudo.")); });
  }

  // ---- routing propio (mismo contrato que la clásica) ------------------------

  function directState() {
    var params = new URLSearchParams(loc().search);
    var patient = params.get("patient") || "";
    var isNew = params.get("new") === "1";
    if (patient && isNew) {
      hist().replaceState({}, "", "/admin/pacientes?patient=" + encodeURIComponent(patient));
      isNew = false;
    }
    return { id: isNew ? "nuevo" : patient };
  }

  // Invalidates every pending continuation of the outgoing route: the search
  // debounce timer, the list request (search/list/load-more), the detail
  // fetch and any in-flight mutation. Their busy states belong to the dead
  // route, so the new route starts clean — an old request's completion can
  // never unlock (or paint for) a newer owner.
  function cancelPendingWork() {
    cancelDependentRefresh();
    if (searchTimer !== null) {
      clearTimeout(searchTimer);
      searchTimer = null;
    }
    if (listReq && listReq.ctl) listReq.ctl.abort();
    listReq = null;
    if (primary) { primary.abort(); primary = null; }
    if (loadingMore) {
      if (loadingMore.ctl) loadingMore.ctl.abort();
      loadingMore = null;
    }
    if (flight) {
      if (flight.ctl) flight.ctl.abort();
      flight = null;
    }
  }

  function route() {
    if (!alive()) return Promise.resolve();
    cancelPendingWork();
    var gen = ++routeGen;
    // The URL now identifies a different record. Withdraw the old controls
    // immediately: otherwise Save could submit patient A's visible fields
    // to patient B's newly selected URL while B's detail is still loading.
    host.innerHTML = '<p class="adm-empty" role="status">'
      + esc(T("admin.queue.common.loading", null, "Cargando…")) + '</p>';
    var d = doc();
    $$("#adm-nav a", d).forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-nav") === "pacientes"); });
    var h1 = d.getElementById("adm-h1");
    if (h1) { h1.style.display = ""; h1.textContent = T("admin.queue.patients.title", null, "Pacientes"); }
    var sub = d.getElementById("adm-sub");
    if (sub) sub.style.display = "none";

    var parsed = directState();
    var p = parsed.id === "nuevo" ? renderForm(gen, null) : (parsed.id ? renderForm(gen, parsed.id) : renderList(gen));
    return p.catch(function (e) {
      if (!ownsRoute(gen)) return;
      var m = String(e && e.message);
      if (m === "401" || m === "stale") return;
      paintRouteError(m === "403" ? 403 : 0);
    });
  }

  // ---- turnos de la ficha: cancelar y marcar asistencia ----------------------

  // «Editar datos»: abre y cierra el formulario de la persona (el DNI, el
  // nombre, el teléfono…). Cerrado es la ficha normal: sus turnos.
  function alternarDatos(boton) {
    var caja = $("#pac-datos");
    if (!caja) return;
    var abrir = caja.hasAttribute("hidden");
    if (abrir) caja.removeAttribute("hidden"); else caja.setAttribute("hidden", "");
    boton.setAttribute("aria-expanded", abrir ? "true" : "false");
    if (abrir) {
      var primero = $("#pat-name");
      if (primero && primero.focus) primero.focus();
    }
  }

  function turnoFicha(id) {
    return TURNOS_FICHA.filter(function (x) { return String(x.id) === String(id); })[0];
  }

  // La pregunta de cancelar va en la misma fila, no en una ventana del
  // navegador (como en la cola).
  function preguntarCancelar(id, confirmando) {
    var t = turnoFicha(id);
    var fila = $('[data-pt-fila="' + String(id).replace(/"/g, "") + '"]');
    var celda = fila && fila.querySelector(".pat-appt-acc");
    if (t && celda) celda.innerHTML = turnoAccionesHtml(t, confirmando);
  }

  function accionTurno(id, accion, valor) {
    var token = beginMutation();
    if (!token) return;
    var dni = directState().id;
    var cancelar = accion === "cancel";
    var restaurar = accion === "restore";
    api("/api/admin/requests/" + encodeURIComponent(id) + "/" + (cancelar || restaurar ? accion : "attendance"), token.gen, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: cancelar || restaurar ? "{}" : JSON.stringify({ asistencia: valor }), signal: token.ctl.signal
    }).then(function (res) {
      if (flight !== token) return;
      if (!ownsRoute(token.gen)) { endMutation(token); return; }
      if (!res.ok) {
        endMutation(token);
        toast(errText(res.json, cancelar
          ? T("admin.queue.cancel.error", null, "No se pudo cancelar el turno.")
          : restaurar
            ? T("admin.queue.patient_appts.restore_error", null, "Ese turno ya no se puede restaurar. Reprogramalo.")
            : T("admin.queue.common.save_error", null, "No se pudo guardar.")));
        // El horario se ocupó o pasó entre que se abrió la ficha y el clic:
        // la ficha se vuelve a pedir y Restaurar desaparece de esa fila.
        if (restaurar && res.status === 409) renderForm(token.gen, dni);
        return;
      }
      endMutation(token);
      toast(cancelar
        ? T("admin.queue.patient_appts.cancelled_done", null, "Turno cancelado.")
        : restaurar
          ? T("admin.queue.patient_appts.restored_done", null, "Turno restaurado.")
          : T("admin.queue.patient_appts.attendance_done", null, "Asistencia guardada."));
      renderForm(token.gen, dni);
    }).catch(function () {
      mutationFailed(token, T("admin.queue.attendance.save_error_network", null, "No se pudo guardar. Revisá tu conexión."));
    });
  }

  // Reprogramar un turno cancelado es sacar uno nuevo desde la mesa: el mismo
  // modal que «Nuevo turno» de la cola, con este paciente, el servicio y el
  // médico del cancelado ya elegidos; sólo falta el horario.
  function reprogramar(id) {
    var t = turnoFicha(id);
    if (!t || !FICHA_PACIENTE) return;
    var gen = routeGen;
    var paciente = FICHA_PACIENTE;
    importStatic("platform/admin-create-appointment.mjs").then(function (m) {
      if (!ownsRoute(gen) || !m || typeof m.abrir !== "function") return;
      m.abrir({ paciente: paciente, servicio_id: t.servicio_id || "", medico: t.medico || "" }, {
        onCreated: function () { if (ownsRoute(gen)) renderForm(gen, paciente.dni); }
      });
    }).catch(function () {
      toast(T("admin.queue.common.load_error", null, "No se pudo cargar. Reintentá."));
    });
  }

  // ---- eventos (delegados al host: viven una vez por mount) ------------------

  function onClick(e) {
    if (!alive() || !e || !e.target) return;
    var t = e.target;
    if (t.closest && t.closest("[data-pat-mas]")) { cargarMas(); return; }
    var claimBtn = t.closest && t.closest("[data-claim-resolve]");
    if (claimBtn) {
      resolverReclamo(Number(claimBtn.getAttribute("data-claim-id")), claimBtn.getAttribute("data-claim-resolve") === "1");
      return;
    }
    if (t.closest && t.closest("[data-clear-filters]")) { limpiarFiltros(); return; }
    var th = t.closest && t.closest("th[data-sort]");
    if (th && !(t.closest && t.closest("[data-tbl-res]"))) {
      var key = th.getAttribute("data-sort");
      if (!key) return;
      // Ascendente al estrenar la columna: en Asistencia eso deja arriba a
      // quien más falta, que es a quién hay que llamar antes.
      ORDEN = ORDEN.key === key ? { key: key, dir: -ORDEN.dir } : { key: key, dir: 1 };
      if (Prefs) Prefs.set("patients.sort", ORDEN);
      paintTable();
      return;
    }
    if (t.closest && t.closest("#pat-coverage-add")) {
      var cob = leerCobertura(true);
      cob.obras_sociales.push({ nombre: "", origen: "declarada" });
      repintarCobertura(cob);
      var inputs = $$("[data-cob-nombre]");
      if (inputs.length && inputs[inputs.length - 1].focus) inputs[inputs.length - 1].focus();
      return;
    }
    var quitar = t.closest && t.closest("[data-cob-quitar]");
    if (quitar) {
      var c = leerCobertura(true);
      c.obras_sociales.splice(+quitar.getAttribute("data-cob-quitar"), 1);
      repintarCobertura(c);
      return;
    }
    if (t.closest && t.closest("#pac-dep-add")) { abrirDepForm(null); return; }
    if (t.closest && t.closest("#pac-accesos-ver")) { cargarAccesos(); return; }
    if (t.closest && t.closest("#pac-datos-exportar")) { exportarDatos(); return; }
    var ed = t.closest && t.closest("[data-dep-edit]");
    if (ed) {
      var dniEd = ed.getAttribute("data-dep-edit");
      abrirDepForm(DEPS.filter(function (x) { return String(x.dni) === String(dniEd); })[0]);
      return;
    }
    var q = t.closest && t.closest("[data-dep-quitar]");
    if (q) {
      var qd = q.getAttribute("data-dep-quitar");
      var dep = DEPS.filter(function (x) { return String(x.dni) === String(qd); })[0] || {};
      var dniActual = directState().id;
      quitarDependiente(dniActual, dep);
      return;
    }
    if (t.closest && t.closest("#dep-cancel")) {
      depEditando = null;
      var caja = $("#pat-dep-form");
      if (caja) caja.innerHTML = "";
      var seccionDep = $("#pat-deps");
      if (seccionDep) seccionDep.removeAttribute("data-form");
      return;
    }
    if (t.closest && t.closest("#dep-save")) {
      guardarDependiente(directState().id, depEditando);
      return;
    }
    var editar = t.closest && t.closest("#pac-edit");
    if (editar) { alternarDatos(editar); return; }
    var pc = t.closest && t.closest("[data-pt-cancel]");
    if (pc) { preguntarCancelar(pc.getAttribute("data-pt-cancel"), true); return; }
    var pn = t.closest && t.closest("[data-pt-cancel-no]");
    if (pn) { preguntarCancelar(pn.getAttribute("data-pt-cancel-no"), false); return; }
    var py = t.closest && t.closest("[data-pt-cancel-yes]");
    if (py) { accionTurno(py.getAttribute("data-pt-cancel-yes"), "cancel"); return; }
    var pa = t.closest && t.closest("[data-pt-asis]");
    if (pa) { accionTurno(pa.getAttribute("data-pt-asis"), "attendance", pa.getAttribute("data-valor")); return; }
    var pr = t.closest && t.closest("[data-pt-restore]");
    if (pr) { accionTurno(pr.getAttribute("data-pt-restore"), "restore"); return; }
    var pg = t.closest && t.closest("[data-pt-reprog]");
    if (pg) { reprogramar(pg.getAttribute("data-pt-reprog")); return; }
    if (t.closest && t.closest("#pac-save")) {
      var id = directState().id;
      guardarPaciente(id === "nuevo", id === "nuevo" ? null : id);
      return;
    }
    if (t.closest && t.closest("#pac-arch")) {
      archivarPaciente(directState().id, formActivo);
      return;
    }
    if (t.closest && t.closest("#pac-temp-pass")) {
      nuevaTemporal(directState().id);
    }
  }

  function onChange(e) {
    if (!alive() || !e || !e.target) return;
    var el = e.target;
    var filtro = { "pac-f-estado": "estado", "pac-f-banda": "banda", "pac-f-cobertura": "cobertura" }[el.id];
    if (filtro) {
      FILTROS[filtro] = el.value;
      if (Prefs) Prefs.set("patients.filters", FILTROS);
      if (el.classList) el.classList.toggle("on", !!el.value);
      paintTable();
    }
  }

  function onInput(e) {
    if (!alive() || !e || !e.target) return;
    if (e.target.hasAttribute && e.target.hasAttribute("data-cob-nombre")) {
      var row = e.target.closest(".pat-coverage-row");
      var radio = row && row.querySelector('input[name="pac-principal"]');
      if (radio) radio.value = String(e.target.value || "").trim();
      return;
    }
    if (e.target.id !== "pac-q") return;
    var q = e.target;
    if (searchTimer !== null) clearTimeout(searchTimer);
    var v = String(q.value || "").trim();
    // The new intent owns the input immediately, not 300ms later. An old
    // response or session check must not erase typing during debounce.
    if (listReq && listReq.ctl) listReq.ctl.abort();
    listReq = null;
    if (loadingMore && loadingMore.ctl) loadingMore.ctl.abort();
    loadingMore = null;
    searchTimer = setTimeout(function () {
      searchTimer = null;
      if (!alive()) return;
      Q = v;
      if (Prefs) Prefs.set("patients.search", Q);
      var gen = routeGen;
      var result = fetchList(gen);
      var token = listReq;
      result.then(function () {
        if (!ownsRoute(gen) || listReq !== token) return;
        var nq = $("#pac-q");
        if (nq && nq.focus) {
          nq.focus();
          if (nq.setSelectionRange) nq.setSelectionRange(String(nq.value || "").length, String(nq.value || "").length);
        }
      });
    }, 300);
  }

  function onKeydown(e) {
    if (!alive() || !e || !e.target) return;
    if (e.key !== "Enter" && e.key !== " ") return;
    var th = e.target.closest && e.target.closest("th[data-sort]");
    if (!th) return;
    if (e.preventDefault) e.preventDefault();
    // Ordenar por teclado además de por mouse: la mesa de admisión incluye a
    // una persona que trabaja con lector de pantalla.
    var key = th.getAttribute("data-sort");
    if (!key) return;
    ORDEN = ORDEN.key === key ? { key: key, dir: -ORDEN.dir } : { key: key, dir: 1 };
    if (Prefs) Prefs.set("patients.sort", ORDEN);
    paintTable();
  }

  // Intercepta la navegación interna (nombre, crumb, "Nuevo paciente", nav):
  // pushState + route, sin recargar la página.
  function onDocClick(event) {
    if (!alive() || !event || event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest && event.target.closest('a[href^="/admin/pacientes"]');
    if (!link) return;
    if (event.preventDefault) event.preventDefault();
    hist().pushState({}, "", link.getAttribute("href"));
    route();
  }

  function onPopstate() {
    if (!alive()) return;
    route();
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (ownedStyle) { ownedStyle.remove(); ownedStyle = null; }
    routeGen += 1;
    cancelPendingWork();
    host.removeEventListener("click", onClick);
    host.removeEventListener("change", onChange);
    host.removeEventListener("input", onInput);
    host.removeEventListener("keydown", onKeydown);
    var d = doc();
    if (d && d.removeEventListener) d.removeEventListener("click", onDocClick);
    var w = typeof window !== "undefined" ? window : globalThis;
    if (w && w.removeEventListener) w.removeEventListener("popstate", onPopstate);
    if (soltarLector) { soltarLector(); soltarLector = null; }
    if (medObs) { medObs.disconnect(); medObs = null; medObsEl = null; medObsDocs = null; }
    if (turObs) { turObs.disconnect(); turObs = null; }
    if (medFrame !== null && typeof window !== "undefined" && window.cancelAnimationFrame) { window.cancelAnimationFrame(medFrame); medFrame = null; }
    if (ANCHOS) ANCHOS.dispose();
    if (COLPICK) COLPICK.dispose();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }

  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing patients dependency: " + name));
  }

  function take(mod, name, check) {
    if (!mod || !check(mod)) throw new Error("Invalid patients dependency: " + name);
    return mod;
  }

  mounts.set(host, dispose);
  var dependencies = Promise.all([
    importStatic("platform/admin-session.mjs"),
    importStatic("platform/i18n.mjs"),
    importStatic("platform/dates.mjs"),
    importStatic("platform/format.mjs"),
    importStatic("platform/admin-preferences.mjs"),
    importStatic("platform/admin-table.mjs"),
    importStatic("platform/account-form.mjs"),
    importStatic("platform/admin-filter-controls.mjs"),
  ]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function (mods) {
    if (!alive()) return;
    Session = take(mods[0], "platform/admin-session.mjs", function (m) { return typeof m.confirmar === "function"; });
    I18n = take(mods[1], "platform/i18n.mjs", function (m) { return typeof m.t === "function"; });
    prepareFilterLabels();
    Dates = take(mods[2], "platform/dates.mjs", function (m) { return typeof m.nacimiento === "function" && typeof m.amigable === "function" && typeof m.pasada === "function"; });
    Format = take(mods[3], "platform/format.mjs", function (m) {
      return typeof m.dni === "function" && typeof m.edad === "function" && typeof m.edadTexto === "function"
        && typeof m.personaEdad === "function" && typeof m.telNacional === "function"
        && typeof m.contactoIconoHtml === "function" && typeof m.medicosTexto === "function" && typeof m.medicosTitulo === "function"
        && typeof m.medicosLista === "function" && typeof m.dniHtml === "function";
    });
    Prefs = take(mods[4], "platform/admin-preferences.mjs", function (m) {
      return typeof m.load === "function" && typeof m.get === "function" && typeof m.set === "function";
    });
    Table = take(mods[5], "platform/admin-table.mjs", function (m) {
      return typeof m.crear === "function" && typeof m.columnas === "function";
    });
    Account = take(mods[6], "platform/account-form.mjs", function (m) {
      return typeof m.parseFlexibleDate === "function" && typeof m.isoToArg === "function";
    });
    Filters = take(mods[7],"platform/admin-filter-controls.mjs", function (m) {
      return typeof m.applyDisclosure === "function" && typeof m.showIfChoice === "function"
        && typeof m.offerFilter === "function" && typeof m.emptyStateHtml === "function" && typeof m.noMatchHtml === "function";
    });
    return Prefs.load().then(function () {
      if (!alive()) return;
      installStyle();
      host.addEventListener("click", onClick);
      host.addEventListener("change", onChange);
      host.addEventListener("input", onInput);
      host.addEventListener("keydown", onKeydown);
      var d = doc();
      if (d && d.addEventListener) d.addEventListener("click", onDocClick);
      var w = typeof window !== "undefined" ? window : globalThis;
      if (w && w.addEventListener) w.addEventListener("popstate", onPopstate);
      return route();
    });
  }).catch(function (error) {
    if (!alive()) return;
    paintRouteError(0);
    throw error;
  });
  return dispose;
}
