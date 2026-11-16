/*
 * hsi-tidy.js — the window HSI opens on a turno, with only what is there and
 * what the desk needs now. Progressive disclosure on HSI's own screen:
 *  - the patient's name IS the link to the patient («Ir a Paciente» and
 *    «Editar información del paciente» are gone: the profile edits);
 *  - a datum HSI does not have is not drawn at all (no «Sin cobertura», no
 *    «Sin información»);
 *  - HSI's internal patient number leaves the DNI line;
 *  - the default modality, the «Turno» heading, who-booked-it, the empty
 *    reports block and the dividers leave.
 *
 * Only the visible layer changes: elements are marked `data-hr-oculto` (CSS
 * hides them, content-hsi.css) and the one text node of the DNI line is
 * trimmed in place. Nothing HSI holds is edited, and every control the
 * extension drives (the four state buttons, «Cancelar turno») stays put.
 * Idempotent: it runs on every change of the page.
 */
(function () {
  "use strict";
  if (self.HRTidy) return;

  var OCULTO = "data-hr-oculto";
  var VACIOS = /^\s*sin\s+(cobertura|informaci[oó]n)\s*$/i;

  function todos(sel, base) {
    return Array.prototype.slice.call((base || document).querySelectorAll(sel));
  }

  function ocultar(el) {
    if (el && !el.hasAttribute(OCULTO)) el.setAttribute(OCULTO, "");
  }

  function texto(el) {
    return (el.textContent || "").replace(/\s+/g, " ").trim();
  }

  // The DNI line reads «DNI 90100104 - ID 4»: HSI's own row number is not
  // something the desk reads out to anybody.
  function sinNumeroInterno(root) {
    todos(".subtitle", root).forEach(function (el) {
      var nodo = el.firstChild;
      while (nodo && nodo.nodeType !== 3) nodo = nodo.nextSibling;
      if (nodo && /\s-\s*ID\s*\d+\s*$/i.test(nodo.nodeValue)) {
        nodo.nodeValue = nodo.nodeValue.replace(/\s-\s*ID\s*\d+\s*$/i, "");
      }
    });
  }

  // The name is the link: a click, or Enter on it, presses HSI's own «Ir a
  // Paciente» (kept in the page, hidden) so HSI does the navigation.
  function nombreEsEnlace(root) {
    var ir = root.querySelector("#patient-profile-action");
    var nombre = ir && root.querySelector("#personal-data .info .title");
    if (!nombre) return;
    ocultar(ir);
    if (nombre.hasAttribute("data-hr-enlace")) return;
    nombre.setAttribute("data-hr-enlace", "");
    nombre.setAttribute("role", "link");
    nombre.setAttribute("tabindex", "0");
    nombre.addEventListener("click", function () { ir.click(); });
    nombre.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); ir.click(); }
    });
  }

  // Coverage and phone: shown only when HSI has them. The edit button goes
  // always; with nothing left the whole block goes and the turno takes the
  // full width.
  function datosDelPaciente(root) {
    var ventana = root.querySelector("#window");
    var captions = todos(".mat-caption", root);
    var bloques = [];
    captions.forEach(function (c) {
      var bloque = c.parentElement;
      if (bloque && bloques.indexOf(bloque) < 0) bloques.push(bloque);
    });
    var seccion = bloques.length ? bloques[0].parentElement : null;
    if (!seccion || seccion.id) return;
    var quedan = 0;
    bloques.forEach(function (b) {
      var valor = texto(b).replace(texto(b.querySelector(".mat-caption")), "").trim();
      if (!valor || VACIOS.test(valor) || b.querySelector(".patient-no-info")) ocultar(b);
      else quedan++;
    });
    todos("button", seccion).forEach(function (b) {
      var icono = b.querySelector("mat-icon");
      if (icono && texto(icono) === "edit") ocultar(b);
    });
    if (ventana) {
      if (quedan) ventana.removeAttribute("data-hr-sin-datos");
      else { ocultar(seccion); ventana.setAttribute("data-hr-sin-datos", ""); }
    }
  }

  // The turno block: the date and the hour are the turno; «Turno» as a
  // heading, the usual «Presencial», and who booked it are not.
  function bloqueDelTurno(root) {
    var turno = root.querySelector("#appointment");
    if (!turno) return;
    ocultar(turno.querySelector(".appointment-title"));
    ocultar(turno.querySelector(".assigned-by"));
    todos("app-modality-label", turno).forEach(function (m) {
      // The label's text is «personPresencial» (the icon's name comes first).
      if (/^(person\s*)?presencial$/i.test(texto(m))) ocultar(m.parentElement);
    });
  }

  function restos(root) {
    todos("mat-divider", root).forEach(ocultar);
    var informes = root.querySelector("#reports");
    if (informes && !texto(informes)) ocultar(informes);
  }

  function ordenar(root) {
    if (!root) return;
    sinNumeroInterno(root);
    nombreEsEnlace(root);
    datosDelPaciente(root);
    bloqueDelTurno(root);
    restos(root);
  }

  function turnosAbiertos() {
    return todos("mat-dialog-container app-appointment");
  }

  var pendiente = false;
  function alCambiar() {
    if (pendiente) return;
    pendiente = true;
    (self.requestAnimationFrame || setTimeout)(function () {
      pendiente = false;
      turnosAbiertos().forEach(ordenar);
    });
  }

  function vigilar() {
    if (self.__hrTidyVigila || !document.body) return;
    self.__hrTidyVigila = true;
    new MutationObserver(alCambiar).observe(document.body, { childList: true, subtree: true, characterData: true });
    alCambiar();
  }

  self.HRTidy = { ordenar: ordenar, vigilar: vigilar };
})();
