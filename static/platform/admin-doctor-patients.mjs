/* admin-doctor-patients.js — «Mis pacientes»: el roster LONGITUDINAL de un
   médico (Pinamar Turnos, 2026-08-01). It owns the direct
   /admin/my-patients query state.

   Contrato de API (doctor_patients.py):
     GET /api/admin/doctor-patients[?medico=<id>]
       -> {medico_id, medico, items:[{dni,nombre,contacto,
           ultima_entrevista:{id,triage,resumen,estado,fecha}|null,
           proximo_turno:{fecha}|null}], medicos?:[{id,nombre}]}
     rol medico: siempre sus propios pacientes, el picker no aparece.
     rol admin/operador: sin ?medico= trae `medicos` (picker) e items vacío.

   Orden por severidad de triage (ROJO > NARANJA > AMARILLO > VERDE > sin
   entrevista), ya resuelto por el server — acá sólo se pinta en ese orden.
*/
const MOUNTS = new WeakMap();

function plano(s) {
  return String(s == null ? "" : s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/* Rows that match what the doctor typed: a number matches the DNI by its
   digits (dots or not), words match the name in any order, accents ignored. */
export function filtrarPacientes(items, q) {
  var texto = plano(q).trim();
  if (!texto) return items || [];
  var digitos = texto.replace(/[^0-9]/g, "");
  var palabras = texto.replace(/[0-9.\s-]+/g, " ").trim().split(/\s+/).filter(Boolean);
  return (items || []).filter(function (p) {
    if (digitos && String(p.dni || "").replace(/[^0-9]/g, "").indexOf(digitos) === -1) return false;
    var nombre = plano(p.nombre);
    return palabras.every(function (w) { return nombre.indexOf(w) !== -1; });
  });
}

export function mountDoctorPatients(options) {
  "use strict";
  options = options || {};
  var root = options.root || document;
  var prior = MOUNTS.get(root);
  if (prior) prior();
  var disposed = false;
  var epoch = 0;
  function owns(token) { return !disposed && token === epoch; }
  var documentRef = root.ownerDocument || root;
  function host() { return root.id === "sec-mis-pacientes" ? root : root.querySelector("#sec-mis-pacientes"); }
  function importStatic(key) {
    var url = window.SANA_STATIC && window.SANA_STATIC[key];
    return window.sanaImportStatic ? window.sanaImportStatic(key) : (url ? import(url) : Promise.reject(new Error("Missing static module: " + key)));
  }

  var Dates = null;
  var datesUrl = window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"];
  var datesReady = datesUrl ? import(datesUrl).then(function (m) { Dates = m; })
    .catch(function () { return null; }) : Promise.resolve(null);
  var Format = null;
  var formatReady = importStatic("platform/format.mjs").then(function (m) { Format = m; });
  var prefsReady = importStatic("platform/admin-preferences.mjs").then(function (m) {
    return m.load();
  });
  var sessionReady = importStatic("platform/admin-session.mjs");
  var Table = null;
  var tableReady = importStatic("platform/admin-table.mjs").then(function (m) { Table = m; return m; });
  // The shared progressive-disclosure rule (empty / no-match / data).
  var Filters = null;
  var filtersReady = importStatic("platform/admin-filter-controls.mjs").then(function (m) { Filters = m; return m; });

  var $ = function (s, r) { return (r || root).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || root).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  var I18n = null;
    var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  // Ídem admin-patients.js: la única implementación del DNI con puntos es
  // format.mjs (Format.dni) — cargada antes que este módulo en todas las
  // páginas que lo pintan.
  var fmtDni = function (dni) { return Format.dni(dni); };

  function fmt() {
    return Format;
  }

  function nombre(v) {
    var raw = String(v == null ? "" : v).trim();
    return Format.nombre(raw);
  }

  // Mismo chip que Pacientes de hoy (admin-queue.js:TRIAGE_LABEL /
  // .queue-triage--<color>, clases globales de admin.html) — se repite acá
  // en vez de importarse porque admin-queue.js lo mantiene en un closure
  // propio; lo que se reusa de verdad es la CSS, no la función.
  function triageLabels() {
    return {
      ROJO: T("admin.queue.triage.rojo", null, "Rojo"),
      NARANJA: T("admin.queue.triage.naranja", null, "Naranja"),
      AMARILLO: T("admin.queue.triage.amarillo", null, "Amarillo"),
      VERDE: T("admin.queue.triage.verde", null, "Verde")
    };
  }

  function triageChipHtml(iv) {
    if (iv && iv.estado === "active") return '<span class="queue-triage-curso">' + esc(T("admin.queue.triage.en_curso", null, "En curso")) + "</span>";
    var key = String((iv || {}).triage || "").toUpperCase();
    var label = triageLabels()[key];
    if (!label) return "";
    return '<span class="queue-triage queue-triage--' + key.toLowerCase() + '">' + esc(label) + "</span>";
  }

  // Fecha amigable: relativo() para la última entrevista (ya pasó, "hace 2
  // horas" / la fecha absoluta si es vieja) y amigable(dia,hora) para el
  // próximo turno (es futuro — relativo() lo leería como "recién", mismo
  // clamp que usa para cualquier diferencia negativa).
  //
  // El server manda las dos fechas YA en el huso de la instalación
  // (doctor_patients._build: astimezone, "2026-10-25T09:00:00-03:00"): el día
  // y la hora se leen de ese texto (Dates.delServidor), nunca se re-convierten
  // al huso del navegador (una compu en UTC leía 12:00 para un turno de las 09:00).
  function amigableDe(iso) {
    return (Dates && Dates.delServidor(iso)) || String(iso || "").slice(0, 16).replace("T", " ");
  }

  // relativo() dice "recién" / "hace N minutos" / "hace N horas" (eso no
  // depende del huso); pasadas sus 6 horas escribe la fecha con el reloj del
  // navegador, así que ahí se pasa al reloj de la instalación.
  var RELATIVO_HASTA_MS = 6 * 3600 * 1000;
  function fechaEntrevista(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (!isNaN(d) && Date.now() - d.getTime() >= RELATIVO_HASTA_MS) return amigableDe(iso);
    return (Dates && Dates.relativo(iso)) || "";
  }

  function fechaTurno(iso) {
    return iso ? amigableDe(iso) : "";
  }

  var STATE = { medicoId: null, medico: null, entrevistaActiva: false, items: [], medicos: null, rol: null, q: "" };

  function api(path, token) {
    return fetch(path, { credentials: "same-origin" }).then(function (res) {
      if (!owns(token)) throw new Error("stale");
      var boundary = (res.status === 401 || res.status === 403)
        ? sessionReady.then(function (mod) { return owns(token) ? mod.confirmar(res, undefined, function () { return owns(token); }) : false; })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        // Sólo un 401 que whoami confirma (o el 403 de 2FA) se lleva la
        // pantalla a otra página; uno sin confirmar (Basic Auth del sitio, un
        // proxy) termina en «No se pudo cargar. Reintentar», no en «Cargando…».
        if (transitioned) throw new Error("redirect");
        if (res.status === 401) throw new Error("401");
        return res.json().catch(function () { return {}; }).then(function (j) {
          if (!owns(token)) throw new Error("stale");
          // Un 200 con cuerpo que no es JSON, o sin la lista `items` que el
          // contrato de build_payload SIEMPRE manda (vacía incluida), no es
          // "todavía no hay pacientes": es una respuesta rota. Sin este corte
          // se pintaba como roster vacío exitoso — el catch de arriba volvía
          // un JSON roto en {} y caía justo en ese estado. Sólo se exige en
          // res.ok: los cuerpos de error nunca prometieron esta forma.
          if (res.ok && (!j || typeof j !== "object" || Array.isArray(j) || !Array.isArray(j.items))) throw new Error("malformed");
          return { ok: res.ok, status: res.status, j: j };
        });
      });
    });
  }

  function fetchRoster(medicoId, token) {
    var qs = medicoId ? "?medico=" + encodeURIComponent(medicoId) : "";
    return api("/api/admin/doctor-patients" + qs, token);
  }

  function selectedDoctorFromUrl() {
    if (STATE.rol === "medico") return null;
    return new URLSearchParams(window.location.search).get("doctor") || null;
  }

  function setDirectDoctorUrl(id) {
    var url = new URL(window.location.href);
    if (id) url.searchParams.set("doctor", id);
    else url.searchParams.delete("doctor");
    window.history.pushState({}, "", url.pathname + url.search);
  }

  function stripDoctorUrlForMedico() {
    if (STATE.rol !== "medico" || !window.location.search) return;
    window.history.replaceState({}, "", window.location.pathname);
  }

  // ---- tabla (SanaTable — mismo motor de anchos que las demás) -----------

  // La fecha de la entrevista y lo que dijo son DOS datos, así que son dos
  // columnas (responsable del producto, 2026-08-05): apiladas en una sola celda, el resumen entraba
  // recortado abajo de un link y la columna no se podía ni ensanchar ni apagar.
  // El resumen ya no se corta a mano en 60 caracteres: se recorta por CSS y el
  // texto entero lo muestra el globo de admin-table.js al pasar por encima o
  // tocarlo.
  // Triage, Última entrevista y Resumen sólo existen cuando el médico de la
  // lista tiene la entrevista previa encendida (responsable del producto, 2026-10-27: «triage, en
  // curso» donde no la tiene): apagada no son columnas ni entran al engranaje.
  function cols(entrevistaActiva) {
    var lista = [
      { key: "paciente", label: T("admin.queue.doctor_patients.col_paciente", null, "Paciente"), min: 160, max: 320, flex: 2, always: true },
      { key: "dni", label: "DNI", min: 96, max: 132, "default": true },
      { key: "contacto", label: T("admin.queue.doctor_patients.col_contacto", null, "Contacto"), min: 130, "default": true }
    ];
    if (entrevistaActiva) {
      lista.unshift({ key: "triage", label: T("admin.queue.doctor_patients.col_triage", null, "Triage"), min: 96, "default": true });
      lista.push({ key: "entrevista", label: T("admin.queue.doctor_patients.col_entrevista", null, "Última entrevista"), min: 130, max: 200, "default": true });
      lista.push({ key: "resumen", label: T("admin.queue.doctor_patients.col_resumen", null, "Resumen"), min: 160, max: 420, flex: 2, clip: true, "default": true });
    }
    lista.push({ key: "turno", label: T("admin.queue.doctor_patients.col_turno", null, "Próximo turno"), min: 150, max: 260, flex: 1, "default": true });
    return lista;
  }

  // El elegidor del admin cambia de médico sin recargar la pantalla: el
  // engranaje se arma de nuevo cuando cambia si el médico tiene entrevista.
  var COLPICK = null;
  var COLPICK_ENTREVISTA = null;
  function colPick() {
    var activa = !!STATE.entrevistaActiva;
    if (COLPICK && COLPICK_ENTREVISTA !== activa) {
      COLPICK.dispose();
      COLPICK = null;
    }
    if (!COLPICK && Table) {
      COLPICK_ENTREVISTA = activa;
      COLPICK = Table.columnas({ clave: "mis-pacientes", cols: cols(activa), onChange: render });
    }
    return COLPICK;
  }
  function colsActivas() {
    var p = colPick();
    return p ? p.activas() : cols(!!STATE.entrevistaActiva);
  }

  var ANCHOS = null;
  function anchos() {
    if (!ANCHOS && Table) {
      ANCHOS = Table.crear({
        clave: "mis-pacientes",
        selector: "#dp-table table",
        tablas: function () { return host() ? host().querySelectorAll("#dp-table table") : []; },
        cols: colsActivas
      });
    }
    return ANCHOS;
  }

  var CELDA = {
    triage: function (p) { return triageChipHtml(p.ultima_entrevista); },
    paciente: function (p) {
      var nombrePaciente = nombre(p.nombre || (p.dni ? fmtDni(p.dni) : T("admin.queue.doctor_patients.sin_nombre", null, "Sin nombre")));
      return '<span class="dp-pac-nombre">' + esc(nombrePaciente) + "</span>";
    },
    dni: function (p) {
      return p.dni ? '<span class="dp-pac-dni">' + (Format.dniHtml(p.dni) || esc(fmtDni(p.dni))) + "</span>"
        : "";
    },
    contacto: function (p) {
      return p.contacto ? fmt().contactoQuienHtml(p) : "";
    },
    entrevista: function (p) {
      var iv = p.ultima_entrevista;
      var href = iv ? "/admin/consult?interview=" + encodeURIComponent(iv.id) : "";
      return iv
        ? '<a href="' + href + '" class="dp-iv-link">' + esc(fechaEntrevista(iv.fecha) || T("admin.queue.doctor_patients.ver", null, "Ver")) + "</a>"
        : "";
    },
    resumen: function (p) {
      var iv = p.ultima_entrevista;
      return iv && iv.resumen
        ? '<span class="dp-iv-resumen">' + esc(iv.resumen) + "</span>"
        : "";
    },
    turno: function (p) {
      return p.proximo_turno ? esc(fechaTurno(p.proximo_turno.fecha)) : "";
    }
  };

  function filaHtml(p, activas) {
    var conTriage = activas.some(function (c) { return c.key === "triage"; });
    return '<tr class="cnt-tr" data-export-dni="' + esc(String(p.dni || "").replace(/\D/g, "")) + '">' + activas.map(function (c) {
      var html = CELDA[c.key] ? CELDA[c.key](p) : "";
      // En la tarjeta del teléfono el color de triage cierra la línea del
      // nombre: va dentro de la celda del paciente y su propia celda (la
      // primera de la fila en la tabla ancha) no se muestra ahí, así la fila
      // empieza en el mismo borde izquierdo que el título.
      if (c.key === "paciente" && conTriage) {
        var chip = CELDA.triage(p);
        if (chip) html += '<span class="dp-triage-junto" data-export-omit>' + chip + "</span>";
      }
      return '<td data-col="' + esc(c.key) + '" data-label="' + esc(c.label) + '">' + html + "</td>";
    }).join("") + "</tr>";
  }

  // La banda de la pantalla: el selector de médico (cuando lo hay) a la
  // izquierda y el engranaje de columnas a la derecha, como en Pacientes.
  function pickerHtml(conEngranaje) {
    var sel = "";
    if (STATE.rol !== "medico" && STATE.medicos) {
      var opts = '<option value="">' + esc(T("admin.queue.doctor_patients.elegir_medico", null, "Elegí un médico…")) + '</option>' + STATE.medicos.map(function (m) {
        return '<option value="' + esc(m.id) + '"' + (m.id === STATE.medicoId ? " selected" : "") + ">" + esc(nombre(m.nombre)) + "</option>";
      }).join("");
      sel = '<div class="adm-tools queue-controls"><label class="sr-only" for="dp-medico">'
        + esc(T("admin.queue.doctor_patients.medico_label", null, "Médico"))
        + '</label><select class="adm-sel adm-picker" id="dp-medico">' + opts + "</select></div>";
    }
    var gear = "", search = "";
    if (conEngranaje) {
      var p = colPick();
      gear = p ? p.gearHtml() : "";
      // The file says whose patients they are when someone else picked the doctor.
      var elegido = STATE.rol !== "medico" && STATE.medicos ? STATE.medicos.filter(function (m) { return m.id === STATE.medicoId; })[0] : null;
      if (Table && Table.exportHtml) gear += Table.exportHtml({ scope: "#dp-table", titlePart: elegido ? nombre(elegido.nombre) : "" });
      // Same control, same words as Pacientes (usability study 2026-10-15:
      // two doctors looked for a place to type a DNI and found none).
      var ph = T("admin.queue.patients.search_placeholder", null, "DNI o nombre");
      search = '<input class="cnt-in cnt-search" id="dp-q" type="search" autocomplete="off" placeholder="'
        + esc(ph) + '" aria-label="' + esc(ph) + '" value="' + esc(STATE.q) + '">';
    }
    if (!sel && !gear && !search) return "";
    // The gear and «Descargar» travel together at the right end of the
    // search's own line, never one of them alone on the next line (one
    // screen header, product owner 2026-11-08).
    return '<div class="adm-band">' + sel + search + (gear ? '<span class="dp-tools">' + gear + "</span>" : "") + "</div>";
  }

  // Lo que va dentro de #dp-table: la tabla, o — si la búsqueda dejó a todos
  // afuera — el estado compartido «ningún resultado» con su botón para
  // limpiarla, sin un encabezado de tabla vacío arriba.
  function resultadosHtml(activas) {
    var visibles = filtrarPacientes(STATE.items, STATE.q);
    if (!visibles.length) {
      return Filters.noMatchHtml({
        title: T("admin.queue.doctor_patients.sin_resultados", { q: STATE.q }, "Ningún paciente coincide con «{q}»."),
        clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros")
      });
    }
    var thead = activas.map(function (c) { return '<th class="cnt-th">' + esc(c.label) + "</th>"; }).join("");
    return '<table class="cnt-table"><thead><tr>' + thead + "</tr></thead><tbody>"
      + visibles.map(function (p) { return filaHtml(p, activas); }).join("") + "</tbody></table>";
  }

  function hayResultados() {
    return filtrarPacientes(STATE.items, STATE.q).length > 0;
  }

  function marcarResultados(sec) {
    var hay = hayResultados();
    Filters.applyDisclosure(sec, hay ? "data" : "no-match");
    var a = hay ? anchos() : null;
    if (a) a.programarFit();
  }

  function pintarResultados(sec) {
    var cont = $("#dp-table", sec);
    if (!cont) return;
    cont.innerHTML = resultadosHtml(colsActivas());
    marcarResultados(sec);
  }

  function wireSearch(sec) {
    var input = $("#dp-q", sec);
    if (!input) return;
    input.addEventListener("input", function () {
      if (disposed || sec !== host()) return;
      STATE.q = input.value;
      pintarResultados(sec);
    });
    var cont = $("#dp-table", sec);
    if (cont) cont.addEventListener("click", function (e) {
      if (disposed || sec !== host()) return;
      if (!(e.target && e.target.closest && e.target.closest("[data-clear-filters]"))) return;
      STATE.q = "";
      input.value = "";
      pintarResultados(sec);
      if (input.focus) input.focus();
    });
  }

  function render() {
    if (disposed) return;
    var sec = host();
    if (!sec) return;
    var body = pickerHtml(false);
    // The doctor picker (non-doctor roles) is a required choice, not a data
    // tool, and it lives in an .adm-band: the shell's "empty" rule would hide
    // it, so the states before a roster exists leave the screen unmarked —
    // this render already shows no search, gear or table there.
    if (sec.removeAttribute) sec.removeAttribute("data-disclosure");
    if (STATE.rol !== "medico" && !STATE.medicoId) {
      body += '<p class="adm-empty">' + esc(T("admin.queue.doctor_patients.elegir_medico_empty", null, "Elegí un médico para ver sus pacientes.")) + '</p>';
      sec.innerHTML = body;
      wirePicker(sec);
      return;
    }
    if (!STATE.items.length) {
      body += Filters.emptyStateHtml({ title: T("admin.queue.doctor_patients.sin_pacientes", null, "Todavía no hay pacientes referidos a este médico.") });
      sec.innerHTML = body;
      if (!$("#dp-medico", sec)) Filters.applyDisclosure(sec, "empty");
      wirePicker(sec);
      return;
    }
    body = pickerHtml(true) + '<div class="cnt-table-wrap" id="dp-table">' + resultadosHtml(colsActivas()) + "</div>";
    sec.innerHTML = body;
    marcarResultados(sec);
    wirePicker(sec);
    wireSearch(sec);
  }

  function wirePicker(sec) {
    var sel = $("#dp-medico", sec);
    if (!sel) return;
    sel.addEventListener("change", function () {
      if (disposed || sec !== host()) return;
      var id = sel.value || null;
      setDirectDoctorUrl(id);
      cargar(id);
    });
  }

  function cargar(medicoIdDesdeHash) {
    if (disposed) return;
    var token = ++epoch;
    var sec = host();
    if (!sec) return;
    sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.queue.common.loading", null, "Cargando…")) + '</p>';
    return fetchRoster(STATE.rol === "medico" ? null : medicoIdDesdeHash, token).then(function (res) {
      if (!owns(token)) return;
      if (!res.ok) throw new Error(String(res.status));
      STATE.medicoId = res.j.medico_id || null;
      STATE.medico = res.j.medico || null;
      STATE.entrevistaActiva = !!res.j.entrevista_activa;
      STATE.items = res.j.items || [];
      if (res.j.medicos) STATE.medicos = res.j.medicos;
      render();
    }).catch(function (e) {
      if (!owns(token) || String(e.message) === "redirect" || String(e.message) === "stale") return;
      sec.innerHTML = String(e.message) === "403"
        ? '<p class="adm-empty">' + esc(T("admin.queue.common.no_permission", null, "No tenés permiso para ver esta sección.")) + '</p>'
        : '<p class="adm-empty" role="alert">' + esc(T("admin.queue.common.load_error", null, "No se pudo cargar.")) + ' <button type="button" data-retry>Reintentar</button></p>';
      var retry = sec.querySelector("[data-retry]");
      if (retry) retry.addEventListener("click", function () { cargar(selectedDoctorFromUrl()); }, { once: true });
    });
  }

  // ---- CSS propio (patrón admin-preparations.js: inyectado una vez) ------

  var cssTag = null;
  function inyectarCss() {
    if (cssTag) return;
    var css = ".dp-pac-dni{display:block;color:var(--text);font-size:13px;margin-top:2px;}"
      + ".dp-iv-link{display:block;}"
      + ".dp-triage-junto{display:none;}"
      + ".dp-iv-resumen{display:block;font-size:13px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;}"
      + ".dp-tools{display:inline-flex;align-items:center;gap:4px;flex:none;margin-left:auto;}"
      + "#sec-mis-pacientes .adm-band .cnt-search{flex:1 1 160px;max-width:420px;min-width:0;}"
      // The doctor picker has no CSS of its own: it is an .adm-sel in a
      // .queue-controls row and looks like every other one (admin-direct.css).
      + "@media(max-width:720px){#sec-mis-pacientes,#sec-mis-pacientes .cnt-table-wrap{max-width:100%;min-width:0;}#sec-mis-pacientes .cnt-table-wrap{overflow:visible;contain:none;}#sec-mis-pacientes .cnt-table,#sec-mis-pacientes .cnt-table tbody,#sec-mis-pacientes .cnt-table tr,#sec-mis-pacientes .cnt-table td{display:block;width:100%!important;min-width:0!important;box-sizing:border-box;}#sec-mis-pacientes .cnt-table{max-width:100%!important;}#sec-mis-pacientes .cnt-table colgroup{display:none;}#sec-mis-pacientes .cnt-table thead{display:none;}#sec-mis-pacientes .cnt-table tr{padding:12px var(--adm-gutter);border-bottom:1px solid var(--border-soft);}#sec-mis-pacientes .cnt-table td{padding:2px 0;border:0;white-space:normal;}#sec-mis-pacientes .cnt-table td::before{content:none;}#sec-mis-pacientes .cnt-table td[data-col='paciente']{font-weight:650;}#sec-mis-pacientes .cnt-table td[data-col='resumen']{display:none;}#sec-mis-pacientes .cnt-table td[data-col='contacto'],#sec-mis-pacientes .cnt-table td[data-col='entrevista'],#sec-mis-pacientes .dp-iv-link{overflow-wrap:anywhere;}#sec-mis-pacientes .adm-empty{padding-left:var(--adm-gutter);padding-right:var(--adm-gutter);}"
      // Information diet: a field with nothing to say is an empty cell in
      // the wide table and takes no line on the phone card. The triage colour
      // sits at the end of the name's line, and the two dates say what they
      // are (two bare dates under a name read as one range).
      + "#sec-mis-pacientes .cnt-table td:empty{display:none;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='triage']{display:none;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='paciente']{display:flex!important;align-items:baseline;column-gap:12px;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='paciente'] .dp-pac-nombre{flex:1 1 0;min-width:0;}"
      + "#sec-mis-pacientes .cnt-table .dp-triage-junto{display:inline;flex:none;font-weight:400;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='entrevista'] .dp-iv-link{display:inline;}"
      // The contact icons open their own line at the card's edge: the 20px
      // glyph sits centred in its tap box (32px, 44px on a phone or touch
      // screen — format.mjs), so the first box steps back by that inset and
      // the first glyph stands on the name's edge. The cell does not clip
      // (the width engine's td{overflow:hidden}), so the box stays whole.
      + "#sec-mis-pacientes .cnt-table td[data-col='contacto']{overflow:visible;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='contacto']>.cnt-ico-link:first-child{margin-left:-6px;}"
      + "#sec-mis-pacientes .cnt-table td[data-col='entrevista']::before,#sec-mis-pacientes .cnt-table td[data-col='turno']::before{content:attr(data-label) \" \";color:var(--hint-ink,var(--text-2));}}"
      + "@media(max-width:640px),(max-width:720px) and (pointer:coarse){#sec-mis-pacientes .cnt-table td[data-col='contacto']>.cnt-ico-link:first-child{margin-left:-12px;}}";
    cssTag = documentRef.createElement("style");
    cssTag.textContent = css;
    documentRef.head.appendChild(cssTag);
  }

  // ---- routing propio (mismo patrón que admin-my-appointments.js) --------

  function route() {
    inyectarCss();
    var el = host();
    if (el) el.style.display = "";
    STATE.rol = options.role || null;
    stripDoctorUrlForMedico();
    return cargar(selectedDoctorFromUrl());
  }
  function directDependencies() { return Promise.all([i18nReady, prefsReady, datesReady, formatReady, tableReady, sessionReady, filtersReady]); }
  function bootDirectScreen() {
    return directDependencies().then(function () { return disposed ? undefined : route(); });
  }
  function onPopstate() { if (!disposed) bootDirectScreen().catch(showLoadError); }
  function showLoadError() {
    if (disposed) return;
    var sec = host();
    if (sec) {
      sec.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.queue.common.load_error", null, "No se pudo cargar.")) + ' <button type="button" data-retry>Reintentar</button></p>';
      var retry = sec.querySelector("[data-retry]");
      if (retry) retry.addEventListener("click", function () { bootDirectScreen().catch(showLoadError); }, { once: true });
    }
  }
  window.addEventListener("popstate", onPopstate);
  var dispose = function () {
    if (disposed) return;
    disposed = true;
    epoch++;
    window.removeEventListener("popstate", onPopstate);
    if (ANCHOS) ANCHOS.dispose();
    if (COLPICK) COLPICK.dispose();
    if (cssTag && cssTag.remove) cssTag.remove();
    cssTag = null;
    if (MOUNTS.get(root) === dispose) MOUNTS.delete(root);
  };
  // The page's load deadline ends when the dependencies are in; the first
  // read after that is the screen's own waiting and failure state.
  dispose.loaded = directDependencies().then(function () { return true; }, function () { return false; });
  dispose.ready = bootDirectScreen().catch(function (error) { showLoadError(); throw error; });
  MOUNTS.set(root, dispose);
  return dispose;
}
