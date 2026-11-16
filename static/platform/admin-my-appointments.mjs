/* admin-my-appointments.js — "Mis turnos": la vista propia del médico (Pinamar
   Turnos). It is loaded only by its direct screen and owns that screen's
   rendering lifecycle.

   Contrato de API (doctor_appointments.py, rol 'medico' — cualquier otro rol
   recibe 403; admin_auth.require_admin(permitir_medico=True) + chequeo de
   rol server-side):
     GET /api/admin/my-appointments?days=14 -> {medico:{id,nombre}, dias,
       turnos:[{id,turno_dt,dia_iso,hora,paciente,dni,servicio,centro,
       preparacion}], total}
     GET /api/admin/my-appointments/feed    -> {url} del feed ICS (copiable)

   `turnos` ya viene ordenado por turno_dt asc con hora ya convertida a
   America/Argentina/Buenos_Aires (dia_iso/hora) — acá sólo se agrupa por
   dia_iso preservando el orden (un scan, no un sort de nuevo).
*/
const MOUNTS = new WeakMap();

export function mountMyAppointments(options) {
  "use strict";
  options = options || {};
  var root = options.root || document;
  var prior = MOUNTS.get(root);
  if (prior) prior();
  var disposed = false;
  var epoch = 0;
  function owns(token) { return !disposed && token === epoch; }
  var documentRef = root.ownerDocument || root;
  function host() { return root.id === "sec-mis-turnos" ? root : root.querySelector("#sec-mis-turnos"); }
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
  var Table = null;
  var tableReady = importStatic("platform/admin-table.mjs").then(function (m) { Table = m; return m; });
  var sessionReady = importStatic("platform/admin-session.mjs");

  var $ = function (s, r) { return (r || root).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || root).querySelectorAll(s)); };

  // «Hoy» de la instalación (dates.mjs) para el contador del día: toISOString() es UTC, y de
  // 21 a 24 h en Pinamar ya contaba el día siguiente. Sin dates.mjs, como antes.
  function hoyIsoTurnos() {
    return Dates && Dates.hoyIsoInstalacion ? Dates.hoyIsoInstalacion()
      : new Date().toISOString().slice(0, 10);
  }
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  function toast(msg) { if (window.toast) window.toast(msg); }

  var I18n = null;
    var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
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

  var DIAS = 14;
  var CACHE = null;
  // The feed address is a credential: the server shows it in full only when
  // it is created or replaced, afterwards {url: null, hint: last 4} (alembic
  // 0040). FEED_URL holds it for this page only; FEED_HINT is the other state.
  var FEED_URL = null;
  var FEED_HINT = "";
  var FEED_FAILED = false;

  function fetchTurnos(force, token) {
    if (!force && CACHE) return Promise.resolve(CACHE);
    return fetch("/api/admin/my-appointments?days=" + DIAS, { credentials: "same-origin" }).then(function (res) {
      if (!owns(token)) throw new Error("stale");
      var boundary = (res.status === 401 || res.status === 403)
        ? sessionReady.then(function (mod) { return owns(token) ? mod.confirmar(res, undefined, function () { return owns(token); }) : false; })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        // Sólo un 401 que whoami confirma (o el 403 de 2FA) se lleva la
        // pantalla a otra página; uno sin confirmar (Basic Auth del sitio, un
        // proxy) sigue el camino de reintento de abajo, no la deja muda.
        if (transitioned) throw new Error("redirect");
        if (res.status === 401) throw new Error("401");
        if (res.status === 403) throw new Error("403");
        if (!res.ok) throw new Error("bad-" + res.status);
        // Un 200 con cuerpo que no es JSON, o sin la lista `turnos` que el
        // contrato de my_appointments_data SIEMPRE manda (vacía incluida),
        // no es "no tenés turnos": es una respuesta rota. Sin este corte se
        // pintaba como lista vacía exitosa y quedaba en CACHE, con lo que el
        // Reintentar devolvía el mismo cuerpo en vez de volver a pedirlo.
        return res.json().catch(function () { throw new Error("malformed"); }).then(function (j) {
          if (!owns(token)) throw new Error("stale");
          if (!j || typeof j !== "object" || Array.isArray(j) || !Array.isArray(j.turnos)) throw new Error("malformed");
          CACHE = j;
          return CACHE;
        });
      });
    });
  }

  function fetchFeedUrl(token) {
    if (FEED_URL) return Promise.resolve(FEED_URL);
    FEED_FAILED = false;
    return fetch("/api/admin/my-appointments/feed", { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) throw new Error("bad-" + res.status);
      return res.json().then(function (j) {
        if (!owns(token)) return null;
        FEED_URL = (j && j.url) || null;
        FEED_HINT = (j && j.hint) || "";
        if (!FEED_URL && !FEED_HINT) FEED_FAILED = true;
        return FEED_URL;
      });
    }).catch(function () { if (owns(token)) FEED_FAILED = true; return null; });
  }

  // "Hoy, Miércoles 16 de julio" / "Mañana, Jueves 17 de julio" / "Martes 21
  // de julio" — reusa fechas.js (dia()/larga()) en vez de reinventar el
  // formato: dia() ya sabe decir "Hoy"/"Mañana"/weekday+fecha; para Hoy y
  // Mañana le sumamos la fecha absoluta de larga() (que NUNCA dice "Hoy").
  function grupoLabel(diaIso) {
    if (!Dates) return diaIso;
    var rel = Dates.dia(diaIso);
    // Comparar contra el catálogo (date.today/date.tomorrow), no contra el
    // literal "Hoy"/"Mañana": dates.mjs habla el idioma de la página desde
    // PR14 (2026-08-04), así que en inglés dia() devuelve "Today"/"Tomorrow"
    // — comparar contra castellano fijo hacía que esta rama nunca matcheara
    // ahí y la fecha absoluta se perdiera en silencio sólo en inglés.
    if (rel === T("date.today", null, "Hoy") || rel === T("date.tomorrow", null, "Mañana")) {
      var abs = Dates.larga(diaIso, null);
      return abs ? rel + ", " + abs : rel;
    }
    return rel || diaIso;
  }

  // Mismo criterio de preparación que my-requests.js:prepBoxHtml
  // (banner de ayuno + bullets), compactado para una celda de tabla.
  function prepCellHtml(prepList) {
    if (!prepList || !prepList.length) return '<span class="mt-prep-none">—</span>';
    var body = prepList.map(function (e) {
      var ay = e.ayuno || {}, banner = "", bullets = "", prep = e.preparacion || [];
      if (e.no_preparation_needed) {
        if (!prep.length) banner = "<p>" + T("admin.queue.my_appointments.prep_none_needed", null, "No necesita preparación especial.") + "</p>";
      } else if (ay.required && ay.horas) {
        banner = "<p><b>" + applyVars(Tn("admin.queue.my_appointments.prep_fasting_hours", ay.horas, { horas: esc(ay.horas) }, "Ayuno de {horas} hora.", "Ayuno de {horas} horas."), { horas: esc(ay.horas) }) + "</b>"
          + " " + (ay.agua_permitida
            ? T("admin.queue.my_appointments.prep_water_allowed", null, "Puede tomar agua.")
            : T("admin.queue.my_appointments.prep_water_forbidden", null, "Tampoco agua.")) + "</p>";
      }
      if (prep.length === 1) {
        bullets = "<p>" + esc(prep[0]) + "</p>";
      } else if (prep.length > 1) {
        bullets = "<ul>" + prep.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
      }
      return banner + bullets;
    }).join("");
    if (!body) return '<span class="mt-prep-none">—</span>';
    // In the downloaded file the cell holds the preparation itself, not the
    // «Ver preparación» toggle that folds it here.
    return '<details data-export="' + esc(body.replace(/<\/(p|li)>/g, " ").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()
        .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&amp;/g, "&"))
      + '"><summary class="mt-prep-toggle">' + T("admin.queue.my_appointments.prep_toggle", null, "Ver preparación") + '</summary><div class="mt-prep-body">' + body + "</div></details>";
  }

  // Edad/contacto/consultorio/sobreturno (auditoría 2026-07-20): lo que el
  // médico necesita para su día — el server ya los manda en cada turno.
  // La edad la escribe format.mjs para todo el panel: NÚMERO pelado, que la
  // columna ya se llama "Edad" y repetir "años" en cada fila es ruido (responsable del producto,
  // 2026-08-05). Los bebés siguen contándose en meses, que eso lo decide
  // edadTexto y no cada tabla.
  function edadDe(iso) {
    return Format.edadTexto(iso);
  }

  // Cómo se escribe un contacto — y de quién es (problems.md №4) — lo decide
  // format.mjs, igual que en «Mis pacientes».
  function contactoCell(t) {
    if (!t.contacto) return "—";
    return Format.contactoQuienHtml(t, "");
  }

  // Qué tan seguido viene esta persona (patient_ranking.py) — la MISMA celda
  // que Pacientes de hoy, donde se llama "Historial". Acá la columna se llama
  // "Asistencia" porque en esta tabla no hay ninguna otra que se llame así (no
  // se marca presente/ausente desde Mis turnos). La etiqueta sale del `banda`
  // del server vía catálogo (la `etiqueta` del server es castellano fijo y se
  // colaba en los otros idiomas, responsable del producto 2026-09-16).
  function bandaLabel(r) {
    var mapa = {
      "viene-siempre": "always_comes",
      "falta-a-veces": "sometimes_misses",
      "falta-seguido": "often_misses",
      "sin-historial": "no_history"
    };
    var clave = mapa[r && r.banda];
    return clave ? T("admin.queue.patients.band." + clave, null, r.etiqueta) : ((r && r.etiqueta) || "");
  }

  function asistenciaCell(t) {
    var r = t.ranking;
    if (!r || r.sin_historial) return '<span class="mt-prep-none">—</span>';
    return '<span class="pat-rank" title="' + esc(r.puntaje + "% · " + (r.motivos || []).join("\n")) + '">'
      + '<span class="attendance-dot attendance-dot--' + esc(r.nivel || "none") + '" aria-hidden="true"></span>'
      + "<span>" + esc(bandaLabel(r)) + "</span></span>";
  }

  // Mismas claves que admin-queue.js/admin-doctor-patients.js (namespace
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

  function triageCell(t) {
    var key = String((t.entrevista || {}).triage || "").toUpperCase();
    var label = triageLabels()[key];
    if (!label) return '<span class="mt-prep-none">—</span>';
    return '<span class="queue-triage queue-triage--' + key.toLowerCase() + '">' + esc(label) + "</span>";
  }

  // El renglón de la entrevista previa. Se recorta a una línea y el texto
  // entero lo muestra el globo de admin-table.js al pasar por encima o tocar.
  function resumenCell(t) {
    var e = t.entrevista || {};
    if (!e.resumen) return '<span class="mt-prep-none">—</span>';
    return '<span class="queue-triage-resumen">' + esc(e.resumen) + "</span>";
  }

  // Lo que pasó o va a pasar con el turno: si ya se marcó la asistencia,
  // eso; si no, lo que el paciente contestó al recordatorio. Sin respuesta no
  // se escribe nada — «sin confirmar» en cada fila de la semana es ruido.
  function estadoCell(t) {
    var partes = [];
    if (t.sobreturno) {
      partes.push('<span class="mt-sobre">' + esc(T("admin.queue.my_appointments.overbooked_tag", null, "Sobreturno")) + "</span>");
    }
    var st = null;
    if (t.asistencia === "presente") st = ["good", T("admin.queue.attendance.presente", null, "Atendió")];
    else if (t.asistencia === "ausente") st = ["bad", T("admin.queue.attendance.ausente", null, "No vino")];
    else if (t.confirmacion === "si") st = ["good", T("admin.queue.table.confirmo", null, "Confirmó")];
    else if (t.confirmacion === "no") st = ["bad", T("admin.queue.confirm.aviso_no", null, "Avisó que no viene")];
    if (st) {
      partes.push('<span class="mt-estado"><span class="attendance-dot attendance-dot--' + st[0] + '" aria-hidden="true"></span>'
        + esc(st[1]) + "</span>");
    }
    return partes.join(" ");
  }

  // Las columnas de la tabla. Lo del día va de fábrica; el servicio, el
  // consultorio, el centro y la preparación quedan detrás del engranaje (responsable del producto,
  // 2026-08-05: un médico que mira SU agenda ya sabe en qué consultorio
  // atiende y de qué servicio es — once filas repitiéndoselo no le dicen nada,
  // y le tapan lo que sí necesita antes de que entre el paciente).
  function cols(entrevistaActiva) {
    var lista = [
      { key: "hora", label: T("admin.queue.my_appointments.col_time", null, "Hora"), min: 62, always: true },
      // Sin `flex`: el nombre es lo que identifica la fila y mide lo que pide
      // (hasta 340). Si falta ancho ceden el resumen y el contacto, y después
      // caen columnas (DROP); el nombre no se recorta para que entre otra.
      { key: "paciente", label: T("admin.queue.my_appointments.col_patient", null, "Paciente"), min: 220, max: 340, always: true },
      { key: "dni", label: T("admin.queue.my_appointments.col_dni", null, "DNI"), min: 92, "default": true },
      { key: "edad", label: T("admin.queue.my_appointments.col_age", null, "Edad"), min: 54, "default": true, numeric: true },
      { key: "asistencia", label: T("admin.queue.my_appointments.col_attendance", null, "Asistencia"), min: 118, max: 190, "default": true }
    ];
    // Sólo si la entrevista previa está habilitada para este médico: una
    // columna cuyas celdas vienen TODAS vacías no se esconde sola.
    if (entrevistaActiva) {
      lista.push({ key: "triage", label: T("admin.queue.my_appointments.col_triage", null, "Triage"), min: 84, "default": true });
      lista.push({ key: "resumen", label: T("admin.queue.my_appointments.col_intake_summary", null, "Captura de síntomas"), min: 160, max: 420, flex: 2, clip: true, "default": true });
    }
    // min 170: a full «+54 9 2254 10-0099» must stay readable when the
    // interview summary takes the spare width (2026-10-23 screenshot: at 130
    // the number was cut to «+54 9 2254 10-0…», the one thing to dial).
    // max 340: the spare width no longer widens it (it goes to the filler
    // column), so the cap is what a long email needs in full.
    lista.push({ key: "contacto", label: T("admin.queue.my_appointments.col_contact", null, "Contacto"), min: 170, max: 340, flex: 1, "default": true });
    // Lo que se sabe del turno antes de que llegue (confirmó / avisó que no
    // viene) o, ya pasado, si vino: un punto y la palabra, como en Pacientes
    // de hoy (responsable del producto, 2026-10-27: hora, paciente y estado en un orden claro).
    // max 300: «Sobreturno · Avisó que no viene» entero (con 220 se cortaba).
    lista.push({ key: "estado", label: T("admin.queue.my_appointments.col_status", null, "Estado"), min: 120, max: 300, "default": true });
    lista.push({ key: "servicio", label: T("admin.queue.my_appointments.col_service", null, "Servicio"), min: 110, max: 260, flex: 1 });
    lista.push({ key: "consultorio", label: T("admin.queue.my_appointments.col_office", null, "Consultorio"), min: 96 });
    lista.push({ key: "centro", label: T("admin.queue.my_appointments.col_center", null, "Centro"), min: 110, max: 220 });
    lista.push({ key: "preparacion", label: T("admin.queue.my_appointments.col_prep", null, "Preparación"), min: 120, max: 260 });
    // Sobrante vacío al final, como en Pacientes de hoy (handoff 2026-07-27):
    // las columnas quedan a su ancho y juntas —el estado al lado del
    // contacto— y la fila igual llega hasta el borde.
    lista.push({ key: "relleno", label: "", min: 0, always: true, relleno: true });
    return lista;
  }

  var CELDA = {
    hora: function (t) { return esc(t.hora); },
    estado: estadoCell,
    paciente: function (t) {
      return esc(Format.nombre(t.paciente)) || esc(T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre"));
    },
    dni: function (t) { return Format.dniHtml(t.dni) || "—"; },
    edad: function (t) { return esc(edadDe(t.nacimiento)) || "—"; },
    asistencia: asistenciaCell,
    triage: triageCell,
    resumen: resumenCell,
    contacto: function (t) { return contactoCell(t); },
    servicio: function (t) { return esc(t.servicio || "—"); },
    consultorio: function (t) { return esc(t.consultorio || "—"); },
    centro: function (t) { return esc(t.centro || "—"); },
    preparacion: function (t) { return prepCellHtml(t.preparacion); }
  };

  // Una celda sin dato queda en blanco en la tabla ancha (una raya en cada
  // fila no dice nada y tapa las que sí traen dato); en la fila de dos
  // renglones (≤1100 px) no ocupa lugar.
  function vacia(html) {
    return !html || html === "—" || html.indexOf('class="mt-prep-none"') !== -1;
  }

  function diaTable(turnos, activas, diaIso) {
    var conEdad = activas.some(function (c) { return c.key === "edad"; });
    var rows = turnos.map(function (t) {
      return '<tr class="cnt-tr" id="turno-' + esc(t.id) + '" data-export-dni="' + esc(String(t.dni || "").replace(/\D/g, "")) + '">' + activas.map(function (c) {
        if (c.relleno) return '<td data-col="relleno" class="mt-vacio"></td>';
        var html = CELDA[c.key] ? CELDA[c.key](t) : "";
        var sinDato = vacia(html);
        if (sinDato) html = "";
        var clases = (c.numeric ? "cnt-numeric" : "") + (sinDato ? " mt-vacio" : "");
        // En la fila de dos renglones la edad va pegada al nombre («Sofía
        // Herrera, 73»), como en la tarjeta del turno; la celda de la edad no
        // se muestra ahí. Un bebé viene escrito en meses y va igual.
        if (c.key === "paciente" && conEdad) {
          var edad = esc(edadDe(t.nacimiento));
          if (edad) html += '<span class="mt-edad-junto" data-export-omit>, ' + edad + "</span>";
        }
        return '<td data-col="' + esc(c.key) + '" data-label="' + esc(c.label) + '"'
          + (clases.trim() ? ' class="' + clases.trim() + '"' : "") + '>' + html + "</td>";
      }).join("") + "</tr>";
    }).join("");
    // Cada <col> lleva su clave, como en Pacientes de hoy: cuando no entra y
    // una columna cae (DROP), se oculta también su <col> y las celdas de la
    // derecha no se corren a una columna de ancho 0.
    // In the downloaded file the day of each table becomes its first column.
    var dia = diaIso ? ((Dates && Dates.larga && Dates.larga(diaIso, null)) || diaIso) : "";
    return '<div class="cnt-table-wrap"><table class="cnt-table" data-export-group="' + esc(dia)
      + '" data-export-group-head="' + esc(T("admin.shell.export.day_column", null, "Día")) + '"><colgroup>'
      + activas.map(function (c) { return '<col data-col="' + esc(c.key) + '">'; }).join("")
      + "</colgroup><thead><tr>"
      + activas.map(function (c) {
        if (c.relleno) return '<th class="cnt-th" data-col="relleno" aria-hidden="true"></th>';
        return '<th class="cnt-th' + (c.numeric ? " cnt-numeric" : "") + '" data-col="' + esc(c.key) + '">' + esc(c.label) + "</th>";
      }).join("")
      + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  // Agrupa preservando el orden que ya trae el server (turno_dt asc) — un
  // solo scan, sin volver a ordenar.
  function agrupar(turnos) {
    var grupos = [], porDia = {};
    turnos.forEach(function (t) {
      if (!porDia[t.dia_iso]) { porDia[t.dia_iso] = []; grupos.push(t.dia_iso); }
      porDia[t.dia_iso].push(t);
    });
    return grupos.map(function (diaIso) { return { diaIso: diaIso, turnos: porDia[diaIso] }; });
  }

  // El engranaje de columnas es el mismo motor que Pacientes, Usuarios y la
  // cola (SanaTable.columnas): se arma recién con la primera respuesta, que es
  // cuando sabemos si esta cuenta tiene la entrevista previa habilitada.
  var COLPICK = null;
  function colPick(entrevistaActiva) {
    if (!COLPICK && Table) {
      COLPICK = Table.columnas({
        clave: "mis-turnos", cols: cols(entrevistaActiva),
        onChange: function () { if (CACHE && !disposed) render(CACHE, epoch); }
      });
    }
    return COLPICK;
  }

  // Los anchos, con el mismo motor que el resto de la consola (SanaTable):
  // una tabla por día, todas medidas JUNTAS para que las columnas de hoy y las
  // de mañana caigan en la misma vertical. Sin esto, el resumen de la
  // entrevista —que puede ser un párrafo— estiraba la tabla de su día y la
  // dejaba corrida respecto de las otras; y sin ancho fijo no hay recorte, así
  // que el globo de texto completo tampoco tenía de qué agarrarse.
  // Se mide con la tabla a su ancho natural (`mt-measuring`): medida a 100 %
  // el navegador repartía el sobrante entre todas las columnas, Hora y DNI
  // salían anchas y el nombre del paciente se cortaba a 1440 px. El sobrante
  // no se reparte (`fill: false`): queda en el relleno del final. Si ni con
  // todo en su mínimo entra, caen primero las del engranaje, después
  // Asistencia, DNI y Edad, y por último la entrevista previa (el resumen y
  // después el triage) — hora, paciente, contacto y estado quedan siempre.
  // Hasta 1100 px no hay tabla que encajar: es la fila de dos renglones
  // (admin-direct.css).
  var DROP = ["preparacion", "centro", "consultorio", "servicio", "asistencia", "dni", "edad", "resumen", "triage"];
  var ANCHOS = null;
  function anchos() {
    if (!ANCHOS && Table) {
      ANCHOS = Table.crear({
        clave: "mis-turnos",
        selector: "#sec-mis-turnos .mt-day table",
        tablas: function () { return host() ? host().querySelectorAll(".mt-day table") : []; },
        cols: function () { return COLPICK ? COLPICK.activas() : cols(false); },
        host: host,
        medirClase: "mt-measuring",
        drop: DROP,
        fill: false,
        dropClase: function (k) { return "mt-hide-" + k; },
        apilado: function () {
          try { return window.matchMedia("(max-width:1100px)").matches; } catch (e) { return false; }
        }
      });
    }
    return ANCHOS;
  }

  var renderGeneration = 0;
  function render(data, token) {
    if (!owns(token)) return;
    var generation = ++renderGeneration;
    var sec = host();
    if (!sec) return;
    var turnos = data.turnos || [];

    var pick = colPick(!!data.entrevista_activa);
    var activas = pick ? pick.activas() : cols(!!data.entrevista_activa);

    var html = "";
    if (!turnos.length) {
      // Vacío: el título, qué va a aparecer acá y, si todavía nadie puede
      // sacarle un turno (sin horarios cargados), el único paso que lo
      // cambia. Sin engranaje ni contadores: no hay nada que configurar.
      if (data.tiene_horarios === false) {
        html += '<div class="mt-empty"><h2>' + esc(T("admin.queue.my_appointments.no_schedule_title", null, "Todavía no tenés horarios de atención")) + "</h2>"
          + "<p>" + esc(T("admin.queue.my_appointments.no_schedule_body", null, "Cargá tus horarios y los turnos que te den van a aparecer acá.")) + "</p>"
          + '<a class="btn btn-accent" href="/admin/my-schedules">' + esc(T("admin.queue.my_appointments.no_schedule_action", null, "Cargar mis horarios")) + "</a></div>";
      } else {
        html += '<div class="mt-empty"><h2>' + esc(applyVars(T("admin.queue.my_appointments.empty_state", { dias: DIAS }, "No tenés turnos en los próximos {dias} días"), { dias: DIAS })) + "</h2>"
          + "<p>" + esc(T("admin.queue.my_appointments.empty_body", null, "Cuando te den un turno, aparece acá por día y hora.")) + "</p></div>";
      }
    } else {
      // The gear and «Descargar» ride on the title's line after the
      // calendar icon; no «Hoy N turnos · N esta semana» counter (responsable del producto,
      // 2026-10-14 «never make counters»; one screen header 2026-11-08).
      titleTools((pick ? pick.gearHtml() : "")
        + (Table && Table.exportHtml ? Table.exportHtml({ scope: "#sec-mis-turnos" }) : ""));
      agrupar(turnos).forEach(function (g) {
        // «Imprimir»: the day on HSI's A4 sheet («Turnos programados»); the
        // server prints this doctor's own day (/admin/cola/print).
        html += '<div class="mt-day"><div class="mt-day-head"><h2 class="mt-day-h">' + esc(grupoLabel(g.diaIso)) + "</h2>"
          + '<a class="btn btn-quiet btn-sm mt-print" data-mt-imprimir="' + esc(g.diaIso) + '" href="/admin/cola/print?dia='
          + esc(encodeURIComponent(g.diaIso)) + '&amp;auto=1" target="_blank" rel="noopener">'
          + esc(T("admin.queue.print.print", null, "Imprimir")) + "</a></div>"
          + diaTable(g.turnos, activas, g.diaIso) + "</div>";
      });
    }
    if (!turnos.length) titleTools("");
    sec.innerHTML = html;
    var a = anchos();
    if (a) a.programarFit();

    // El enlace de cada evento del calendario abre /admin/mis-turnos#turno-<id>:
    // la primera pintura con ese turno lo trae a la vista y lo marca.
    if (!jumped && generation === 1) jumpToHashTurno(sec);
  }

  // #mt-tools: the screen's own controls at the right end of the title row
  // (.adm-title-action, admin-direct.css). Empty markup takes it away.
  function titleTools(markup) {
    var doc = documentRef;
    if (!doc || typeof doc.getElementById !== "function") return;
    var old = doc.getElementById("mt-tools");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!markup || disposed) return;
    var cal = calButton();
    var row = (cal && cal.parentNode) || (doc.querySelector && doc.querySelector(".adm-h1row"));
    if (!row || typeof row.insertBefore !== "function" || typeof doc.createElement !== "function") return;
    var box = doc.createElement("span");
    box.id = "mt-tools";
    box.className = "adm-title-action mt-tools";
    box.innerHTML = markup;
    var before = (cal && cal.parentNode === row) ? cal.nextSibling
      : (row.querySelector && row.querySelector(".alert-bell-wrap"));
    row.insertBefore(box, before || null);
  }

  var jumped = false;
  function jumpToHashTurno(sec) {
    var hash = (typeof location !== "undefined" && location.hash) || "";
    if (!/^#turno-\d+$/.test(hash) || !sec.querySelector) return;
    var row = sec.querySelector(hash);
    if (!row) return;
    jumped = true;
    if (row.classList) row.classList.add("mt-target");
    if (row.scrollIntoView) row.scrollIntoView({ block: "center" });
  }

  // =====================================================================
  // «Agregar a mi calendario» (responsable del producto, 2026-10-27): la dirección de
  // suscripción del médico (GET /api/admin/my-appointments/feed) con los
  // pasos para Google y Apple, y «Cambiar la dirección» (POST …/feed/rotate)
  // si se compartió por error. Se pide recién al abrir el panel. El feed no
  // lleva datos del paciente: servicio, centro y el enlace a este turno.
  // =====================================================================
  var GOOGLE_ADD_URL = "https://calendar.google.com/calendar/u/0/r/settings/addbyurl";
  var calOpen = false, calConfirm = false, calCopied = false;
  function calButton() { return documentRef.getElementById ? documentRef.getElementById("mt-cal-open") : null; }
  function calHost() { return documentRef.getElementById ? documentRef.getElementById("mt-cal") : null; }

  function calHtml(url) {
    var pasos = function (lista) { return "<ol>" + lista.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ol>"; };
    var webcal = url.replace(/^https?:/, "webcal:");
    return '<h2 class="mt-cal-h">' + esc(T("admin.queue.my_appointments.cal_title", null, "Tus turnos en tu calendario")) + "</h2>"
      + "<p>" + esc(T("admin.queue.my_appointments.cal_intro", null, "Agregá esta dirección una vez y tus turnos aparecen y se actualizan solos. En el calendario se ve el servicio y el centro de cada turno, nunca los datos del paciente.")) + "</p>"
      + '<div class="mt-cal-url"><input type="text" readonly aria-label="' + esc(T("admin.queue.my_appointments.cal_address", null, "Dirección del calendario")) + '" value="' + esc(url) + '">'
      + '<button type="button" class="btn btn-accent" data-cal-copy>' + esc(calCopied
        ? T("admin.queue.my_appointments.cal_copied", null, "Copiada")
        : T("admin.queue.my_appointments.cal_copy", null, "Copiar dirección")) + "</button></div>"
      + '<p class="mt-cal-once">' + esc(T("admin.queue.my_appointments.cal_once", null, "Agregala a tu calendario ahora: por seguridad, esta dirección no se vuelve a mostrar.")) + "</p>"
      + '<details class="mt-cal-app"><summary>Google Calendar</summary>'
      + '<a class="btn" href="' + esc(GOOGLE_ADD_URL) + '" target="_blank" rel="noopener" data-cal-google>' + esc(T("admin.queue.my_appointments.cal_google_btn", null, "Copiar y abrir Google Calendar")) + "</a>"
      + pasos([
        T("admin.queue.my_appointments.cal_google_step1", null, "Tocá «Copiar y abrir Google Calendar»: se abre «Agregar desde una URL»."),
        T("admin.queue.my_appointments.cal_google_step2", null, "Pegá la dirección en «URL del calendario»."),
        T("admin.queue.my_appointments.cal_google_step3", null, "Confirmá con «Agregar calendario». Google trae los turnos nuevos cada algunas horas.")
      ]) + "</details>"
      + '<details class="mt-cal-app"><summary>' + esc(T("admin.queue.my_appointments.cal_apple", null, "iPhone, iPad o Mac")) + "</summary>"
      + '<a class="btn" href="' + esc(webcal) + '">' + esc(T("admin.queue.my_appointments.cal_apple_btn", null, "Abrir en Calendario")) + "</a>"
      + pasos([
        T("admin.queue.my_appointments.cal_apple_step1", null, "Tocá «Abrir en Calendario» y confirmá con «Suscribirse»."),
        T("admin.queue.my_appointments.cal_apple_step2", null, "Si no se abre: en el iPhone, Ajustes → Calendario → Cuentas → Agregar cuenta → Otra → Agregar calendario suscripto, y pegá la dirección.")
      ]) + "</details>"
      + '<details class="mt-cal-app"><summary>' + esc(T("admin.queue.my_appointments.cal_other", null, "Otra app")) + "</summary>"
      + "<p>" + esc(T("admin.queue.my_appointments.cal_other_step", null, "Buscá «Suscribirse a un calendario» o «Agregar calendario por URL» y pegá la dirección.")) + "</p></details>"
      + '<div class="mt-cal-rotate">' + (calConfirm
        ? "<p>" + esc(T("admin.queue.my_appointments.cal_rotate_confirm", null, "La dirección actual deja de funcionar y vas a tener que agregar la nueva en tu calendario.")) + "</p>"
          + '<button type="button" class="btn btn-no" data-cal-rotate-yes>' + esc(T("admin.queue.my_appointments.cal_rotate_yes", null, "Cambiar la dirección")) + "</button> "
          + '<button type="button" class="btn" data-cal-rotate-no>' + esc(T("admin.queue.my_appointments.cal_rotate_no", null, "No cambiarla")) + "</button>"
        : '<button type="button" class="btn" data-cal-rotate>' + esc(T("admin.queue.my_appointments.cal_rotate", null, "Cambiar la dirección")) + "</button>")
      + "</div>";
  }

  // The address exists and only its last 4 characters are known: nothing to
  // do when the calendar already shows the appointments; a new one replaces it.
  function calHintHtml(hint) {
    return '<h2 class="mt-cal-h">' + esc(T("admin.queue.my_appointments.cal_title", null, "Tus turnos en tu calendario")) + "</h2>"
      + "<p>" + esc(applyVars(T("admin.queue.my_appointments.cal_exists", { hint: hint }, "Ya tenés una dirección de calendario, la que termina en {hint}. Si tus turnos aparecen en tu calendario, no hace falta nada más."), { hint: hint })) + "</p>"
      + "<p>" + esc(T("admin.queue.my_appointments.cal_hidden", null, "Por seguridad no se vuelve a mostrar. Para agregarlos ahora o en otro calendario, creá una dirección nueva: la anterior deja de funcionar.")) + "</p>"
      + '<div class="mt-cal-rotate"><button type="button" class="btn btn-accent" data-cal-rotate-yes>'
      + esc(T("admin.queue.my_appointments.cal_new", null, "Crear una dirección nueva")) + "</button></div>";
  }

  function paintCal() {
    var h = calHost(), b = calButton();
    if (b && b.setAttribute) b.setAttribute("aria-expanded", calOpen ? "true" : "false");
    if (!h) return;
    if (!calOpen) { h.hidden = true; h.innerHTML = ""; return; }
    h.hidden = false;
    h.innerHTML = FEED_URL ? calHtml(FEED_URL)
      : FEED_HINT ? calHintHtml(FEED_HINT)
      : FEED_FAILED ? '<p class="mt-cal-wait" role="alert">' + esc(T("admin.queue.my_appointments.cal_load_retry", null, "No pudimos traer tu dirección ahora. Cerrá y probá de nuevo en un momento.")) + "</p>"
      : '<p class="mt-cal-wait">' + esc(T("admin.queue.my_appointments.cal_loading", null, "Preparando tu dirección…")) + "</p>";
  }

  function copiar(text) {
    var input = calHost() && calHost().querySelector ? calHost().querySelector(".mt-cal-url input") : null;
    return new Promise(function (resolve) {
      function fallback() { try { if (input) { input.select(); documentRef.execCommand("copy"); } } catch (e) {} resolve(); }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(resolve, fallback);
      else fallback();
    });
  }

  function toggleCal() {
    if (disposed) return;
    calOpen = !calOpen;
    calConfirm = false; calCopied = false;
    if (calOpen && !FEED_URL && !FEED_HINT) FEED_FAILED = false;  // reopening retries
    paintCal();
    if (calOpen && !FEED_URL && !FEED_HINT) {
      var token = epoch;
      fetchFeedUrl(token).then(function () { if (!disposed && calOpen) paintCal(); });
    }
  }

  function rotate() {
    return fetch("/api/admin/my-appointments/feed/rotate", { method: "POST", credentials: "same-origin" }).then(function (res) {
      if (!res.ok) throw new Error("bad-" + res.status);
      return res.json();
    }).then(function (j) {
      if (disposed) return;
      if (j && j.url) { FEED_URL = j.url; FEED_HINT = j.hint || ""; }
      calConfirm = false; calCopied = false;
      paintCal();
      toast(T("admin.queue.my_appointments.cal_rotated", null, "Listo: la dirección anterior ya no funciona."));
    }).catch(function () {
      if (disposed) return;
      calConfirm = false; paintCal();
      toast(T("admin.queue.my_appointments.cal_rotate_retry", null, "No pudimos cambiarla ahora. Probá de nuevo en un momento."));
    });
  }

  function onCalClick(e) {
    var t = e.target;
    if (!t || !t.closest) return;
    if (t.closest("[data-cal-copy]")) {
      copiar(FEED_URL || "").then(function () { if (!disposed) { calCopied = true; paintCal(); } });
    } else if (t.closest("[data-cal-google]")) {
      copiar(FEED_URL || "");
    } else if (t.closest("[data-cal-rotate]")) {
      calConfirm = true; paintCal();
    } else if (t.closest("[data-cal-rotate-no]")) {
      calConfirm = false; paintCal();
    } else if (t.closest("[data-cal-rotate-yes]")) {
      rotate();
    }
  }
  var calBtnRef = calButton(), calHostRef = calHost();
  if (calBtnRef && calBtnRef.addEventListener) calBtnRef.addEventListener("click", toggleCal);
  if (calHostRef && calHostRef.addEventListener) calHostRef.addEventListener("click", onCalClick);

  // =====================================================================
  // routing propio (ver comentario del encabezado)
  // =====================================================================

  // Sin conexión o con el servidor ocupado no hay un error que leer: la
  // pantalla sigue diciendo que está trayendo los turnos y vuelve a pedirlos
  // sola, cada vez más espaciado (1, 1, 2, 3, 5, 8… s, tope 60). El botón
  // pide ya, sin esperar el próximo intento.
  var RETRY_STEPS = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 60];
  var retryN = 0, retryTimer = null;
  function clearRetry() { if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; } }
  function route() {
    if (disposed) return;
    clearRetry();
    var token = ++epoch;
    return fetchTurnos(false, token).then(function (data) { retryN = 0; render(data, token); }).catch(function (e) {
      if (!owns(token) || String(e.message) === "redirect" || String(e.message) === "stale") return;
      var sec = host();
      if (!sec) return;
      if (String(e.message) === "403") {
        sec.innerHTML = '<p class="adm-empty">' + T("admin.queue.common.no_permission", null, "No tenés permiso para ver esta sección.") + "</p>";
        return;
      }
      sec.innerHTML = '<div class="mt-loading" role="status"><p>' + esc(T("admin.queue.my_appointments.reconnecting", null, "Estamos trayendo tus turnos. Lo intentamos de nuevo solos en unos segundos."))
        + '</p><button type="button" class="btn" data-retry>' + esc(T("admin.queue.my_appointments.retry_now", null, "Intentar ahora")) + "</button></div>";
      var retry = sec.querySelector("[data-retry]");
      if (retry) retry.addEventListener("click", route, { once: true });
      var wait = RETRY_STEPS[Math.min(retryN++, RETRY_STEPS.length - 1)] * 1000;
      retryTimer = setTimeout(function () { retryTimer = null; if (owns(token)) route(); }, wait);
    });
  }
  function routeDependencies() { return Promise.all([i18nReady, datesReady, formatReady, tableReady, sessionReady]); }
  function readyRoute() { return routeDependencies().then(function () { return disposed ? undefined : route(); }); }
  var dispose = function () {
    if (disposed) return;
    disposed = true;
    titleTools("");
    epoch++;
    clearRetry();
    if (calBtnRef && calBtnRef.removeEventListener) calBtnRef.removeEventListener("click", toggleCal);
    if (calHostRef && calHostRef.removeEventListener) calHostRef.removeEventListener("click", onCalClick);
    if (ANCHOS) ANCHOS.dispose();
    if (COLPICK) COLPICK.dispose();
    if (MOUNTS.get(root) === dispose) MOUNTS.delete(root);
  };
  // The page's load deadline ends when the dependencies are in; the first
  // read after that is the screen's own waiting and failure state.
  dispose.loaded = routeDependencies().then(function () { return true; }, function () { return false; });
  dispose.ready = readyRoute();
  MOUNTS.set(root, dispose);
  return dispose;
}
