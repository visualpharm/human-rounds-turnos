/* admin-queue.mjs — "Pacientes de hoy" (2026-07-24): la cola de turnos
 * aprobados de UN día, con marcado de asistencia en un click. Its direct
 * /admin/cola screen owns its CSS and rendering lifecycle.
 *
 * 2026-11-09 (the product owner, supersedes the column rounds below): the row is the hour,
 * «Name, N years» as one link with the attendance bars and the «Orden» clip,
 * and the doctor with the service under it. DNI, Atendió/No vino, what the
 * AI read (Prácticas, Diagnóstico) and the symptoms summary live in the
 * appointment card (overlayHtml), with the AI mark next to the AI text.
 *
 * Datos: GET /api/admin/day-queue?dia=YYYY-MM-DD (doctor_appointments.py, abierto
 * a admin/operador/medico) -> {dia, hoy, grupos:[{medico_id,medico,turnos}],
 * total}. Un rol 'medico' recibe UN solo grupo (el suyo); admin/operador
 * reciben todos los médicos con turnos aprobados ese día + un grupo
 * `medico:null` para lo sin médico asignado (laboratorio) — el filtro por
 * médico de acá es enteramente client-side sobre esa misma respuesta (no
 * hace falta un segundo pedido con ?medico=, ya tenemos todo el día).
 *
 * Cada turno ya trae `asistencia`/`asistencia_en`/`elegible_asistencia`
 * server-computados (doctor_appointments._appointment_public) — acá sólo se pinta un
 * botón sólido "Atendió" + uno gris "No vino" (un click cada uno, sin
 * combo+Guardar) que llaman a POST /api/admin/requests/<id>/attendance
 * {asistencia}, el mismo endpoint que ya usa la tabla de resueltas de
 * Solicitudes (ahora también abierto a rol médico, dueño de su turno).
 *
 * Attendance timestamps always come from the server. The mutation response
 * uses asistencia_actualizada_en and the queue projection uses asistencia_en;
 * both are formatted in ART, regardless of the database connection's offset.
 *
 * Ronda 2 (2026-07-24, mismo día): la tabla suma columnas Prácticas/
 * Diagnóstico (de ai_campos, ver campoTexto) entre Servicio y Orden; el
 * overlay de la orden ahora elige <img>/<embed>/<object> según orden_mime
 * (agregado en doctor_appointments._appointment_public — root cause del box vacío era
 * un <object> sin type) y se agranda a dos columnas (campos angostos +
 * documento grande, ver ordenHtml/CSS .solmod*).
 *
 * Ronda 3 (2026-07-24, el responsable del producto — no relitigar): UNA LÍNEA POR TURNO y anchos
 * medidos, no cableados.
 *  - Los anchos px por nth-child murieron. `autoFit()` mide el ancho NATURAL
 *    de cada columna (pasada con table-layout:auto + celdas sin recortar,
 *    clase .queue-measuring), toma el máximo de TODAS las tablas de médico
 *    (así siguen alineadas entre sí) y reparte el sobrante/faltante entre
 *    las columnas de texto (`flex` en COLS) con un mínimo por columna. El
 *    navegador decide, nosotros sólo lo hacemos caber y lo igualamos entre
 *    tablas.
 *  - Prácticas/Diagnóstico NO envuelven: recortan con "…" y el texto
 *    completo va en `title` (hover). Son diagnósticos repetidos y largos —
 *    envolverlos rompía la fila en 2-3 líneas.
 *  - Anchos arrastrables tipo Excel. Desde 2026-07-24 (2ª vuelta) el motor
 *    —medir, encajar, arrastrar, recordar— vive en admin-table.js y lo usan
 *    TODAS las tablas del admin; acá quedó sólo lo propio de esta pantalla
 *    (COLS con su flex/min y el orden de caída). El ancho elegido se sigue
 *    guardando en localStorage `st_cola_cols`.
 *  - Cabeceras ordenables (cnt-th-sortable, misma lengua que #medicos);
 *    orden por defecto = Hora ascendente. Con búsqueda activa manda la
 *    relevancia SÓLO hasta que alguien toca una cabecera (STATE.sortTouched).
 *  - Celdas compactas: orden = ✓ o nada, sobreturno a la derecha de la hora,
 *    vacío = celda vacía (no un guión). Cuando el ancho no alcanza no se
 *    apilan datos dentro de la celda: se CAEN columnas por prioridad
 *    (Diagnóstico → Prácticas → Confirmó, ver DROP) y lo que cae sigue estando
 *    a un click, en la ficha del paciente. El apilado ≤640px sigue igual, con
 *    el texto completo (ahí sí envuelve).
 *
 * Ronda 4 (2026-07-27, handoff de Claude Design "Pinamar Admin Pacientes De
 * Hoy" — no relitigar):
 *  - UNA SOLA TABLA para todo el día. Antes cada médico tenía su tabla con su
 *    propio <thead>, y con seis grupos la pantalla eran seis encabezados
 *    repetidos. Ahora hay un <thead> ÚNICO y pegajoso (sticky) y cada médico
 *    es un <tbody> que arranca con su fila de encabezado (nombre + Crear turno
 *    + especialidad · centro + N turnos + cuántos pasaron sin registrar). Los
 *    grupos comparten columnas por construcción, no por medición.
 *    OJO: el <thead> pegajoso exige que el wrap NO sea contenedor de scroll
 *    (`.queue-wrap{overflow:visible}`) — con `overflow-x:auto` el sticky se
 *    pega al tope del wrap, o sea nunca se mueve.
 *  - La EDAD viaja al lado del nombre, en gris (la columna suelta murió): es
 *    el dato que se lee junto al paciente, no una columna que ordenar.
 *  - "Ahora HH:MM" es una línea que separa lo que ya pasó de lo que falta,
 *    dentro de cada médico, y sólo aparece donde de verdad separa algo (hoy,
 *    en orden cronológico, con turnos de los dos lados).
 *  - Sobreturno = "S/T" angosto pegado a la hora (the product owner: "much more compact and
 *    it relates to the hour, not name").
 *  - La BÚSQUEDA va PRIMERA y hace también de filtro de fecha (responsable del producto: "search
 *    comes first… if users type a date, we can search by that date"):
 *    fechaDeTexto() entiende 24/7, 24-07-2026, 2026-07-24, "24 de julio",
 *    hoy/mañana/ayer, carga ese día y busca con el resto del texto.
 *
 * Ronda 5 (2026-07-27, el responsable del producto mirando la pantalla real):
 *  - Los HUECOS de agenda ("Libre / Dar este turno") se controlan con
 *    "Mostrar horarios libres". Arrancan visibles para incluir al médico que
 *    todavía tiene cero pacientes. Apagarla dura esta sesión, no la próxima
 *    apertura: ocultar sin querer no puede hacer desaparecer médicos mañana.
 *  - Los nombres se muestran en Nombre Propio: el padrón los manda gritados
 *    ("MONICA ROSA ALUSTIZA") y así se leían en la tabla. Sólo cambia lo que se
 *    MUESTRA — el valor original es el que sigue viajando al backend.
 */
// Own the asynchronous day boundary independently of its DOM renderer. An
// intent supersedes the previous request immediately, even when fetch ignores
// abort or the previous response is still parsing/confirming a session.
export function createDayQueueLoader({ request, confirm, apply, fail }) {
  let disposed = false, version = 0, controller = null;
  return {
    invalidate() {
      version++;
      if (controller) controller.abort();
    },
    async load(day) {
      if (disposed) return;
      const ownVersion = ++version;
      if (controller) controller.abort();
      controller = new AbortController();
      const current = () => !disposed && ownVersion === version;
      try {
        const response = await request(day, controller.signal);
        if (!current()) return;
        if (response.status === 401 || response.status === 403) {
          const transitioned = await confirm(response, current);
          if (!current()) return;
          if (transitioned) return true;
          throw new Error(String(response.status));
        }
        if (!response.ok) throw new Error("bad-" + response.status);
        const data = await response.json();
        if (current()) { apply(data); return true; }
      } catch (error) {
        if (current() && error?.name !== "AbortError") { fail(error); return false; }
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      version++;
      if (controller) controller.abort();
    }
  };
}

const queueOwners = new WeakMap();

export function mount(root, options = {}) {
  root = root || document;
  queueOwners.get(root)?.();
return (function () {
  "use strict";

  var disposed = false;
  var initialReadiness = null;
  function finishInitialReadiness(error) {
    if (!initialReadiness) return;
    var pending = initialReadiness;
    initialReadiness = null;
    if (error) pending.reject(error);
    else pending.resolve();
  }
  var cleanups = new Set();
  var timers = new Set();
  function ownCleanup(cleanup) {
    function release() {
      if (!cleanups.delete(release)) return;
      cleanup();
    }
    cleanups.add(release);
    return release;
  }
  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    return ownCleanup(function () { target.removeEventListener(type, handler); });
  }
  function later(callback, delay) {
    var timer = window.setTimeout(function () {
      timers.delete(timer);
      if (!disposed) callback();
    }, delay);
    timers.add(timer);
    return timer;
  }
  function cancelTimer(timer) {
    window.clearTimeout(timer);
    timers.delete(timer);
  }
  var mutations = new Map();
  function beginMutation(key, onFinish) {
    var previous = mutations.get(key);
    if (previous) previous.finish();
    var controller = new AbortController();
    var release = ownCleanup(function () {
      controller.abort();
      if (!disposed && onFinish) onFinish();
    });
    var operation = {
      signal: controller.signal,
      current: function () { return !disposed && mutations.get(key) === operation; },
      finish: function () {
        release();
        if (mutations.get(key) === operation) mutations.delete(key);
      }
    };
    mutations.set(key, operation);
    return operation;
  }
  function invalidateDayMutations() {
    mutations.forEach(function (operation, key) {
      if (key.indexOf("attendance:") === 0) operation.finish();
    });
  }

  var Dates = null;
  var datesUrl = window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"];
  var datesReady = datesUrl ? import(datesUrl).then(function (m) { Dates = m; })
    .catch(function () { return null; }) : Promise.resolve(null);
  var Format = null;
  var formatReady = window.sanaImportStatic("platform/format.mjs").then(function (m) { Format = m; });
  var FilterControls = null;
  var filterControlsReady = window.sanaImportStatic("platform/admin-filter-controls.mjs").then(function (m) { FilterControls = m; });
  // Qué es la captura de síntomas: sin el bloque, el encabezado del médico
  // sigue con su botón — nunca una pantalla rota por esto.
  var Explainer = null;
  var EXPLAINER_OPEN = false; // la cola se repinta seguido: el pliegue abierto sigue abierto
  var explainerReady = window.sanaImportStatic("platform/intake-explainer.mjs")
    .then(function (m) { Explainer = m; m.ensureCss(document); }).catch(function () { return null; });
  // The attendance forecast («Historial»): bars, the header's Information
  // button and the one explanation they open — shared with «Pacientes».
  var Forecast = null;
  var forecastReady = window.sanaImportStatic("platform/attendance-forecast.mjs").then(function (m) {
    if (disposed) return;
    Forecast = m;
    ownCleanup(m.bind(document, T));
  });
  var Prefs = null;
  var prefsReady = window.sanaImportStatic("platform/admin-preferences.mjs").then(function (m) {
    if (disposed) return;
    Prefs = m;
    return m.load().then(function () {
      if (disposed) return;
      VISIBLE_KEYS = loadVisibleKeys();
      HAS_STORED_COLS = storedVisibleKeys() !== null;
    });
  });
  var sessionReady = window.sanaImportStatic("platform/admin-session.mjs");
  // The extension's «Cargar en HSI» on each row wears the panel's one HSI
  // look (hsi-sync.mjs): the turning icon while it loads, the check once saved.
  var HsiSync = null;
  window.sanaImportStatic("platform/hsi-sync.mjs").then(function (m) {
    HsiSync = m;
    m.ensureCss();
    // the day may have painted before the module arrived
    if (STATE.hsiInstalar && document.getElementById("sec-cola") && !document.getElementById("queue-hsi-instalar")) render();
  }, function () {});
  var Table = null;
  var tableReady = window.sanaImportStatic("platform/admin-table.mjs").then(function (m) { Table = m; return m; });
  var createAppointmentReady = null;
  var createAppointmentSeq = 0;
  var createAppointmentIntent = {};
  var createAppointmentHost = null;
  var createAppointmentOwner = null;
  var createAppointmentContext = null;
  var releaseCreateAppointment = null;
  function createAppointmentOwnerFor(host) {
    // The queue re-renders its doctor detail pane after incoming day updates.
    // A button from that pane is intentionally disposable; the still-mounted
    // queue screen, not that button's node identity, owns a pending intent.
    return $("#sec-cola") || (host && host.parentElement) || null;
  }
  function createAppointmentContextFor(pre) {
    // A same-screen refresh replaces controls but leaves these identities
    // intact. Selecting another doctor or moving the queue to another day is
    // an operator decision, so a held old create action must not reopen.
    return {
      dia: pre && pre.dia ? pre.dia : "",
      seleccionado: pre && pre.medico ? (STATE.seleccionado || "") : null
    };
  }
  function createAppointmentContextIsCurrent(context) {
    if (disposed || !context) return false;
    if (context.dia && context.dia !== STATE.dia) return false;
    return context.seleccionado === null || context.seleccionado === (STATE.seleccionado || "");
  }
  function loadCreateAppointment() {
    if (!createAppointmentReady) {
      createAppointmentReady = window.sanaImportStatic("platform/admin-create-appointment.mjs").then(function (m) {
        return m;
      }, function (err) {
        createAppointmentReady = null;
        throw err;
      });
    }
    return createAppointmentReady;
  }
  function clearCreateAppointmentLoadError() {
    var nodes = document.querySelectorAll("[data-crear-turno-error]");
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
  }
  function showCreateAppointmentLoadError() {
    var msg = T("admin.queue.create_appt.load_failed", null, "No se pudo cargar. Reintentá.");
    toast(msg);
    var host = createAppointmentHost && createAppointmentHost.isConnected ? createAppointmentHost : null;
    var btn = $("#adm-crear-turno");
    if (!host) host = btn && btn.parentElement;
    if (!host) return;
    clearCreateAppointmentLoadError();
    var p = document.createElement("p");
    p.className = "adm-empty";
    p.setAttribute("role", "alert");
    p.setAttribute("data-crear-turno-error", "1");
    p.appendChild(document.createTextNode(msg + " "));
    var retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn-quiet";
    retry.setAttribute("data-crear-turno-retry", "1");
    retry.textContent = T("admin.queue.create_appt.recargar", null, "Recargar");
    p.appendChild(retry);
    host.appendChild(p);
  }
  function openCreateAppointment(pre, host) {
    if (disposed) return Promise.resolve();
    var seq = ++createAppointmentSeq;
    createAppointmentIntent = pre || {};
    createAppointmentHost = host || null;
    createAppointmentOwner = createAppointmentOwnerFor(host);
    createAppointmentContext = createAppointmentContextFor(createAppointmentIntent);
    return loadCreateAppointment().then(function (m) {
      if (disposed || seq !== createAppointmentSeq) return;
      if (!createAppointmentOwner || !createAppointmentOwner.isConnected) return;
      if (!createAppointmentContextIsCurrent(createAppointmentContext)) return;
      clearCreateAppointmentLoadError();
      var release = m.abrir(createAppointmentIntent, {
        onCreated: function (saved) { refreshQueue(saved.dia); }
      });
      if (typeof release === "function") releaseCreateAppointment = release;
    }).catch(function () {
      if (disposed || seq !== createAppointmentSeq) return;
      if (!createAppointmentOwner || !createAppointmentOwner.isConnected) return;
      if (!createAppointmentContextIsCurrent(createAppointmentContext)) return;
      showCreateAppointmentLoadError();
    });
  }
  function syncCrearTurno() {
    var b = $("#adm-crear-turno");
    if (!b) return;
    var agenda = window.CURRENT_ROLE === "admin" || window.CURRENT_ROLE === "admisionista";
    b.style.display = agenda ? "" : "none";
  }

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  function toast(msg) { if (window.toast) window.toast(msg); }
  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.resolve(null);
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
  // Sustituye {var} en lo que devolvió T(): esa rama NO pasa por i18n.t()
  // (quien hace el reemplazo real) cuando window.SanaI18n todavía no existe —
  // hoy es el caso de admin.html, que no carga i18n.js. Reaplicar acá es
  // inofensivo cuando SanaI18n YA sustituyó: no queda ningún "{var}" que
  // encontrar (mismo motivo que dates.mjs hace su propio fmt(), documentado
  // en su encabezado).
  function applyVars(text, vars) {
    if (!vars) return text;
    var out = text;
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) out = out.split("{" + k + "}").join(String(vars[k]));
    }
    return out;
  }

  // ---- CSS propio (una sola inyección) -------------------------------------


  // ---- estado + helpers -----------------------------------------------------

  // filtro = índice de médico ("" = todos); filtroServicio = nombre exacto de
  // servicio ("" = todos); especialidad/centro recortan el mismo grupo de
  // agenda; q = texto de búsqueda libre; sort = columna+sentido
  // (por defecto Hora ascendente, responsable del producto); sortTouched = si alguien ya eligió
  // una columna a mano (con búsqueda activa, hasta entonces manda el puntaje
  // de relevancia).
  // q = lo que la persona escribió (es el valor del input); qBusca = lo que
  // queda para buscar de verdad una vez que se le sacó la FECHA, si escribió
  // una (esa se convirtió en el día que se está mirando). Escribir sólo una
  // fecha deja qBusca vacío: se ve ese día entero, en orden.
  // verLibres = mostrar los huecos de agenda todavía sin paciente. Arranca
  // apagado y la elección se guarda como el resto de la pantalla (responsable del producto
  // 27.10, q1044534: «Hidden, per the checkbox»). Un día sin turnos pero con
  // agendas abiertas lo dice y señala la casilla.
  var STATE = {
    // `filtro` guarda grupoKey (id del médico, no posición en el arreglo): al
    // pasar a otro día el orden de grupos cambia y un índice podía seleccionar
    // otra persona. La etiqueta queda para que un filtro sin resultados siga
    // siendo legible y se pueda limpiar.
    dia: null, hoy: true, grupos: [], filtro: "", filtroMedicoEtiqueta: "", filtroServicio: "", filtroEspecialidad: "", filtroCentro: "", q: "", qBusca: "",
    sort: { key: "hora", dir: 1 }, sortTouched: false, verLibres: false,
    // Entrevistas por QR sin turno matcheado (fase 4, sólo llega para rol
    // 'medico') — no se persiste, viene fresca en cada carga del día.
    entrevistasSinTurno: [],
    // Config de la entrevista previa del propio médico (fase 7, sólo rol
    // 'medico'; null si el módulo está apagado) — tampoco se persiste.
    entrevistaConfig: null,
    // The doctor the list is narrowed to (= filtro or null): «Crear turno»
    // starts with that doctor.
    seleccionado: null
  };
  var TURNOS_BY_ID = {};
  // Per appointment of the painted list: its doctor's group (the Médico
  // column) and its search score (the flat list sorts by relevance).
  var GRUPO_DE = {};
  var SCORE_DE = {};
  var EDITING = {};
  var queueStream = null;
  var initialQueueConsumed = false;
  var PREFS_READY = false;
  function hydratePrefs() {
    if (PREFS_READY || !Prefs) return;
    var saved = Prefs.get("day_queue.filters", {});
    ["filtro", "filtroMedicoEtiqueta", "filtroServicio", "filtroEspecialidad", "filtroCentro", "q", "qBusca", "sort", "sortTouched", "verLibres"].forEach(function (key) {
      if (Object.prototype.hasOwnProperty.call(saved, key)) STATE[key] = saved[key];
    });
    // La vista propia del médico no ofrece filtro de médico (su única agenda
    // es él): un filtro guardado por otra sesión quedaría sin control para
    // quitarlo y vaciaría el día en silencio.
    if (window.CURRENT_ROLE === "medico") { STATE.filtro = ""; STATE.filtroMedicoEtiqueta = ""; }
    VISIBLE_KEYS = loadVisibleKeys();
    HAS_STORED_COLS = storedVisibleKeys() !== null;
    COLS = activeCols();
    PREFS_READY = true;
  }
  function savePrefs() {
    if (!Prefs) return;
    Prefs.set("day_queue.filters", {
      filtro: STATE.filtro, filtroMedicoEtiqueta: STATE.filtroMedicoEtiqueta,
      filtroServicio: STATE.filtroServicio, filtroEspecialidad: STATE.filtroEspecialidad,
      filtroCentro: STATE.filtroCentro,
      q: STATE.q, qBusca: STATE.qBusca,
      sort: STATE.sort, sortTouched: STATE.sortTouched,
      verLibres: STATE.verLibres
    });
  }

  // Columnas de la tabla. `min` = piso al achicar; `max` = techo de la
  // medición natural; `flex` = peso al repartir el sobrante/faltante (0 =
  // ancho de contenido, no se toca). El ORDEN de este array ES el orden de
  // las columnas y el de los <col> del colgroup.
  //
  // The row says who comes, when and to whom (the product owner, 2026-11-09): the hour,
  // «Name, N years» as one link with the attendance bars and the referral
  // icon next to it, and the doctor with the service under the name.
  // Everything else about the appointment — DNI, attendance, confirmation,
  // what the AI read from the referral, the symptoms summary — lives in the
  // appointment card the name opens. The gear only offers the extra columns
  // a desk may still want on the list.
  // Los `label` se resuelven UNA vez, al cargar el módulo, y otra vez en
  // localizeColumns() cuando el catálogo nativo está listo.
  var COLS_ALL = [
    { key: "hora", label: T("admin.queue.table.hora", null, "Hora"), min: 62, always: true },
    // `entero`: el nombre con la edad se lee entero; antes de recortarlo cae
    // una columna vacía o de DROP (responsable del producto, 2026-11-04).
    { key: "paciente", label: T("admin.queue.table.paciente", null, "Paciente"), min: 150, max: 360, flex: 1, always: true, entero: 2 },
    // One flat list for the day (the product owner, 2026-11-08): the doctor is a column
    // and a link that narrows the list to that doctor, the service under it.
    // Not in the gear: it is there unless the list is one doctor's day
    // (columnaMedico). A name column has no width cap.
    { key: "medico", label: T("admin.queue.table.medico", null, "Médico"), min: 140, flex: 1, always: true, entero: 2 },
    // Lo que contestó al recordatorio del día previo (2026-07-24). En la
    // ficha siempre; en la lista, sólo si la persona la prende.
    { key: "confirmo", label: T("admin.queue.table.confirmo", null, "Confirmó"), min: 78 },
    { key: "solicitado", label: T("admin.queue.table.solicitado", null, "Solicitado"), min: 104 },
    { key: "cobertura", label: T("admin.queue.table.cobertura", null, "Cobertura"), min: 110, max: 260, flex: 1 },
    // Entrevista previa (modules/intake/, fase 4): el color del triage y
    // «Iniciar consulta». El resumen que escribió la IA va en la ficha, con
    // su marca de IA (responsable del producto, 2026-11-09). NO "default": una instalación sin la
    // entrevista vería siempre una columna en blanco; se muestra sola cuando
    // el médico la habilitó o el día trae entrevistas (autoShowEntrevista).
    { key: "entrevista", label: T("admin.queue.table.entrevista", null, "Síntomas"), min: 120, max: 220, flex: 1 },
    // Sobrante vacío al final (handoff 2026-07-27): mantiene la fila del ancho
    // del bloque —el hover y la línea de abajo llegan hasta el borde—.
    { key: "relleno", label: "", min: 0, always: true, relleno: true }
  ];

  // This registry is evaluated while the classic controller is parsed.  Its
  // labels must be refreshed after the native catalog is ready, before the
  // first table render; otherwise an English cold load permanently keeps the
  // Spanish fallbacks in the column picker.
  function localizeColumns() {
    var labels = {
      hora: ["admin.queue.table.hora", "Hora"], paciente: ["admin.queue.table.paciente", "Paciente"],
      medico: ["admin.queue.table.medico", "Médico"], confirmo: ["admin.queue.table.confirmo", "Confirmó"],
      solicitado: ["admin.queue.table.solicitado", "Solicitado"], cobertura: ["admin.queue.table.cobertura", "Cobertura"],
      entrevista: ["admin.queue.table.entrevista", "Síntomas"]
    };
    COLS_ALL.forEach(function (column) {
      var item = labels[column.key];
      if (item) column.label = T(item[0], null, item[1]);
    });
  }

  // El ancho elegido a mano lo guarda SanaTable bajo esta misma clave: los
  // anchos que ya tenía quien viene usando la pantalla siguen valiendo.
  var LS_COLS = "st_cola_cols";
  var LS_VISIBLE = "st_cola_visible_cols";

  function defaultVisibleKeys() {
    return COLS_ALL.filter(function (c) { return c["default"]; }).map(function (c) { return c.key; });
  }

  // El array guardado (admin-preferences.mjs, backed por Postgres — ver
  // get(), que devuelve el fallback SÓLO cuando la clave
  // nunca se seteó, `Object.prototype.hasOwnProperty`) o null si esta cuenta
  // nunca tocó el engranaje. null es información real: "nunca eligió", no
  // "eligió vacío" — de ahí sale si el auto-show de Entrevista (más abajo)
  // puede aplicar sin pisar una elección explícita.
  function storedVisibleKeys() {
    var keys = Prefs ? Prefs.get("day_queue.columns", null) : null;
    return Array.isArray(keys) ? keys : null;
  }

  // «Confirmó» was a factory column until 2026-11-09, so nearly every saved
  // list holds it: it is taken out of those lists ONCE (it now reads in the
  // card) and noted, so ticking it again afterwards sticks. Keys of columns
  // that no longer exist (dni, servicio, orden, historial, asistencia,
  // prácticas, diagnóstico) are simply ignored.
  var CARD_MOVED = "day_queue.columns_card_moved";

  function loadVisibleKeys() {
    var stored = storedVisibleKeys();
    if (!stored) return defaultVisibleKeys();
    if (Prefs && !Prefs.get(CARD_MOVED, false)) {
      Prefs.set(CARD_MOVED, true);
      if (stored.indexOf("confirmo") !== -1) {
        stored = stored.filter(function (k) { return k !== "confirmo"; });
        Prefs.set("day_queue.columns", stored);
      }
    }
    return stored.filter(function (k) {
      return COLS_ALL.some(function (c) { return c.key === k && !c.always; });
    });
  }

  var VISIBLE_KEYS = loadVisibleKeys();
  // true en cuanto exista CUALQUIER array guardado, aunque no incluya
  // "entrevista" — el formato es plano (una lista de claves visibles), no
  // distingue "la destildó a propósito" de "nunca la vio como opción"; ante
  // esa ambigüedad gana la mano de la cuenta, sea cual sea el motivo por el
  // que "entrevista" no está en la lista.
  var HAS_STORED_COLS = storedVisibleKeys() !== null;

  // Auto-show de Síntomas: a doctor who enabled capture gets the expanded
  // queue shape immediately, even before today's first interview arrives.
  // Existing data also reveals the column for admin/reception views. A saved
  // column preference always wins so each doctor can tailor their own table.
  function entrevistaPresente() {
    return (STATE.grupos || []).some(function (g) {
      return (g.turnos || []).some(function (t) { return !!t.entrevista; });
    }) || !!(STATE.entrevistasSinTurno && STATE.entrevistasSinTurno.length);
  }

  function autoShowEntrevista() {
    var habilitada = !!(STATE.entrevistaConfig && STATE.entrevistaConfig.efectivo);
    return !HAS_STORED_COLS && (habilitada || entrevistaPresente());
  }

  function activeCols() {
    var auto = autoShowEntrevista();
    return COLS_ALL.filter(function (c) {
      return c.always || VISIBLE_KEYS.indexOf(c.key) !== -1 || (auto && c.key === "entrevista");
    });
  }
  var COLS = activeCols();

  function apilado() {
    try { return window.matchMedia("(max-width:640px)").matches; } catch (e) { return false; }
  }

  // Normaliza para comparar/buscar: minúsculas sin acentos.
  function norm(s) {
    return String(s == null ? "" : s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  // Edad canónica — NÚMERO pelado: la columna ya se llama "Edad", repetir
  // "años" en cada fila es ruido (responsable del producto, ronda 3).
  function edadDe(iso) {
    return Format.edad(iso);
  }

  function horaDeIso(iso) {
    if (!iso) return "";
    try {
      return new Intl.DateTimeFormat("es-AR", {
        timeZone: "America/Argentina/Buenos_Aires", hour: "2-digit", minute: "2-digit", hour12: false
      }).format(new Date(iso));
    } catch (e) { return ""; }
  }

  // The browser clock is only used for the current-time indicator.
  function ahoraArtHHMM() {
    try {
      return new Intl.DateTimeFormat("es-AR", {
        timeZone: "America/Argentina/Buenos_Aires", hour: "2-digit", minute: "2-digit", hour12: false
      }).format(new Date());
    } catch (e) {
      var d = new Date(), p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
      return p(d.getHours()) + ":" + p(d.getMinutes());
    }
  }

  // Fecha de hoy en ART, del reloj del navegador pero con el huso forzado —
  // igual que ahoraArtHHMM. Sólo se usa para SITUAR el día que ya trajo el
  // server (¿pasó, es hoy, falta?), nunca para elegir qué día pedir: eso lo
  // sigue decidiendo el server (ver gotcha del encabezado).
  function hoyArtIso() {
    try {
      return new Intl.DateTimeFormat("en-CA", {
        timeZone: "America/Argentina/Buenos_Aires",
        year: "numeric", month: "2-digit", day: "2-digit"
      }).format(new Date());
    } catch (e) {
      var d = new Date(), p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
      return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
    }
  }

  // ¿Esa hora del día en pantalla ya pasó? Un día anterior está todo pasado;
  // uno futuro, nada. Es lo que decide la línea de "Ahora", la hora en ámbar y
  // el aviso de turnos sin registrar.
  function esPasado(hora) {
    var hoy = hoyArtIso(), dia = STATE.dia || hoy;
    if (dia < hoy) return true;
    if (dia > hoy) return false;
    return String(hora || "") < ahoraArtHHMM();
  }

  var MESES_NOMBRE = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "setiembre", "octubre", "noviembre", "diciembre"];
  var MES_NUM = { enero: 1, febrero: 2, marzo: 3, abril: 4, mayo: 5, junio: 6, julio: 7,
    agosto: 8, septiembre: 9, setiembre: 9, octubre: 10, noviembre: 11, diciembre: 12 };

  function isoDe(y, m, d) {
    if (!(m >= 1 && m <= 12) || !(d >= 1 && d <= 31)) return null;
    var p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    var iso = y + "-" + p(m) + "-" + p(d);
    var dt = new Date(y, m - 1, d, 12);
    // 31 de febrero no existe: el Date lo corre de mes y ahí se nota.
    return dt.getMonth() === m - 1 && dt.getDate() === d ? iso : null;
  }

  // Una fecha escrita adentro del buscador (responsable del producto, handoff 2026-07-27: "if
  // users type a date, we can search by that date"). Entiende 24/7, 24-7-26,
  // 2026-07-24, "24 de julio", "24 jul", hoy/mañana/ayer. Devuelve
  // {iso, resto} — `resto` es el texto que queda para buscar de verdad — o
  // null si ahí no hay ninguna fecha.
  function fechaDeTexto(q) {
    var t = norm(q).trim();
    if (!t) return null;
    var hoy = hoyArtIso();
    var anioActual = parseInt(hoy.slice(0, 4), 10);
    var rel = { hoy: 0, manana: 1, ayer: -1, anteayer: -2, "pasado manana": 2 };
    var out = null;

    Object.keys(rel).forEach(function (palabra) {
      if (out) return;
      var re = new RegExp("(^|\\s)" + palabra + "(\\s|$)");
      if (re.test(t)) out = { iso: shiftDia(hoy, rel[palabra]), resto: t.replace(re, " ") };
    });
    if (out) return { iso: out.iso, resto: out.resto.trim() };

    // 2026-07-24
    var m = /(^|\s)(\d{4})-(\d{1,2})-(\d{1,2})(\s|$)/.exec(t);
    if (m) {
      var iso = isoDe(+m[2], +m[3], +m[4]);
      if (iso) return { iso: iso, resto: t.replace(m[0], " ").trim() };
    }
    // 24/7, 24-7-26, 24.07.2026 — día primero, como se escribe acá.
    m = /(^|\s)(\d{1,2})[\/\-.](\d{1,2})(?:[\/\-.](\d{2,4}))?(\s|$)/.exec(t);
    if (m) {
      var anio = m[4] ? (m[4].length === 2 ? 2000 + +m[4] : +m[4]) : anioActual;
      var iso2 = isoDe(anio, +m[3], +m[2]);
      if (iso2) return { iso: iso2, resto: t.replace(m[0], " ").trim() };
    }
    // "24 de julio", "24 jul", "24 julio 2026" (el mes por prefijo: jul, sept…)
    m = /(^|\s)(\d{1,2})\s+(?:de\s+)?([a-z]{3,})(?:\s+(?:de\s+)?(\d{4}))?(\s|$)/.exec(t);
    if (m) {
      var nombre = null;
      for (var i = 0; i < MESES_NOMBRE.length; i++) {
        if (MESES_NOMBRE[i].indexOf(m[3]) === 0) { nombre = MESES_NOMBRE[i]; break; }
      }
      if (nombre) {
        var iso3 = isoDe(m[4] ? +m[4] : anioActual, MES_NUM[nombre], +m[2]);
        if (iso3) return { iso: iso3, resto: t.replace(m[0], " ").trim() };
      }
    }
    return null;
  }

  function shiftDia(diaIso, delta) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(diaIso || "");
    if (!m) return diaIso;
    var d = new Date(+m[1], +m[2] - 1, +m[3], 12, 0, 0);
    d.setDate(d.getDate() + delta);
    var p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  // "Hoy, Jueves 24 de julio" / "Mañana, Viernes 25 de julio" / "Miércoles
  // 23 de julio" — misma composición que admin-my-appointments.js:grupoLabel.
  // Comparar contra el catálogo (date.today/date.tomorrow), no contra el
  // literal "Hoy"/"Mañana": dates.mjs habla el idioma de la página desde PR14
  // (2026-08-04) y en inglés dia() devuelve "Today"/"Tomorrow" — comparar
  // contra castellano fijo hacía que esta rama nunca matcheara ahí.
  // The day row says «Hoy» once, on its button; the label is the short date
  // («dom 8 nov», responsable del producto 2026-11-08: «Hoy, domingo 8 de noviembre» + «Hoy»).
  function fechaLabel(diaIso) {
    if (!Dates) return diaIso || "";
    var corta = Dates.cortaFija ? Dates.cortaFija(diaIso) : Dates.corta(diaIso, "");
    return corta || diaIso || "";
  }

  // "hoy" / "mañana" / "el miércoles 23 de julio" — para meter en una frase
  // (empty state), a diferencia de fechaLabel() que es la etiqueta del nav.
  // El artículo + minúscula de mitad de frase es gramática de castellano, no
  // universal (mismo bug que arregló PR14 en site_studies._get_label): en
  // inglés date.weekday_with_article es sólo "{day}", sin artículo ni cambio
  // de mayúscula.
  function fechaParaTexto(diaIso) {
    var rel = Dates ? Dates.dia(diaIso) : diaIso;
    if (rel === T("date.today", null, "Hoy")) return T("date.today_lower", null, "hoy");
    if (rel === T("date.tomorrow", null, "Mañana")) return T("date.tomorrow_lower", null, "mañana");
    if (!rel) return diaIso || "";
    var esCastellano = ((document.documentElement.lang || "es").slice(0, 2)) === "es";
    var dia = esCastellano ? rel.charAt(0).toLowerCase() + rel.slice(1) : rel;
    return applyVars(T("date.weekday_with_article", { day: dia }, "el {day}"), { day: dia });
  }

  // ---- asistencia -------------------------------------------------------

  // Accesibilidad (reunión 2026-07-15: quien administra los turnos del
  // hospital es NO VIDENTE y hoy trabaja con un lector de pantalla contra un
  // sistema que no la ayuda). En una tabla, un lector lee la fila entera al
  // llegar, pero un botón lo anuncia SOLO por su texto: cinco "Atendió"
  // seguidos son indistinguibles. Por eso cada control lleva en su nombre
  // accesible a quién y a qué hora corresponde.
  function quien(t) {
    return nombrePropio((t && t.paciente) || "") || T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre");
  }

  // Los nombres llegan como los cargó cada sistema: "MONICA ROSA ALUSTIZA"
  // gritado desde el padrón, "Mónica Rosa" tipeado a mano. Un nombre TODO en
  // mayúsculas se pasa a Nombre Propio (responsable del producto, 2026-07-27); uno que ya viene
  // mixto se respeta tal cual — quien lo escribió con una mayúscula rara sabrá
  // por qué. Sólo cambia lo que se MUESTRA: lo que viaja al backend (el médico
  // de "Crear turno", por ejemplo) sigue siendo el valor original.
  //
  // La implementación vivía acá y se mudó a format.mjs (2026-08-01, responsable del producto: "the
  // doctor names have normal capitalization, everywhere"): el sitio del
  // paciente necesitaba la misma regla y una SEGUNDA copia de cómo se escribe
  // un nombre es el bug. Esto es sólo el alias local.
  function nombrePropio(v) {
    return Format.nombre(v);
  }

  function botonesHtml(id) {
    var t = TURNOS_BY_ID[id] || {};
    var ref = quien(t) + (t.hora ? ", " + t.hora : "");
    // The panel's segmented control (.queue-seg, the day navigation's) at
    // row height — not two tall pills (the product owner, 2026-10-26: «buttons too tall
    // and ugly»).
    return '<div class="queue-seg queue-seg--row queue-attendance-btns" role="group" data-export-omit aria-label="'
      + esc(applyVars(T("admin.queue.attendance.aria_group", { ref: ref }, "Asistencia de {ref}"), { ref: ref })) + '">'
      + '<button type="button" data-asis="' + esc(id) + '" data-valor="presente"'
      + ' aria-label="' + esc(applyVars(T("admin.queue.attendance.aria_mark_present", { ref: ref }, "Marcar que atendió a {ref}"), { ref: ref })) + '">'
      + esc(T("admin.queue.attendance.presente", null, "Atendió")) + "</button>"
      + '<button type="button" data-asis="' + esc(id) + '" data-valor="ausente"'
      + ' aria-label="' + esc(applyVars(T("admin.queue.attendance.aria_mark_absent", { ref: ref }, "Marcar que no vino {ref}"), { ref: ref })) + '">'
      + esc(T("admin.queue.attendance.ausente", null, "No vino")) + "</button>"
      + "</div>";
  }

  // Lápiz de "corregir": la palabra repetida en cada fila marcada era la línea
  // más ruidosa de la pantalla (handoff 2026-07-27). El nombre accesible sigue
  // diciendo de quién es el turno — un lector no ve el ícono.
  var LAPIZ = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M12 20h9"></path><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"></path></svg>';

  function marcadoHtml(t) {
    var vino = t.asistencia === "presente";
    var label = vino ? T("admin.queue.attendance.presente", null, "Atendió") : T("admin.queue.attendance.ausente", null, "No vino");
    var hora = horaDeIso(t.asistencia_en);
    // A recorded status reads as a status, not as a button or a link: a
    // colored dot (the «Historial» dot) and plain text (the product owner, 2026-10-26).
    return '<span class="queue-attendance-marked">'
      + '<span class="attendance-dot attendance-dot--' + (vino ? "good" : "bad") + '" aria-hidden="true"></span>'
      + '<span class="' + (vino ? "queue-attendance-yes" : "queue-attendance-no") + '">' + esc(label) + "</span>"
      + (hora ? '<span class="queue-attendance-h">' + esc(hora) + "</span>" : "")
      + '<button type="button" class="queue-fix" data-corregir="' + esc(t.id) + '" title="' + esc(T("admin.queue.attendance.corregir", null, "Corregir")) + '"'
      + ' aria-label="' + esc(applyVars(T("admin.queue.attendance.aria_corregir", { who: quien(t) }, "Corregir la asistencia de {who}"), { who: quien(t) })) + '">' + LAPIZ + "</button></span>";
  }

  function asistenciaCellHtml(t) {
    if (t.asistencia && !EDITING[t.id]) return marcadoHtml(t);
    if (!t.elegible_asistencia) return '<span class="cnt-muted">—</span>';
    return botonesHtml(t.id);
  }

  // Región viva: lo que cambia sin recargar (una asistencia marcada, el día
  // que se terminó de cargar, un error) se ANUNCIA. Sin esto, para quien no
  // ve la pantalla marcar asistencia no produce ninguna señal de que pasó
  // algo — que es exactamente lo que hace inusable un panel.
  function anunciar(msg) {
    if (disposed) return;
    var el = document.getElementById("cola-live");
    if (!el) return;
    // Repetir el mismo texto no vuelve a anunciarlo: se limpia primero.
    el.textContent = "";
    later(function () { el.textContent = msg; }, 30);
  }

  // Attendance is marked in the appointment card, not in the row (the product owner,
  // 2026-11-09: no Atendió / No vino on this screen, a card for the
  // appointment where everything is written instead).
  function celdaAsistencia(id) {
    return document.querySelector('[data-cola-asis-box="' + id + '"]');
  }

  // Marcar asistencia no repinta la tabla entera (se perdería el scroll y el
  // foco), así que lo que cambia se actualiza a mano: el bloque de asistencia
  // de la ficha, el peso de la fila y la hora, que deja de estar vencida.
  function refrescarFila(id) {
    var t = TURNOS_BY_ID[id];
    if (!t) return;
    var box = celdaAsistencia(id);
    if (box) box.innerHTML = asistenciaCellHtml(t);
    var tr = document.querySelector('[data-cola-row="' + id + '"]');
    if (!tr) return;
    tr.classList.toggle("queue-tr-done", !!t.asistencia);
    var hora = tr.querySelector(".queue-hour");
    if (hora) hora.classList.toggle("queue-hour-overdue", !t.asistencia && esPasado(t.hora));
  }

  function toggleCorregir(id) {
    EDITING[id] = true;
    refrescarFila(id);
  }

  function marcarAsistencia(id, valor, btnEl) {
    if (disposed) return;
    var grupo = btnEl && btnEl.closest(".queue-attendance-btns");
    var operation = beginMutation("attendance:" + id, function () {
      if (grupo) grupo.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
    });
    if (grupo) grupo.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    fetch("/api/admin/requests/" + encodeURIComponent(id) + "/attendance", {
      method: "POST", credentials: "same-origin", signal: operation.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asistencia: valor })
    }).then(function (res) {
      if (!operation.current()) return null;
      return res.json().catch(function () { return {}; }).then(function (j) { return { res: res, body: j }; });
    }).then(function (r) {
      if (!r || !operation.current()) return null;
      var boundary = (r.res.status === 401 || r.res.status === 403)
        ? sessionReady.then(function (mod) { return operation.current() ? mod.confirmar(r.res, r.body, operation.current) : false; })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) { return { r: r, transitioned: transitioned }; });
    }).then(function (outcome) {
      if (!outcome || !operation.current()) return;
      var r = outcome.r;
      if (outcome.transitioned) return;
      if (!r.res.ok) {
        var msg = r.res.status === 409
          ? T("admin.queue.attendance.future_error", null, "Ese turno todavía no se puede marcar: es de un día futuro.")
          : errText(r.body, T("admin.queue.common.save_error", null, "No se pudo guardar."));
        toast(msg);
        anunciar(msg);
        if (grupo) grupo.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
        return;
      }
      var t = TURNOS_BY_ID[id];
      if (t) {
        var saved = r.body && r.body.solicitud;
        if (saved) {
          t.asistencia = saved.asistencia;
          t.asistencia_en = saved.asistencia_actualizada_en ?? saved.asistencia_en ?? null;
        }
        EDITING[id] = false;
      }
      refrescarFila(id);
      var celda = celdaAsistencia(id);
      anunciar((valor === "presente"
        ? applyVars(T("admin.queue.attendance.announce_presente", { who: quien(t) }, "Atendió: {who}"), { who: quien(t) })
        : applyVars(T("admin.queue.attendance.announce_ausente", { who: quien(t) }, "No vino: {who}"), { who: quien(t) })));
      // El botón que se apretó ya no existe (la celda se repintó): el foco
      // queda en el body y quien navega con teclado pierde el lugar. Se
      // devuelve a "corregir", que es lo único accionable que quedó ahí.
      var fix = celda && celda.querySelector(".queue-fix");
      if (fix) fix.focus();
    }).catch(function () {
      if (!operation.current()) return;
      toast(T("admin.queue.attendance.save_error_network", null, "No se pudo guardar. Revisá tu conexión."));
      anunciar(T("admin.queue.attendance.save_error_specific", null, "No se pudo guardar la asistencia."));
      if (grupo) grupo.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
    }).finally(operation.finish);
  }

  // ---- overlay de la solicitud (orden + campos extraídos por IA) -----------
  // Mismo contrato visual que admin-content.js:solicitudModalHtml/solCampoRows
  // (reusa las clases .solmod-*), pero self-contained: los datos ya vienen en
  // el turno de la cola (ai_campos/orden_url/tiene_orden), sin un segundo
  // fetch a /api/admin/requests (esa lista es admin/operador-only y un
  // médico no puede pedirla).

  // Mismo criterio que admin.html:sinGritos (no importable desde acá, ver
  // encabezado de archivo/instrucción del responsable del producto): texto TODO en mayúsculas
  // ("HEMOGRAMA COMPLETO") pasa a oración; un valor mixto se respeta tal cual.
  function sinGritos(t) {
    t = String(t == null ? "" : t);
    if (t.length > 3 && t === t.toUpperCase() && /[A-ZÁÉÍÓÚÑ]/.test(t)) {
      var low = t.toLowerCase();
      return low.charAt(0).toUpperCase() + low.slice(1);
    }
    return t;
  }

  // ai_campos[key] puede ser un string/array plano O un wrapper
  // {valor,acuerdo} (ai_referral.py real) — unwrap .valor, junta arrays con
  // " · " (un datum por celda, nunca una lista con viñetas acá) y sentence-
  // case si viene gritado. "" si no hay dato (la celda pinta un guión).
  function campoTexto(campos, key) {
    if (!campos) return "";
    var c = campos[key];
    if (c == null) return "";
    var v = (c && typeof c === "object" && !Array.isArray(c)) ? c.valor : c;
    if (Array.isArray(v)) v = v.filter(Boolean).join(" · ");
    if (v == null || v === "") return "";
    return sinGritos(String(v));
  }

  function campoRows(campos) {
    if (!campos) return "";
    var labels = {
      dni: T("admin.queue.overlay.dni", null, "DNI"),
      paciente: T("admin.queue.table.paciente", null, "Paciente"),
      prepaga: T("admin.queue.overlay.prepaga", null, "Prepaga"),
      afiliado: T("admin.queue.overlay.afiliado", null, "N° afiliado"),
      medico: T("admin.queue.overlay.medico", null, "Médico"),
      matricula: T("admin.queue.overlay.matricula", null, "Matrícula"),
      especialidad: T("admin.queue.overlay.especialidad", null, "Especialidad"),
      diagnostico: T("admin.queue.table.diagnostico", null, "Diagnóstico"),
      practicas: T("admin.queue.table.practicas", null, "Prácticas"),
      fecha: T("admin.queue.overlay.fecha", null, "Fecha")
    };
    var html = "";
    Object.keys(labels).forEach(function (k) {
      var c = campos[k];
      if (!c) return;
      var v = c.valor;
      if (k === "practicas") v = Array.isArray(v) ? v.filter(Boolean).join(" · ") : "";
      if (v == null || v === "") return;
      var vHtml = k === "dni" ? (Format.dniHtml(v) || esc(v)) : esc(v);
      html += '<div class="solmod-row"><span class="solmod-k">' + esc(labels[k]) + '</span><span class="solmod-v">' + vHtml + "</span></div>";
    });
    return html;
  }

  // Root cause del box vacío (2026-07-24): un <object data="URL"> SIN type
  // renderiza en blanco en Chromium cuando la URL es una imagen — el
  // navegador no siempre infiere el tipo del Content-Type real que manda
  // booking_requests.stream_referral. Ahora el turno de la cola SÍ trae orden_mime
  // (doctor_appointments._appointment_public) y elegimos el elemento correcto acá:
  // imagen -> <img> (clickeable, abre el original en pestaña nueva — es UNA
  // sola foto, sin flechas de galería); pdf -> <embed> con su propio visor;
  // mime desconocido/ausente -> el <object> de siempre, como red de
  // contención (deja al navegador intentar y cae al link "Abrir la orden").
  function ordenHtml(t) {
    if (!(t.tiene_orden && t.orden_url)) {
      return '<p class="cnt-hint">' + esc(T("admin.queue.overlay.sin_orden", null, "Sin orden adjunta.")) + "</p>";
    }
    var url = esc(t.orden_url);
    var mime = String(t.orden_mime || "");
    if (/^image\//.test(mime)) {
      return '<a class="solmod-orden-link" href="' + url + '" target="_blank" rel="noopener" aria-label="'
        + esc(T("admin.queue.overlay.ver_completo", null, "Ver la orden en tamaño completo")) + '">'
        + '<img class="solmod-orden" src="' + url + '" alt="' + esc(T("admin.queue.overlay.orden_alt", null, "Orden adjunta")) + '"></a>';
    }
    if (mime === "application/pdf") {
      return '<embed class="solmod-orden" type="application/pdf" src="' + url + '">';
    }
    return '<object class="solmod-orden" data="' + url + '">'
      + '<p class="cnt-hint">' + esc(T("admin.queue.overlay.preview_error", null, "No se pudo previsualizar.")) + ' <a href="' + url + '" target="_blank" rel="noopener">'
      + esc(T("admin.queue.overlay.abrir_orden", null, "Abrir la orden")) + "</a>.</p></object>";
  }

  // «Name, N años» — one phrase, the row's link and the card's title.
  function nombreEdad(t) {
    var nombre = nombrePropio(t.paciente) || T("admin.queue.overlay.sin_nombre", null, "(sin nombre)");
    var edad = edadDe(t.nacimiento);
    if (edad == null) return nombre;
    var anios = Tn("admin.queue.table.edad_anios", edad, null, "{n} año", "{n} años");
    return applyVars(T("admin.queue.table.nombre_edad", { nombre: nombre, edad: anios }, "{nombre}, {edad}"), { nombre: nombre, edad: anios });
  }

  // The AI mark goes right above the text the AI wrote (Decreto 742/2026),
  // the same paragraph Solicitudes puts over its reading — never a notice
  // over the whole list (the product owner, 2026-11-09).
  function marcaIAHtml() {
    return '<p class="adm-ai-notice queue-card-ai" data-ai-notice-inline="clinical">'
      + esc(T("ai.notice.clinical", null, "Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención."))
      + "</p>";
  }

  // What the card knows about the appointment, label and value. A value the
  // appointment does not have is not a row.
  function datoHtml(label, valorHtml) {
    if (!valorHtml) return "";
    return '<div class="solmod-row"><span class="solmod-k">' + esc(label) + '</span><span class="solmod-v">' + valorHtml + "</span></div>";
  }

  function historialDatoHtml(t) {
    if (!Forecast || !Forecast.level(t.ranking)) return "";
    return '<span class="queue-card-forecast">' + Forecast.barsHtml(t.ranking, T)
      + "<span>" + esc(Forecast.patientLine(t.ranking, T)) + "</span></span>";
  }

  // Lo que el paciente contó antes de venir (Entrevista previa): el color del
  // triage, el resumen entero que escribió la IA con su marca, y la consulta.
  function sintomasHtml(t) {
    var iv = t.entrevista;
    if (!iv) return "";
    var estado = String(iv.estado || "");
    var chips = estado === "active"
      ? '<span class="queue-triage-curso">' + esc(T("admin.queue.triage.en_curso", null, "En curso")) + "</span>"
      : triageChipHtml(iv.triage);
    if (iv.actualizada) {
      chips += '<span class="queue-triage-actualizada" title="' + esc(T("admin.queue.triage.actualizada_title", null, "El paciente agregó información después de la primera entrevista")) + '">'
        + esc(T("admin.queue.triage.actualizada", null, "Actualizada")) + "</span>";
    }
    var resumen = String(iv.resumen || "").trim();
    var accion = consultaAccionHtml(iv);
    if (!chips && !resumen && !accion) return "";
    return '<section class="queue-card-sec queue-card-sintomas"><p class="solmod-label">' + esc(T("admin.queue.table.entrevista", null, "Síntomas")) + "</p>"
      + (resumen ? marcaIAHtml() : "")
      + (chips ? '<p class="queue-card-chips">' + chips + "</p>" : "")
      + (resumen ? '<p class="queue-card-resumen">' + esc(resumen) + "</p>" : "")
      + (accion ? '<p class="queue-card-accion">' + accion + "</p>" : "")
      + "</section>";
  }

  // The appointment card (the product owner, 2026-11-09): the row says who comes, when and
  // to whom; everything else about the appointment is written here — when,
  // with whom, attendance, DNI, history, the reminder answer, coverage, the
  // symptoms and the referral with what the AI read from it.
  function overlayHtml(t) {
    var nombre = nombrePropio(t.paciente) || T("admin.queue.overlay.sin_nombre", null, "(sin nombre)");
    var head = '<div class="solmod-head"><b>' + esc(nombreEdad(t)) + "</b>"
      + '<button type="button" class="solmod-x hr-x" aria-label="' + esc(T("admin.queue.overlay.cerrar", null, "Cerrar")) + '"></button></div>';
    var g = GRUPO_DE[t.id];
    var medico = g ? etiquetaMedicoGrupo(g) : "";
    var cuando = [t.hora, t.servicio || (g && g.especialidad) || "", medico].filter(Boolean).map(esc).join(" · ");
    var svc = '<p class="solmod-svc">' + cuando
      + (t.centro ? " · " + esc(t.centro) : "") + "</p>";
    var asis = (t.asistencia || t.elegible_asistencia)
      ? '<div class="queue-card-asis" data-cola-asis-box="' + esc(t.id) + '">' + asistenciaCellHtml(t) + "</div>" : "";
    var datos = datoHtml(T("admin.queue.table.dni", null, "DNI"), Format.digits(t.dni) ? Format.dniHtml(Format.digits(t.dni)) : "")
      + datoHtml(T("admin.queue.table.historial", null, "Historial"), historialDatoHtml(t))
      + datoHtml(T("admin.queue.table.confirmo", null, "Confirmó"), t.confirmacion ? confirmoHtml(t) : "")
      + datoHtml(T("admin.queue.table.cobertura", null, "Cobertura"), t.cobertura ? esc(t.cobertura) : "")
      + datoHtml(T("admin.queue.table.solicitado", null, "Solicitado"), t.solicitado_en ? esc(fechaCorta(t.solicitado_en)) : "");
    var campos = campoRows(t.ai_campos);
    var conOrden = !!(t.tiene_orden && t.orden_url);
    // El DOCUMENTO primero y grande, los campos leídos al lado: la ficha se
    // abre para comparar el papel con lo que leyó el modelo (handoff
    // 2026-07-27). Sin orden ni lectura, no hay bloque.
    var cuerpo = (conOrden || campos)
      ? '<div class="solmod-body' + (conOrden ? "" : " solmod-body--solo") + '">'
        + (conOrden ? '<div class="solmod-side">' + ordenHtml(t) + "</div>" : "")
        + (campos
          ? '<div class="solmod-campos"><p class="solmod-label">' + esc(T("admin.queue.overlay.datos_extraidos", null, "Datos extraídos")) + "</p>"
            + marcaIAHtml() + campos + "</div>"
          : "")
        + "</div>"
      : "";
    // role=dialog + aria-modal + un nombre propio: sin esto el lector lo lee
    // como texto más que apareció en algún lado de la página, y quien no ve la
    // pantalla no se entera de que se abrió nada.
    return '<div class="solmod-back"><div class="solmod queue-card" role="dialog" aria-modal="true"'
      + ' aria-label="' + esc(applyVars(T("admin.queue.overlay.ficha_de", { nombre: nombre }, "Ficha de {nombre}"), { nombre: nombre })) + '" tabindex="-1">' + head + svc
      + asis
      + (datos ? '<div class="queue-card-datos">' + datos + "</div>" : "")
      + sintomasHtml(t)
      + cuerpo + cancelarHtml(t) + "</div></div>";
  }

  // Cancelar el turno (contraparte técnica, 2026-10-19: «Hay que tener la opción de
  // cancelar/eliminar turno»). Sólo mientras el turno sigue por delante y sin
  // asistencia: uno que ya pasó se marca «No vino». Se confirma en el mismo
  // diálogo, sin ventana del navegador.
  function cancelarHtml(t) {
    if (t.asistencia || esPasado(t.hora)) return "";
    return '<div class="solmod-cancel" data-cola-cancel-box>'
      + '<button type="button" class="btn btn-quiet" data-cola-cancel>' + esc(T("admin.queue.cancel.boton", null, "Cancelar turno")) + "</button></div>";
  }

  function cancelarConfirmHtml(t) {
    var cuando = t.hora ? applyVars(T("admin.queue.cancel.pregunta_hora", { hora: t.hora }, "¿Cancelar el turno de las {hora}?"), { hora: t.hora })
      : T("admin.queue.cancel.pregunta", null, "¿Cancelar este turno?");
    return '<p class="solmod-cancel-q"><b>' + esc(cuando) + "</b> "
      + esc(T("admin.queue.cancel.efecto", null, "El horario queda libre y, si el paciente tiene email, le avisamos.")) + "</p>"
      + '<div class="solmod-cancel-actions"><button type="button" class="btn btn-ok" data-cola-cancel-yes>' + esc(T("admin.queue.cancel.si", null, "Sí, cancelar el turno")) + "</button>"
      + '<button type="button" class="btn btn-quiet" data-cola-cancel-no>' + esc(T("admin.queue.cancel.no", null, "No")) + "</button></div>";
  }

  function cancelarTurno(id, box, cerrar) {
    if (disposed) return;
    var t = TURNOS_BY_ID[id];
    var botones = box.querySelectorAll("button");
    var operation = beginMutation("cancel:" + id, function () {
      botones.forEach(function (b) { b.disabled = false; });
    });
    botones.forEach(function (b) { b.disabled = true; });
    fetch("/api/admin/requests/" + encodeURIComponent(id) + "/cancel", {
      method: "POST", credentials: "same-origin", signal: operation.signal,
      headers: { "Content-Type": "application/json" }, body: "{}"
    }).then(function (res) {
      if (!operation.current()) return null;
      return res.json().catch(function () { return {}; }).then(function (j) { return { res: res, body: j }; });
    }).then(function (r) {
      if (!r || !operation.current()) return null;
      var boundary = (r.res.status === 401 || r.res.status === 403)
        ? sessionReady.then(function (mod) { return operation.current() ? mod.confirmar(r.res, r.body, operation.current) : false; })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) { return { r: r, transitioned: transitioned }; });
    }).then(function (outcome) {
      if (!outcome || !operation.current()) return;
      var r = outcome.r;
      if (outcome.transitioned) return;
      if (!r.res.ok) {
        var msg = errText(r.body, T("admin.queue.cancel.error", null, "No se pudo cancelar el turno."));
        toast(msg);
        anunciar(msg);
        botones.forEach(function (b) { b.disabled = false; });
        return;
      }
      var hecho = applyVars(T("admin.queue.cancel.hecho", { who: quien(t) }, "Turno cancelado: {who}"), { who: quien(t) });
      cerrar();
      toast(hecho);
      anunciar(hecho);
      refreshQueue();
    }).catch(function () {
      if (!operation.current()) return;
      toast(T("admin.queue.attendance.save_error_network", null, "No se pudo guardar. Revisá tu conexión."));
      botones.forEach(function (b) { b.disabled = false; });
    }).finally(operation.finish);
  }

  function abrirOverlay(id) {
    if (disposed) return;
    var t = TURNOS_BY_ID[id];
    if (!t) return;
    var wrap = document.createElement("div");
    wrap.innerHTML = overlayHtml(t);
    var back = wrap.firstChild;
    document.body.appendChild(back);
    // El foco entra al diálogo y vuelve a donde estaba al cerrarlo: si no,
    // cerrar deja el foco en el body y hay que recorrer la página de nuevo.
    var volverA = document.activeElement;
    var dlg = back.querySelector(".solmod");
    if (dlg) dlg.focus();
    var releaseOverlay = ownCleanup(function () {
      back.remove();
      removeKey();
      removeClick();
    });
    function cerrar() {
      releaseOverlay();
      if (!disposed && volverA && volverA.isConnected && volverA.focus) volverA.focus();
    }
    function onEsc(e) { if (e.key === "Escape") cerrar(); }
    var removeClick = listen(back, "click", function (e) {
      if (e.target === back || e.target.closest(".solmod-x")) { cerrar(); return; }
      var box = e.target.closest("[data-cola-cancel-box]");
      if (!box) return;
      if (e.target.closest("[data-cola-cancel]")) {
        box.innerHTML = cancelarConfirmHtml(t);
        var no = box.querySelector("[data-cola-cancel-no]");
        if (no) no.focus();
      } else if (e.target.closest("[data-cola-cancel-no]")) {
        box.outerHTML = cancelarHtml(t);
        var abrir = back.querySelector("[data-cola-cancel]");
        if (abrir) abrir.focus();
      } else if (e.target.closest("[data-cola-cancel-yes]")) {
        cancelarTurno(id, box, cerrar);
      }
    });
    var removeKey = listen(document, "keydown", onEsc);
  }

  // ---- render -------------------------------------------------------------

  // Celda de Prácticas/Diagnóstico: un solo datum en UNA línea, recortado con
  // "…" y con el texto entero en `title` (hover). Vacío = celda vacía (nada de
  // guiones) y línea oculta en el modo apilado (.queue-td-empty, CSS <640px).
  function campoCellHtml(valor, label, key) {
    var vacio = !valor;
    var cls = "queue-td-clip" + (vacio ? " queue-td-empty" : "");
    return '<td data-col="' + esc(key) + '" data-label="' + esc(label) + '" class="' + cls + '"'
      + (vacio ? "" : ' title="' + esc(valor) + '"') + ">" + (vacio ? "" : esc(valor)) + "</td>";
  }

  // Respuesta al recordatorio del día previo. Vacío = todavía no contestó —
  // que es distinto de "dijo que no" (ese turno normalmente ya se liberó y no
  // llega hasta acá; si llega, es porque no se pudo cancelar y hay que verlo).
  function confirmoHtml(t) {
    if (t.confirmacion === "si") {
      return '<span class="queue-confirm queue-confirm-yes">' + esc(T("admin.queue.confirm.si", null, "Sí")) + "</span>";
    }
    // "No" con el porqué en el title: el que llega hasta acá es el que no se
    // pudo liberar solo. La palabra corta mantiene angosta la columna.
    if (t.confirmacion === "no") {
      return '<span class="queue-confirm queue-confirm-no" title="' + esc(T("admin.queue.confirm.aviso_no", null, "Avisó que no viene")) + '">'
        + esc(T("admin.queue.confirm.no", null, "No")) + "</span>";
    }
    // Todavía no contestó: un guión apagado dice "no hay respuesta", que es un
    // estado; la celda vacía se leía como un dato que faltó cargar.
    return '<span class="queue-confirm-none" aria-label="' + esc(T("admin.queue.confirm.sin_respuesta", null, "Sin respuesta")) + '">—</span>';
  }

  // Qué tan seguido viene esta persona (patient_ranking.py): TRES barras al
  // lado del nombre, como el nivel de señal del teléfono (responsable del producto, 2026-11-09),
  // las llenas son el nivel (3 viene siempre · 2 falta a veces · 1 falta
  // seguido). Sin historial, nada. La pieza es compartida con «Pacientes»
  // (platform/attendance-forecast.mjs): las barras son un botón que abre la
  // explicación con la línea de ESE paciente.
  function historialBarrasHtml(t) {
    if (!Forecast || !Forecast.level(t.ranking)) return "";
    return Forecast.cellHtml(t.ranking, T);
  }

  // Entrevista previa (modules/intake/, fase 4): el color del triage y
  // "Iniciar consulta"; el resumen que escribió la IA va en la ficha, con su
  // marca. Mismas claves que admin-doctor-patients.js (namespace
  // admin.queue.triage.*) — un mismo estado no puede decir dos cosas según la
  // pantalla.
  function triageLabels() {
    return {
      ROJO: T("admin.queue.triage.rojo", null, "Rojo"),
      NARANJA: T("admin.queue.triage.naranja", null, "Naranja"),
      AMARILLO: T("admin.queue.triage.amarillo", null, "Amarillo"),
      VERDE: T("admin.queue.triage.verde", null, "Verde")
    };
  }

  function triageChipHtml(triage) {
    var key = String(triage || "").toUpperCase();
    var label = triageLabels()[key];
    if (!label) return "";
    return '<span class="queue-triage queue-triage--' + key.toLowerCase() + '">' + esc(label) + "</span>";
  }

  // Una consulta ya confirmada (problems.md №1, 2026-10-23) dice «Atendido»
  // y abre la consulta de sólo lectura — «Iniciar consulta» otra vez invitaba
  // a emitir una segunda receta. Mismo botón y misma URL en los dos casos.
  function consultaAccionHtml(iv) {
    if (iv.consulta_confirmada) {
      return '<span class="queue-triage-atendido">' + esc(T("admin.queue.triage.atendido", null, "Atendido")) + "</span> "
        + '<button type="button" class="btn btn-quiet btn-sm queue-consult" data-iniciar-consulta="'
        + esc(iv.id) + '">' + esc(T("admin.queue.triage.ver_consulta", null, "Ver consulta")) + "</button>";
    }
    if (String(iv.estado || "") !== "done") return "";
    return '<button type="button" class="btn btn-quiet btn-sm queue-consult" data-iniciar-consulta="'
      + esc(iv.id) + '">' + esc(T("admin.queue.triage.iniciar_consulta", null, "Iniciar consulta")) + "</button>";
  }

  function entrevistaCellHtml(t) {
    var iv = t.entrevista;
    var lbl = T("admin.queue.table.entrevista", null, "Síntomas");
    if (!iv) return '<td data-col="entrevista" data-label="' + esc(lbl) + '" class="queue-td-empty"></td>';
    var estado = String(iv.estado || "");
    var partes = estado === "active"
      ? '<span class="queue-triage-curso">' + esc(T("admin.queue.triage.en_curso", null, "En curso")) + "</span>"
      : triageChipHtml(iv.triage);
    // On the phone the cell is a block and its parts sit inline: a space in
    // the markup keeps the chip, «Actualizada» and the button apart (and
    // innerText readable); the desktop flex column ignores it.
    if (iv.actualizada) {
      // "Actualizar entrevista" (2026-08-01): el paciente agregó info nueva
      // después del primer cierre — marca discreta, mismo espíritu que la
      // "S/T" de sobreturno (título con la explicación completa).
      partes += (partes ? " " : "") + '<span class="queue-triage-actualizada" title="' + esc(T("admin.queue.triage.actualizada_title", null, "El paciente agregó información después de la primera entrevista")) + '">'
        + esc(T("admin.queue.triage.actualizada", null, "Actualizada")) + "</span>";
    }
    var accion = consultaAccionHtml(iv);
    if (accion) partes += (partes ? " " : "") + accion.replace(/<button /g, "<button data-export-omit ");
    return '<td data-col="entrevista" data-label="' + esc(lbl) + '" class="queue-td-triage'
      + (partes ? "" : " queue-td-empty") + '">' + partes + "</td>";
  }

  // Fecha corta de "Solicitado" a través del módulo canónico compartido.
  function fechaCorta(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d) || !Dates) return String(iso);
    var p = function (n) { return String(n).length < 2 ? "0" + n : "" + n; };
    var diaIso = d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
    return Dates.corta(diaIso, p(d.getHours()) + ":" + p(d.getMinutes())) || String(iso);
  }

  // The referral the patient uploaded: the panel's paperclip (Icons8 SF
  // Regular, as in Solicitudes) with the word «Orden», in the patient's line
  // (responsable del producto, 2026-11-09). No referral, nothing.
  function ordenBotonHtml(t, nombre) {
    if (!t.tiene_orden) return "";
    var aria = applyVars(T("admin.queue.table.ver_orden_de", { nombre: nombre }, "Ver la orden de {nombre}"), { nombre: nombre });
    return '<button type="button" class="queue-orden" data-cola-abrir="' + esc(t.id) + '" data-export-omit aria-haspopup="dialog" aria-label="' + esc(aria) + '">'
      + '<span class="queue-orden-ico" aria-hidden="true"></span>' + esc(T("admin.queue.table.orden", null, "Orden")) + "</button>";
  }

  function celdaHtml(col, t) {
    var sobreTitle = T("admin.queue.free_slot.sobreturno", null, "Sobreturno");
    var sobre = t.sobreturno
      ? '<span class="queue-overbooking" title="' + esc(sobreTitle) + '" aria-label="' + esc(sobreTitle) + '">S/T</span>'
      : "";
    var vencida = !t.asistencia && esPasado(t.hora);
    var hora = '<span class="queue-hour' + (vencida ? " queue-hour-overdue" : "") + '"><span>'
      + esc(t.hora) + "</span>" + sobre + "</span>";
    var nombre = nombrePropio(t.paciente) || T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre");
    var lblHora = T("admin.queue.table.hora", null, "Hora");
    var lblPaciente = T("admin.queue.table.paciente", null, "Paciente");
    var lblConfirmo = T("admin.queue.table.confirmo", null, "Confirmó");
    var lblSolicitado = T("admin.queue.table.solicitado", null, "Solicitado");
    var lblCobertura = T("admin.queue.table.cobertura", null, "Cobertura");
    if (col.key === "hora") return '<td data-col="hora" data-label="' + esc(lblHora) + '">' + hora + "</td>";
    if (col.key === "paciente") {
      // «Name, N años» is ONE link to the appointment card (the product owner, 2026-11-09);
      // the history bars and the referral follow it on the same line. With
      // no doctor column (a doctor's own day, or the list narrowed to one
      // doctor) the service goes under the name when the day mixes services.
      var texto = nombreEdad(t);
      var pac = '<button type="button" class="queue-pat-link" data-cola-abrir="' + esc(t.id) + '"'
        + ' aria-haspopup="dialog" aria-label="' + esc(applyVars(T("admin.queue.table.ver_ficha", { nombre: texto }, "Ver la ficha de {nombre}"), { nombre: texto })) + '">'
        + esc(texto) + "</button>";
      var debajo = SERVICIO_BAJO_NOMBRE && t.servicio
        ? '<span class="queue-sub" data-export-omit>' + esc(t.servicio) + "</span>" : "";
      return '<td data-col="paciente" data-label="' + esc(lblPaciente) + '" class="queue-td-pat" data-export="' + esc(texto) + '">'
        + '<span class="queue-pat-line">' + pac + historialBarrasHtml(t) + ordenBotonHtml(t, nombre) + "</span>"
        + debajo + "</td>";
    }
    if (col.key === "medico") return medicoCellHtml(GRUPO_DE[t.id], t);
    if (col.key === "confirmo") {
      // En el modo apilado el "sin respuesta" no se muestra: un rótulo
      // "Confirmó" con un guión abajo es una línea que no dice nada.
      var sinResp = !t.confirmacion;
      return '<td data-col="confirmo" data-label="' + esc(lblConfirmo) + '" class="queue-td-confirm'
        + (sinResp ? " queue-td-empty" : "") + '">' + confirmoHtml(t) + "</td>";
    }
    if (col.key === "solicitado") return '<td data-col="solicitado" data-label="' + esc(lblSolicitado) + '">' + esc(fechaCorta(t.solicitado_en)) + "</td>";
    if (col.key === "cobertura") return campoCellHtml(t.cobertura, lblCobertura, "cobertura");
    if (col.key === "entrevista") return entrevistaCellHtml(t);
    if (col.key === "relleno") return '<td data-col="relleno"></td>';
    return "";
  }

  // The doctor of each row: a link that narrows the day to that doctor
  // (the product owner, 2026-11-08), the service of the appointment under the name
  // (the product owner, 2026-11-09). «Sin médico asignado» is a value of the column and
  // narrows like any other.
  function medicoCellHtml(g, t) {
    var lbl = T("admin.queue.table.medico", null, "Médico");
    if (!g) return '<td data-col="medico" data-label="' + esc(lbl) + '" class="queue-td-empty"></td>';
    var nombre = etiquetaMedicoGrupo(g);
    var servicio = (t && t.servicio) || g.especialidad || "";
    var aria = applyVars(T("admin.queue.table.ver_medico", { nombre: nombre }, "Ver sólo los pacientes de {nombre}"), { nombre: nombre });
    return '<td data-col="medico" data-label="' + esc(lbl) + '" class="queue-td-doc" data-export="' + esc(nombre) + '">'
      + '<button type="button" class="queue-doctor-link" data-cola-doctor="' + esc(grupoKey(g)) + '" aria-label="' + esc(aria) + '">'
      + esc(nombre) + "</button>"
      + (servicio ? '<span class="queue-sub" data-export-omit>' + esc(servicio) + "</span>" : "")
      + "</td>";
  }

  function filaHtml(t) {
    return '<tr class="cnt-tr' + (t.asistencia ? " queue-tr-done" : "") + '" data-cola-row="' + esc(t.id) + '"'
      + ' data-export-dni="' + esc(Format.digits(t.dni) || "") + '">'
      + COLS.map(function (c) { return celdaHtml(c, t); }).join("")
      + "</tr>";
  }

  // La línea de ahora: lo de arriba ya debería haber pasado. Sólo se dibuja
  // donde de verdad separa el pasado del futuro (ver tbodyGrupo). "Ahora" es
  // una palabra suelta, no pegada a un día/mes traducido — la hora que la
  // sigue es un reloj (HH:MM), no una fecha con gramática propia por idioma.
  function filaAhoraHtml() {
    return '<tr class="queue-now-tr"><td class="queue-now-td" colspan="' + COLS.length + '">'
      + '<span class="queue-now"><span class="queue-now-t">' + esc(applyVars(T("admin.queue.table.ahora", { hora: ahoraArtHHMM() }, "Ahora {hora}"), { hora: ahoraArtHHMM() })) + "</span>"
      + '<span class="queue-now-l"></span></span></td></tr>';
  }

  // Un hueco de la agenda que todavía no tiene paciente (the reference-deployment counterpart,
  // reunión 2026-07-24: "acá Roger de 8 a 9:30, pero si hay un turno libre de
  // 8:30 no lo veo para poder dárselo a alguien que vino"). Va INTERCALADO en
  // la misma tabla, en su lugar cronológico: la jornada se lee de arriba a
  // abajo y el hueco se ve donde está, no en una lista aparte.
  function filaLibreHtml(hora, g) {
    var medico = g && g.medico;
    var puede = puedeCrearTurno();
    // In the list of several doctors the free hour says whose it is.
    var deQuien = COLS.some(function (c) { return c.key === "medico"; }) && g
      ? '<span class="queue-free-who">' + esc(etiquetaMedicoGrupo(g)) + "</span>" : "";
    var ref = hora + (medico ? " de " + nombrePropio(medico) : "");
    // La FILA entera es el blanco del click, no sólo el botón (responsable del producto,
    // 2026-08-05: "clicking an empty slot creates a turno"). Un hueco se lee
    // como "acá no hay nadie" y lo natural es clickear el renglón, no apuntar
    // a un botón chico al costado. El botón se queda igual: es lo que se
    // enfoca con el teclado y lo que nombra la acción — la fila nada más
    // repite su mismo destino, con los mismos data-*, así el delegado de
    // click no necesita saber cuál de los dos se tocó.
    var dar = puede
      ? ' data-cola-dar="' + esc(hora) + '" data-cola-dar-medico="' + esc(medico || "") + '"'
      : "";
    var libreTxt = T("admin.queue.free_slot.libre", null, "Libre");
    var accion = puede
      ? '<button type="button" class="btn btn-quiet btn-sm queue-give"' + dar
        + ' aria-label="' + esc(applyVars(T("admin.queue.free_slot.dar_aria", { ref: ref }, "Dar el turno libre de las {ref}"), { ref: ref })) + '">'
        + esc(T("admin.queue.free_slot.dar_boton", null, "Dar este turno")) + "</button>"
      : '<span class="queue-free-txt">' + esc(libreTxt) + "</span>";
    return '<tr class="cnt-tr queue-tr-free' + (puede ? " queue-tr-give" : "") + '"' + dar + ">"
      + '<td data-col="hora" data-label="' + esc(T("admin.queue.table.hora", null, "Hora")) + '"><span class="queue-hour"><span>' + esc(hora) + "</span></span></td>"
      + '<td class="queue-td-free" data-export-skip colspan="' + (COLS.length - 1) + '">'
      + (puede ? '<span class="queue-free-k">' + esc(libreTxt) + "</span>" : "") + deQuien + accion + "</td>"
      + "</tr>";
  }

  // Quién puede sacar un turno en un hueco: la mesa (admin/operador/
  // admisionista) y también el MÉDICO (responsable del producto, 2026-08-05). Un médico sigue sin
  // agendar por otros, pero #cola le muestra UNA sola agenda —la suya—, y el
  // hueco libre de su propio día es exactamente el caso de la contraparte técnica: el paciente
  // ya está enfrente. El gate de verdad es el servidor: admin_appointments
  // fuerza el médico del turno al de la sesión, así el rol no puede cargarle
  // un turno a la agenda de un colega ni tocando la request a mano.
  function puedeCrearTurno() {
    return window.CURRENT_ROLE === "admin" || window.CURRENT_ROLE === "operador"
      || window.CURRENT_ROLE === "admisionista" || window.CURRENT_ROLE === "medico";
  }

  // Cabecera: ordenable (misma lengua que #medicos). La manija de ancho la
  // monta SanaTable (admin-table.js) — es el mismo motor en todas las tablas
  // del admin, así que no se pinta acá. La flecha vive en un hueco de ancho
  // fijo para que ordenar no corra los anchos medidos.
  function theadHtml() {
    var h = "";
    COLS.forEach(function (c) {
      if (c.relleno) { h += '<th class="cnt-th" data-col="relleno" aria-hidden="true"></th>'; return; }
      var on = STATE.sort.key === c.key;
      var flecha = '<span class="queue-arrow" aria-hidden="true">' + (on ? (STATE.sort.dir === 1 ? "▲" : "▼") : "") + "</span>";
      var label = esc(c.label);
      // scope + aria-sort: el lector nombra la columna al leer cada celda y
      // dice por cuál está ordenada la tabla, en vez de leer una flecha suelta.
      var sort = on ? (STATE.sort.dir === 1 ? "ascending" : "descending") : "none";
      // tabindex: ordenar tiene que poder hacerse con el teclado, no sólo con
      // el mouse (el delegado de keydown de más abajo escucha Enter/Espacio).
      h += '<th scope="col" aria-sort="' + sort + '" tabindex="0" class="cnt-th cnt-th-sortable' + (on ? " on" : "")
        + '" data-col="' + c.key + '" data-cola-sort="' + c.key + '"'
        + ' title="' + esc(applyVars(T("admin.queue.table.ordenar_por", { label: c.label }, "Ordenar por {label}"), { label: c.label })) + '">' + label + flecha + "</th>";
    });
    return h;
  }

  // ¿La lista es la jornada en orden? Con búsqueda o con un orden por columna
  // elegido a mano deja de serlo, y ahí ni los huecos libres ni la línea de
  // "Ahora" dicen nada (un "libre" en el medio de una lista por nombre no
  // significa nada).
  function esCronologico() {
    return !STATE.qBusca && STATE.sort.key === "hora" && STATE.sort.dir === 1;
  }

  // Stable selection key: doctor ID, name fallback, then the unassigned group.
  function grupoKey(g) {
    return g.medico_id || g.medico || "_sin_medico";
  }

  // ONE flat list for the day (the product owner, 2026-11-08: «a flat list of today's
  // patients»): every doctor's rows in one <tbody>, in the order the table
  // is sorted. In the day's own order the free hours of the agendas go in
  // their place (the stable sort keeps a booked row before a free one at
  // the same time).
  function tbodyDia(filasTurno, grupos) {
    var cronologico = esCronologico();
    var filas = filasTurno.slice();
    if (cronologico && STATE.verLibres) {
      grupos.forEach(function (g) {
        (g.libres || []).forEach(function (h) { filas.push({ hora: h, t: null, g: g }); });
      });
      filas.sort(function (a, b) { return String(a.hora).localeCompare(String(b.hora)) || ((a.t ? 0 : 1) - (b.t ? 0 : 1)); });
    }
    // "Ahora" sólo donde separa algo: hoy, en orden cronológico y con turnos de
    // los dos lados de la hora.
    var marcarAhora = cronologico && STATE.dia === hoyArtIso()
      && filas.some(function (r) { return esPasado(r.hora); })
      && filas.some(function (r) { return !esPasado(r.hora); });
    var puesta = false;
    var rows = filas.map(function (r) {
      var linea = "";
      if (marcarAhora && !puesta && !esPasado(r.hora)) { linea = filaAhoraHtml(); puesta = true; }
      return linea + (r.t ? filaHtml(r.t) : filaLibreHtml(r.hora, r.g));
    }).join("");
    return "<tbody>" + rows + "</tbody>";
  }

  // The service rides under the doctor's name. Without the doctor column (a
  // doctor's own day, or the list narrowed to one doctor) it goes under the
  // patient's name — only when the day mixes services: «Dermatología» under
  // each of 11 names says what the header already says (the product owner, 2026-11-05).
  var SERVICIO_BAJO_NOMBRE = false;
  function serviciosDistintos(grupos) {
    var vistos = {};
    var n = 0;
    grupos.forEach(function (g) {
      (g.turnos || []).forEach(function (t) {
        var s = norm(t.servicio || g.especialidad).trim();
        if (s && !vistos[s]) { vistos[s] = true; n++; }
      });
    });
    return n > 1;
  }

  // UNA tabla para todo el día. <caption> oculto: sin título propio, un lector
  // de pantalla la anuncia sólo como "tabla, N filas".
  // The doctor column is there unless the list is one doctor's day: a
  // doctor's own screen needs no name, a list narrowed to one doctor names
  // it once, above the table.
  function columnaMedico(grupos) {
    return window.CURRENT_ROLE !== "medico" && !STATE.filtro && !!grupos.length;
  }

  function tablaDelDia(filas, grupos) {
    var conMedico = columnaMedico(grupos);
    SERVICIO_BAJO_NOMBRE = !conMedico && serviciosDistintos(grupos);
    COLS = activeCols().filter(function (c) {
      return conMedico || c.key !== "medico";
    });
    var colgroup = "<colgroup>" + COLS.map(function (c) {
      return '<col data-col="' + c.key + '">';
    }).join("") + "</colgroup>";
    var titulo = '<caption class="sr-only">' + esc(applyVars(T("admin.queue.table.turnos_de", { fecha: fechaParaTexto(STATE.dia) }, "Turnos de {fecha}"), { fecha: fechaParaTexto(STATE.dia) })) + "</caption>";
    return '<div class="cnt-table-wrap queue-wrap"><table class="cnt-table queue-table" data-export-day="' + esc(STATE.dia || "") + '">' + titulo + colgroup
      + "<thead><tr>" + theadHtml() + "</tr></thead>"
      + tbodyDia(filas, grupos)
      + "</table></div>";
  }

  // ---- anchos: el motor compartido (admin-table.js) -------------------------
  // Medir el ancho natural, hacerlo caber, dejar arrastrar y recordar el ancho
  // elegido es EXACTAMENTE lo mismo que necesitan Pacientes, Usuarios, Médicos
  // y el calendario del médico. Vive una sola vez en SanaTable; acá sólo se
  // declara lo que es propio de esta pantalla: qué columnas hay, cuáles ceden
  // ancho (`flex`) y en qué orden caen cuando no entran.

  // Columnas que pueden caerse (en este orden) cuando el ancho no alcanza:
  // las que eligió la persona en el engranaje ceden antes que el nombre y el
  // médico. Lo que cae sigue estando a un click, en la ficha.
  var DROP = ["solicitado", "cobertura", "confirmo"];

  function hostCola() { return document.getElementById("cola-results"); }

  // Una columna cuyas celdas de turno son todas «—» (.queue-td-empty) no
  // dice nada: si falta ancho, es la primera en caer.
  function columnaVacia(k) {
    var h = hostCola();
    if (!h) return false;
    var tds = h.querySelectorAll('table.queue-table tr[data-cola-row] > td[data-col="' + k + '"]');
    if (!tds.length) return false;
    for (var i = 0; i < tds.length; i++) {
      if (!tds[i].classList.contains("queue-td-empty")) return false;
    }
    return true;
  }

  // Lo que pide el texto de cada celda de una columna (nombre con edad,
  // servicio), medido mientras la tabla se mide sin recortar: con eso el
  // motor reparte la falta de ancho donde tapa menos texto. El botón de la
  // extensión HSI no cuenta como texto: su lugar es el relleno derecho.
  function anchosCelda(k) {
    var h = hostCola();
    if (!h || typeof document.createRange !== "function") return null;
    var r = document.createRange();
    var out = [];
    h.querySelectorAll('table.queue-table tr[data-cola-row] > td[data-col="' + k + '"]').forEach(function (td) {
      var hsi = td.querySelector(".hr-hsi");
      r.selectNodeContents(td);
      if (hsi && hsi.parentNode === td) r.setEndBefore(hsi);
      var st = getComputedStyle(td);
      var w = r.getBoundingClientRect().width + (parseFloat(st.paddingLeft) || 0) + (parseFloat(st.paddingRight) || 0);
      out.push(Math.min(Math.ceil(w), Math.ceil(td.getBoundingClientRect().width)));
    });
    return out;
  }

  // El motor vive en el módulo ES canónico admin-table.mjs (fila 4.1): la
  // instancia se crea en cuanto resuelve el import — ANTES del primer render,
  // porque readyRoute también espera tableReady — y el `selector` queda
  // reservado desde ese mismo instante, igual que cuando el script era
  // clásico y evaluaba antes que este archivo.
  var ANCHOS_TBL = null;
  tableReady = tableReady.then(function (m) {
    if (disposed) return;
    ANCHOS_TBL = m.crear({
      clave: "cola",
      almacen: LS_COLS,                 // conserva los anchos ya elegidos
      selector: "table.queue-table",
      host: hostCola,
      medirClase: "queue-measuring",
      tablas: function () {
        var h = hostCola();
        return h ? h.querySelectorAll("table.queue-table") : [];
      },
      // El bloque de la tabla de turnos, no el primero de #cola-results: con
      // un médico elegido, el primero es la lista de médicos (353 px a 1440)
      // y la cola se encajaba en ese ancho — todas las columnas a su mínimo y
      // el resto vacío en «relleno».
      medio: function () {
        var h = hostCola();
        var tb = h && h.querySelector("table.queue-table");
        return tb ? tb.closest(".cnt-table-wrap") : null;
      },
      cols: colsConHsi,
      drop: DROP,
      vacia: columnaVacia,
      // La cola tiene su propio engranaje: una columna que no viene de
      // fábrica y está prendida la eligió la persona, y se ve aunque esté
      // vacía; las demás vacías no se dibujan (admin-table.mjs).
      elegida: function (k) {
        return VISIBLE_KEYS.indexOf(k) !== -1 && COLS_ALL.some(function (c) { return c.key === k && !c["default"]; });
      },
      anchosCelda: anchosCelda,
      fill: false,
      dropClase: function (k) { return "queue-hide-" + k; },
      apilado: function () {
        return apilado() || window.matchMedia("(max-width:1024px)").matches;
      }
    });
    observarBotonHsi();
  });

  function autoFit() { if (ANCHOS_TBL) ANCHOS_TBL.autoFit(); }

  // «Cargar en HSI» — la extensión de la mesa (extensions/hsi-humanizador,
  // content-turnos.css) — se pinta DENTRO de la celda del paciente, sobre un
  // relleno derecho que reserva su propio CSS, y llega DESPUÉS de medir: la
  // columna quedaba con el ancho de siempre y el botón tapaba el nombre (plan
  // de lanzamiento P2). Ahora, con el botón:
  //  - «Paciente» pide el nombre entero MÁS el lugar del botón (la edad puede
  //    recortarse, el nombre no); lo que falte lo ceden las otras columnas de
  //    texto y caen columnas por prioridad (DROP), como ante cualquier falta
  //    de ancho;
  //  - si aun así la fila no entra en su ancho, el botón va DEBAJO del nombre
  //    (`queue-hsi-debajo`, el mismo arreglo que en tablet): nunca tapa el
  //    nombre ni empuja la fila fuera de la pantalla.
  // Un ancho arrastrado a mano sigue mandando.
  var HSI_DEBAJO = "queue-hsi-debajo";
  function celdaHsi() {
    var h = hostCola();
    var caja = h && typeof h.querySelector === "function" && h.querySelector('td[data-col="paciente"] > .hr-hsi');
    return caja ? caja.parentNode : null;
  }
  function colsConHsi() {
    var td = celdaHsi();
    if (!td) return COLS;
    var st = getComputedStyle(td);
    var izq = parseFloat(st.paddingLeft) || 0, der = parseFloat(st.paddingRight) || 0;
    var extra = Math.max(0, Math.ceil(der - izq));
    if (!extra) return COLS;   // botón debajo: la columna es la de siempre
    var nombre = 0;
    hostCola().querySelectorAll('td[data-col="paciente"] .queue-pat-link').forEach(function (el) {
      nombre = Math.max(nombre, el.scrollWidth);
    });
    return COLS.map(function (c) {
      if (c.key !== "paciente") return c;
      var max = (c.max || c.min) + extra;
      return Object.assign({}, c, { min: Math.min(max, Math.max(c.min + extra, Math.ceil(nombre + izq + der))), max: max });
    });
  }
  function filaSeSale() {
    var h = hostCola();
    var tb = h && h.querySelector("table.queue-table");
    var medio = tb && tb.closest(".cnt-table-wrap");
    return !!(tb && medio && tb.getBoundingClientRect().right > medio.getBoundingClientRect().right + 1);
  }
  // Medir con el botón a la derecha; si la fila no entra, el botón debajo.
  // Todo en el mismo frame: no se llega a pintar el intento.
  var hsiPend = null;
  function encajarHsi() {
    if (hsiPend !== null || disposed) return;
    hsiPend = window.requestAnimationFrame(function () {
      hsiPend = null;
      var sec = document.getElementById("sec-cola");
      if (disposed || !sec || !ANCHOS_TBL || !celdaHsi()) return;
      sec.classList.remove(HSI_DEBAJO);
      ANCHOS_TBL.autoFit();
      if (filaSeSale()) {
        sec.classList.add(HSI_DEBAJO);
        ANCHOS_TBL.autoFit();
      }
    });
  }
  // La extensión decora cada render de la tabla: cuando sus botones aparecen,
  // se vuelve a encajar.
  function observarBotonHsi() {
    var sec = document.getElementById("sec-cola");
    if (!sec || !window.MutationObserver) return;
    var obs = new window.MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var nodos = records[i].addedNodes;
        for (var j = 0; j < nodos.length; j++) {
          if (nodos[j].classList && nodos[j].classList.contains("hr-hsi")) { encajarHsi(); return; }
        }
      }
    });
    obs.observe(sec, { childList: true, subtree: true });
    listen(window, "resize", function () { later(encajarHsi, 200); });
    ownCleanup(function () {
      obs.disconnect();
      if (hsiPend !== null) window.cancelAnimationFrame(hsiPend);
      hsiPend = null;
    });
  }

  var Calendar = null;
  var calendarReady = (window.sanaImportStatic
    ? window.sanaImportStatic("platform/calendar-grid.mjs")
    : import(new URL("./calendar-grid.mjs", import.meta.url)))
    .then(function (mod) { Calendar = typeof mod.gridHtml === "function" ? mod : null; if (!disposed) paintDateNav(); }).catch(function () { Calendar = null; });
  var calendarOpen = false, calendarMonth = null, calendarData = null;
  var calendarSerial = 0, calendarController = null, calendarError = false;
  function monthRange(iso, delta) {
    var parts = (iso || STATE.dia).split("-").map(Number);
    var first = new Date(parts[0], parts[1] - 1 + (delta || 0), 1, 12);
    var last = new Date(first.getFullYear(), first.getMonth() + 1, 0, 12);
    function wire(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
    return { from: wire(first), to: wire(last) };
  }
  function calendarCurrentKey() { return [STATE.dia, STATE.filtro, STATE.filtroCentro, calendarMonth].join("|"); }
  function refreshCalendar() {
    if (disposed || !STATE.dia) return Promise.resolve();
    var key = calendarCurrentKey(), serial = ++calendarSerial;
    if (calendarController) calendarController.abort();
    calendarController = new AbortController();
    var range = monthRange(calendarMonth || STATE.dia);
    var query = new URLSearchParams({ desde: range.from, hasta: range.to, dia: STATE.dia });
    if (STATE.filtro) query.set("medico", STATE.filtro);
    if (STATE.filtroCentro) query.set("centro", STATE.filtroCentro);
    var live = function () { return !disposed && serial === calendarSerial && key === calendarCurrentKey(); };
    return fetch("/api/admin/day-queue/calendar?" + query.toString(), { credentials: "same-origin", signal: calendarController.signal })
      .then(function (res) {
        return (res.status === 401 || res.status === 403
          ? sessionReady.then(function (session) { return live() ? session.confirmar(res, undefined, live) : true; })
          : Promise.resolve(false))
          .then(function (moved) {
            if (moved || !live()) return null;
            if (!res.ok) throw new Error("calendar-unavailable");
            return res.json();
          });
      }).then(function (data) {
        if (!data || !live()) return;
        if (!Array.isArray(data.dias) || data.dia !== STATE.dia ||
            data.dias.some(function (day) { return typeof day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(day); })) throw new Error("calendar-invalid");
        calendarData = data; calendarError = false;
        paintDateNav();
      }).catch(function () {
        if (!live()) return;
        calendarError = true; calendarData = null;
        paintDateNav();
      });
  }
  function paintDateNav() {
    var old = root.querySelector(".queue-date-picker");
    if (old) old.outerHTML = dateNavHtml();
  }
  // Una sola fila maneja el día (responsable del producto, 2026-10-26, sobre la pantalla del
  // médico): la fecha a la izquierda, después UN control segmentado de una
  // sola altura — anterior / Hoy / siguiente — y al lado el del mes, con la
  // misma geometría. Anterior/siguiente saltan al día con turnos más cercano
  // (calendarData), por eso su nombre accesible lo dice. Los horarios del
  // médico NO se cuelgan acá: son «Mis agendas» en la navegación de la sección.
  function dayStepHtml(direction, disabled) {
    var label = T(direction < 0 ? 'admin.queue.calendar.previous_day' : 'admin.queue.calendar.next_day', null, direction < 0 ? 'Día anterior con turnos' : 'Día siguiente con turnos');
    return '<button type="button" class="queue-icon-btn queue-seg-arrow" data-cola-nav="' + direction + '" aria-label="' + esc(label) + '" title="' + esc(label) + '"' +
      (disabled ? ' disabled' : '') + '><span class="queue-icon ' + (direction < 0 ? 'queue-icon-prev' : 'queue-icon-next') + '" aria-hidden="true"></span></button>';
  }
  // The day reads once (the product owner, 2026-11-08: "today was written twice"): the
  // short date («dom 8 nov»), then the day before / «Hoy» / the day after and
  // the month as a calendar icon — the panel's outlined buttons (the product owner,
  // 2026-11-09: only the icons after the search go bare).
  function dateNavHtml() {
    var prev = calendarData && calendarData.anterior;
    var next = calendarData && calendarData.siguiente;
    var mes = T('admin.queue.calendar.show_month', null, 'Ver mes');
    var html = '<div class="queue-date-picker"><div class="queue-datenav">' +
      '<span class="queue-date-label">' + esc(fechaLabel(STATE.dia)) + '</span>' +
      '<div class="queue-day-seg" role="group" aria-label="' + esc(T('admin.queue.calendar.day_nav', null, 'Cambiar de día')) + '">' +
      dayStepHtml(-1, !prev) +
      '<button type="button" class="queue-day-hoy" data-cola-hoy="1"' + (STATE.hoy ? ' aria-current="date"' : '') + '>' + esc(T('admin.queue.calendar.today', null, 'Hoy')) + '</button>' +
      dayStepHtml(1, !next) + '</div>' +
      '<button type="button" class="queue-icon-btn queue-month-btn" data-cola-calendar-toggle="1" aria-pressed="' + calendarOpen + '" aria-label="' + esc(mes) + '" title="' + esc(mes) + '">' +
      '<span class="queue-icon queue-icon-calendar" aria-hidden="true"></span></button></div>';
    if (calendarOpen && Calendar && STATE.dia) {
      var range = monthRange(calendarMonth || STATE.dia);
      var days = (calendarData ? calendarData.dias : []).map(function (day) { return { dia: day, className: 'queue-calendar-occupied', count:calendarData.conteos && calendarData.conteos[day] }; });
      // Days the shown doctor attends with nobody booked yet: ringed, so the
      // calendar answers "when do I work" and not only "when am I booked".
      var attends = ((calendarData && calendarData.atencion) || []).filter(function (day) {
        return typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day) && !(calendarData.dias || []).includes(day);
      });
      var attendsTitle = T('admin.queue.calendar.attends_title', null, 'Atiende, sin turnos');
      attends.forEach(function (day) { days.push({ dia: day, className: 'queue-calendar-attends', title: attendsTitle }); });
      if (!days.length) days = [{ dia: range.from }];
      html += '<div class="queue-month-calendar">' +
        Calendar.gridHtml(days, { from: range.from, to: range.to, selected: STATE.dia, dateAttr: 'data-cola-calendar-date' }) + '</div>';
    }
    if (calendarError) html += '<button type="button" class="queue-today-link" data-cola-calendar-retry="1">' + esc(T("admin.queue.calendar.retry", null, "Recargar calendario")) + '</button>';
    return html + '</div>';
  }

  // El filtro de médico es un <select> de verdad, nunca pills ni un
  // desplegable dibujado a mano (invariante clavado en admin-cola.test.mjs):
  // vive detrás del embudo; el camino corto es el nombre del médico en la
  // fila, que muestra sólo sus pacientes (elegirMedico).
  function etiquetaMedicoGrupo(g) {
    return nombrePropio(g.medico) || T("admin.queue.group.sin_medico", null, "Sin médico asignado");
  }

  // Una selección activa no desaparece cuando el día nuevo no la contiene.
  // Mantenerla a la vista hace explícito por qué la lista quedó vacía y deja
  // "Todos…" disponible para quitarla; jamás ensanchamos el resultado en
  // silencio al cruzar de fecha.
  function opcionesConSeleccion(options, value, etiqueta) {
    if (!value || options.some(function (o) { return String(o.value) === String(value); })) return options;
    var texto = etiqueta || String(value);
    return options.concat([{
      value: value,
      label: applyVars(T("admin.queue.filters.sin_resultados_dia", { label: texto }, "{label} (sin resultados ese día)"), { label: texto })
    }]);
  }

  function medicoFiltroHtml() {
    return filtroSelectHtml("queue-doctor-filter", "data-cola-medfiltro", "1",
      T("admin.queue.filters.filtrar_medico", null, "Filtrar por médico"), STATE.filtro,
      [{ value: "", label: T("admin.queue.filters.todos_medicos", null, "Todos los médicos") }].concat(STATE.grupos.map(function (g) {
        return { value: grupoKey(g), label: etiquetaMedicoGrupo(g) };
      })), STATE.filtroMedicoEtiqueta || T("admin.queue.filters.medico_anterior", null, "Médico seleccionado anteriormente"));
  }

  function filtroSelectHtml(className, attr, attrValue, label, value, options, selectedLabel) {
    return FilterControls.select({
      className: "adm-sel " + className, label: label, value: value,
      options: opcionesConSeleccion(options, value, selectedLabel),
      attrs: (function () { var attrs = {}; attrs[attr] = attrValue; return attrs; })()
    });
  }

  // «Imprimir»: the doctor's day on the A4 sheet HSI prints («Turnos
  // programados», /admin/cola/print), in a new tab that opens the print
  // dialog itself. Only a catalog doctor with appointments that day has one;
  // a doctor's own session prints its own day whatever the link says. An
  // icon in the controls row like the other actions (the product owner, 2026-11-08),
  // there only while the list is one doctor's day.
  function imprimirHtml(g) {
    if (!g || !g.medico_id || !(g.turnos || []).length) return "";
    var href = "/admin/cola/print?dia=" + encodeURIComponent(STATE.dia || "")
      + "&medico=" + encodeURIComponent(g.medico_id) + "&auto=1";
    var lbl = T("admin.queue.print.print", null, "Imprimir");
    return '<a class="queue-icon-btn" data-cola-imprimir href="' + esc(href) + '" target="_blank" rel="noopener"'
      + ' aria-label="' + esc(lbl) + '" title="' + esc(lbl) + '"><span class="queue-icon queue-icon-print" aria-hidden="true"></span></a>';
  }

  // The doctor the list is narrowed to (a click on a doctor in the list or
  // the doctor filter): the name once, above the table, and the way back to
  // everyone. A doctor's own day carries no such line (the product owner, 2026-10-26).
  function medicoElegidoHtml(g) {
    if (!g || window.CURRENT_ROLE === "medico") return "";
    var detalle = [g.especialidad, g.centro].filter(Boolean).join(" · ");
    return '<div class="queue-doctor-head" data-cola-elegido="' + esc(grupoKey(g)) + '"><h2 data-export-title-part>' + esc(etiquetaMedicoGrupo(g)) + "</h2>"
      + (detalle ? '<span class="queue-doctor-sub">' + esc(detalle) + "</span>" : "")
      + '<button type="button" class="queue-doctor-all" data-cola-doctor="">'
      + esc(T("admin.queue.table.ver_todos", null, "Ver todos los médicos")) + "</button></div>";
  }

  // The day as ONE list (the product owner, 2026-11-08), sorted by the table's own
  // column (Hora by default, Médico or any other from the header).
  function listaDelDiaHtml(salida, buscando) {
    var filas = [];
    salida.forEach(function (g) {
      g.turnos.forEach(function (t) { filas.push({ hora: t.hora, t: t, g: g, score: SCORE_DE[t.id] || 0 }); });
    });
    if (buscando && !STATE.sortTouched) {
      filas.sort(function (a, b) { return b.score - a.score || cmpHora(a.t, b.t); });
    } else {
      filas.sort(function (a, b) { return cmpCol(a.t, b.t); });
    }
    var uno = salida.length === 1 ? salida[0] : null;
    var elegido = STATE.filtro && uno && grupoKey(uno) === STATE.filtro ? uno : null;
    // No AI notice over the list: the list shows no AI text, the card marks
    // the AI text where it is (the product owner, 2026-11-09).
    return medicoElegidoHtml(elegido) + tablaDelDia(filas, salida);
  }

  // The print icon lives in the controls row, painted with the list: it
  // prints one doctor's day, so it is there only while the list is one.
  function pintarImprimir(salida) {
    var slot = document.querySelector("[data-cola-print-slot]");
    if (!slot) return;
    var uno = salida && salida.length === 1 ? salida[0] : null;
    var propio = window.CURRENT_ROLE === "medico" || (uno && STATE.filtro && grupoKey(uno) === STATE.filtro);
    slot.innerHTML = propio ? imprimirHtml(uno) : "";
  }

  // Narrow the day to one doctor ("" = everyone): the doctor link in the
  // list, «Ver todos» and the doctor filter all land here.
  function elegirMedico(key) {
    STATE.filtro = key || "";
    var activo = STATE.grupos.filter(function (g) { return grupoKey(g) === STATE.filtro; })[0] || null;
    STATE.filtroMedicoEtiqueta = activo ? etiquetaMedicoGrupo(activo) : "";
    STATE.seleccionado = activo ? grupoKey(activo) : null;
    var sel = document.querySelector("[data-cola-medfiltro]");
    if (sel && sel.value !== STATE.filtro) sel.value = STATE.filtro;
    calendarData = null; paintDateNav();
    savePrefs();
    renderResultados();
    refreshCalendar();
  }

  // Servicios distintos del día (sobre TODOS los grupos, no sobre el filtro de
  // médico activo — así la lista de servicios es estable al cambiar de médico).
  function serviciosDelDia() {
    var visto = {}, out = [];
    STATE.grupos.forEach(function (g) {
      g.turnos.forEach(function (t) {
        var s = t.servicio || "";
        if (s && !visto[s]) { visto[s] = 1; out.push(s); }
      });
    });
    out.sort(function (a, b) { return a.localeCompare(b, "es"); });
    return out;
  }

  function servicioFiltroHtml() {
    return filtroSelectHtml("queue-service-filter", "data-cola-servfiltro", "1",
      T("admin.queue.filters.filtrar_servicio", null, "Filtrar por servicio"), STATE.filtroServicio,
      [{ value: "", label: T("admin.queue.filters.todos_servicios", null, "Todos los servicios") }].concat(serviciosDelDia().map(function (s) { return { value: s, label: s }; })), STATE.filtroServicio);
  }

  function gruposCampo(campo) {
    var values = {};
    STATE.grupos.forEach(function (g) { if (g[campo]) values[g[campo]] = true; });
    return Object.keys(values).sort(function (a, b) { return a.localeCompare(b, "es"); });
  }

  // Las opciones del filtro de centro son TODOS los centros que la cuenta ve
  // (los manda el servidor), no los de las agendas del día en pantalla: un
  // centro sin agenda ese lunes sigue siendo un centro que la mesa elige
  // (contraparte técnica, 2026-10-24: «Todos los centros» sólo ofrecía dos). Sin la lista
  // del servidor (vista del médico) se cae a los centros de los grupos.
  function centrosFiltro() {
    return STATE.centros && STATE.centros.length ? STATE.centros : gruposCampo("centro");
  }

  // Un médico en dos centros no tiene un único `centro` en el encabezado, pero
  // sí aparece bajo cada uno de los suyos (`centros`).
  function grupoEnCentro(g, centro) {
    return (g.centros && g.centros.length ? g.centros : (g.centro ? [g.centro] : [])).indexOf(centro) >= 0;
  }

  function especialidadFiltroHtml() {
    return filtroSelectHtml("queue-specialty-filter", "data-cola-especialidadfiltro", "1",
      T("admin.queue.filters.filtrar_especialidad", null, "Filtrar por especialidad"), STATE.filtroEspecialidad,
      [{ value: "", label: T("admin.queue.filters.todas_especialidades", null, "Todas las especialidades") }].concat(gruposCampo("especialidad").map(function (s) { return { value: s, label: s }; })), STATE.filtroEspecialidad);
  }

  function centroFiltroHtml() {
    return filtroSelectHtml("queue-center-filter", "data-cola-centrofiltro", "1",
      T("admin.queue.filters.filtrar_centro", null, "Filtrar por centro"), STATE.filtroCentro,
      [{ value: "", label: T("admin.queue.filters.todos_centros", null, "Todos los centros") }].concat(centrosFiltro().map(function (s) { return { value: s, label: s }; })), STATE.filtroCentro);
  }

  // La vista propia del médico busca SOLO paciente (nombre o DNI): el
  // buscador lo dice y scoreTurno lo cumple — la pantalla es su lista de
  // pacientes de hoy, no la cola de la mesa.
  function buscarInputHtml() {
    var propio = window.CURRENT_ROLE === "medico";
    return '<input type="search" class="adm-search queue-search" data-cola-buscar="1" autocomplete="off"'
      + ' placeholder="' + esc(T(propio ? "admin.queue.filters.buscar_placeholder_medico" : "admin.queue.filters.buscar_placeholder",
        null, propio ? "Paciente o DNI" : "Paciente o médico")) + '"'
      + ' aria-label="' + esc(T(propio ? "admin.queue.filters.buscar_aria_medico" : "admin.queue.filters.buscar_aria",
        null, propio ? "Buscar paciente de hoy por nombre o DNI" : "Buscar en la cola por paciente, médico, servicio o fecha")) + '"'
      + ' value="' + esc(STATE.q) + '">';
  }

  // Interruptor de los huecos de agenda. Es un checkbox y no un <select>
  // porque es binario (misma regla que el filtro de sexo en el sitio): un
  // desplegable de dos opciones para prender algo es un click de más.
  function libresChkHtml() {
    return '<label class="queue-free-chk"><input type="checkbox" data-cola-libres="1"'
      + (STATE.verLibres ? " checked" : "") + ">" + esc(T("admin.queue.filters.mostrar_libres", null, "Mostrar horarios libres")) + "</label>";
  }

  function columnGearHtml() {
    // El tilde sigue el estado EFECTIVO (COLS), no sólo VISIBLE_KEYS: si
    // Entrevista está auto-mostrada, el engranaje tiene que decir "sí" —
    // si dijera "no" con la columna a la vista, destildar cualquier OTRA
    // columna guardaría un array sin Entrevista por accidente.
    var auto = autoShowEntrevista();
    var opts = COLS_ALL.filter(function (c) { return !c.always; }).map(function (c) {
      var on = VISIBLE_KEYS.indexOf(c.key) !== -1 || (auto && c.key === "entrevista");
      return '<label class="cnt-gear-opt"><input type="checkbox" data-cola-col="' + esc(c.key) + '"'
        + (on ? " checked" : "") + '><span>' + esc(c.label) + "</span></label>";
    }).join("");
    var elegirCols = esc(T("admin.queue.filters.elegir_columnas", null, "Elegir columnas"));
    return '<div class="cnt-gear-wrap queue-columns">'
      + '<button type="button" class="cnt-gear-btn" data-cola-gear aria-label="' + elegirCols + '" title="' + elegirCols + '" aria-haspopup="true" aria-expanded="false">'
      + '<span class="adm-icon" aria-hidden="true">view_column</span></button>'
      + '<div class="cnt-gear-pop" data-cola-gear-pop>' + opts + "</div></div>";
  }

  // «Descargar» (the product owner, 2026-11-07): the shared button of the table engine,
  // next to the gear; it takes the patients of the doctor on screen, the day
  // and the filter as they are (admin-table.mjs, DESCARGAR).
  function exportButtonHtml() {
    return Table && Table.exportHtml ? Table.exportHtml({ scope: "#cola-results" }) : "";
  }

  // Relevancia de un turno contra las palabras buscadas. Cada campo pesa
  // distinto (paciente/servicio > médico/prácticas/diagnóstico > el resto) y
  // dentro del campo el match puntúa por tramo: exacto > empieza-con >
  // arranque-de-palabra > substring. AND entre palabras: si una palabra no
  // aparece en ningún campo, el turno no matchea (score 0). Sin stemming — es
  // una cola de admin, se busca por nombre/servicio, no por especialidad.
  // En la vista propia del médico sólo puntúan paciente y DNI: buscar por
  // médico no tiene sentido (es él) y servicio/prácticas/diagnóstico/edad/
  // hora son campos de la mesa, no los suyos — lo que promete el placeholder.
  function scoreTurno(t, medico, palabras) {
    var campos = [
      [norm(t.paciente), 3],
      // Con o sin puntos: la mesa lo tipea como lo lee en el documento.
      [Format.digits(t.dni), 3]
    ];
    if (window.CURRENT_ROLE !== "medico") campos = campos.concat([
      [norm(t.servicio), 3],
      [norm(medico), 2],
      [norm(campoTexto(t.ai_campos, "practicas")), 2],
      [norm(campoTexto(t.ai_campos, "diagnostico")), 2],
      [String(edadDe(t.nacimiento) == null ? "" : edadDe(t.nacimiento)), 1],
      [norm(t.hora), 1],
      [norm(t.tiene_orden ? "con orden" : "sin orden"), 1],
      [t.sobreturno ? "sobreturno" : "", 1]
    ]);
    var total = 0;
    for (var w = 0; w < palabras.length; w++) {
      var p = palabras[w], best = 0;
      // «30.009.042» tal como la cola lo muestra: la palabra son sus dígitos.
      if (/^[\d.\-]*\d[\d.\-]*$/.test(p)) p = Format.digits(p);
      for (var c = 0; c < campos.length; c++) {
        var texto = campos[c][0], peso = campos[c][1];
        if (!texto) continue;
        var idx = texto.indexOf(p);
        if (idx < 0) continue;
        var tier = texto === p ? 4 : idx === 0 ? 3 : (texto.charAt(idx - 1) === " " ? 2 : 1);
        var s = peso * tier;
        if (s > best) best = s;
      }
      if (best === 0) return 0; // AND: esta palabra no matcheó nada
      total += best;
    }
    return total;
  }

  // Aplica los dos <select> (médico + servicio). La búsqueda de texto NO se
  // aplica acá (se hace en renderResultados con puntaje/orden).
  function gruposFiltrados() {
    var gs = STATE.grupos;
    if (STATE.filtro) gs = gs.filter(function (g) { return grupoKey(g) === STATE.filtro; });
    if (STATE.filtroEspecialidad) gs = gs.filter(function (g) { return g.especialidad === STATE.filtroEspecialidad; });
    if (STATE.filtroCentro) {
      gs = gs.filter(function (g) { return grupoEnCentro(g, STATE.filtroCentro); }).map(function (g) {
        // Con turnos de dos centros se muestran sólo los del elegido.
        var propios = g.turnos.filter(function (t) { return !t.centro || t.centro === STATE.filtroCentro; });
        return propios.length === g.turnos.length ? g : Object.assign({}, g, { turnos: propios });
      });
    }
    if (STATE.filtroServicio) {
      gs = gs.map(function (g) {
        return {
          medico_id: g.medico_id,
          medico: g.medico,
          especialidad: g.especialidad,
          centro: g.centro,
          turnos: g.turnos.filter(function (t) { return (t.servicio || "") === STATE.filtroServicio; })
        };
      }).filter(function (g) { return g.turnos.length; });
    }
    return gs;
  }

  // Comparación secundaria por hora (dentro del mismo puntaje de relevancia).
  function cmpHora(a, b) { return String(a.hora || "").localeCompare(String(b.hora || "")); }

  // Valor de orden de un turno para una columna. "" / null = vacío, y los
  // vacíos van SIEMPRE al final (mismo criterio que el listado de #medicos),
  // sin importar el sentido.
  function sortVal(t, key) {
    switch (key) {
      case "hora": return String(t.hora || "");
      case "paciente": return norm(t.paciente);
      // «Sin médico asignado» is an empty value: last, whatever the direction.
      case "medico": return GRUPO_DE[t.id] && nombrePropio(GRUPO_DE[t.id].medico) ? norm(nombrePropio(GRUPO_DE[t.id].medico)) : "";
      case "confirmo": return t.confirmacion === "si" ? 0 : t.confirmacion === "no" ? 1 : 2;
      case "solicitado": return String(t.solicitado_en || "");
      case "cobertura": return norm(t.cobertura);
    }
    return "";
  }

  function cmpCol(a, b) {
    var key = STATE.sort.key, dir = STATE.sort.dir;
    var va = sortVal(a, key), vb = sortVal(b, key);
    var ea = va === null || va === "", eb = vb === null || vb === "";
    if (ea && eb) return cmpHora(a, b);
    if (ea) return 1;
    if (eb) return -1;
    var r = typeof va === "number"
      ? va - vb
      : String(va).localeCompare(String(vb), "es");
    return (r * dir) || cmpHora(a, b);
  }

  // Una entrevista por QR sin turno matcheado (fase 4): lista real (un <li>
  // por entrevista), no tarjetas sueltas — nombre tal como lo escribió,
  // hora, triage y el mismo botón "Iniciar consulta" que la columna de la
  // tabla.
  function entrevistaSinTurnoItemHtml(iv) {
    var nombre = iv.nombre || T("admin.queue.overlay.sin_nombre", null, "(sin nombre)");
    var chip = iv.estado === "active"
      ? '<span class="queue-triage-curso">' + esc(T("admin.queue.triage.en_curso", null, "En curso")) + "</span>"
      : triageChipHtml(iv.triage);
    var resumen = String(iv.resumen || "").trim();
    var accion = consultaAccionHtml(iv);
    return '<li class="queue-unmatched-item">'
      + '<span class="queue-unmatched-head">'
      + '<span class="queue-unmatched-name">' + esc(nombre) + "</span>"
      + (iv.hora ? '<span class="queue-unmatched-hour">' + esc(iv.hora) + "</span>" : "")
      + (chip || "") + "</span>"
      + (resumen ? '<span class="queue-unmatched-resumen">' + esc(resumen) + "</span>" : "")
      + (accion ? '<span class="queue-unmatched-accion">' + accion + "</span>" : "")
      + "</li>";
  }

  // La captura de síntomas del PROPIO médico (fase 7) es el interruptor del
  // panel — el mismo .hor-switch de «Límites» — con su nombre a tamaño de
  // texto, nunca una línea chica con un botón del navegador (responsable del producto,
  // 2026-10-26). Pisa SU override (dos estados, nunca "heredar": eso es un
  // concepto de admin, ver #entrevistas). Un solo nombre, el de Sistema
  // («Captura de síntomas»); «¿Qué es?» es un ícono en la misma línea y el
  // cartel con el QR sólo aparece con la captura encendida — apagada no hay
  // QR que pegar (responsable del producto, 2026-11-08).
  function entrevistaHeaderHtml() {
    var cfg = STATE.entrevistaConfig;
    if (window.CURRENT_ROLE !== "medico" || !cfg) return "";
    var nombre = T("admin.queue.intake.switch", null, "Captura de síntomas");
    var queEs = Explainer ? Explainer.summary(T) : "";
    return '<div class="queue-entrevista-cfg">'
      + '<label class="queue-entrevista-switch"><span class="hor-switch"><input type="checkbox" role="switch" data-entrevista-toggle="' + (cfg.efectivo ? "0" : "1") + '"'
      + (cfg.efectivo ? " checked" : "") + '><span></span></span>' + esc(nombre) + "</label>"
      + (Explainer ? '<button type="button" class="queue-icon-btn" data-cola-explainer="1" aria-expanded="' + EXPLAINER_OPEN + '" aria-label="' + esc(queEs) + '" title="' + esc(queEs) + '">'
        + '<span class="queue-icon queue-icon-info" aria-hidden="true"></span></button>' : "")
      + (cfg.efectivo ? '<a href="' + esc(cfg.cartel_url) + '" target="_blank" rel="noopener">' + esc(T("admin.queue.intake.cartel", null, "Cartel para la puerta")) + "</a>" : "")
      + "</div>";
  }

  // El aviso de IA (Decreto 742/2026, core/ai_register.py) acompaña al
  // contenido que la IA elaboró — la entrevista previa (triage y resumen) y lo
  // leído de la orden (prácticas, diagnóstico) — y va justo antes de él. Un
  // día sin ese contenido no lleva aviso: no es una plaqueta sobre la pantalla
  // (responsable del producto, 2026-10-26, sobre un día vacío con el aviso arriba de todo).
  // Over the whole screen it only fronts the interviews without an
  // appointment; an appointment's AI content carries its mark inside the
  // card, right above that text (marcaIAHtml; product owner, 2026-11-09).
  function hayContenidoIA() {
    return !!(STATE.entrevistasSinTurno && STATE.entrevistasSinTurno.length);
  }

  function avisoIAHtml() {
    if (!hayContenidoIA()) return "";
    return avisoIATexto();
  }

  function avisoIATexto() {
    return '<p class="queue-ai-notice" data-ai-notice="clinical">' + esc(T("ai.notice.clinical", null,
      "Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención.")) + "</p>";
  }

  // «¿Qué es?» abre, debajo del interruptor, el mismo bloque que la pestaña
  // Síntomas de su ficha (platform/intake-explainer.mjs); cerrado por
  // defecto porque esta es la pantalla de todos los días (responsable del producto, 2026-10-27).
  function entrevistaExplainerHtml() {
    var cfg = STATE.entrevistaConfig;
    if (window.CURRENT_ROLE !== "medico" || !cfg || !EXPLAINER_OPEN || !Explainer) return "";
    return '<div class="queue-intake-explainer">' + Explainer.html(T) + "</div>";
  }

  // «Cargar en HSI» on each row is the desk's extension's; while this computer
  // lacks it, one line says where to install it (the reference-deployment counterpart, 2026-10-27: «nunca
  // entendí cómo… llegábamos a esa pantalla»). The server sends it only
  // without an HSI push and never for a doctor's own day; the CSS hides it
  // once the extension marks the page.
  function hsiInstalarHtml() {
    if (!HsiSync || !HsiSync.instalarHtml || !STATE.hsiInstalar) return "";
    return HsiSync.instalarHtml(STATE.hsiInstalar, { attrs: ' id="queue-hsi-instalar"' });
  }

  function entrevistasSinTurnoHtml() {
    var lista = STATE.entrevistasSinTurno || [];
    if (window.CURRENT_ROLE !== "medico" || !lista.length) return "";
    return '<section class="queue-unmatched" aria-label="' + esc(T("admin.queue.intake.sin_turno_aria", null, "Capturas de síntomas sin turno")) + '">'
      + '<h3 class="queue-unmatched-h">' + esc(T("admin.queue.intake.sin_turno_h", null, "Capturas de síntomas sin turno")) + "</h3>"
      + '<ul class="queue-unmatched-list">' + lista.map(entrevistaSinTurnoItemHtml).join("") + "</ul>"
      + "</section>";
  }

  // Estructura fija: controles (una vez) + contenedor #cola-results que se
  // repinta con renderResultados en cada búsqueda/filtro, sin rearmar los
  // controles — así el input de búsqueda NO pierde foco al tipear.
  function render() {
    hydratePrefs();
    // El auto-show de Entrevista depende de los DATOS del día (¿trajo alguna
    // entrevista?), no sólo de la preferencia guardada — así que se
    // recalcula en cada carga, no sólo la primera vez que hydratePrefs corre.
    COLS = activeCols();
    var sec = document.getElementById("sec-cola");
    if (!sec) return;
    TURNOS_BY_ID = {};
    EDITING = {};
    var totalTodos = STATE.grupos.reduce(function (n, g) { return n + g.turnos.length; }, 0);
    // Un día sin ningún turno dado PERO con agenda abierta sigue teniendo algo
    // que mostrar: los huecos libres son justamente lo que la mesa reparte.
    var totalLibres = STATE.grupos.reduce(function (n, g) { return n + ((g.libres || []).length); }, 0);
    // Región viva primero en el DOM: los anuncios de asistencia y de carga del
    // día entran acá (ver anunciar()). Vive fuera de lo que se repinta.
    // La BÚSQUEDA va primera (responsable del producto, handoff 2026-07-27: "search comes first"):
    // es la herramienta que reemplaza a casi todos los filtros — incluido el
    // día, que se puede escribir ahí adentro (ver fechaDeTexto). El buscador
    // se muestra siempre que haya algo cargado, aunque el día en pantalla esté
    // vacío: escribir otra fecha es justamente cómo se sale de un día vacío.
    // El día va al lado del buscador y las acciones (columnas, descargar,
    // imprimir) quedan como íconos al final de la MISMA fila (responsable del producto,
    // 2026-11-08: una sola fila de control). Los filtros esperan detrás del
    // embudo en todos los anchos y abren en su propia línea debajo.
    var html = '<p id="cola-live" class="sr-only" role="status" aria-live="polite"></p>'
      + '<div class="queue-controls">' + buscarInputHtml() + dateNavHtml()
      + '<div class="queue-actions">' + columnGearHtml() + exportButtonHtml()
      + '<span class="queue-print-slot" data-cola-print-slot></span></div>'
      + '<div class="queue-filters">'
      + (window.CURRENT_ROLE !== "medico" && (STATE.grupos.length > 1 || STATE.filtro) ? medicoFiltroHtml() : "")
      + (gruposCampo("especialidad").length > 1 || STATE.filtroEspecialidad ? especialidadFiltroHtml() : "")
      + (centrosFiltro().length > 1 || STATE.filtroCentro ? centroFiltroHtml() : "")
      + (serviciosDelDia().length > 1 || STATE.filtroServicio ? servicioFiltroHtml() : "")
      + (totalLibres ? libresChkHtml() : "")
      + "</div></div>"
      // Fase 7: el encabezado de activar/desactivar + cartel va PRIMERO
      // (es config de la práctica, no datos del día), después las
      // entrevistas por QR sin turno matcheado (fase 4): sólo rol médico,
      // arriba de la tabla del día — es otra cosa que el turno con
      // entrevista adjunta, así que no comparte fila con nada.
      + entrevistaHeaderHtml() + entrevistaExplainerHtml() + avisoIAHtml() + entrevistasSinTurnoHtml();
    // Dónde se instala la extensión de HSI (contraparte técnica, 2026-10-27), arriba del día.
    html += hsiInstalarHtml();
    if (!totalTodos && !totalLibres && !STATE.entrevistasSinTurno.length) {
      html += '<p class="adm-empty">' + esc(applyVars(T("admin.queue.empty.sin_turnos", { fecha: fechaParaTexto(STATE.dia) }, "No hay turnos aprobados para {fecha}."), { fecha: fechaParaTexto(STATE.dia) })) + "</p>";
      sec.innerHTML = html;
      restaurarFocoBusqueda();
      return;
    }
    html += '<div id="cola-results"></div>';
    sec.innerHTML = html;
    renderResultados();
    restaurarFocoBusqueda();
  }

  // Escribir una fecha en el buscador recarga el día, y recargar el día
  // re-arma los controles: sin esto, quien está tipeando "24 de jul" pierde el
  // foco a mitad de palabra y sigue escribiendo en la nada.
  var FOCO_BUSQUEDA = false;
  function restaurarFocoBusqueda() {
    if (!FOCO_BUSQUEDA) return;
    FOCO_BUSQUEDA = false;
    var el = document.querySelector("[data-cola-buscar]");
    if (!el) return;
    el.focus();
    try { el.setSelectionRange(el.value.length, el.value.length); } catch (e) { /* type=search */ }
  }

  function renderResultados() {
    var host = document.getElementById("cola-results");
    if (!host) return;
    TURNOS_BY_ID = {};
    GRUPO_DE = {};
    SCORE_DE = {};
    EDITING = {};
    var palabras = norm(STATE.qBusca).split(/\s+/).filter(Boolean);
    var salida = [];
    gruposFiltrados().forEach(function (g) {
      var filas = g.turnos.map(function (t) {
        return { t: t, score: palabras.length ? scoreTurno(t, g.medico, palabras) : 0 };
      });
      if (palabras.length) filas = filas.filter(function (r) { return r.score > 0; });
      // Sin turnos pero con huecos el grupo se muestra igual — es una agenda
      // entera libre. Con búsqueda activa no: el hueco no matchea nada. Y con
      // los libres apagados tampoco: no quedaría nada que mostrar de ese médico.
      if (!filas.length && (palabras.length || !STATE.verLibres || !(g.libres || []).length)) return;
      filas.forEach(function (r) { SCORE_DE[r.t.id] = r.score; });
      salida.push({ medico_id: g.medico_id, medico: g.medico,
                    especialidad: g.especialidad, centro: g.centro, libres: g.libres || [],
                    turnos: filas.map(function (r) { return r.t; }) });
    });
    pintarImprimir(salida.length === 1 ? salida : null);
    if (!salida.length) {
      // Un día con agendas abiertas pero sin nadie anotado no es un día vacío:
      // hay lugares para dar, y están a un click del checkbox de arriba.
      var hayLibres = !STATE.q && !STATE.verLibres
        && STATE.grupos.some(function (g) { return (g.libres || []).length; });
      // A day of open agendas only, free hours hidden: the same sentence as an
      // empty day (and as the server's first paint), then where they are.
      var sinResultados = STATE.q
        ? applyVars(T("admin.queue.empty.sin_resultados_query", { q: STATE.q }, "No se encontraron turnos para «{q}»."), { q: STATE.q })
        : hayLibres
          ? applyVars(T("admin.queue.empty.sin_turnos", { fecha: fechaParaTexto(STATE.dia) }, "No hay turnos aprobados para {fecha}."), { fecha: fechaParaTexto(STATE.dia) })
          : T("admin.queue.empty.sin_resultados", null, "No se encontraron turnos.");
      host.innerHTML = '<p class="adm-empty">' + esc(sinResultados)
        + (hayLibres ? " " + esc(T("admin.queue.empty.hay_libres_hint", null, "Hay agendas abiertas: marcá «Mostrar horarios libres» para verlas.")) : "")
        + "</p>"
        // Narrowed to a doctor with nothing that day: the way back is right
        // under the sentence, not inside the folded filters.
        + (STATE.filtro && window.CURRENT_ROLE !== "medico"
          ? '<button type="button" class="queue-doctor-all" data-cola-doctor="">'
            + esc(T("admin.queue.table.ver_todos", null, "Ver todos los médicos")) + "</button>"
          : "");
      return;
    }
    // Una agenda ENTERA sin turnos dados no puede empujar hacia abajo la
    // jornada real: en un día cualquiera hay 30 profesionales con agenda y 3
    // con pacientes. Van detrás de un botón, que es lo que la mesa abre
    // cuando necesita ubicar a alguien que vino sin turno.
    var conTurnos = salida.filter(function (g) { return g.turnos.length; });
    var vacias = salida.filter(function (g) { return !g.turnos.length; });
    var html = "";
    if (!STATE.verVacias) salida = conTurnos;
    salida.forEach(function (g) {
      g.turnos.forEach(function (t) { TURNOS_BY_ID[t.id] = t; GRUPO_DE[t.id] = g; });
    });
    if (salida.length) html += listaDelDiaHtml(salida, palabras.length > 0);
    // No count on the button (the product owner, 2026-10-26: «no need for counters»).
    if (vacias.length && !STATE.verVacias) {
      html += '<button type="button" class="queue-show-empty" data-cola-vacias="1">'
        + esc(T("admin.queue.empty.ver_agendas", null, "Ver agendas sin turnos")) + "</button>";
    } else if (vacias.length) {
      html += '<button type="button" class="queue-show-empty" data-cola-vacias="1">'
        + esc(T("admin.queue.empty.ocultar_agendas", null, "Ocultar las agendas sin turnos")) + "</button>";
    }
    host.innerHTML = html;
    // The fit runs in the SAME frame as the repaint (2026-11-10). Left to the
    // next frame, a repaint that lands late in a frame is painted once at the
    // table's natural width and jumps when the fit arrives: at 1280 the doctor
    // column moved 296 px, once for the first paint and once for the day's
    // reconciliation (CLS 0.053 each). A section still hidden has no width to
    // fit; the engine watches its medium and fits when it gets one.
    autoFit();
  }

  // ---- fetch ----------------------------------------------------------------

  // diaIso === undefined -> mantiene el día actual en pantalla (o "hoy" si
  // todavía no hay ninguno, primera carga); null/"" fuerza "hoy" del server
  // (no calculamos "hoy" del lado del cliente para no pisar el huso ART con
  // el reloj/zona del navegador — ver gotcha del encabezado).
  function aplicarDatos(data) {
    if (data.dia !== STATE.dia) { invalidateDayMutations(); calendarData = null; }
    STATE.dia = data.dia;
    STATE.hoy = !!data.hoy;
    STATE.grupos = data.grupos || [];
    STATE.centros = data.centros || null;
    // El valor nuevo siempre es una identidad. Preferencias viejas guardaban
    // un índice sin identidad: no se lo mapea al grupo de otro día porque eso
    // puede terminar filtrando a otra persona. Se conserva como selección
    // visible y sin resultados para que la mesa decida quitarla.
    var actual = STATE.grupos.filter(function (g) { return grupoKey(g) === STATE.filtro; })[0];
    if (actual) STATE.filtroMedicoEtiqueta = etiquetaMedicoGrupo(actual);
    STATE.seleccionado = actual ? grupoKey(actual) : null;
    STATE.entrevistasSinTurno = data.entrevistas_sin_turno || [];
    STATE.entrevistaConfig = data.entrevista_config || null;
    STATE.hsiInstalar = data.hsi_instalar || null;
  }

  var dayLoader = createDayQueueLoader({
    request: function (day, signal) {
      var url = "/api/admin/day-queue" + (day ? "?dia=" + encodeURIComponent(day) : "");
      return fetch(url, { credentials: "same-origin", signal: signal });
    },
    confirm: function (response, current) {
      return sessionReady.then(function (mod) {
        return current() ? mod.confirmar(response, undefined, current) : false;
      }).then(function (transitioned) {
        if (transitioned && current()) finishInitialReadiness();
        return transitioned;
      });
    },
    apply: function (data) {
      requestedDay = data.dia;
      aplicarDatos(data);
      hydratePrefs();
      render();
      refreshCalendar();
      // Cambiar de día no mueve el foco ni recarga la página: sin este aviso,
      // para un lector de pantalla la pantalla simplemente "no hizo nada".
      var n = STATE.grupos.reduce(function (a, g) { return a + g.turnos.length; }, 0);
      var libres = STATE.grupos.reduce(function (a, g) { return a + ((g.libres || []).length); }, 0);
      var turnoTxt = Tn("admin.queue.announce.turnos", n, null, "{n} turno", "{n} turnos");
      var fechaCap = fechaParaTexto(STATE.dia).replace(/^./, function (c) { return c.toUpperCase(); });
      var resumen;
      if (libres && STATE.verLibres) {
        var libreTxt = Tn("admin.queue.announce.lugares", libres, null, "{n} lugar libre", "{n} lugares libres");
        resumen = applyVars(T("admin.queue.announce.resumen_con_libres", { fecha: fechaCap, turnos: turnoTxt, libres: libreTxt }, "{fecha}: {turnos}, {libres}."), { fecha: fechaCap, turnos: turnoTxt, libres: libreTxt });
      } else {
        resumen = applyVars(T("admin.queue.announce.resumen", { fecha: fechaCap, turnos: turnoTxt }, "{fecha}: {turnos}."), { fecha: fechaCap, turnos: turnoTxt });
      }
      anunciar(resumen);
      finishInitialReadiness();
    },
    fail: function (e) {
      finishInitialReadiness(e);
      // El loader sólo llama a fail con un 401 que whoami no confirmó (la
      // contraseña del sitio, un proxy): una confirmada ya se llevó la página.
      var sec = document.getElementById("sec-cola");
      if (!sec) return;
      // A transient refresh failure must not erase the readable SSR/current
      // queue. Permission refusal is different: its old data must be hidden.
      if (String(e.message) !== "403" && sec.innerHTML) {
        anunciar(T("admin.queue.common.load_error", null, "No se pudo cargar. Reintentá."));
        return;
      }
      sec.innerHTML = String(e.message) === "403"
        ? '<p class="adm-empty">' + esc(T("admin.queue.common.no_permission", null, "No tenés permiso para ver esta sección.")) + "</p>"
        : '<p class="adm-empty">' + esc(T("admin.queue.common.load_error", null, "No se pudo cargar. Reintentá.")) + "</p>";
    }
  });

  var requestedDay;
  var loadSerial = 0;
  var loading = false;
  var refreshPending = false;
  function cargar(diaIso) {
    if (disposed) return;
    var day = diaIso !== undefined ? diaIso : (requestedDay !== undefined ? requestedDay : STATE.dia);
    if (day !== requestedDay) {
      calendarSerial++;
      calendarData = null;
      if (calendarController) calendarController.abort();
      createAppointmentSeq++;
      invalidateDayMutations();
    }
    requestedDay = day;
    cancelTimer(TIMER_FECHA);
    TIMER_FECHA = null;
    var serial = ++loadSerial;
    loading = true;
    refreshPending = false;
    return dayLoader.load(day).finally(function () {
      if (disposed || serial !== loadSerial) return;
      loading = false;
      if (refreshPending) {
        refreshPending = false;
        cargar();
      }
    });
  }
  function refreshQueue(eventDay) {
    if (disposed || TIMER_FECHA !== null) return;
    var day = requestedDay || STATE.dia;
    if (eventDay && eventDay !== day) return;
    if (loading) { refreshPending = true; return; }
    cargar();
  }

  // ---- delegación de clicks/cambios -----------------------------------------

  // Enter/Espacio sobre un encabezado ordena, igual que el click. Los <th>
  // llevan tabindex (ver theadHtml): sin esto la tabla sólo se podía ordenar
  // con el mouse, que para la administrativa no vidente es lo mismo que no
  // poder ordenarla.
  listen(document, "keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    if (e.target.closest && e.target.closest("[data-forecast-info]")) return;
    var th = e.target.closest && e.target.closest("[data-cola-sort]");
    if (th) { e.preventDefault(); th.click(); return; }
    var doctor = e.target.closest && e.target.closest("[data-cola-doctor]");
    if (doctor && e.target === doctor) { e.preventDefault(); doctor.click(); }
  });

  listen(document, "click", function (e) {
    // A doctor's name in the list narrows the day to that doctor; «Ver todos»
    // (an empty key) brings everyone back.
    var doctorSelect = e.target.closest("[data-cola-doctor]");
    if (doctorSelect) {
      e.preventDefault();
      elegirMedico(doctorSelect.getAttribute("data-cola-doctor"));
      return;
    }
    var gear = e.target.closest("[data-cola-gear]");
    if (gear) {
      e.preventDefault();
      var pop = document.querySelector("[data-cola-gear-pop]");
      if (pop) {
        var open = pop.style.display === "flex";
        pop.style.display = open ? "none" : "flex";
        gear.setAttribute("aria-expanded", open ? "false" : "true");
      }
      return;
    }
    if (!e.target.closest("[data-cola-gear-pop]")) {
      var popAbierto = document.querySelector("[data-cola-gear-pop]");
      var gearAbierto = document.querySelector("[data-cola-gear]");
      if (popAbierto) popAbierto.style.display = "none";
      if (gearAbierto) gearAbierto.setAttribute("aria-expanded", "false");
    }
    // Ordenar por columna. Se ignora si el click viene de la manija de ancho
    // o si acabamos de arrastrar (el pointerup dispara un click igual).
    var th = e.target.closest("[data-cola-sort]");
    if (th && !e.target.closest("[data-tbl-res]") && !e.target.closest("[data-forecast-info]")) {
      var k = th.getAttribute("data-cola-sort");
      if (STATE.sort.key === k) STATE.sort.dir = -STATE.sort.dir;
      else STATE.sort = { key: k, dir: 1 };
      STATE.sortTouched = true;
      savePrefs();
      renderResultados();
      return;
    }
    // "Dar este turno" en un hueco libre: abre el mismo modal de Crear turno
    // con el médico, el día y la HORA ya puestos — a la mesa sólo le queda
    // decir quién es el paciente.
    var retry = e.target.closest("[data-crear-turno-retry]");
    if (retry) {
      e.preventDefault();
      openCreateAppointment(createAppointmentIntent, createAppointmentHost);
      return;
    }
    var titleCreate = e.target.closest("#adm-crear-turno");
    if (titleCreate) {
      e.preventDefault();
      // With the list narrowed to one doctor, the new appointment starts
      // with that doctor and the day on screen.
      var actual = STATE.seleccionado && STATE.grupos.filter(function (g) { return grupoKey(g) === STATE.seleccionado; })[0];
      openCreateAppointment(actual && actual.medico ? { medico: actual.medico, dia: STATE.dia } : {}, titleCreate);
      return;
    }
    var dar = e.target.closest("[data-cola-dar]");
    if (dar) {
      e.preventDefault();
      openCreateAppointment({
        medico: dar.getAttribute("data-cola-dar-medico") || "",
        dia: STATE.dia,
        hora: dar.getAttribute("data-cola-dar") || ""
      }, dar);
      return;
    }
    var abrir = e.target.closest("[data-cola-abrir]");
    if (abrir) { e.preventDefault(); abrirOverlay(abrir.getAttribute("data-cola-abrir")); return; }
    // "Iniciar consulta" (entrevista previa, fase 4): la pantalla de consulta
    // recién se construye en fase 5 — el enlace ya queda cableado, puede
    // morir en un hash sin pantalla todavía (aceptado, ver handoff).
    var consulta = e.target.closest("[data-iniciar-consulta]");
    if (consulta) {
      e.preventDefault();
      var interviewId = consulta.getAttribute("data-iniciar-consulta");
      location.assign("/admin/consult?interview=" + encodeURIComponent(interviewId));
      return;
    }
    // «¿Qué es?» abre y cierra la explicación en el lugar, sin repintar el día.
    var queEsBtn = e.target.closest("[data-cola-explainer]");
    if (queEsBtn) {
      EXPLAINER_OPEN = !EXPLAINER_OPEN;
      queEsBtn.setAttribute("aria-expanded", String(EXPLAINER_OPEN));
      var cfgRow = queEsBtn.closest(".queue-entrevista-cfg");
      var abierta = document.querySelector("#sec-cola .queue-intake-explainer");
      if (abierta) abierta.remove();
      if (EXPLAINER_OPEN && cfgRow) cfgRow.insertAdjacentHTML("afterend", entrevistaExplainerHtml());
      return;
    }
    // Activar/Desactivar la captura de síntomas del propio médico (fase 7):
    // dos estados nada más, nunca "heredar" (eso vive en #entrevistas,
    // solo_admin) — ver entrevistaHeaderHtml(). El interruptor cambia al
    // instante; si el guardado falla vuelve a donde estaba.
    var entToggle = e.target.closest("[data-entrevista-toggle]");
    if (entToggle) {
      var operation = beginMutation("intake-settings");
      var nuevoOn = entToggle.getAttribute("data-entrevista-toggle") === "1";
      var revertir = function () { if (entToggle.type === "checkbox") entToggle.checked = !nuevoOn; };
      fetch("/api/admin/intake/my-settings", {
        method: "POST", credentials: "same-origin", signal: operation.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ override: nuevoOn })
      }).then(function (r) {
        if (!operation.current()) return null;
        var boundary = (r.status === 401 || r.status === 403)
          ? sessionReady.then(function (mod) { return operation.current() ? mod.confirmar(r, undefined, operation.current) : false; })
          : Promise.resolve(false);
        return boundary.then(function (transitioned) {
          if (!operation.current() || transitioned) return null;
          return r.json().then(function (body) {
            if (!r.ok || body?.ok !== true) {
              if (body?.error) return { error: body.error };
              throw new Error('intake-settings-save-failed');
            }
            return body;
          });
        });
      }).then(function (j) {
        if (!j || !operation.current()) return;
        if (j.error) { revertir(); if (window.toast) window.toast(errText(j.error, T("admin.queue.common.save_error", null, "No se pudo guardar."))); return; }
        if (window.toast) window.toast(nuevoOn
          ? T("admin.queue.intake.activada", null, "Captura de síntomas activada.")
          : T("admin.queue.intake.desactivada", null, "Captura de síntomas desactivada."));
        cargar();
      }).catch(function () {
        if (!operation.current()) return;
        revertir();
        if (window.toast) window.toast(T("admin.queue.common.save_error", null, "No se pudo guardar."));
      }).finally(operation.finish);
      return;
    }
    var vac = e.target.closest("[data-cola-vacias]");
    if (vac) { STATE.verVacias = !STATE.verVacias; renderResultados(); return; }
    var nav = e.target.closest("[data-cola-nav]");
    if (nav) {
      var nextDay = calendarData && (Number(nav.getAttribute("data-cola-nav")) < 0 ? calendarData.anterior : calendarData.siguiente);
      if (nextDay) { calendarMonth = nextDay; calendarData = null; cargar(nextDay); }
      return;
    }
    var toggle = e.target.closest("[data-cola-calendar-toggle]");
    if (toggle) { calendarOpen = !calendarOpen; paintDateNav(); refreshCalendar(); return; }
    var month = e.target.closest("[data-cola-month]");
    if (month) { calendarMonth = monthRange(calendarMonth || STATE.dia, Number(month.getAttribute("data-cola-month"))).from; calendarData = null; refreshCalendar(); return; }
    var picked = e.target.closest("[data-cola-calendar-date]");
    if (picked) { var pickedDay = picked.getAttribute("data-cola-calendar-date"); calendarOpen = false; calendarMonth = pickedDay; calendarData = null; cargar(pickedDay); return; }
    var reloadCalendar = e.target.closest("[data-cola-calendar-retry]");
    if (reloadCalendar) { refreshCalendar(); return; }
    var hoy = e.target.closest("[data-cola-hoy]");
    if (hoy) { calendarMonth = null; calendarData = null; cargar(null); return; }
    var corregir = e.target.closest("[data-corregir]");
    if (corregir) { toggleCorregir(corregir.getAttribute("data-corregir")); return; }
    var asis = e.target.closest("[data-asis]");
    if (asis) { marcarAsistencia(asis.getAttribute("data-asis"), asis.getAttribute("data-valor"), asis); return; }
  });

  // Médico y servicio sólo repintan el listado (#cola-results), NO los
  // controles — así los <select>/el buscador conservan su valor y el input
  // su foco.
  listen(document, "change", function (e) {
    var col = e.target.closest("[data-cola-col]");
    if (col) {
      VISIBLE_KEYS = Array.prototype.slice.call(document.querySelectorAll("[data-cola-col]:checked"))
        .map(function (x) { return x.getAttribute("data-cola-col"); });
      if (Prefs) {
        Prefs.set("day_queue.columns", VISIBLE_KEYS);
        Prefs.set(CARD_MOVED, true);
      }
      // A partir de acá esta cuenta SÍ tiene un array guardado (aunque no
      // haya tocado el checkbox de Entrevista): el auto-show se apaga y gana
      // lo que haya quedado tildado, sea cual sea el motivo.
      HAS_STORED_COLS = true;
      COLS = activeCols();
      if (!COLS.some(function (c) { return c.key === STATE.sort.key; })) {
        STATE.sort = { key: "hora", dir: 1 };
        STATE.sortTouched = false;
      }
      savePrefs();
      renderResultados();
      return;
    }
    var libres = e.target.closest("[data-cola-libres]");
    if (libres) {
      STATE.verLibres = !!libres.checked;
      savePrefs();
      renderResultados();
      return;
    }
    var med = e.target.closest("[data-cola-medfiltro]");
    if (med) { elegirMedico(med.value); return; }
    var serv = e.target.closest("[data-cola-servfiltro]");
    if (serv) { STATE.filtroServicio = serv.value; savePrefs(); renderResultados(); return; }
    var especialidad = e.target.closest("[data-cola-especialidadfiltro]");
    if (especialidad) { STATE.filtroEspecialidad = especialidad.value; savePrefs(); renderResultados(); return; }
    var centro = e.target.closest("[data-cola-centrofiltro]");
    if (centro) { STATE.filtroCentro = centro.value; calendarData = null; paintDateNav(); savePrefs(); renderResultados(); refreshCalendar(); return; }
  });

  // Búsqueda en vivo: repinta sólo el listado, el input mantiene el foco. Si
  // adentro de lo escrito hay una FECHA (responsable del producto, handoff 2026-07-27), esa fecha
  // deja de ser texto a buscar y pasa a ser el día que se mira — el resto
  // sigue filtrando. Así "24/7 whelan" es "los turnos de Whelan el 24 de
  // julio" sin tocar ningún otro control.
  listen(document, "input", function (e) {
    var buscar = e.target.closest("[data-cola-buscar]");
    if (!buscar) return;
    STATE.q = buscar.value;
    // The doctor's field promises patient name/DNI, and a typed date still opens
    // that day, as at the desk (requirement H3, product owner 21.10 item 13).
    var fecha = fechaDeTexto(STATE.q);
    STATE.qBusca = fecha ? fecha.resto : STATE.q;
    savePrefs();
    if (fecha && fecha.iso !== STATE.dia) {
      // Con un respiro: "24 de julio" pasa por "24 de jul" y no tiene sentido
      // pedirle al server cada estado intermedio.
      pedirDia(fecha.iso, document.activeElement === buscar);
      return;
    }
    if (TIMER_FECHA !== null) {
      cancelTimer(TIMER_FECHA);
      TIMER_FECHA = null;
      // Clearing a typed date retracts navigation, not the visible queue.
      cargar(STATE.dia);
    }
    renderResultados();
  });

  var TIMER_FECHA = null;
  function pedirDia(iso, conFoco) {
    cancelTimer(TIMER_FECHA);
    createAppointmentSeq++;
    if (iso !== requestedDay) invalidateDayMutations();
    requestedDay = iso;
    calendarSerial++;
    calendarData = null;
    if (calendarController) calendarController.abort();
    loadSerial++;
    loading = false;
    refreshPending = false;
    dayLoader.invalidate();
    TIMER_FECHA = later(function () {
      TIMER_FECHA = null;
      if (iso === STATE.dia) { renderResultados(); return; }
      FOCO_BUSQUEDA = !!conFoco;
      cargar(iso);              // trae el día y repinta todo (controles incluidos)
    }, 260);
  }

  // ---- routing (mismo patrón que admin-system.js) --------------------------

  function datosIniciales() {
    if (options.initialData === false) return null;
    if (initialQueueConsumed) return null;
    initialQueueConsumed = true;
    var el = document.getElementById("queue-initial-data");
    if (!el) return null;
    try {
      var data = JSON.parse(el.textContent || "{}");
      return data.server_unavailable ? null : data;
    } catch (e) {
      return null;
    }
  }

  function iniciarEventos() {
    if (disposed || !window.EventSource || queueStream) return;
    queueStream = new window.EventSource("/api/admin/day-queue/events");
    listen(queueStream, "ready", function () {
      if (disposed) return;
      // The first document is SSR. Reconcile once after the stream has its
      // cursor so a mutation between render and EventSource connection cannot
      // leave this tab stale.
      refreshQueue();
    });
    listen(queueStream, "queue-update", function (event) {
      if (disposed) return;
      var data;
      try { data = JSON.parse(event.data || "{}"); } catch (e) { return; }
      // The event contains only the day. The scoped JSON reload remains the
      // sole source for patient data and preserves the current screen.
      if (data.day) refreshQueue(data.day);
    });
    // EventSource reconnects by itself. A blocked/proxied stream simply
    // leaves the existing queue fully usable without a visible error.
  }

  function route() {
    var el = document.getElementById("sec-cola");
    if (!el) return;
    el.style.display = "";
    var nav = document.querySelectorAll("#adm-nav a");
    nav.forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-nav") === "cola"); });
    var h1 = document.getElementById("adm-h1");
    if (h1) { h1.style.display = ""; h1.textContent = T("admin.queue.titulo", null, "Pacientes de hoy"); }
    syncCrearTurno();
    var sub = document.getElementById("adm-sub");
    if (sub) sub.style.display = "none";
    var initial = datosIniciales();
    if (initial) {
      aplicarDatos(initial);
      // The first SSE reconciliation refreshes this already-owned day; it
      // must not look like navigation and invalidate an early create intent.
      requestedDay = STATE.dia;
      hydratePrefs();
      refreshCalendar();
      render();
    } else {
      var ready = new Promise(function (resolve, reject) { initialReadiness = { resolve: resolve, reject: reject }; });
      iniciarEventos();
      cargar();
      return ready;
    }
    iniciarEventos();
  }

  function routeDependencies() { return Promise.all([i18nReady, datesReady, formatReady, filterControlsReady, forecastReady, prefsReady, tableReady, sessionReady, explainerReady]); }
  function readyRoute() { return routeDependencies().then(function () { if (disposed) return; localizeColumns(); return route(); }); }
  var dispose = function () {
    if (disposed) return;
    disposed = true;
    finishInitialReadiness();
    createAppointmentSeq++;
    if (releaseCreateAppointment) releaseCreateAppointment();
    releaseCreateAppointment = null;
    mutations.forEach(function (operation) { operation.finish(); });
    dayLoader.dispose();
    calendarSerial++;
    if (calendarController) calendarController.abort();
    timers.forEach(function (timer) { window.clearTimeout(timer); });
    timers.clear();
    Array.from(cleanups).forEach(function (release) { release(); });
    if (ANCHOS_TBL && ANCHOS_TBL.dispose) ANCHOS_TBL.dispose();
    if (queueStream) queueStream.close();
    if (queueOwners.get(root) === dispose) queueOwners.delete(root);
  };
  queueOwners.set(root, dispose);
  listen(document.defaultView || window, "pagehide", dispose);
  // The page's load deadline ends when the dependencies are in; the first
  // read after that is the screen's own waiting and failure state.
  dispose.loaded = routeDependencies().then(function () { return true; }, function () { return false; });
  dispose.ready = readyRoute().catch(function (error) { dispose(); throw error; });
  return dispose;
})();
}
