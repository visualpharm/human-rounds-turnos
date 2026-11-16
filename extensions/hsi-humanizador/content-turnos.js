/*
 * content-turnos.js — puts «Cargar en HSI» on every row of «Pacientes de
 * hoy» and shows, on that same row, how the load ended.
 *
 * The turno's data comes from the panel's own endpoint with the operator's
 * session (same origin, no new access); the row only gives the id. Which rows
 * were already loaded is remembered by id alone — never a name or a document.
 *
 * Manual mode turns the same button into «Copiar DNI»: the document lands on
 * the operator's clipboard and HSI opens; nothing is ever typed into HSI.
 * In every mode the list just read also travels, in memory only, to the
 * service worker — it is what auto mode looks a typed DNI up against.
 *
 * Besides the day queue, this page asks the panel for the changes of the
 * turnos it loaded (GET /api/admin/day-queue/changes, same session, ids only):
 * on load, every five minutes and when the tab comes back.
 */
(function () {
  "use strict";

  // A site allowed while its tab was open gets this script injected there and
  // then registered: one copy per page, never two buttons per row.
  if (self.__hrTurnos) return;
  self.__hrTurnos = true;

  var S = self.HRShared;
  var HECHOS = "turnosCargados";
  var estados = {};   // id -> { estado, detalle }
  var hechos = {};    // id -> timestamp of the confirmed save
  var modo = "boton"; // manual | boton | auto
  var alAprobar = false; // «Cargar en HSI apenas apruebo o creo un turno»

  // What happened to a turno after it reached HSI (cancelled, moved, attended)
  // is passed on from a panel of changes, one press each. This computer
  // remembers WHERE each turno it loaded sits in HSI — day, hour, doctor,
  // never a name or a document — and compares it with the panel's answer.
  var REGISTRO = "turnosEnHsi";
  var VIGENCIA_REGISTRO = 60 * 24 * 3600 * 1000;
  var registro = {};  // id -> { dia, hora, medico, cancelado?, presente?, visto }
  var lugares = {};   // id -> the place of a load in progress
  var acciones = {};  // id -> { c, paso, activo, texto, error } change being passed
  var pendientes = []; // what the panel still has to pass to HSI
  var listos = {};    // id -> text of a change just passed, shown for a moment
  var CAMBIOS_MAX = 100;
  // The installation's hour as the panel last said it, and when: what has
  // passed is judged by it, not by this computer's clock.
  var reloj = { ahora: "", en: 0 };
  var OCULTAR = "/api/admin/day-queue/changes/{id}/hide";

  var TEXTO = {
    reposo: "Cargar en HSI",
    abriendo: "Abriendo HSI…",
    cargando: "Cargando en HSI…",
    esperando: "Confirmá en HSI",
    guardado: "Guardado en HSI",
    manual: "DNI copiado",
    alta: "Terminá el alta en HSI",
    error: "Cargar en HSI",
    "en-cola": "Envío automático a HSI"
  };
  // A turno the extension sent by itself says so while it travels.
  var AUTO = "Envío automático a HSI";
  var EN_COLA = "Va a HSI apenas termine el turno que se está cargando.";

  function textoReposo() {
    return modo === "manual" ? "Copiar DNI" : TEXTO.reposo;
  }

  // HSI takes one turno at a time (background.js refuses a second load while
  // one is open). What is in the middle of a load, and what still waits.
  var ACTIVOS = ["abriendo", "cargando", "esperando", "alta", "preguntando"];
  var disparados = {}; // id -> true: sent by itself, from a just-made turno
  var enCola = [];     // [{ id, dia }] auto turnos waiting for the current load

  function ocupado() {
    return Object.keys(estados).some(function (k) { return ACTIVOS.indexOf(estados[k].estado) !== -1; }) ||
      Object.keys(acciones).some(function (k) { return acciones[k].activo; });
  }

  // A button that names its own day («Turno creado» in «Crear turno», a turno
  // for any day) reads that day's queue; a row reads the one on screen.
  function ultimaConsulta(dia) {
    if (dia && /^\d{4}-\d{2}-\d{2}$/.test(dia)) return "/api/admin/day-queue?dia=" + dia;
    var recursos = performance.getEntriesByType("resource");
    for (var i = recursos.length - 1; i >= 0; i--) {
      var u = new URL(recursos[i].name);
      if (u.pathname === "/api/admin/day-queue") return u.pathname + u.search;
    }
    return "/api/admin/day-queue";
  }

  // The one network access of this whole extension: the panel's own
  // day-queue, same origin, with the operator's session.
  function buscarCola(dia) {
    return fetch(ultimaConsulta(dia), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("http"); return r.json(); });
  }

  // The shape auto mode needs, no more: patient, contact, coverage — in
  // memory only, never on disk.
  function reducido(t, g) {
    return {
      id: t.id, dia: t.dia_iso, hora: t.hora, paciente: t.paciente || "",
      dni: S.soloDigitos(t.dni), sexo: t.sexo || "", contacto: t.contacto || "",
      cobertura: t.cobertura || ""
    };
  }

  function reportarCola(cola) {
    if (!chrome.runtime || !chrome.runtime.id) return;
    var lista = [];
    var grupos = (cola && cola.grupos) || [];
    for (var i = 0; i < grupos.length; i++) {
      var turnos = grupos[i].turnos || [];
      for (var j = 0; j < turnos.length; j++) lista.push(reducido(turnos[j], grupos[i]));
    }
    chrome.runtime.sendMessage({ tipo: "cola", turnos: lista }).catch(function () {});
  }

  function buscarTurno(id, dia) {
    return buscarCola(dia).then(function (cola) {
      if (!dia) reportarCola(cola);
      var grupos = cola.grupos || [];
      for (var i = 0; i < grupos.length; i++) {
        var g = grupos[i], turnos = g.turnos || [];
        for (var j = 0; j < turnos.length; j++) {
          var t = turnos[j];
          if (String(t.id) !== String(id)) continue;
          return {
            id: t.id, dia: t.dia_iso, hora: t.hora, paciente: t.paciente || "",
            dni: S.soloDigitos(t.dni), sexo: t.sexo || "", contacto: t.contacto || "",
            nacimiento: t.nacimiento || "", cobertura: t.cobertura || "", servicio: t.servicio || "",
            medico: g.medico || "", especialidad: g.especialidad || "", centro: t.centro || g.centro || "",
            centroId: t.centro_id || "",
            // The center's own institution in HSI, as Human Rounds knows it
            // ("" on a site that does not send it yet).
            institucion: S.soloDigitos(t.hsi_institucion)
          };
        }
      }
      return null;
    });
  }

  function pintar(boton, id) {
    var e = estados[id] || (hechos[id] ? { estado: "guardado" } : { estado: "reposo" });
    boton.textContent = e.estado === "reposo" ? textoReposo()
      : (disparados[id] && (e.estado === "abriendo" || e.estado === "cargando")) ? AUTO
        : (TEXTO[e.estado] || textoReposo());
    boton.dataset.estado = e.estado;
    boton.dataset.hrVisto = "1";
    boton.title = e.detalle || (e.estado === "en-cola" ? EN_COLA : "");
    // A saved turno stays locked: a second load would be a second turno. A
    // row inside the patient alta stays locked too: its job is waiting for
    // the operator's «Guardar» and goes on by itself afterwards.
    boton.disabled = e.estado === "abriendo" || e.estado === "cargando" || e.estado === "guardado" || e.estado === "alta";
    var aviso = boton.nextElementSibling;
    if (aviso && aviso.classList.contains("hr-hsi-aviso")) {
      aviso.textContent = (e.estado === "error" || e.estado === "alta") ? (e.detalle || "") : "";
    }
  }

  function repintar(id) {
    document.querySelectorAll('[data-hr-hsi="' + CSS.escape(String(id)) + '"]').forEach(function (b) { pintar(b, id); });
  }

  function poner(estadoId, estado, detalle) {
    estados[estadoId] = { estado: estado, detalle: detalle || "" };
    repintar(estadoId);
    // A load ended (saved, failed, given up): the next waiting turno goes.
    if (ACTIVOS.indexOf(estado) === -1 && estado !== "en-cola") setTimeout(siguiente, 0);
  }

  function siguiente() {
    if (ocupado() || !enCola.length) return;
    var t = enCola.shift();
    if (hechos[t.id] || !estados[t.id] || estados[t.id].estado !== "en-cola") return siguiente();
    alTocar(t.id, t.dia);
  }

  // A turno just approved or created (the panel marks its control with
  // `data-hr-hsi-auto`) goes by itself when the operator chose so — once per
  // page, never one already saved, never in «Solo copiar el DNI» (copying
  // needs the operator's own click).
  var saltados = {}; // id -> true: on screen while the choice was off
  function enviarSolos() {
    var solos = alAprobar && modo !== "manual";
    document.querySelectorAll("[data-hr-hsi][data-hr-hsi-auto]").forEach(function (b) {
      var id = b.getAttribute("data-hr-hsi");
      if (!id || disparados[id] || saltados[id] || hechos[id] || estados[id]) return;
      if (!solos) { saltados[id] = true; return; }
      disparados[id] = true;
      if (ocupado() || enCola.length) {
        enCola.push({ id: id, dia: b.getAttribute("data-hr-hsi-dia") });
        poner(id, "en-cola");
      } else {
        alTocar(id, b.getAttribute("data-hr-hsi-dia"));
      }
    });
  }

  // No row may stay on «Abriendo HSI…»: if HSI has not answered in time the
  // button comes back, with the reason, ready to be pressed again.
  var SIN_RESPUESTA = "No pude hablar con HSI: abrí HSI y probá de nuevo.";
  var relojes = {};

  function vigilar(id, ms) {
    clearTimeout(relojes[id]);
    relojes[id] = setTimeout(function () {
      var e = estados[id];
      if (e && e.estado === "abriendo") poner(id, "error", SIN_RESPUESTA);
    }, ms);
  }

  // What HSI may need and the row does not carry is asked HERE, the moment
  // the button is pressed and the operator is still looking at the row —
  // never later, in the middle of HSI (owner, 2026-10-09). The answer goes
  // into the load and back to Human Rounds, so it is asked only once.
  var GUARDAR_DATOS = "/api/admin/requests/{id}/patient-data";

  // The birth date is NOT asked here: HSI wants it only to create a patient
  // it does not have, and then the panel asks for it in the alta itself
  // (content-hsi.js pedirDatosDelAlta). An existing patient loads without it.
  function camposQueFaltan(turno) {
    var campos = [];
    if (!String(turno.paciente || "").trim()) campos.push({ clave: "paciente", etiqueta: "Nombre y apellido", tipo: "text" });
    if (!S.sexoHsi(turno.sexo)) campos.push({ clave: "sexo", etiqueta: "Sexo", opciones: [["F", "Femenino"], ["M", "Masculino"], ["X", "X"]] });
    return campos;
  }

  function preguntar(id, turno, campos) {
    return new Promise(function (ok) {
      // A repaint of the list can take the button away for a moment (the row
      // is drawn again and decorar() puts the control back a beat later): that
      // is no reason to drop the press. The question then stands at the top
      // of the page.
      var boton = document.querySelector('[data-hr-hsi="' + CSS.escape(String(id)) + '"]');
      var viejo = document.querySelector(".hr-hsi-datos");
      if (viejo) viejo.remove();
      var form = document.createElement("form");
      form.className = "hr-hsi-datos";
      var titulo = document.createElement("strong");
      // Away from its row the question names the patient it is about.
      titulo.textContent = "Falta para cargar en HSI" + (!boton && turno.paciente ? ": " + turno.paciente : "");
      form.appendChild(titulo);
      campos.forEach(function (c) {
        var fila = document.createElement("label");
        var rotulo = document.createElement("span");
        rotulo.textContent = c.etiqueta;
        fila.appendChild(rotulo);
        var entrada;
        if (c.opciones) {
          entrada = document.createElement("select");
          entrada.appendChild(document.createElement("option"));
          c.opciones.forEach(function (o) {
            var op = document.createElement("option");
            op.value = o[0];
            op.textContent = o[1];
            entrada.appendChild(op);
          });
        } else {
          entrada = document.createElement("input");
          entrada.type = c.tipo;
        }
        entrada.name = c.clave;
        entrada.required = true;
        fila.appendChild(entrada);
        form.appendChild(fila);
      });
      var botones = document.createElement("div");
      var seguir = document.createElement("button");
      seguir.type = "submit";
      seguir.className = "hr-hsi-seguir";
      seguir.textContent = "Cargar en HSI";
      var dejar = document.createElement("button");
      dejar.type = "button";
      dejar.className = "hr-hsi-dejar";
      dejar.textContent = "Cancelar";
      botones.appendChild(seguir);
      botones.appendChild(dejar);
      form.appendChild(botones);
      form.addEventListener("click", function (e) { e.stopPropagation(); });
      dejar.addEventListener("click", function () { form.remove(); ok(null); });
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var datos = {};
        campos.forEach(function (c) { datos[c.clave] = String(form.elements[c.clave].value || "").trim(); });
        if (datos.nacimiento != null && !S.fecha(datos.nacimiento)) return;
        form.remove();
        ok(datos);
      });
      // On the page itself, under the button: the table clips what overflows
      // its cells, and a repaint of the list must not take the question away.
      var r = boton ? boton.getBoundingClientRect() : { bottom: 96, right: window.innerWidth };
      form.style.top = Math.min(r.bottom + 4, window.innerHeight - 260) + "px";
      form.style.left = Math.max(8, r.right - 260) + "px";
      document.body.appendChild(form);
      var primero = form.querySelector("input, select");
      if (primero) primero.focus();
    });
  }

  // Back to Human Rounds, same origin, with the operator's session. A panel
  // that does not offer this yet (or refuses it) never stops the load.
  function guardarEnTurnos(id, datos) {
    var cuerpo = {};
    if (datos.nacimiento) cuerpo.nacimiento = datos.nacimiento;
    if (datos.sexo) cuerpo.sexo = datos.sexo;
    if (datos.paciente) {
      var partes = S.nombreYApellido(datos.paciente);
      // Only what was really given: the panel refuses an empty value.
      if (partes.nombre) cuerpo.nombre = partes.nombre;
      if (partes.apellido) cuerpo.apellido = partes.apellido;
    }
    if (!Object.keys(cuerpo).length) return Promise.resolve();
    return fetch(GUARDAR_DATOS.replace("{id}", encodeURIComponent(id)), {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo)
    }).then(function () {}, function () {});
  }

  function completar(id, turno) {
    var campos = camposQueFaltan(turno);
    if (!campos.length) return Promise.resolve(turno);
    clearTimeout(relojes[id]);
    poner(id, "preguntando");
    return preguntar(id, turno, campos).then(function (datos) {
      if (!datos) return null;
      Object.keys(datos).forEach(function (k) { turno[k] = datos[k]; });
      poner(id, "abriendo");
      vigilar(id, 15000);
      return guardarEnTurnos(id, datos).then(function () { return turno; });
    });
  }

  function alTocar(id, dia) {
    if (modo === "manual") return manualmente(id, dia);
    // The question is on screen: answering it is the next move. (If the list
    // repainted and took it away, the press starts over.)
    if (estados[id] && estados[id].estado === "preguntando" && document.querySelector(".hr-hsi-datos")) return;
    poner(id, "abriendo");
    vigilar(id, 15000);
    buscarTurno(id, dia).then(function (turno) {
      if (!turno) return poner(id, "error", "Actualizá la página y probá de nuevo.");
      if (!turno.dni) return poner(id, "error", "Este turno no tiene DNI.");
      return completar(id, turno);
    }).then(function (turno) {
      if (turno === undefined) return;
      if (turno === null) return poner(id, "reposo");
      lugares[id] = S.lugarDeTurno(turno);
      return chrome.runtime.sendMessage({ tipo: "cargar", turno: turno }).then(function (r) {
        if (!r || !r.ok) return poner(id, "error", (r && r.motivo) || SIN_RESPUESTA);
        // A tab that had to be opened also has to load HSI first.
        if (estados[id] && estados[id].estado === "abriendo") vigilar(id, r.nueva ? 45000 : 15000);
      });
    }).catch(function () {
      // The extension was updated under this page, or the list could not be read.
      poner(id, "error", chrome.runtime && chrome.runtime.id ? "No se pudo leer el turno. Actualizá la página." : "Actualizá esta página (F5) y probá de nuevo.");
    });
  }

  // Manual mode: the DNI on the clipboard (this click is the gesture the
  // clipboard needs), HSI up front, and nothing typed into it. Ever.
  function manualmente(id, dia) {
    poner(id, "abriendo");
    vigilar(id, 15000);
    buscarTurno(id, dia).then(function (turno) {
      if (!turno) return poner(id, "error", "Actualizá la página y probá de nuevo.");
      if (!turno.dni) return poner(id, "error", "Este turno no tiene DNI.");
      var copia = navigator.clipboard && navigator.clipboard.writeText
        ? navigator.clipboard.writeText(turno.dni)
        : Promise.reject(new Error("clipboard"));
      return copia.then(function () {
        return chrome.runtime.sendMessage({ tipo: "manual", turno: reducido(turno, {}) });
      }).then(function (r) {
        if (!r || !r.ok) return poner(id, "error", (r && r.motivo) || "No pude copiar el DNI.");
        poner(id, "manual");
        setTimeout(function () {
          if (estados[id] && estados[id].estado === "manual") poner(id, "reposo");
        }, 6000);
      }, function () {
        poner(id, "error", "No pude copiar el DNI. Volvé a tocar el botón.");
      });
    }).catch(function () {
      poner(id, "error", "No se pudo leer el turno. Actualizá la página.");
    });
  }

  function decorar() {
    document.querySelectorAll("tr[data-cola-row]").forEach(function (fila) {
      if (fila.querySelector("[data-hr-hsi]")) return;
      var id = fila.getAttribute("data-cola-row");
      var celda = fila.querySelector('td[data-col="paciente"]') || fila.querySelector("td:nth-child(2)") || fila.lastElementChild;
      if (!id || !celda) return;
      var caja = document.createElement("span");
      caja.className = "hr-hsi";
      var boton = document.createElement("button");
      boton.type = "button";
      boton.className = "hr-hsi-boton";
      boton.setAttribute("data-hr-hsi", id);
      var aviso = document.createElement("span");
      aviso.className = "hr-hsi-aviso";
      aviso.setAttribute("role", "status");
      caja.appendChild(boton);
      caja.appendChild(aviso);
      celda.appendChild(caja);
      pintar(boton, id);
    });
    // The panel's own control (static/platform/hsi-sync.mjs: «Turno creado»,
    // the approvals just made, an approved request) comes with its hook
    // already drawn: it only needs this computer's state on it.
    document.querySelectorAll("[data-hr-hsi]:not([data-hr-visto])").forEach(function (b) {
      pintar(b, b.getAttribute("data-hr-hsi"));
    });
    enviarSolos();
  }

  // The panel reads this to know a click on its control reaches HSI here. Not
  // `data-hr-hsi`: that is the control's own hook, and <html> carrying it
  // would be painted as one.
  document.documentElement.dataset.hrHumanizador = "1";

  document.addEventListener("click", function (e) {
    var boton = e.target.closest && e.target.closest("[data-hr-hsi]");
    if (!boton) return;
    e.preventDefault();
    e.stopPropagation();
    alTocar(boton.getAttribute("data-hr-hsi"), boton.getAttribute("data-hr-hsi-dia"));
  }, true);

  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg.tipo !== "estado") return;
    // A change being passed (cancel, move, present) has its own panel; its
    // result never lands on the row's button.
    if (acciones[msg.id] && acciones[msg.id].activo) return estadoDeCambio(msg);
    if (msg.estado === "guardado") {
      hechos[msg.id] = Date.now();
      // Ids only, and only a week of them.
      var limite = Date.now() - 7 * 24 * 3600 * 1000;
      Object.keys(hechos).forEach(function (k) { if (hechos[k] < limite) delete hechos[k]; });
      var guardar = {}; guardar[HECHOS] = hechos;
      chrome.storage.local.set(guardar);
      if (lugares[msg.id]) recordarLugar(msg.id, lugares[msg.id]);
    }
    poner(msg.id, msg.estado, msg.detalle);
  });

  // ---- Changes after the load: cancelled, moved, attended -----------------

  function guardarRegistro() {
    var limite = Date.now() - VIGENCIA_REGISTRO;
    Object.keys(registro).forEach(function (k) { if ((registro[k].visto || 0) < limite) delete registro[k]; });
    var guardar = {}; guardar[REGISTRO] = registro;
    return chrome.storage.local.set(guardar);
  }

  function recordarLugar(id, lugar) {
    registro[id] = { dia: lugar.dia, hora: lugar.hora, medico: lugar.medico, visto: Date.now() };
    guardarRegistro();
    setTimeout(revisar, 0);
  }

  var ultimaRevision = 0;
  function revisar() {
    if (!chrome.runtime || !chrome.runtime.id) return;
    ultimaRevision = Date.now();
    var ids = Object.keys(registro).sort(function (a, b) { return (registro[b].visto || 0) - (registro[a].visto || 0); })
      .slice(0, CAMBIOS_MAX);
    if (!ids.length) { pendientes = []; return dibujarCambios(); }
    fetch("/api/admin/day-queue/changes?ids=" + ids.join(","), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("http"); return r.json(); })
      .then(function (r) {
        if (r.ahora) reloj = { ahora: r.ahora, en: Date.now() };
        pendientes = S.cambiosPorPasar(registro, r.cambios || [], ahoraInstalacion());
        dibujarCambios();
      }, function () {});
  }

  // A panel that does not say the hour (an older installation) leaves this
  // computer's clock, which sits in the same place.
  function ahoraInstalacion() {
    return reloj.ahora ? S.ahoraMas(reloj.ahora, Date.now() - reloj.en) : S.ahoraLocal();
  }

  var TEXTO_CAMBIO = {
    abriendo: "Abriendo HSI…", cargando: "Preparando en HSI…", esperando: "Confirmá en HSI",
    guardado: "Listo en HSI"
  };
  var BOTON_CAMBIO = {
    cancelar: "Cancelar en HSI…", mover: "Mover en HSI…", presente: "Marcar presente en HSI…", crear: "Cargar en HSI"
  };
  var RESUMEN_CAMBIO = {
    cancelar: "Cancelado", mover: "Movido", presente: "Llegó", crear: "Aprobado de nuevo"
  };

  // A move with one of its places already past keeps the word «Movido»: the
  // desk sees a moved turno, only the half still ahead goes to HSI.
  function resumen(c) {
    var t = c.turno, nuevo = S.diaYHora(t.dia_iso, t.hora), viejo = S.diaYHora(c.antes.dia, c.antes.hora);
    var movido = c.accion === "mover" || c.movido;
    var donde = movido ? viejo + " → " + nuevo : c.accion === "cancelar" ? viejo : nuevo;
    return (movido ? RESUMEN_CAMBIO.mover : RESUMEN_CAMBIO[c.accion]) + ": " + (t.paciente || "") + ", " + donde;
  }

  function dibujarCambios() {
    var caja = document.querySelector(".hr-hsi-cambios");
    var filas = pendientes.slice();
    if (!filas.length && !Object.keys(listos).length) { if (caja) caja.remove(); return; }
    if (!caja) {
      caja = document.createElement("div");
      caja.className = "hr-hsi-cambios";
      caja.setAttribute("role", "status");
      document.body.appendChild(caja);
    }
    caja.textContent = "";
    filas.forEach(function (c) {
      var a = acciones[c.id];
      var fila = document.createElement("div");
      fila.className = "hr-hsi-cambio";
      var texto = document.createElement("span");
      texto.textContent = resumen(c);
      fila.appendChild(texto);
      var boton = document.createElement("button");
      boton.type = "button";
      boton.className = "hr-hsi-boton";
      boton.textContent = a && a.activo ? (a.texto || TEXTO_CAMBIO.abriendo) : BOTON_CAMBIO[c.accion];
      boton.disabled = !!(a && a.activo);
      boton.addEventListener("click", function () { pasarCambio(c); });
      fila.appendChild(boton);
      if (a && a.error) {
        var aviso = document.createElement("span");
        aviso.className = "hr-hsi-cambio-error";
        aviso.textContent = a.error;
        fila.appendChild(aviso);
      }
      if (a && a.ocultable) {
        var ocultar = document.createElement("button");
        ocultar.type = "button";
        ocultar.className = "hr-hsi-ocultar";
        ocultar.textContent = "Ocultar";
        ocultar.addEventListener("click", function () { ocultarCambio(c); });
        fila.appendChild(ocultar);
      }
      caja.appendChild(fila);
    });
    Object.keys(listos).forEach(function (id) {
      var fila = document.createElement("div");
      fila.className = "hr-hsi-cambio";
      fila.textContent = listos[id];
      caja.appendChild(fila);
    });
  }

  function pasarCambio(c) {
    if (modo === "manual") {
      acciones[c.id] = { c: c, error: "Estás en modo «solo copiar el DNI». Cambialo en los ajustes." };
      return dibujarCambios();
    }
    if (ocupado()) {
      acciones[c.id] = { c: c, error: "HSI está cargando otro turno. Terminá ese y tocá de nuevo." };
      return dibujarCambios();
    }
    acciones[c.id] = { c: c, paso: c.accion === "mover" ? "cancelar" : c.accion, activo: true, texto: TEXTO_CAMBIO.abriendo, intentos: 0 };
    enviarPaso(c.id);
  }

  // One step of a change goes to HSI as an ordinary job. Cancelling and
  // marking present look for the place HSI holds (`antes`); creating loads
  // the turno at its present place, like any «Cargar en HSI».
  function enviarPaso(id) {
    var a = acciones[id];
    if (!a) return;
    var c = a.c;
    var turno;
    if (a.paso === "crear") {
      turno = S.turnoDeCambio(c.turno);
      var falta = !turno.dni ? "Este turno no tiene DNI." : camposQueFaltan(turno).length
        ? "Faltan datos del paciente: completalos con «Cargar en HSI» en su fila." : "";
      if (falta) return fallarCambio(id, falta);
      lugares[id] = S.lugarDeTurno(turno);
    } else {
      turno = S.turnoDeCambio(c.turno, c.antes);
      turno.accion = a.paso;
      turno.antes = c.antes;
    }
    a.activo = true;
    dibujarCambios();
    chrome.runtime.sendMessage({ tipo: "cargar", turno: turno }).then(function (r) {
      if (r && r.ok) return;
      // The step before this one may still be closing its job: ask again.
      if (a.intentos < 5 && r && /otro turno/.test(r.motivo || "") && a.paso === "crear") {
        a.intentos++;
        return setTimeout(function () { enviarPaso(id); }, 800);
      }
      fallarCambio(id, (r && r.motivo) || SIN_RESPUESTA);
    }, function () { fallarCambio(id, SIN_RESPUESTA); });
  }

  // HSI refusing a change whose turno has passed is no error: HSI never
  // changes a past turno, so the row goes with a quiet word. Any other refusal
  // stays with its reason and an «Ocultar» for when the desk leaves HSI as it is.
  function fallarCambio(id, motivo) {
    var a = acciones[id];
    if (!a) return;
    var c = a.c;
    if (S.cambioPasado(Object.assign({}, c, { antes: registro[id] || c.antes }), ahoraInstalacion())) {
      delete acciones[id];
      pendientes = pendientes.filter(function (p) { return String(p.id) !== String(id); });
      listos[id] = "Ya pasó, queda así en HSI: " + (c.turno.paciente || "");
      dibujarCambios();
      setTimeout(function () { delete listos[id]; dibujarCambios(); }, 5000);
      return setTimeout(siguiente, 0);
    }
    a.activo = false;
    a.error = motivo;
    a.ocultable = true;
    dibujarCambios();
    setTimeout(siguiente, 0);
  }

  // «Ocultar»: the desk leaves HSI as it is. Hidden here at once and, through
  // the panel, on every computer of the desk, with a line in the turno's
  // history. It comes back only if the turno changes again.
  function ocultarCambio(c) {
    if (registro[c.id]) {
      registro[c.id].oculto = S.firmaDeCambio(c);
      guardarRegistro();
    }
    delete acciones[c.id];
    pendientes = pendientes.filter(function (p) { return String(p.id) !== String(c.id); });
    dibujarCambios();
    fetch(OCULTAR.replace("{id}", encodeURIComponent(c.id)), { method: "POST", credentials: "same-origin" }).then(function () {}, function () {});
  }

  function estadoDeCambio(msg) {
    var a = acciones[msg.id];
    if (msg.estado === "error") return fallarCambio(msg.id, msg.detalle || SIN_RESPUESTA);
    if (msg.estado === "reposo") {
      // The operator stopped it in HSI: nothing changed, the change stays pending.
      delete acciones[msg.id];
      dibujarCambios();
      return setTimeout(siguiente, 0);
    }
    if (msg.estado !== "guardado") {
      a.texto = TEXTO_CAMBIO[msg.estado] || msg.detalle || a.texto;
      return dibujarCambios();
    }
    var c = a.c, antes = c.antes;
    if (a.paso === "cancelar") {
      registro[msg.id] = { dia: antes.dia, hora: antes.hora, medico: antes.medico, cancelado: true, visto: Date.now() };
      if (c.accion === "mover") {
        // The old place is free; the turno goes to the new one.
        a.paso = "crear";
        a.intentos = 0;
        a.texto = TEXTO_CAMBIO.abriendo;
        guardarRegistro();
        dibujarCambios();
        return setTimeout(function () { enviarPaso(msg.id); }, 800);
      }
    } else if (a.paso === "presente") {
      registro[msg.id] = { dia: antes.dia, hora: antes.hora, medico: antes.medico, presente: true, visto: Date.now() };
    } else {
      var lugar = lugares[msg.id] || S.lugarDeTurno(S.turnoDeCambio(c.turno));
      registro[msg.id] = { dia: lugar.dia, hora: lugar.hora, medico: lugar.medico, visto: Date.now() };
      hechos[msg.id] = Date.now();
      var guardar = {}; guardar[HECHOS] = hechos;
      chrome.storage.local.set(guardar);
    }
    guardarRegistro();
    delete acciones[msg.id];
    pendientes = pendientes.filter(function (p) { return String(p.id) !== String(msg.id); });
    listos[msg.id] = "Listo en HSI: " + (c.turno.paciente || "");
    dibujarCambios();
    setTimeout(function () { delete listos[msg.id]; dibujarCambios(); }, 5000);
    setTimeout(siguiente, 0);
    setTimeout(revisar, 1000);
  }

  var pendiente = 0;
  var observador = new MutationObserver(function () {
    if (pendiente) return;
    pendiente = setTimeout(function () { pendiente = 0; decorar(); }, 120);
  });

  function repintarTodo() {
    document.querySelectorAll("[data-hr-hsi]").forEach(function (b) { pintar(b, b.getAttribute("data-hr-hsi")); });
  }

  var pedido = {}; pedido[HECHOS] = {}; pedido[REGISTRO] = {}; pedido.modo = "boton"; pedido.cargarAlAprobar = false;
  chrome.storage.local.get(pedido).then(function (r) {
    hechos = r[HECHOS] || {};
    registro = r[REGISTRO] || {};
    // What changed since the last visit is asked once now, then every five
    // minutes and when the tab comes back (each ask is logged by the panel).
    revisar();
    setInterval(revisar, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && Date.now() - ultimaRevision > 60 * 1000) revisar();
    });
    modo = ["manual", "boton", "auto"].indexOf(r.modo) !== -1 ? r.modo : "boton";
    alAprobar = !!r.cargarAlAprobar;
    decorar();
    observador.observe(document.body, { childList: true, subtree: true });
    // The list the operator is looking at is the one auto mode will know
    // about; only where the panel actually read it.
    var recursos = performance.getEntriesByType("resource");
    for (var i = 0; i < recursos.length; i++) {
      if (new URL(recursos[i].name).pathname === "/api/admin/day-queue") {
        buscarCola().then(reportarCola, function () {});
        break;
      }
    }
  });

  // A mode change is read the moment it is saved — the row buttons change
  // their wording with it, without waiting for a reload.
  chrome.storage.onChanged.addListener(function (c) {
    if (c.modo && ["manual", "boton", "auto"].indexOf(c.modo.newValue) !== -1) {
      modo = c.modo.newValue;
      repintarTodo();
    }
    // Switched on or off from now on: what is already on screen stays as it is.
    if (c.cargarAlAprobar) alAprobar = !!c.cargarAlAprobar.newValue;
  });
})();
