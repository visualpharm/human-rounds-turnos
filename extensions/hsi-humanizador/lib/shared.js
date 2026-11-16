/*
 * shared.js — the pieces the three contexts agree on: settings, the HSI
 * presets and the pure matching helpers. Classic script (no import/export):
 * it loads as a content script, inside the options page, through
 * importScripts() in the service worker and through require() in node --test.
 *
 * Nothing in this extension logs or sends patient data anywhere: a turno
 * travels Turnos tab -> service worker memory -> HSI tab, and is dropped the
 * moment the job ends.
 */
(function (root) {
  "use strict";

  // The tutorial video the popup always links to. Left empty, the link stays
  // hidden, never a dead end.
  var VIDEO_DEMO = "https://youtu.be/CUzm7CvrNhg";

  // The Province's HSI is where the desks work, so it comes first and is the
  // default; the test copy is only ever an explicit choice.
  var PRESETS = [
    { id: "provincia", nombre: "HSI de la Provincia de Buenos Aires", url: "https://shc.ms.gba.gov.ar" },
    { id: "prueba", nombre: "HSI de prueba", url: "http://localhost:8280" }
  ];

  // Each center is its own institution in HSI, with its own number. Numbers
  // from the reference-deployment counterpart, the municipality's HSI administrator (2026-10-27), for the
  // Province's HSI only. `centros` holds the Human Rounds
  // center id first and its name second: a row's center picks its institution
  // by itself. A center that is not here is asked for once and remembered.
  var INSTITUCIONES = {
    "https://shc.ms.gba.gov.ar": [
      { id: "1086", nombre: "Hospital de Pinamar", centros: ["hospital-pinamar", "Hospital de Pinamar"] },
      { id: "1068", nombre: "CAPS Pinamar", centros: ["caps-pinamar", "CAPS Pinamar"] },
      { id: "1073", nombre: "CAPS Ostende", centros: ["caps-ostende", "CAPS Ostende"] },
      { id: "1077", nombre: "CAPS Valeria del Mar", centros: ["caps-valeria", "CAPS Valeria del Mar"] },
      { id: "1084", nombre: "CAPS DiabetPin", centros: ["diabetpin", "CAPS DiabetPin"] },
      { id: "1082", nombre: "Discapacidad", centros: ["discapacidad", "Coordinación de Discapacidad y Centro de Día"] }
    ]
  };

  // Human Rounds sites the manifest does not name. A new required host would
  // make Chrome switch the extension off for everyone who already has it until
  // they accept again, so these ride on optional_host_permissions: the
  // operator allows one in the settings page or the popup, and the row script
  // is registered for its admin at runtime.
  var TURNOS_OPCIONALES = [
    { id: "salud", nombre: "salud.pinamar.gob.ar", url: "https://salud.pinamar.gob.ar" }
  ];

  // guardarSolo only ever becomes true by the operator's own "sí", offered
  // after HSI itself confirmed a save they pressed. guardados counts those
  // confirmed saves and paces the offer.
  var DEFAULTS = {
    hsiUrl: PRESETS[0].url,
    institucionId: "",      // the operator's choice; "" = by the row's center
    institucionPorCentro: {}, // center key -> number the operator picked in HSI
    modo: "boton",          // manual | boton | auto
    guardarSolo: false,
    guardados: 0,
    mostrarPasos: true,
    cargarAlAprobar: false, // a turno just approved or created goes to HSI by itself
    turnosApagados: [],     // ids of TURNOS_OPCIONALES the operator unticked
    turnosPropio: ""        // another Human Rounds address, the operator's own
  };

  // The offer comes back on the 1st, 2nd, 3rd, 5th, 8th and 13th confirmed
  // save, then every 20th — often while the habit forms, rarely afterwards.
  function debeOfrecer(guardados) {
    var n = Number(guardados) || 0;
    if (n < 1) return false;
    if ([1, 2, 3, 5, 8, 13].indexOf(n) !== -1) return true;
    return n >= 20 && n % 20 === 0;
  }

  var MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  var DIAS = ["domingo", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado"];

  // "2026-10-14" -> local-midnight Date; never new Date("2026-10-14"), which
  // is UTC and lands on the 13th in Argentina.
  function fecha(iso) {
    var p = String(iso || "").split("-");
    if (p.length !== 3) return null;
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    return isNaN(d.getTime()) ? null : d;
  }

  function lunesDe(d) {
    var l = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    l.setDate(l.getDate() - ((l.getDay() + 6) % 7));
    return l;
  }

  // How many times to press «Próximo» (negative: «Anterior») to get from the
  // week on screen to the turno's week.
  function semanasHasta(iso, desde) {
    var d = fecha(iso);
    if (!d || !desde) return null;
    return Math.round((lunesDe(d) - lunesDe(desde)) / (7 * 24 * 3600 * 1000));
  }

  // The week on screen, read off the grid's own heading: "lunes, 2 de
  // noviembre de 2026 - domingo, 8 de noviembre de 2026" -> its first day.
  function fechaDeTitulo(titulo) {
    var m = norm(titulo).match(/(\d{1,2}) de ([a-z]+) de (\d{4})/);
    if (!m) return null;
    var mes = MESES.indexOf(m[2] === "setiembre" ? "septiembre" : m[2]);
    return mes === -1 ? null : new Date(Number(m[3]), mes, Number(m[1]));
  }

  // Which column of the week grid is the day: headers read "Miércoles oct 14".
  function columnaDelDia(iso, encabezados) {
    var d = fecha(iso);
    if (!d) return -1;
    var dia = DIAS[d.getDay()];
    for (var i = 0; i < encabezados.length; i++) {
      var t = norm(encabezados[i]);
      var numero = (t.match(/(\d{1,2})\s*$/) || [])[1];
      if (t.indexOf(dia) === 0 && Number(numero) === d.getDate()) return i;
    }
    return -1;
  }

  // Which row is the hour: the label column reads "07:00hs.", "07:30hs."…
  function filaDeLaHora(hora, etiquetas) {
    var h = String(hora || "").slice(0, 5);
    for (var i = 0; i < etiquetas.length; i++) {
      if (String(etiquetas[i]).trim().indexOf(h) === 0) return i;
    }
    return -1;
  }

  // The dialog's own heading is the proof of the slot that was opened:
  // "miércoles, 14 de octubre de 2026 ● 07:00hs."
  function esElHorario(iso, hora, titulo) {
    var d = fecha(iso);
    if (!d) return false;
    var t = norm(titulo);
    var dia = d.getDate() + " de " + MESES[d.getMonth()] + " de " + d.getFullYear();
    return t.indexOf(dia) !== -1 && t.indexOf(norm(String(hora).slice(0, 5))) !== -1;
  }

  // An agenda option ends in its validity: "… 09/03/2026 - 31/01/2030".
  function agendaVigente(iso, opcion) {
    var d = fecha(iso);
    var m = String(opcion).match(/(\d{2})\/(\d{2})\/(\d{4})\s*-\s*(\d{2})\/(\d{2})\/(\d{4})/);
    if (!d || !m) return true;
    var desde = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
    var hasta = new Date(Number(m[6]), Number(m[5]) - 1, Number(m[4]));
    return d >= desde && d <= hasta;
  }

  function norm(s) {
    return String(s == null ? "" : s)
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }

  var TITULOS = { dr: 1, dra: 1, doctor: 1, doctora: 1, lic: 1, licenciado: 1, licenciada: 1, de: 1, del: 1, la: 1, las: 1, los: 1, y: 1 };

  function tokens(s) {
    return norm(s).split(" ").filter(function (t) { return t && !TITULOS[t]; });
  }

  // Turnos writes "Andrea Funes", HSI lists "FUNES ANDREA SOLEDAD": the
  // order and the middle names differ, the words do not.
  function coincidencia(buscado, candidato) {
    var a = tokens(buscado), b = tokens(candidato);
    if (!a.length || !b.length) return 0;
    var hits = a.filter(function (t) { return b.indexOf(t) !== -1; }).length;
    return hits / a.length;
  }

  // The one candidate that matches clearly better than every other, or null:
  // an ambiguous match is the operator's call, never a guess.
  function mejor(buscado, candidatos, texto, minimo) {
    var piso = minimo == null ? 0.6 : minimo;
    var puntuados = candidatos.map(function (c) {
      return { c: c, p: coincidencia(buscado, texto ? texto(c) : c) };
    }).filter(function (x) { return x.p >= piso; });
    if (!puntuados.length) return null;
    puntuados.sort(function (x, y) { return y.p - x.p; });
    if (puntuados.length > 1 && puntuados[1].p === puntuados[0].p) return null;
    return puntuados[0].c;
  }

  function soloDigitos(s) {
    return String(s == null ? "" : s).replace(/\D/g, "");
  }

  // M / F / X as Turnos stores it -> the label of HSI's radio.
  function sexoHsi(sexo) {
    var k = norm(sexo).charAt(0);
    if (k === "m") return "Masculino";
    if (k === "f") return "Femenino";
    if (k === "x") return "X";
    return null;
  }

  // "contacto" is one free field in Turnos: a phone or an e-mail.
  function contacto(valor) {
    var v = String(valor == null ? "" : valor).trim();
    if (!v) return { telefono: "", email: "" };
    if (v.indexOf("@") !== -1) return { telefono: "", email: v };
    return { telefono: soloDigitos(v), email: "" };
  }

  // HSI asks for the phone in two boxes, area code and number. An Argentine
  // number is 10 digits once the 0 and the 15 are gone; the area code is 2, 3
  // or 4 of them. Without a known area code the split stays the operator's.
  function telefonoPartes(telefono, prefijosConocidos) {
    var d = soloDigitos(telefono);
    if (d.indexOf("54") === 0 && d.length > 10) d = d.slice(2);
    if (d.charAt(0) === "9" && d.length === 11) d = d.slice(1);
    if (d.charAt(0) === "0") d = d.slice(1);
    var conocidos = prefijosConocidos || ["2254", "2267", "2255", "2257", "2252", "223", "11"];
    var i;
    // The mobile "15" people still write after the area code: 2254 15-401234.
    for (i = 0; d.length === 12 && i < conocidos.length; i++) {
      if (d.indexOf(conocidos[i] + "15") === 0) d = conocidos[i] + d.slice(conocidos[i].length + 2);
    }
    if (d.length !== 10) return { prefijo: "", numero: d };
    for (i = 0; i < conocidos.length; i++) {
      if (d.indexOf(conocidos[i]) === 0) return { prefijo: conocidos[i], numero: d.slice(conocidos[i].length) };
    }
    return { prefijo: "", numero: d };
  }

  function origen(url) {
    try { return new URL(url).origin; } catch (e) { return ""; }
  }

  function patronDeOrigen(url) {
    var o = origen(url);
    return o ? o + "/*" : "";
  }

  // ---- which HSI institution a turno goes to -------------------------------

  function institucionesDe(hsiUrl) {
    return INSTITUCIONES[origen(hsiUrl)] || [];
  }

  // The key a center is remembered by: its id when the row carries it, its
  // name otherwise.
  function claveCentro(turno) {
    return norm((turno && (turno.centroId || turno.centro)) || "");
  }

  function institucionDelCentro(hsiUrl, turno) {
    var id = norm(turno && turno.centroId), nombre = norm(turno && turno.centro);
    if (!id && !nombre) return "";
    var lista = institucionesDe(hsiUrl);
    for (var i = 0; i < lista.length; i++) {
      var claves = lista[i].centros.map(norm);
      if ((id && claves.indexOf(id) !== -1) || (nombre && claves.indexOf(nombre) !== -1)) return lista[i].id;
    }
    return "";
  }

  // The number the row itself carries: Human Rounds knows each center's
  // institution («Institución en HSI» in its panel, 2026-10-27). Those are the
  // real HSI's numbers, so they hold everywhere but on the test copy.
  function institucionDeLaFila(hsiUrl, turno) {
    var n = soloDigitos(turno && turno.institucion);
    var prueba = PRESETS.filter(function (p) { return p.id === "prueba"; })[0];
    return n && !(prueba && origen(hsiUrl) === origen(prueba.url)) ? n : "";
  }

  // The number to open in HSI for this turno, or "" (ask the operator):
  // the center's own number first — the one the row carries, then this
  // extension's table for sites that do not send it yet; a CAPS turno belongs
  // in that CAPS, whoever loads it — then the operator's choice in the
  // settings, then the number they picked in HSI for this center before.
  function institucionPara(ajustes, turno) {
    var a = ajustes || {};
    var propia = institucionDeLaFila(a.hsiUrl, turno) || institucionDelCentro(a.hsiUrl, turno);
    if (propia) return propia;
    if (soloDigitos(a.institucionId)) return soloDigitos(a.institucionId);
    var clave = claveCentro(turno);
    return clave ? soloDigitos((a.institucionPorCentro || {})[clave]) : "";
  }

  // ---- where the turnos are seen: ONE place ----------------------------------
  // «¿Dónde ves los turnos?» is a single choice: a known site's id, or "otra"
  // with the operator's own address. Settings saved by the old checkboxes map
  // onto it: an address of their own wins (they typed it), else the first site.
  function turnosElegido(ajustes) {
    var a = ajustes || {};
    if (origen(a.turnosPropio)) return "otra";
    return TURNOS_OPCIONALES.length ? TURNOS_OPCIONALES[0].id : "otra";
  }

  // The institution number in an HSI address: …/institucion/1086/turnos.
  function institucionDeUrl(url) {
    var m = String(url || "").match(/\/institucion\/(\d+)/);
    return m ? m[1] : "";
  }

  // ---- the reduced form that may touch disk -------------------------------
  // The recent list lives in chrome.storage.local, which survives the browser:
  // only this shape ever reaches it — never a full name, never a full DNI.

  // "Marta Pruebauno" -> "Marta P."; a lone name stays whole.
  function nombreCorto(paciente) {
    var partes = String(paciente || "").trim().split(/\s+/).filter(Boolean);
    if (!partes.length) return "";
    return partes.length === 1 ? partes[0] : partes[0] + " " + partes[partes.length - 1].charAt(0) + ".";
  }

  // "90100101" -> "…101": enough to recognize the row, not enough to identify
  // anyone. Shorter fragments stay as they are.
  function dniCorto(dni) {
    var d = soloDigitos(dni);
    return d.length > 3 ? "…" + d.slice(-3) : d;
  }

  // A message that will be kept on disk: any document-like run of digits (7 or
  // more, with or without the dots people type) collapses, so a pasted DNI or
  // phone can never leak into it.
  function sinDnis(texto) {
    return String(texto || "")
      .replace(/\b\d{1,3}(?:\.\d{3}){2,}\b/g, "…")
      .replace(/\d{7,}/g, "…");
  }

  // ---- what auto mode and the patient alta need ----------------------------

  // The one known turno with that DNI, or null: two rows with the same DNI is
  // the operator's call, never a guess.
  function turnoPorDni(turnos, dni) {
    var objetivo = soloDigitos(dni);
    if (!objetivo) return null;
    var hallados = (turnos || []).filter(function (t) {
      return soloDigitos(t.dni) === objetivo;
    });
    return hallados.length === 1 ? hallados[0] : null;
  }

  // "Marta Pruebauno" -> primer nombre + primer apellido, the boxes HSI's
  // patient alta asks for. The first word is the name, the rest is the surname.
  function nombreYApellido(paciente) {
    var partes = String(paciente || "").trim().split(/\s+/).filter(Boolean);
    if (!partes.length) return { nombre: "", apellido: "" };
    return { nombre: partes[0], apellido: partes.slice(1).join(" ") };
  }

  // "1990-05-01" -> "01051990": the ddMMyyyy HSI's date box types as you go.
  function fechaDdmmaaaa(iso) {
    var d = fecha(iso);
    if (!d) return "";
    function dos(n) { return String(n).length === 1 ? "0" + n : String(n); }
    return dos(d.getDate()) + dos(d.getMonth() + 1) + d.getFullYear();
  }

  // What the patient alta needs and the row does not carry. DNI is checked
  // before anything reaches here.
  function faltantesParaAlta(turno) {
    var faltan = [];
    if (!String(turno.paciente || "").trim()) faltan.push("el nombre y apellido");
    if (!sexoHsi(turno.sexo)) faltan.push("el sexo");
    if (!fecha(turno.nacimiento)) faltan.push("la fecha de nacimiento");
    return faltan;
  }

  // What Human Rounds changed in a turno this computer already put in HSI,
  // and HSI still has the old way. `registro` is what was loaded (id -> {dia,
  // hora, medico, cancelado, presente}), `cambios` what the panel answers now
  // (`/api/admin/day-queue/changes`). One step per turno, the first that
  // applies: cancelled, then moved (day, hour or doctor), then present. A
  // turno whose old place is already free in HSI (cancelled there) and is
  // approved again only needs «crear»: loaded like any turno. A turno that is
  // already as HSI has it gives nothing.
  //
  // `ahora` ("YYYY-MM-DD HH:MM", the installation's hour) leaves out what HSI
  // no longer changes: a place that has passed is never cancelled, moved or
  // loaded (2026-11-05: three cancelled turnos of past days stayed in the
  // panel for good). A move keeps the half that is still ahead: an old place
  // that passed only loads the new one, a move into the past only frees the
  // old one. Presence is marked until that day ends. A change somebody hid
  // («Ocultar») stays out until the turno changes again: `oculto` from the
  // panel, or this computer's own mark in the registry.
  function cambiosPorPasar(registro, cambios, ahora) {
    var pendientes = [];
    (cambios || []).forEach(function (c) {
      var antes = registro && registro[c.id];
      var t = c.turno;
      if (!antes || !t) return;
      if (c.oculto || (antes.oculto && antes.oculto === firmaDeCambio(c))) return;
      var base = { id: c.id, estado: c.estado, antes: antes, turno: t };
      var viejoPaso = yaPaso(antes.dia, antes.hora, ahora), nuevoPaso = yaPaso(t.dia_iso, t.hora, ahora);
      if (c.estado === "cancelada") {
        if (!antes.cancelado && !viejoPaso) pendientes.push(Object.assign(base, { accion: "cancelar" }));
        return;
      }
      if (c.estado !== "aprobada") return;
      if (antes.cancelado) {
        if (!nuevoPaso) pendientes.push(Object.assign(base, { accion: "crear" }));
        return;
      }
      var mudo = t.dia_iso !== antes.dia || t.hora !== antes.hora ||
        (antes.medico && (t.medico || "") !== antes.medico);
      if (mudo) {
        if (!viejoPaso && !nuevoPaso) pendientes.push(Object.assign(base, { accion: "mover" }));
        else if (!nuevoPaso) pendientes.push(Object.assign(base, { accion: "crear", movido: true }));
        else if (!viejoPaso) pendientes.push(Object.assign(base, { accion: "cancelar", movido: true }));
      } else if (t.asistencia === "presente" && !antes.presente && !yaPaso(antes.dia, "", ahora)) {
        pendientes.push(Object.assign(base, { accion: "presente" }));
      }
    });
    return pendientes;
  }

  // Whether a place is behind `ahora` ("YYYY-MM-DD HH:MM"): an earlier day, or
  // today before this minute. Without an hour only an earlier day; without
  // `ahora` nothing has passed.
  function yaPaso(dia, hora, ahora) {
    if (!dia || !ahora) return false;
    var hoy = String(ahora).slice(0, 10);
    if (dia !== hoy) return dia < hoy;
    var m = String(hora || "").match(/^(\d{1,2}):(\d{2})/);
    if (!m) return false;
    return (m[1].length === 1 ? "0" + m[1] : m[1]) + ":" + m[2] < String(ahora).slice(11, 16);
  }

  // A change HSI refused, looked at again at the hour of the refusal: true
  // when nothing of it is left to do because its places have passed. HSI
  // never changes a past turno, and that is no error of the desk.
  function cambioPasado(c, ahora) {
    var registro = {}; registro[c.id] = c.antes;
    return !cambiosPorPasar(registro, [{ id: c.id, estado: c.estado, turno: c.turno }], ahora).length;
  }

  // What a hidden change is: its state, place, doctor and attendance. Any of
  // them changing is a new change for HSI.
  function firmaDeCambio(c) {
    var t = c.turno || {};
    return [c.estado || "", t.dia_iso || "", t.hora || "", t.medico || "", t.asistencia || ""].join("|");
  }

  // "YYYY-MM-DD HH:MM" a while after the one the panel said. The text is read
  // as a clock face, so the installation's own time zone travels untouched.
  function ahoraMas(ahora, ms) {
    var m = String(ahora || "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/);
    if (!m) return "";
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) + (ms || 0));
    return d.toISOString().slice(0, 10) + " " + d.toISOString().slice(11, 16);
  }

  // The computer's own clock, same shape, for a panel that does not say the hour.
  function ahoraLocal(d) {
    d = d || new Date();
    function dos(n) { return n < 10 ? "0" + n : String(n); }
    return d.getFullYear() + "-" + dos(d.getMonth() + 1) + "-" + dos(d.getDate()) + " " + dos(d.getHours()) + ":" + dos(d.getMinutes());
  }

  // The turno as the loader reads it, from a row of the changes answer (the
  // same fields the day queue gives, flat). `lugar` = the day, hour and doctor
  // to work on: the new ones to load a turno, the old ones (what HSI holds)
  // to cancel it or mark it present.
  function turnoDeCambio(t, lugar) {
    lugar = lugar || {};
    return {
      id: t.id, dia: lugar.dia || t.dia_iso, hora: lugar.hora || t.hora, paciente: t.paciente || "",
      dni: soloDigitos(t.dni), sexo: t.sexo || "", contacto: t.contacto || "",
      nacimiento: t.nacimiento || "", cobertura: t.cobertura || "", servicio: t.servicio || "",
      medico: lugar.medico || t.medico || "", especialidad: t.especialidad || "",
      centro: t.centro || "", centroId: t.centro_id || "",
      institucion: soloDigitos(t.hsi_institucion)
    };
  }

  // A turno drawn on the week grid starts with its hour: "10:00 PRUEBAUNO,
  // MARTA." -> "10:00". Anything else gives "".
  function horaDeEvento(texto) {
    var m = String(texto || "").match(/^\s*(\d{1,2}):(\d{2})/);
    return m ? (m[1].length === 1 ? "0" + m[1] : m[1]) + ":" + m[2] : "";
  }

  // The turno window names its patient in the subtitle: "DNI 90100101 - ID 1".
  // The grid never shows a document, so this is the proof of WHOSE turno
  // was opened.
  function dniDeVentana(texto) {
    var m = String(texto || "").match(/DNI\s*([\d.]+)/i);
    return m ? soloDigitos(m[1]) : "";
  }

  // "2026-10-29", "09:00" -> "29/10 09:00": a place as the desk says it.
  function diaYHora(dia, hora) {
    var d = fecha(dia);
    if (!d) return String(hora || "");
    function dos(n) { return String(n).length === 1 ? "0" + n : String(n); }
    return dos(d.getDate()) + "/" + dos(d.getMonth() + 1) + (hora ? " " + hora : "");
  }

  // What this computer remembers of a turno it put in HSI — a place, never a
  // person: day, hour and doctor. It is what a later change is compared with.
  function lugarDeTurno(turno) {
    return { dia: turno.dia, hora: turno.hora, medico: turno.medico || "" };
  }

  var api = {
    cambiosPorPasar: cambiosPorPasar, turnoDeCambio: turnoDeCambio, lugarDeTurno: lugarDeTurno, diaYHora: diaYHora,
    yaPaso: yaPaso, cambioPasado: cambioPasado, firmaDeCambio: firmaDeCambio, ahoraMas: ahoraMas, ahoraLocal: ahoraLocal,
    horaDeEvento: horaDeEvento, dniDeVentana: dniDeVentana,
    PRESETS: PRESETS, DEFAULTS: DEFAULTS, VIDEO_DEMO: VIDEO_DEMO,
    TURNOS_OPCIONALES: TURNOS_OPCIONALES, INSTITUCIONES: INSTITUCIONES,
    institucionesDe: institucionesDe, claveCentro: claveCentro,
    institucionDelCentro: institucionDelCentro, institucionDeLaFila: institucionDeLaFila,
    institucionPara: institucionPara,
    institucionDeUrl: institucionDeUrl, turnosElegido: turnosElegido,
    norm: norm, tokens: tokens, coincidencia: coincidencia, mejor: mejor,
    soloDigitos: soloDigitos, sexoHsi: sexoHsi, contacto: contacto,
    telefonoPartes: telefonoPartes, origen: origen, patronDeOrigen: patronDeOrigen,
    nombreCorto: nombreCorto, dniCorto: dniCorto, sinDnis: sinDnis,
    turnoPorDni: turnoPorDni, nombreYApellido: nombreYApellido,
    fechaDdmmaaaa: fechaDdmmaaaa, faltantesParaAlta: faltantesParaAlta,
    debeOfrecer: debeOfrecer, fecha: fecha, semanasHasta: semanasHasta, fechaDeTitulo: fechaDeTitulo,
    columnaDelDia: columnaDelDia, filaDeLaHora: filaDeLaHora,
    esElHorario: esElHorario, agendaVigente: agendaVigente
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HRShared = api;
})(typeof self !== "undefined" ? self : this);
