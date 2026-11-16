/*
 * hsi-dom.js — how to read and operate HSI's Angular Material screen.
 *
 * Every selector here was taken from the rendered DOM of the HSI test copy
 * (v2.71.2) and checked against the province's screen recording (v2.77.6).
 * Angular ignores a bare `input.value = …`: values go through the native
 * setter plus the events its forms listen to.
 */
(function (root) {
  "use strict";

  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  function todos(sel, base) {
    return Array.prototype.slice.call((base || document).querySelectorAll(sel));
  }

  function primero(sel, base) {
    return todos(sel, base).filter(visible)[0] || null;
  }

  function texto(el) {
    return el ? (el.textContent || "").replace(/\s+/g, " ").trim() : "";
  }

  function escribir(input, valor) {
    var desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value");
    input.focus();
    if (desc && desc.set) desc.set.call(input, String(valor));
    else input.value = String(valor);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // The field whose floating label reads `etiqueta`, when its id is unknown.
  function campoPorEtiqueta(etiqueta, base) {
    var S = root.HRShared;
    var campos = todos("mat-form-field", base).filter(visible);
    for (var i = 0; i < campos.length; i++) {
      var l = campos[i].querySelector("mat-label, label");
      if (l && S.norm(texto(l)).indexOf(S.norm(etiqueta)) === 0) {
        return campos[i].querySelector("input, mat-select, textarea");
      }
    }
    return null;
  }

  function botonPorTexto(palabra, base) {
    var S = root.HRShared;
    return todos("button", base).filter(visible).filter(function (b) {
      return !b.disabled && S.norm(texto(b)) === S.norm(palabra);
    })[0] || null;
  }

  function opciones() {
    return todos(".cdk-overlay-container mat-option").filter(visible);
  }

  function dialogo() {
    return primero("mat-dialog-container app-new-appointment") || null;
  }

  function hayOtroDialogo() {
    return !!primero("mat-dialog-container") && !dialogo();
  }

  // A second window on top of «Nuevo turno»: HSI asking the operator
  // something ("the patient already has a turno that day — assign anyway?").
  function pregunta() {
    return todos("mat-dialog-container").filter(visible).filter(function (d) {
      return !d.querySelector("app-new-appointment");
    })[0] || null;
  }

  // HSI's notices that appeared after the last olvidarAvisos(): a notice
  // lingers for seconds, and an old «Turno confirmado» must never vouch for
  // the next turno.
  function avisos() {
    return todos("snack-bar-container, .mat-snack-bar-container").filter(visible).filter(function (s) {
      return !s.hasAttribute("data-hr-visto");
    }).map(function (s) {
      // The leading word is the Material icon's ligature ("check", "cancel").
      var t = texto(s).replace(/^(check_circle|check|cancel|error_outline|error|warning|info|close)\s*/i, "");
      return { exito: s.classList.contains("success"), error: s.classList.contains("error"), texto: t };
    });
  }

  function olvidarAvisos() {
    todos("snack-bar-container, .mat-snack-bar-container").forEach(function (s) {
      s.setAttribute("data-hr-visto", "1");
    });
  }

  // The X that clears a typeahead's current choice.
  function botonLimpiar(input) {
    var campo = input && input.closest("mat-form-field");
    return campo ? todos("button", campo).filter(visible)[0] || null : null;
  }

  function calendario() {
    return primero("mwl-calendar-week-view");
  }

  // The heading over the grid that spells out the week on screen.
  function tituloSemana() {
    var cabeza = primero("app-agenda #header") || primero("app-agenda");
    if (!cabeza) return "";
    var con = todos("*", cabeza).filter(visible).filter(function (e) {
      return !e.children.length && /\d{1,2} de \S+ de \d{4}/.test(e.textContent);
    })[0];
    return texto(con);
  }

  function encabezados() {
    return todos("mwl-calendar-week-view .cal-day-headers .cal-header").map(texto);
  }

  function etiquetasDeHora() {
    return todos("mwl-calendar-week-view .cal-time-label-column .cal-hour-segment").map(texto);
  }

  function segmento(columna, fila) {
    var col = todos("mwl-calendar-week-view .cal-time-events .cal-day-column")[columna];
    return col ? todos(".cal-hour-segment", col)[fila] || null : null;
  }

  // Free = inside the agenda's hours (green) AND with no turno drawn over
  // it: HSI leaves the block green under an existing turno.
  function estaLibre(seg) {
    if (!seg || !seg.classList.contains("bg-green")) return false;
    var col = seg.closest(".cal-day-column");
    var r = seg.getBoundingClientRect();
    var medio = r.top + r.height / 2;
    return !todos(".cal-event", col).some(function (ev) {
      var e = ev.getBoundingClientRect();
      return e.height > 0 && e.top <= medio && e.bottom >= medio;
    });
  }

  // ---- a turno that already exists -----------------------------------------

  // The turnos drawn in one day's column of the week grid.
  function eventosDelDia(columna) {
    var col = todos("mwl-calendar-week-view .cal-time-events .cal-day-column")[columna];
    return col ? todos(".cal-event", col).filter(visible) : [];
  }

  // The window HSI opens on a turno (not the «Nuevo turno» one): ready once
  // its spinner is gone.
  function turnoAbierto() {
    var dlg = primero("mat-dialog-container app-appointment");
    return dlg && !primero(".spinner", dlg) ? dlg : null;
  }

  function cerrarTurnoAbierto() {
    var x = primero("#close-dialog");
    if (x) x.click();
    return !!x;
  }

  function botonCancelarTurno(base) {
    return primero("#cancel-appointment", base) || null;
  }

  // The question «¿Cancelar turno?»: a required reason and two buttons.
  function dialogoCancelar() {
    return primero("mat-dialog-container app-cancel-appointment") || null;
  }

  function motivoDeCancelacion() {
    return primero("textarea#motivo", dialogoCancelar() || undefined) || null;
  }

  // «Si, cancelar turno» — the one that really cancels.
  function botonSiCancelar() {
    var d = dialogoCancelar();
    return d ? primero("#okButton-button", d) : null;
  }

  // The four state buttons of «Estado de turno», in HSI's order: Asignado,
  // Confirmado, Ausente, Atendido. Only Confirmado is enabled before the day.
  function botonesDeEstado(base) {
    return todos("section#state button.mat-stroked-button", base).filter(visible);
  }

  // The question that follows pressing Confirmado (it offers to update the
  // coverage first): a window that is neither «Nuevo turno» nor the turno.
  function preguntaDeConfirmado() {
    return todos("mat-dialog-container").filter(visible).filter(function (d) {
      return !d.querySelector("app-appointment") && !d.querySelector("app-new-appointment") &&
        !d.querySelector("app-cancel-appointment");
    })[0] || null;
  }

  // «No, confirmar turno» — confirms without touching the coverage.
  function botonConfirmarTurno() {
    var d = preguntaDeConfirmado();
    return d ? primero("#okButton-button", d) : null;
  }

  // The coverage select of «Nuevo turno»'s contact step. Found by its form
  // control name — its visible text is the chosen coverage's name (or "Sin
  // cobertura"), never the word "cobertura", once something is chosen.
  function selectCobertura(base) {
    return primero('mat-select[formcontrolname="patientMedicalCoverage"]', base) || null;
  }

  // HSI's own way out when the DNI is nobody's: a button inside the turno
  // dialog that opens the patient search with the DNI and sex already in.
  function botonCrearPaciente(base) {
    return botonPorTexto("Crear paciente", base);
  }

  // The search of the patient alta screen. A search: pressing it only reads.
  function botonBuscarPaciente() {
    return botonPorTexto("Buscar");
  }

  // The card HSI answers with when the patient it was asked to create
  // already exists — the alta is over before it started.
  // Only a card that carries THAT document counts: after a search HSI also
  // lists people who merely look alike (same name, same birth date), and a
  // lookalike is never proof that this patient exists.
  function tarjetaPaciente(dni) {
    var d = String(dni == null ? "" : dni).replace(/\D/g, "");
    if (!d) return null;
    return todos("app-card-patient").filter(visible).filter(function (c) {
      return texto(c).replace(/[.\s]/g, "").indexOf(d) !== -1;
    })[0] || null;
  }

  // HSI is showing possible matches, none of them with that document.
  function hayParecidos(dni) {
    return todos("app-card-patient").filter(visible).length > 0 && !tarjetaPaciente(dni);
  }

  // The patient search/alta screen (/pacientes/search): id-addressed fields,
  // the same sex radios as the turno dialog, already carrying whatever the
  // dialog knew when HSI navigated here (the DNI box is prefilled from the
  // URL and goes by its own id, unlike the dialog's #identifNumber).
  var ALTA = {
    dni: "#identificationNumber",
    nombre: "#firstName",
    apellido: "#lastName",
    nacimiento: "#birthDate",
    radios: "mat-radio-button"
  };

  root.HRDom = {
    visible: visible, todos: todos, primero: primero, texto: texto, escribir: escribir,
    campoPorEtiqueta: campoPorEtiqueta, botonPorTexto: botonPorTexto, opciones: opciones,
    dialogo: dialogo, hayOtroDialogo: hayOtroDialogo, pregunta: pregunta, avisos: avisos, olvidarAvisos: olvidarAvisos,
    botonLimpiar: botonLimpiar, tituloSemana: tituloSemana, calendario: calendario,
    encabezados: encabezados, etiquetasDeHora: etiquetasDeHora, segmento: segmento, estaLibre: estaLibre,
    eventosDelDia: eventosDelDia, turnoAbierto: turnoAbierto, cerrarTurnoAbierto: cerrarTurnoAbierto,
    botonCancelarTurno: botonCancelarTurno, dialogoCancelar: dialogoCancelar, motivoDeCancelacion: motivoDeCancelacion,
    botonSiCancelar: botonSiCancelar, botonesDeEstado: botonesDeEstado, preguntaDeConfirmado: preguntaDeConfirmado,
    botonConfirmarTurno: botonConfirmarTurno,
    selectCobertura: selectCobertura, botonCrearPaciente: botonCrearPaciente,
    botonBuscarPaciente: botonBuscarPaciente, tarjetaPaciente: tarjetaPaciente, hayParecidos: hayParecidos, ALTA: ALTA
  };
})(typeof self !== "undefined" ? self : this);
