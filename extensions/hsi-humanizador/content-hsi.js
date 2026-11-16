/*
 * content-hsi.js — loads one turno into HSI's «Nuevo turno», on screen, the
 * way the desk does it by hand: professional -> agenda -> day and hour ->
 * patient -> contact -> Confirmar.
 *
 * Two rules shape everything here:
 *  - Confirmar is the operator's unless they turned «guardar solo» on. The
 *    extension fills the form and stops; it reports "guardado" only on HSI's
 *    own evidence (its «Turno confirmado» notice and the dialog closing).
 *  - Anything it cannot decide without guessing (an ambiguous professional, a
 *    taken slot, an unknown sex) it hands to the operator and resumes when
 *    the screen moves on. It never guesses on a patient's record.
 *
 * A patient HSI does not have gets HSI's own alta: the search screen is
 * filled from the row, HSI's «Buscar» (a search — it only reads) is pressed,
 * and the operator's part is pressing «Guardar» on HSI's new-patient screen.
 * The moment HSI's own evidence says the patient exists, the same job goes
 * back to loading the turno by itself; if the alta is left unfinished, the
 * job ends with the reason and the row's button comes back.
 *
 * Auto mode fills the same patient/contact part of a «Nuevo turno» the
 * operator already opened at the right slot: a DNI leaving its box is looked
 * up among the turnos of the last «Pacientes de hoy» read (memory only), and
 * only an unambiguous match moves. If the operator got there first, their
 * values stay.
 *
 * A turno HSI already holds can change afterwards in Human Rounds: cancelled
 * (`turno.accion` = "cancelar") or attended (`"presente"`). The same road
 * leads to its agenda, the turno is opened on the grid and identified by the
 * DNI in its own window, and HSI's own button does the rest: «Si, cancelar
 * turno» (always the operator's), or Confirmado then «No, confirmar turno»
 * (the operator's too, unless «guardar solo»). A moved turno is a cancel of
 * the old place followed by the ordinary load of the new one. HSI cannot be
 * told «Atendido» or «Ausente» from the desk before the visit: «Confirmado»
 * is what the desk can do.
 *
 * Nothing is logged and nothing leaves the tab: the turno lives in this
 * script's memory until the job ends.
 */
(function () {
  "use strict";
  if (self.__hrHsiCargado) return;
  self.__hrHsiCargado = true;

  var S = self.HRShared, D = self.HRDom;
  var CARGA = 60000;       // HSI answering a navigation or a search
  var OPERADOR = 600000;   // the operator doing their part (ten minutes)
  var MARCA_NAVEGADO = "hrHsiNavegado";
  var actual = null;       // { turno, ajustes, creado, cancelado, auto }
  var misAjustes = null;   // the settings this page last heard about

  var PASOS = [
    ["turnos", "Abrir Gestión de turnos"],
    ["profesional", "Buscar al profesional"],
    ["agenda", "Elegir la agenda"],
    ["horario", "Ir al día y la hora"],
    ["paciente", "Buscar al paciente"],
    ["contacto", "Completar el contacto"],
    ["confirmar", "Confirmar el turno"]
  ];

  function Cancelado() {}
  function Fallo(motivo) { this.motivo = motivo; }

  // ---- panel ---------------------------------------------------------------

  var panel = null;

  function crear(tag, clase, txt) {
    var e = document.createElement(tag);
    if (clase) e.className = clase;
    if (txt != null) e.textContent = txt;
    return e;
  }

  function armarPanel(turno, pasos) {
    quitarPanel();
    panel = crear("section", "hr-hsi-panel");
    panel.setAttribute("aria-live", "polite");
    var cabeza = crear("header", "hr-hsi-cabeza");
    cabeza.appendChild(crear("strong", "hr-hsi-titulo", [turno.hora, turno.paciente].filter(Boolean).join(" · ")));
    var cerrar = crear("button", "hr-hsi-cancelar", "Cancelar");
    cerrar.type = "button";
    cerrar.addEventListener("click", cancelar);
    cabeza.appendChild(cerrar);
    panel.appendChild(cabeza);
    var lista = crear("ol", "hr-hsi-pasos");
    (pasos || PASOS).forEach(function (p) {
      var li = crear("li", "hr-hsi-paso", p[1]);
      li.dataset.paso = p[0];
      lista.appendChild(li);
    });
    panel.appendChild(lista);
    panel.appendChild(crear("p", "hr-hsi-mensaje"));
    panel.appendChild(crear("div", "hr-hsi-acciones"));
    document.body.appendChild(panel);
  }

  function quitarPanel() {
    if (panel) panel.remove();
    panel = null;
  }

  function marcar(paso, estado) {
    if (!panel) return;
    var li = panel.querySelector('[data-paso="' + paso + '"]');
    if (li) li.dataset.estado = estado;
  }

  function mensaje(txt, tono) {
    if (!panel) return;
    var p = panel.querySelector(".hr-hsi-mensaje");
    p.textContent = txt || "";
    p.dataset.tono = tono || "";
  }

  function acciones(botones) {
    if (!panel) return;
    var caja = panel.querySelector(".hr-hsi-acciones");
    caja.textContent = "";
    (botones || []).forEach(function (b) {
      var boton = crear("button", "hr-hsi-accion" + (b.principal ? " hr-hsi-accion--principal" : ""), b.texto);
      boton.type = "button";
      boton.addEventListener("click", b.alTocar);
      caja.appendChild(boton);
    });
  }

  function resaltar(el) {
    todosResaltados().forEach(function (x) { x.classList.remove("hr-hsi-foco"); });
    if (el) {
      el.classList.add("hr-hsi-foco");
      el.scrollIntoView({ block: "center", inline: "nearest" });
    }
  }

  function todosResaltados() {
    return D.todos(".hr-hsi-foco");
  }

  // ---- waiting -------------------------------------------------------------

  function pausa(ms) {
    return new Promise(function (ok) { setTimeout(ok, ms); });
  }

  function vivo() {
    if (!actual || actual.cancelado) throw new Cancelado();
  }

  // The step-by-step mode slows down on purpose: the operator has to be able
  // to follow what is being typed.
  function respiro() {
    vivo();
    return pausa(actual.ajustes.mostrarPasos ? 550 : 250);
  }

  function esperar(condicion, limite) {
    var fin = Date.now() + (limite || CARGA);
    // A wait belongs to the turno that started it and dies with it: a wait
    // left over from one turno must never act on the next one's window.
    var mio = actual;
    return new Promise(function (ok, mal) {
      (function mirar() {
        var r;
        try {
          if (!mio || mio !== actual || mio.terminado) throw new Cancelado();
          vivo();
          r = condicion();
        } catch (e) { return mal(e); }
        if (r) return ok(r);
        if (Date.now() > fin) return ok(null);
        setTimeout(mirar, 150);
      })();
    });
  }

  function estado(nombre, detalle, final) {
    var t = actual && actual.turno;
    return chrome.runtime.sendMessage({
      tipo: "estado", estado: nombre, detalle: detalle || "", final: !!final,
      id: t ? t.id : null, paciente: t ? t.paciente : "", dni: t ? t.dni : ""
    }).catch(function () {});
  }

  // Hands a decision to the operator and resumes when `listo` turns true.
  function pedir(paso, txt, listo) {
    marcar(paso, "espera");
    mensaje(txt, "espera");
    estado("esperando", txt);
    return esperar(listo, OPERADOR).then(function (r) {
      if (!r) throw new Fallo("Se acabó el tiempo de espera. Tocá «Cargar en HSI» de nuevo.");
      mensaje("");
      estado("cargando");
      return r;
    });
  }

  // ---- steps ---------------------------------------------------------------

  function institucionEnUrl() {
    return S.institucionDeUrl(location.pathname);
  }

  function enTurnos() {
    return /\/institucion\/\d+\/turnos(\/|$)/.test(location.pathname);
  }

  function campoProfesional() {
    return D.primero("app-professional-select input") || D.campoPorEtiqueta("Profesional");
  }

  function abrirTurnos() {
    marcar("turnos", "curso");
    var p = Promise.resolve();
    if (/^\/auth/.test(location.pathname) || D.primero("app-login")) {
      p = pedir("turnos", "Entrá a HSI con tu usuario y sigo.", function () {
        return !/^\/auth/.test(location.pathname) && !D.primero("app-login");
      });
    }
    // The institution comes from the row's center (S.institucionPara); one
    // that is not known yet is the operator's pick in HSI, remembered for that
    // center — each CAPS and the hospital are different institutions.
    var turno = actual.turno;
    var clave = S.claveCentro(turno);
    return p.then(function () {
      var id = S.institucionPara(actual.ajustes, turno);
      if (id) return id;
      var donde = turno.centro ? "la institución de " + turno.centro : "tu institución";
      return pedir("turnos", "Elegí " + donde + " en HSI y sigo.", institucionEnUrl).then(function (elegida) {
        if (clave) {
          var mapa = Object.assign({}, actual.ajustes.institucionPorCentro || {});
          mapa[clave] = elegida;
          actual.ajustes.institucionPorCentro = mapa;
          chrome.storage.local.set({ institucionPorCentro: mapa });
        } else {
          actual.ajustes.institucionId = elegida;
          chrome.storage.local.set({ institucionId: elegida });
        }
        return elegida;
      });
    }).then(function (id) {
      // A window that was just closed takes a moment to leave the screen.
      return esperar(function () { return !D.hayOtroDialogo() && !D.dialogo(); }, 3000).then(function (libre) {
        if (!libre) throw new Fallo("Cerrá la ventana que quedó abierta en HSI y tocá «Cargar en HSI» de nuevo.");
        return id;
      });
    }).then(function (id) {
      if (enTurnos() && institucionEnUrl() === String(id)) return;
      // A full navigation: this script dies with the page and the job is
      // handed back by the service worker when the new page loads. Once per
      // job — if HSI sends us elsewhere again, the user has no access. (The
      // alta's resume clears the mark on purpose: its detour back to the
      // agendas is a new navigation, not a lost one.)
      if (sessionStorage.getItem(MARCA_NAVEGADO) === String(actual.creado)) {
        throw new Fallo("HSI no abrió Gestión de turnos. Fijate si tu usuario tiene acceso a Turnos.");
      }
      sessionStorage.setItem(MARCA_NAVEGADO, String(actual.creado));
      location.assign(location.origin + "/institucion/" + id + "/turnos");
      return new Promise(function () {});
    }).then(function () {
      return esperar(campoProfesional);
    }).then(function (campo) {
      if (!campo) throw new Fallo("HSI no abrió Gestión de turnos. Fijate si tu usuario tiene acceso a Turnos.");
      marcar("turnos", "listo");
    });
  }

  function elegirProfesional() {
    var turno = actual.turno;
    marcar("profesional", "curso");
    var campo = campoProfesional();
    var tipeado = "";
    // Chosen by hand = the field holds a full choice (not the word we typed),
    // its list is closed and HSI is showing that professional's agendas.
    var aMano = function () {
      resaltar(campoProfesional());
      var quien = turno.medico ? "a " + turno.medico : "al profesional";
      return pedir("profesional", "No encontré " + quien + " en HSI. Elegilo vos y sigo.", function () {
        var c = campoProfesional();
        return c && c.value && S.norm(c.value) !== S.norm(tipeado) && !D.opciones().length && selectorAgenda();
      });
    };
    if (turno.medico && campo.value && S.coincidencia(turno.medico, campo.value) === 1 && selectorAgenda()) {
      marcar("profesional", "listo");
      return Promise.resolve();
    }

    // Whatever was selected before goes first: the agendas on screen must
    // never be a previous professional's.
    var agendaVieja = selectorAgenda();
    var limpiar = respiro().then(function () {
      var x = campo.value && D.botonLimpiar(campo);
      if (x) x.click();
      else if (campo.value) D.escribir(campo, "");
      return pausa(350);
    }).then(function () { campo = campoProfesional(); });

    var palabras = S.tokens(turno.medico);
    var intentos = palabras.length ? [palabras[palabras.length - 1]].concat(palabras.slice(0, -1)) : [];

    function probar(i) {
      if (i >= intentos.length) return Promise.resolve(null);
      resaltar(campo);
      tipeado = intentos[i];
      D.escribir(campo, tipeado);
      return esperar(function () { return D.opciones().length ? D.opciones() : null; }, 6000).then(function (ops) {
        var elegida = ops && S.mejor(turno.medico, ops, D.texto, 0.99);
        if (elegida) return elegida;
        return probar(i + 1);
      });
    }

    return limpiar.then(function () { return probar(0); }).then(function (opcion) {
      if (!opcion) {
        D.escribir(campo, "");
        tipeado = "";
        return aMano();
      }
      resaltar(opcion);
      return respiro().then(function () {
        opcion.click();
        return esperar(function () { var s = selectorAgenda(); return s && s !== agendaVieja; }, 4000);
      });
    }).then(function () {
      resaltar(null);
      marcar("profesional", "listo");
    });
  }

  function selectorAgenda() {
    return D.primero("#agenda-select") || D.campoPorEtiqueta("Seleccione una agenda");
  }

  function elegirAgenda() {
    var turno = actual.turno;
    marcar("agenda", "curso");
    return esperar(selectorAgenda).then(function (sel) {
      if (!sel) throw new Fallo("HSI no mostró las agendas de ese profesional.");
      resaltar(sel);
      return respiro().then(function () {
        (sel.querySelector(".mat-select-trigger") || sel).click();
        return esperar(function () { return D.opciones().length ? D.opciones() : null; }, 8000);
      }).then(function (ops) {
        var vigentes = (ops || []).filter(function (o) { return S.agendaVigente(turno.dia, D.texto(o)); });
        var elegida = vigentes.length === 1 ? vigentes[0]
          : S.mejor(turno.especialidad || turno.servicio, vigentes, D.texto, 0.5);
        if (elegida) {
          resaltar(elegida);
          var ruta = location.pathname;
          return respiro().then(function () {
            elegida.click();
            // The grid on screen may still be the previous agenda's.
            return esperar(function () { return /\/agenda\/\d+/.test(location.pathname) && location.pathname !== ruta; }, 1800);
          });
        }
        var txt = vigentes.length
          ? "Este profesional tiene más de una agenda. Elegí la que corresponde y sigo."
          : "No hay una agenda vigente para ese día. Elegí la agenda vos y sigo.";
        return pedir("agenda", txt, function () { return !D.opciones().length && D.calendario(); });
      });
    }).then(function () {
      return esperar(D.calendario);
    }).then(function (cal) {
      if (!cal) throw new Fallo("HSI no abrió la agenda.");
      resaltar(null);
      marcar("agenda", "listo");
    });
  }

  function botonSemana(id, palabra) {
    return D.primero("#" + id + "-button") || D.botonPorTexto(palabra);
  }

  function irALaSemana() {
    var turno = actual.turno;
    var semana = D.todos("mat-button-toggle").filter(function (t) { return S.norm(D.texto(t)) === "semana"; })[0];
    var p = Promise.resolve();
    if (semana && !semana.classList.contains("mat-button-toggle-checked")) {
      p = respiro().then(function () { semana.querySelector("button").click(); return pausa(400); });
    }
    // One week at a time, re-reading the week on screen after every press:
    // the grid restores a remembered week when an agenda loads, so counting
    // presses blind lands on the wrong one.
    var vueltas = 0;
    var paso = function () {
      var enPantalla = S.fechaDeTitulo(D.tituloSemana());
      var faltan = S.semanasHasta(turno.dia, enPantalla);
      if (faltan == null) throw new Fallo("No pude leer la semana que muestra HSI. Cargá este turno a mano.");
      if (!faltan) return Promise.resolve();
      if (Math.abs(faltan) > 80 || ++vueltas > 90) throw new Fallo("El día del turno no se pudo ubicar en la agenda.");
      var boton = faltan > 0 ? botonSemana("next", "Próximo") : botonSemana("previous", "Anterior");
      if (!boton) throw new Fallo("No encontré cómo cambiar de semana en HSI.");
      var antes = D.tituloSemana();
      resaltar(boton);
      boton.click();
      return esperar(function () { return D.tituloSemana() !== antes; }, 8000).then(function () {
        return pausa(actual.ajustes.mostrarPasos ? 300 : 120);
      }).then(paso);
    };
    // Let the freshly loaded grid settle on its starting week first.
    return p.then(function () {
      var visto = "", desde = 0;
      return esperar(function () {
        var t = D.tituloSemana();
        if (t !== visto) { visto = t; desde = Date.now(); }
        return t && Date.now() - desde > 600;
      }, 15000);
    }).then(paso);
  }

  function abrirHorario() {
    var turno = actual.turno;
    marcar("horario", "curso");
    return irALaSemana().then(function () {
      // The grid paints its free blocks after the week changes.
      return esperar(function () {
        return S.columnaDelDia(turno.dia, D.encabezados()) !== -1 && D.etiquetasDeHora().length;
      }, 15000);
    }).then(function () { return pausa(700); }).then(function () {
      var seg = D.segmento(S.columnaDelDia(turno.dia, D.encabezados()), S.filaDeLaHora(turno.hora, D.etiquetasDeHora()));
      if (D.estaLibre(seg)) {
        resaltar(seg);
        return respiro().then(function () {
          seg.click();
          return esperar(D.dialogo, 15000);
        }).then(function (dlg) {
          if (!dlg) throw new Fallo("HSI no abrió «Nuevo turno» en ese horario.");
          var titulo = D.texto(dlg.querySelector("app-contextual-box") || dlg.querySelector("[mat-dialog-title]") || dlg);
          if (!S.esElHorario(turno.dia, turno.hora, titulo)) {
            throw new Fallo("HSI abrió otro horario. Cerrá la ventana y cargá este turno a mano.");
          }
        });
      }
      if (seg) resaltar(seg);
      return pedir("horario", seg
        ? "Las " + turno.hora + " no están libres en esa agenda de HSI. Elegí vos el horario y sigo."
        : "No encontré ese día y hora en la agenda de HSI. Elegí vos el horario y sigo.", D.dialogo);
    }).then(function () {
      resaltar(null);
      marcar("horario", "listo");
    });
  }

  // Which step the dialog is on comes from its own stepper header, never from
  // which fields happen to be on screen: while the stepper slides, both
  // steps' fields are.
  function enUltimoPaso(dlg) {
    var h = dlg && dlg.querySelector('mat-step-header[aria-selected="true"]');
    return !!h && h.getAttribute("aria-posinset") === h.getAttribute("aria-setsize");
  }

  function pasoPacienteVisible() {
    var dlg = D.dialogo();
    return dlg && !enUltimoPaso(dlg) && D.primero("#identifNumber", dlg);
  }

  function pasoContactoVisible() {
    var dlg = D.dialogo();
    return dlg && enUltimoPaso(dlg) && (D.primero("#phoneNumber", dlg) || D.botonPorTexto("Confirmar", dlg));
  }

  // Sex, then Continuar, then HSI's own verdict — the shared half of the
  // patient step, for the button flow and for auto mode alike. The DNI goes
  // in only where it is not already the operator's own typing.
  function elegirSexoYBuscar(dlg) {
    var turno = actual.turno;
    var numero = D.primero("#identifNumber", dlg);
    if (!numero) throw new Fallo("HSI no mostró la búsqueda de paciente.");
    resaltar(numero);
    return respiro().then(function () {
      if (S.soloDigitos(numero.value) !== S.soloDigitos(turno.dni)) D.escribir(numero, turno.dni);
      var etiqueta = S.sexoHsi(turno.sexo);
      var yaMarcado = D.todos("mat-radio-button", dlg).filter(D.visible).filter(function (r) {
        return r.classList.contains("mat-radio-checked");
      })[0];
      if (!yaMarcado) {
        var radio = etiqueta && D.todos("mat-radio-button", dlg).filter(D.visible).filter(function (r) {
          return S.norm(D.texto(r)) === S.norm(etiqueta);
        })[0];
        if (!radio) {
          return pedir("paciente", "Marcá el sexo como figura en el DNI y tocá Continuar.", pasoContactoVisible);
        }
        resaltar(radio);
        return respiro().then(function () {
          (radio.querySelector("label") || radio).click();
          // The form only accepts Continuar once it has taken the choice in.
          return esperar(function () { return radio.classList.contains("mat-radio-checked"); }, 3000);
        }).then(pulsarContinuar);
      }
      return pulsarContinuar();
    });

    function pulsarContinuar() {
      // Continuar stays disabled until the form has validated the fields.
      return esperar(function () { return D.botonPorTexto("Continuar", D.dialogo()); }, 8000).then(function (seguir) {
        if (!seguir) throw new Fallo("No encontré el botón Continuar en HSI.");
        resaltar(seguir);
        D.olvidarAvisos();
        seguir.click();
        var ultimo = Date.now(), toques = 1;
        return esperar(function () {
          if (pasoContactoVisible()) return { ok: true };
          // The window is gone: the operator closed it or already saved.
          if (!D.dialogo()) return { ok: true };
          var malos = D.avisos().filter(function (a) { return a.error; });
          if (malos.length) return { error: malos[0].texto };
          // A press that landed before the form was ready does nothing:
          // press again, a couple of times at most. The search only reads.
          if (!enUltimoPaso(D.dialogo()) && toques < 4 && Date.now() - ultimo > 3000) {
            toques++;
            ultimo = Date.now();
            // HSI can reset the form right after the window opens and wipe
            // what was typed: put it back before pressing again.
            var n = D.primero("#identifNumber", D.dialogo());
            if (n && S.soloDigitos(n.value) !== S.soloDigitos(turno.dni)) D.escribir(n, turno.dni);
            var marcado = D.todos("mat-radio-button", D.dialogo()).filter(D.visible).filter(function (r) {
              return r.classList.contains("mat-radio-checked");
            })[0];
            if (!marcado) {
              var etiqueta = S.sexoHsi(turno.sexo);
              var radio = etiqueta && D.todos("mat-radio-button", D.dialogo()).filter(D.visible).filter(function (r) {
                return S.norm(D.texto(r)) === S.norm(etiqueta);
              })[0];
              if (radio) (radio.querySelector("label") || radio).click();
            }
            var otra = D.botonPorTexto("Continuar", D.dialogo());
            if (otra) otra.click();
          }
          return null;
        });
      });
    }
  }

  function buscarPaciente() {
    marcar("paciente", "curso");
    return esperar(function () {
      if (pasoPacienteVisible() || pasoContactoVisible()) return true;
      // Some versions stop on «Modalidad» first: presencial is the default.
      var dlg = D.dialogo();
      var seguir = dlg && D.botonPorTexto("Continuar", dlg);
      if (seguir) seguir.click();
      return false;
    }, 15000).then(function () {
      var dlg = D.dialogo();
      if (!dlg || !D.primero("#identifNumber", dlg)) throw new Fallo("HSI no mostró la búsqueda de paciente.");
      return elegirSexoYBuscar(dlg);
    }).then(function (r) {
      if (!r) throw new Fallo("HSI no respondió a la búsqueda del paciente.");
      if (r.error) {
        if (/no encontrado/i.test(r.error)) return darDeAlta();
        throw new Fallo("HSI respondió: " + r.error);
      }
      resaltar(null);
      marcar("paciente", "listo");
    });
  }

  // ---- the patient HSI does not have ---------------------------------------

  // HSI's own «Crear paciente» opens its patient search with the DNI and sex
  // already in. From there the load walks HSI's own alta to the end: the
  // search screen takes what the row carries, HSI's «Buscar» — a search, it
  // only reads — is pressed in every mode, and HSI itself lands on its
  // new-patient screen with everything prefilled. Saving the patient is the
  // operator's «Guardar», unless «guardar solo» is on. The moment HSI's own
  // evidence says the patient exists (its «El paciente se creó exitosamente»
  // notice, or the new patient's profile), the SAME job goes on to load the
  // turno: nobody presses the row again.

  function enAltaBusqueda() {
    return /\/pacientes\/search(\/|$)/.test(location.pathname);
  }

  function enAltaPantallaNueva() {
    return /\/pacientes\/new(\/|$)/.test(location.pathname);
  }

  // The profile HSI opens for a patient that exists: the address of the
  // patient the alta just made.
  function enPerfilDelPaciente() {
    return /\/pacientes\/profile\/\d+/.test(location.pathname);
  }

  // HSI's own notice that it created the patient.
  function avisoPacienteCreado() {
    return D.avisos().filter(function (a) { return a.exito && /se creó/i.test(a.texto); })[0] || null;
  }

  // The pending alta job on the page it woke up on: carry on where HSI left
  // it, resume the turno if the patient already exists, or end the job —
  // with the reason — when the alta was left behind.
  function altaPendiente() {
    if (enPerfilDelPaciente() || avisoPacienteCreado()) return reanudarTrasElAlta();
    if (enAltaBusqueda()) return llenarAltaYBuscar();
    if (enAltaPantallaNueva()) return esperarElAlta();
    throw new Fallo("Se cortó el alta del paciente en HSI. Si ya la terminaste, tocá «Cargar en HSI» de nuevo.");
  }

  // HSI's date box only takes a date with its bars: "01051990" leaves the
  // form invalid under the mask and the operator would have to retype it
  // (seen on the test HSI, v2.71.2).
  function fechaParaHsi(iso) {
    var d = S.fechaDdmmaaaa(iso);
    return d.length === 8 ? d.slice(0, 2) + "/" + d.slice(2, 4) + "/" + d.slice(4) : d;
  }

  // The search screen: fill the boxes the row knows about — only the empty
  // ones, never over what is already on screen — and press HSI's «Buscar».
  // HSI answers by routing to its new-patient screen; if somebody gave the
  // patient their alta meanwhile, it answers with the patient's own card.
  function llenarAltaYBuscar() {
    var turno = actual.turno;
    if (D.tarjetaPaciente(turno.dni)) return reanudarTrasElAlta();
    return esperar(function () {
      return D.primero(D.ALTA.nombre) || null;
    }, CARGA).then(function (campo) {
      if (!campo) throw new Fallo("HSI no abrió el alta del paciente. Empadronalo a mano y tocá de nuevo.");
      // A search screen that carries somebody else's document is not ours:
      // the operator moved on to another patient.
      var previo = D.primero(D.ALTA.dni);
      if (previo && S.soloDigitos(previo.value) && S.soloDigitos(previo.value) !== S.soloDigitos(turno.dni)) {
        throw new Fallo("Se cortó el alta del paciente en HSI. Si ya la terminaste, tocá «Cargar en HSI» de nuevo.");
      }
      var partes = S.nombreYApellido(turno.paciente);
      var numero = D.primero(D.ALTA.dni);
      if (numero && !S.soloDigitos(numero.value)) D.escribir(numero, turno.dni);
      var sexo = S.sexoHsi(turno.sexo);
      var radios = D.todos(D.ALTA.radios).filter(D.visible);
      var marcado = radios.filter(function (r) { return r.classList.contains("mat-radio-checked"); })[0];
      if (!marcado && sexo) {
        var radio = radios.filter(function (r) { return S.norm(D.texto(r)) === S.norm(sexo); })[0];
        if (radio) (radio.querySelector("label") || radio).click();
      }
      [[D.ALTA.nombre, partes.nombre], [D.ALTA.apellido, partes.apellido], [D.ALTA.nacimiento, fechaParaHsi(turno.nacimiento)]].forEach(function (par) {
        var c = D.primero(par[0]);
        if (c && par[1] && !String(c.value || "").trim()) D.escribir(c, par[1]);
      });
      resaltar(null);
      marcar("paciente", "curso");
      return respiro();
    }).then(function () {
      var buscar = D.botonBuscarPaciente();
      if (!buscar) throw new Fallo("No encontré el botón Buscar del alta en HSI.");
      resaltar(buscar);
      D.olvidarAvisos();
      return respiro().then(function () { buscar.click(); });
    }).then(function () {
      return esperar(function () {
        if (enPerfilDelPaciente()) return { listo: true };
        if (enAltaPantallaNueva()) return { alta: true };
        if (D.tarjetaPaciente(turno.dni)) return { listo: true };
        // People who look alike, none with this document: whether one of
        // them is this patient is the operator's call, never ours.
        if (D.hayParecidos(turno.dni)) return { parecidos: true };
        // HSI stayed on its search screen and answered with an error notice
        // (the test HSI's Renaper is down and its warning keeps coming back):
        // the search failed, not the patient. Hand the alta to the operator
        // instead of failing — the job stays alive.
        if (enAltaBusqueda()) {
          var mal = D.avisos().filter(function (a) { return a.error; })[0];
          if (mal) return { aviso: mal.texto };
        }
        return null;
      }, CARGA);
    }).then(function (r) {
      if (r && r.parecidos) return esperarAltaConAviso("", "HSI encontró pacientes parecidos. Fijate si es alguno; si no, seguí con el alta. Con el paciente guardado, sigo con el turno yo.");
      if (r && r.aviso) return esperarAltaConAviso(r.aviso);
      if (!r) throw new Fallo("HSI no respondió a la búsqueda del alta.");
      resaltar(null);
      return r.listo ? reanudarTrasElAlta() : esperarElAlta();
    });
  }

  // HSI's search screen answered with an error notice. The alta is the
  // operator's now — retry the «Buscar» or finish the alta by hand, the row
  // says so with HSI's own answer — and the job waits: the moment the patient
  // exists (its profile, its card or the new-patient screen), the turno
  // continues by itself. Ten minutes cap it, like every operator wait.
  function esperarAltaConAviso(textoHsi, propio) {
    var dni = actual.turno.dni;
    estado("alta", propio || "HSI respondió: " + textoHsi);
    mensaje(propio || "HSI respondió: " + textoHsi + " Volvé a tocar «Buscar» o completá el alta a mano; con el paciente guardado, sigo con el turno yo.", "espera");
    marcar("paciente", "espera");
    resaltar(propio ? null : D.botonBuscarPaciente());
    var fueraDesde = 0;
    return esperar(function () {
      if (enPerfilDelPaciente() || avisoPacienteCreado()) return { listo: true };
      if (enAltaPantallaNueva()) return { alta: true };
      if (D.tarjetaPaciente(dni)) return { listo: true };
      if (enAltaBusqueda()) {
        fueraDesde = 0;
      } else if (!fueraDesde) {
        fueraDesde = Date.now();
      } else if (Date.now() - fueraDesde > 1500) {
        return { abandono: true };
      }
      D.olvidarAvisos();
      return null;
    }, OPERADOR).then(function (r) {
      if (r && r.listo) return reanudarTrasElAlta();
      if (r && r.alta) return esperarElAlta();
      throw new Fallo(r && r.abandono
        ? "Se cortó el alta del paciente en HSI. Si ya la terminaste, tocá «Cargar en HSI» de nuevo."
        : "El alta del paciente no terminó: pasaron diez minutos sin guardar. Tocá «Cargar en HSI» de nuevo.");
    });
  }

  // The new-patient screen. Everything the row carries is already in (HSI
  // prefilled it); the rest — address, phone, coverage — is the operator's.
  // The load waits for HSI's own evidence that the patient exists; with
  // «guardar solo» on it presses «Guardar» itself. A question from HSI is
  // never answered for the operator.
  function esperarElAlta() {
    marcar("paciente", "espera");
    resaltar(null);
    D.olvidarAvisos();
    estado("alta", "Terminá el alta en HSI: con el paciente guardado, el turno sigue solo.");
    if (!actual.ajustes.guardarSolo) {
      mensaje("Revisá los datos y tocá Guardar. Con el paciente creado, sigo con el turno yo.", "espera");
      return vigilarElAlta(false);
    }
    return respiro().then(function () {
      var guardar = D.botonPorTexto("Guardar");
      if (!guardar) throw new Fallo("No encontré el botón Guardar del alta en HSI.");
      resaltar(guardar);
      return respiro().then(function () { D.olvidarAvisos(); guardar.click(); });
    }).then(function () {
      return vigilarElAlta(true);
    });
  }

  // Watches HSI for the patient: the profile it opens for the new patient,
  // or its own success notice. The operator leaving the alta screen ends the
  // job — HSI's own screens slide while it routes, so "gone" has to hold for
  // a moment first. `nuestro` = «guardar solo»: the press was ours, so HSI's
  // refusal is ours to report; and a press that landed before HSI was
  // listening gets a couple of retries.
  function vigilarElAlta(nuestro) {
    var preguntando = false, toques = nuestro ? 1 : 0, ultimoToque = Date.now(), fueraDesde = 0;
    return esperar(function () {
      if (enPerfilDelPaciente() || avisoPacienteCreado()) return { listo: true };
      if (enAltaPantallaNueva()) {
        fueraDesde = 0;
      } else if (!fueraDesde) {
        fueraDesde = Date.now();
      } else if (Date.now() - fueraDesde > 1500) {
        return { abandono: true };
      }
      var avisos = D.avisos();
      if (nuestro && avisos.filter(function (a) { return a.error; })[0]) {
        return { error: avisos.filter(function (a) { return a.error; })[0].texto };
      }
      if (!nuestro) D.olvidarAvisos();
      if (D.pregunta() && !preguntando) {
        preguntando = true;
        resaltar(null);
        mensaje("HSI te hace una pregunta. Respondela vos y sigo.", "espera");
      } else if (!D.pregunta() && preguntando) {
        preguntando = false;
        mensaje("Revisá los datos y tocá Guardar. Con el paciente creado, sigo con el turno yo.", "espera");
      }
      if (nuestro && !preguntando && toques < 4 && Date.now() - ultimoToque > 8000 && enAltaPantallaNueva()) {
        var guardar = D.botonPorTexto("Guardar");
        if (guardar) {
          toques++;
          ultimoToque = Date.now();
          D.olvidarAvisos();
          guardar.click();
        }
      }
      return null;
    }, OPERADOR).then(function (r) {
      if (r && r.listo) return reanudarTrasElAlta();
      if (r && r.error) throw new Fallo("HSI respondió: " + r.error);
      throw new Fallo(r && r.abandono
        ? "Se cortó el alta del paciente en HSI. Si ya la terminaste, tocá «Cargar en HSI» de nuevo."
        : "El alta del paciente no terminó: pasaron diez minutos sin guardar. Tocá «Cargar en HSI» de nuevo.");
    });
  }

  // HSI's own evidence says the patient exists. The same job picks the turno
  // up again from the start: the stage and the once-per-job navigation mark
  // go first, then the whole chain runs as if the row had just been pressed.
  function reanudarTrasElAlta() {
    marcar("paciente", "listo");
    mensaje("");
    estado("cargando");
    return chrome.runtime.sendMessage({ tipo: "etapa", etapa: null }).catch(function () {}).then(function () {
      sessionStorage.removeItem(MARCA_NAVEGADO);
      return abrirTurnos()
        .then(elegirProfesional)
        .then(elegirAgenda)
        .then(abrirHorario)
        .then(buscarPaciente)
        .then(function (r) { return r ? r : completarContacto().then(confirmar); })
        .then(function (r) { return r || {}; });
    });
  }

  // What the alta needs and the row does not carry is asked right here, in
  // the panel, and the load goes on with the answer: nobody is sent back to
  // Turnos to fix the row and start over. The answer travels with the job
  // (memory only), so it survives HSI's page changes.
  function pedirDatosDelAlta() {
    var turno = actual.turno;
    var campos = [];
    if (!String(turno.paciente || "").trim()) campos.push({ clave: "paciente", etiqueta: "Nombre y apellido", tipo: "text" });
    if (!S.sexoHsi(turno.sexo)) campos.push({ clave: "sexo", etiqueta: "Sexo", opciones: [["F", "Femenino"], ["M", "Masculino"], ["X", "X"]] });
    if (!S.fecha(turno.nacimiento)) campos.push({ clave: "nacimiento", etiqueta: "Fecha de nacimiento", tipo: "date" });
    if (!campos.length || !panel) return Promise.resolve();
    marcar("paciente", "espera");
    resaltar(null);
    var pedido ="HSI no tiene a este paciente. Completá lo que falta y sigo con el alta.";
    mensaje(pedido, "espera");
    estado("alta", pedido);
    var form = crear("form", "hr-hsi-datos");
    campos.forEach(function (c) {
      var fila = crear("label", "hr-hsi-dato");
      fila.appendChild(crear("span", "", c.etiqueta));
      var entrada;
      if (c.opciones) {
        entrada = crear("select");
        entrada.appendChild(crear("option", "", ""));
        c.opciones.forEach(function (o) {
          var op = crear("option", "", o[1]);
          op.value = o[0];
          entrada.appendChild(op);
        });
      } else {
        entrada = crear("input");
        entrada.type = c.tipo;
      }
      entrada.name = c.clave;
      entrada.required = true;
      fila.appendChild(entrada);
      form.appendChild(fila);
    });
    var seguir = crear("button", "hr-hsi-accion hr-hsi-accion--principal", "Seguir");
    seguir.type = "submit";
    form.appendChild(seguir);
    panel.querySelector(".hr-hsi-acciones").before(form);
    var respuesta = null;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var datos = {};
      campos.forEach(function (c) { datos[c.clave] = String(form.elements[c.clave].value || "").trim(); });
      if (datos.nacimiento != null && !S.fecha(datos.nacimiento)) return;
      respuesta = datos;
    });
    return esperar(function () { return respuesta; }, OPERADOR).then(function (datos) {
      form.remove();
      if (!datos) throw new Fallo("Se acabó el tiempo de espera. Tocá «Cargar en HSI» de nuevo.");
      Object.keys(datos).forEach(function (k) { turno[k] = datos[k]; });
      mensaje("");
      estado("cargando");
      return chrome.runtime.sendMessage({ tipo: "datos", datos: datos }).catch(function () {});
    });
  }

  function darDeAlta() {
    return pedirDatosDelAlta().then(crearPaciente);
  }

  function crearPaciente() {
    var dlg = D.dialogo();
    var crear = dlg && D.botonCrearPaciente(dlg);
    if (!crear) throw new Fallo("HSI no tiene a este paciente. Empadronalo en HSI y tocá «Cargar en HSI» de nuevo.");
    estado("cargando");
    // The click navigates away from the agenda, and a navigation may take
    // this script with it: mark the stage first, so the page that loads next
    // picks the job up where it stands instead of starting over.
    return chrome.runtime.sendMessage({ tipo: "etapa", etapa: "alta" }).then(function () {
      resaltar(crear);
      return respiro();
    }).then(function () {
      crear.click();
      return llenarAltaYBuscar();
    });
  }

  // ---- the contact step ----------------------------------------------------

  // What HSI already holds for the patient wins: only empty fields are filled.
  function completarContacto() {
    var turno = actual.turno;
    marcar("contacto", "curso");
    var dlg = D.dialogo();
    var c = S.contacto(turno.contacto);
    var tel = S.telefonoPartes(c.telefono);
    var campos = [
      [D.primero("#phonePrefix", dlg), tel.prefijo],
      [D.primero("#phoneNumber", dlg), tel.prefijo ? tel.numero : ""],
      [D.primero("#email-input", dlg), c.email]
    ];
    var uno = function (i) {
      if (i >= campos.length) return Promise.resolve();
      var campo = campos[i][0], valor = campos[i][1];
      if (!campo || !valor || campo.value || campo.disabled) return uno(i + 1);
      resaltar(campo);
      return respiro().then(function () { D.escribir(campo, valor); return uno(i + 1); });
    };
    return uno(0).then(function () { return elegirCobertura(); }).then(function () {
      resaltar(null);
      marcar("contacto", "listo");
    });
  }

  // HSI lists here the coverages the patient's record already holds (and
  // nothing else: «Actualizar Cobertura» is an action, not a coverage). The
  // row's «cobertura» may name one of them; it is chosen only on an
  // unambiguous match. HSI's own preselect is a default, not the operator's
  // hand: the row's coverage wins over it, and without a clear match HSI's
  // value stands.
  function elegirCobertura() {
    var turno = actual.turno;
    var dlg = D.dialogo();
    var sel = dlg && D.selectCobertura(dlg);
    if (!sel || !String(turno.cobertura || "").trim()) return Promise.resolve();
    if (S.coincidencia(turno.cobertura, D.texto(sel)) === 1) return Promise.resolve();
    resaltar(sel);
    return respiro().then(function () {
      (sel.querySelector(".mat-select-trigger") || sel).click();
      return esperar(function () { return D.opciones().length ? D.opciones() : null; }, 8000);
    }).then(function (ops) {
      if (!ops) return { cerrar: true };
      var candidatas = ops.filter(function (o) {
        var t = S.norm(D.texto(o));
        return t && t !== "sin cobertura" && t !== "actualizar cobertura";
      });
      return S.mejor(turno.cobertura, candidatas, D.texto, 0.6) || { cerrar: true };
    }).then(function (elegida) {
      if (elegida && !elegida.cerrar) {
        return respiro().then(function () { elegida.click(); return null; });
      }
      // Nothing matched beyond doubt: close the list, HSI's value stands.
      var fondo = D.primero(".cdk-overlay-backdrop");
      if (fondo) fondo.click();
      else document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", keyCode: 27, which: 27, bubbles: true }));
      return null;
    });
  }

  // ---- the save ------------------------------------------------------------

  // "Guardado" is HSI's word, not ours: its success notice about the turno
  // while the dialog closes. Anything else is not a save.
  // `solo` = the extension pressed Confirmar itself. A question from HSI is
  // never answered for the operator: from there on the turno is theirs.
  function esperarGuardado(solo) {
    var cerradoDesde = 0, preguntando = false, desde = Date.now();
    var aLaEspera = function (txt) {
      marcar("confirmar", "espera");
      mensaje(txt, "espera");
      estado("esperando", txt);
    };
    return esperar(function () {
      var as = D.avisos();
      var bien = as.filter(function (a) { return a.exito && /turno/i.test(a.texto); })[0];
      if (bien && !D.dialogo()) return { guardado: true };
      var mal = as.filter(function (a) { return a.error; })[0];
      if (mal && D.dialogo()) return { error: mal.texto };
      if (D.dialogo()) {
        cerradoDesde = 0;
        if (D.pregunta() && !preguntando) {
          preguntando = true;
          solo = false;
          resaltar(null);
          aLaEspera("HSI te hace una pregunta. Respondela vos y sigo.");
        } else if (!D.pregunta() && preguntando) {
          preguntando = false;
          aLaEspera("Revisá los datos y tocá Confirmar.");
        }
        if (solo && Date.now() - desde > 30000) return { vencido: true };
        return null;
      }
      // Closed with no notice yet: the notice can trail the dialog.
      if (!cerradoDesde) cerradoDesde = Date.now();
      return Date.now() - cerradoDesde > 3000 ? { cerrado: true } : null;
    }, OPERADOR);
  }

  function confirmar() {
    marcar("confirmar", "curso");
    var dlg = D.dialogo();
    // The operator pressed Confirmar while the contact was still being
    // filled: the window is gone, and HSI's notice says whether it saved.
    if (!dlg) return esperarGuardado(false);
    // HSI asked something on top of the window (the patient already has a
    // turno that day): the answer is the operator's, and the save is watched
    // from here — the Confirmar button is unreachable under the question.
    if (D.pregunta()) return esperarGuardado(false);
    var botonConfirmar = D.botonPorTexto("Confirmar", dlg);
    if (!botonConfirmar) throw new Fallo("No encontré el botón Confirmar en HSI.");
    resaltar(botonConfirmar);
    D.olvidarAvisos();
    if (actual.ajustes.guardarSolo) {
      return respiro().then(function () {
        botonConfirmar.click();
        return esperarGuardado(true);
      });
    }
    marcar("confirmar", "espera");
    mensaje("Revisá los datos y tocá Confirmar.", "espera");
    estado("esperando", "Revisá los datos en HSI y tocá Confirmar.");
    var mirar = function () {
      return esperarGuardado(false).then(function (r) {
        if (r && r.error) {
          // HSI refused; the dialog is still open for the operator to fix it.
          mensaje("HSI respondió: " + r.error, "error");
          D.olvidarAvisos();
          return mirar();
        }
        return r;
      });
    };
    return mirar();
  }

  function ofrecerGuardarSolo(guardados) {
    if (actual.ajustes.guardarSolo || !S.debeOfrecer(guardados)) return false;
    mensaje("¿Querés que la próxima vez lo guarde solo?", "");
    acciones([
      { texto: "Sí, guardá solo", principal: true, alTocar: function () {
        chrome.storage.local.set({ guardarSolo: true });
        mensaje("Listo. Lo cambiás cuando quieras desde los ajustes.", "bien");
        acciones([]);
        setTimeout(quitarPanel, 5000);
      } },
      { texto: "No, gracias", alTocar: quitarPanel }
    ]);
    return true;
  }

  // ---- a turno HSI already has ---------------------------------------------

  // What Human Rounds changed after the load, done in HSI the way the desk
  // would: open the agenda at the turno's place, open the turno, and press the
  // one button that does it. `turno.accion` says which ("cancelar" or
  // "presente"); the turno carries the place HSI holds (the panel puts the old
  // day, hour and doctor there), so the same steps that find an agenda find it.
  // HSI's own notice is the only proof: nothing here says "done" by itself.
  var PASOS_CAMBIO = {
    cancelar: [
      ["turnos", "Abrir Gestión de turnos"],
      ["profesional", "Buscar al profesional"],
      ["agenda", "Elegir la agenda"],
      ["horario", "Ir al día y la hora"],
      ["turno", "Abrir el turno"],
      ["confirmar", "Cancelar el turno"]
    ],
    presente: [
      ["turnos", "Abrir Gestión de turnos"],
      ["profesional", "Buscar al profesional"],
      ["agenda", "Elegir la agenda"],
      ["horario", "Ir al día y la hora"],
      ["turno", "Abrir el turno"],
      ["confirmar", "Marcar que vino"]
    ]
  };

  // Written in HSI's required «Motivo de cancelación»: no name, no document.
  var MOTIVO_CANCELACION = "Cancelado desde Human Rounds Turnos";

  // The turno on the grid: the one at that hour of that day whose window
  // names that DNI. The grid shows hour and name, never the document, so the
  // window is opened and read; a turno of somebody else is closed untouched.
  function abrirElTurno() {
    var turno = actual.turno;
    marcar("horario", "curso");
    return irALaSemana().then(function () {
      return esperar(function () {
        return S.columnaDelDia(turno.dia, D.encabezados()) !== -1 && D.etiquetasDeHora().length;
      }, 15000);
    }).then(function () { return pausa(700); }).then(function () {
      marcar("horario", "listo");
      marcar("turno", "curso");
      var hora = String(turno.hora).slice(0, 5);
      var propios = function () {
        return D.eventosDelDia(S.columnaDelDia(turno.dia, D.encabezados())).filter(function (ev) {
          return S.horaDeEvento(D.texto(ev)) === hora;
        });
      };
      var cuantos = propios().length;
      var probar = function (i) {
        var ev = propios()[i];
        if (!ev) {
          throw new Fallo(cuantos
            ? "El turno de las " + hora + " en HSI es de otra persona. No lo toqué."
            : "No encontré este turno en la agenda de HSI (¿ya estaba cancelado?). Fijate en HSI.");
        }
        resaltar(ev);
        return respiro().then(function () {
          ev.click();
          return esperar(D.turnoAbierto, 15000);
        }).then(function (dlg) {
          if (!dlg) throw new Fallo("HSI no abrió el turno.");
          if (S.dniDeVentana(D.texto(dlg)) === S.soloDigitos(turno.dni)) return dlg;
          D.cerrarTurnoAbierto();
          return esperar(function () { return !D.primero("mat-dialog-container"); }, 5000).then(function () {
            return probar(i + 1);
          });
        });
      };
      return probar(0);
    }).then(function (dlg) {
      resaltar(null);
      marcar("turno", "listo");
      return dlg;
    });
  }

  // HSI's verdict on a button of its own: its success notice (matching `exito`)
  // is the proof; the window it opened closing with no notice is not.
  function esperarResultado(ventana, exito, solo) {
    var cerradoDesde = 0, desde = Date.now();
    return esperar(function () {
      var as = D.avisos();
      if (as.filter(function (a) { return a.exito && exito.test(a.texto); })[0]) return { guardado: true };
      var mal = as.filter(function (a) { return a.error; })[0];
      if (mal && ventana()) return { error: mal.texto };
      if (ventana()) {
        cerradoDesde = 0;
        if (solo && Date.now() - desde > 30000) return { vencido: true };
        return null;
      }
      if (!cerradoDesde) cerradoDesde = Date.now();
      return Date.now() - cerradoDesde > 3000 ? { cerrado: true } : null;
    }, OPERADOR);
  }

  // The last press is HSI's own button: the extension fills and stops, and the
  // operator presses it — unless `solo` (the extension presses it).
  function pulsarFinal(boton, ventana, exito, solo, aviso) {
    resaltar(boton);
    D.olvidarAvisos();
    if (solo) {
      return respiro().then(function () {
        boton.click();
        return esperarResultado(ventana, exito, true);
      });
    }
    marcar("confirmar", "espera");
    mensaje(aviso, "espera");
    estado("esperando", aviso);
    var mirar = function () {
      return esperarResultado(ventana, exito, false).then(function (r) {
        if (r && r.error) {
          mensaje("HSI respondió: " + r.error, "error");
          D.olvidarAvisos();
          return mirar();
        }
        return r;
      });
    };
    return mirar();
  }

  function cancelarEnHsi(dlg) {
    marcar("confirmar", "curso");
    var abrir = D.botonCancelarTurno(dlg);
    if (!abrir) throw new Fallo("HSI no deja cancelar este turno (¿ya se atendió o se canceló?). Fijate en HSI.");
    resaltar(abrir);
    return respiro().then(function () {
      abrir.click();
      return esperar(function () { return D.dialogoCancelar() && D.motivoDeCancelacion() && D.botonSiCancelar(); }, 10000);
    }).then(function (ok) {
      if (!ok) throw new Fallo("HSI no abrió la cancelación del turno.");
      D.escribir(D.motivoDeCancelacion(), MOTIVO_CANCELACION);
      // A cancellation cannot be undone: the last press is the operator's
      // even with «guardar solo» on — that setting was earned by saving turnos.
      return pulsarFinal(D.botonSiCancelar(), D.dialogoCancelar, /cancelad/i, false,
        "Tocá «Si, cancelar turno» en HSI para cancelarlo.");
    });
  }

  function marcarPresenteEnHsi(dlg) {
    marcar("confirmar", "curso");
    var botones = D.botonesDeEstado(dlg);
    var confirmado = botones[1];
    if (!confirmado) throw new Fallo("HSI no muestra el estado del turno. Marcalo a mano.");
    // Already as the desk wants it: nothing to press.
    if (confirmado.classList.contains("confirmed") || confirmado.classList.contains("selected")) {
      return Promise.resolve({ guardado: true });
    }
    if (confirmado.disabled) throw new Fallo("HSI no deja marcar este turno como confirmado. Marcalo a mano.");
    resaltar(confirmado);
    D.olvidarAvisos();
    return respiro().then(function () {
      confirmado.click();
      return esperar(function () { return D.botonConfirmarTurno(); }, 10000);
    }).then(function (ok) {
      if (!ok) throw new Fallo("HSI no pidió confirmar el turno.");
      return pulsarFinal(D.botonConfirmarTurno(), D.preguntaDeConfirmado, /estado de turno actualizado/i,
        actual.ajustes.guardarSolo, "Tocá «No, confirmar turno» en HSI para marcar que vino.");
    });
  }

  function cambiar() {
    var accion = actual.turno.accion;
    return abrirTurnos()
      .then(elegirProfesional)
      .then(elegirAgenda)
      .then(abrirElTurno)
      .then(accion === "cancelar" ? cancelarEnHsi : marcarPresenteEnHsi);
  }

  // How a change ends well: the same proof as a load, a different sentence.
  function bienCambio(r, mio) {
    resaltar(null);
    var accion = actual && actual.turno.accion;
    if (r && r.guardado) {
      var dicho = accion === "cancelar" ? "Turno cancelado en HSI." : "Turno confirmado en HSI.";
      marcar("confirmar", "listo");
      estado("guardado", dicho, true);
      if (panel) panel.dataset.fin = "1";
      mensaje(dicho, "bien");
      setTimeout(function () { if (actual === mio || !actual) quitarPanel(); }, 4000);
      actual = null;
      return Promise.resolve();
    }
    if (r && r.error) throw new Fallo("HSI respondió: " + r.error);
    throw new Fallo(r && r.cerrado ? "La ventana de HSI se cerró sin cambiar el turno." : "HSI no confirmó el cambio.");
  }

  // ---- job -----------------------------------------------------------------

  function cancelar() {
    if (actual) actual.cancelado = true;
    resaltar(null);
    quitarPanel();
    estado("reposo", "", true);
    actual = null;
  }

  // How every job ends well: { guardado } confirmed by HSI itself.
  function bien(r, mio) {
    resaltar(null);
    if (r && r.guardado) {
      marcar("confirmar", "listo");
      return chrome.storage.local.get({ guardados: 0 }).then(function (a) {
        var n = a.guardados + 1;
        chrome.storage.local.set({ guardados: n });
        estado("guardado", "", true);
        if (panel) panel.dataset.fin = "1";
        mensaje("Turno guardado en HSI.", "bien");
        if (!ofrecerGuardarSolo(n)) setTimeout(function () { if (actual === mio || !actual) quitarPanel(); }, 4000);
        actual = null;
      });
    }
    if (r && r.error) throw new Fallo("HSI respondió: " + r.error);
    throw new Fallo(r && r.cerrado ? "La ventana de HSI se cerró sin guardar el turno." : "HSI no confirmó el turno.");
  }

  function mal(e, mio) {
    // A turno that is no longer the current one has nothing left to say.
    if (e instanceof Cancelado || mio.cancelado || actual !== mio) return Promise.resolve();
    resaltar(null);
    var motivo = e instanceof Fallo ? e.motivo : "Algo en HSI cambió y no pude seguir. Cargá este turno a mano.";
    var enCurso = panel && panel.querySelector('[data-estado="curso"], [data-estado="espera"]');
    if (enCurso) enCurso.dataset.estado = "error";
    if (panel) panel.dataset.fin = "1";
    mensaje(motivo, "error");
    acciones([{ texto: "Cerrar", alTocar: quitarPanel }]);
    estado("error", motivo, true);
    actual = null;
    return Promise.resolve();
  }

  function correr(trabajo, ajustes) {
    if (actual && !actual.cancelado) return;
    actual = { turno: trabajo.turno, ajustes: ajustes, creado: trabajo.creado, cancelado: false };
    var mio = actual;
    var cambio = PASOS_CAMBIO[trabajo.turno.accion];
    armarPanel(trabajo.turno, cambio);
    estado("cargando");
    // A change to a turno HSI already holds (cancel, mark present) has its own
    // short road and its own ending.
    if (cambio) {
      cambiar().then(function (r) { return bienCambio(r, mio); }).then(null, function (e) { return mal(e, mio); });
      return;
    }
    // A job parked inside the patient alta picks up from where HSI left it,
    // on whatever page it woke up on; anything else loads the whole turno.
    // The alta branch resolves with the turno's own final result — a truthy
    // r below means the turno is already done.
    var cadena = trabajo.etapa === "alta"
      ? Promise.resolve().then(altaPendiente)
      : abrirTurnos()
        .then(elegirProfesional)
        .then(elegirAgenda)
        .then(abrirHorario)
        .then(buscarPaciente)
        .then(function (r) {
          if (r) return r;
          return completarContacto().then(confirmar);
        });
    // `bien` throws a Fallo when HSI did not confirm: it must reach `mal`, so
    // the rejection handler goes in a trailing catch, not beside `bien`.
    cadena.then(function (r) { return bien(r, mio); }).catch(function (e) { return mal(e, mio); });
  }

  // ---- auto mode -----------------------------------------------------------

  // The operator types a DNI in «Nuevo turno»'s search and moves on; if that
  // DNI is exactly one of the turnos of the last «Pacientes de hoy» read,
  // the rest fills itself. Everything runs out of the operator's way: if
  // they touched a field first, their value stays.
  function alDejarElDni(campo) {
    if (!misAjustes || misAjustes.modo !== "auto") return;
    if (actual && !actual.cancelado) return;
    var dlg = D.dialogo();
    if (!dlg || enUltimoPaso(dlg) || D.pregunta()) return;
    var dni = S.soloDigitos(campo.value);
    if (dni.length < 6) return;
    pausa(300).then(function () {
      return chrome.runtime.sendMessage({ tipo: "turnos-conocidos", dni: dni });
    }).then(function (r) {
      if (!r || !r.ok || !r.turno) return null;
      // Still the same screen, still the same DNI, nobody else working?
      if ((actual && !actual.cancelado) || D.pregunta()) return null;
      var dlg2 = D.dialogo();
      if (!dlg2 || enUltimoPaso(dlg2)) return null;
      var numero = D.primero("#identifNumber", dlg2);
      if (!numero || S.soloDigitos(numero.value) !== dni) return null;
      return r.turno;
    }).then(function (turno) {
      if (turno) empezarAuto(turno);
    }).catch(function () {});
  }

  function empezarAuto(turno) {
    chrome.runtime.sendMessage({ tipo: "auto", turno: turno }).then(function (r) {
      if (!r || !r.ok) return;
      actual = { turno: turno, ajustes: misAjustes, creado: Date.now(), cancelado: false, auto: true };
      var mio = actual;
      armarPanel(turno, PASOS.slice(4));
      estado("cargando");
      var dlg = D.dialogo();
      Promise.resolve(dlg && elegirSexoYBuscar(dlg)).then(function (res) {
        if (!res) throw new Fallo("HSI no respondió a la búsqueda del paciente.");
        if (res.error) {
          if (/no encontrado/i.test(res.error)) throw new Fallo("HSI no tiene a este paciente. Cargalo con el botón de la fila.");
          throw new Fallo("HSI respondió: " + res.error);
        }
        marcar("paciente", "listo");
        return completarContacto().then(confirmar);
      }).then(function (r) { return bien(r, mio); }).catch(function (e) { return mal(e, mio); });
    }).catch(function () {});
  }

  document.addEventListener("focusout", function (e) {
    if (e.target && e.target.id === "identifNumber") alDejarElDni(e.target);
  }, true);

  // ---- wiring --------------------------------------------------------------

  // The window HSI opens on a turno loses what is empty or redundant (the
  // visible layer only; lib/hsi-tidy.js).
  if (self.HRTidy) self.HRTidy.vigilar();

  chrome.runtime.onMessage.addListener(function (msg, _quien, responder) {
    if (msg.tipo === "trabajo") correr(msg.trabajo, msg.ajustes);
    // ocupado: a turno is being loaded right now; a second one must wait.
    if (msg.tipo === "estas") responder({ ok: true, ocupado: !!(actual && !actual.cancelado) });
  });

  chrome.runtime.sendMessage({ tipo: "hsi-lista" }).then(function (r) {
    if (r && r.ajustes) misAjustes = r.ajustes;
    if (r && r.trabajo) correr(r.trabajo, r.ajustes);
  }).catch(function () {});

  // A mode change reaches this page the moment it is saved.
  chrome.storage.onChanged.addListener(function (c) {
    if (c.modo) {
      if (!misAjustes) misAjustes = {};
      misAjustes.modo = c.modo.newValue;
    }
  });
})();
