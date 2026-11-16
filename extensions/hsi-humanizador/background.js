/*
 * background.js — carries one turno from the Turnos tab to the HSI tab and
 * the result back. The service worker may be put to sleep mid-job, so the
 * pending job lives in chrome.storage.session: memory only, never on disk,
 * gone when the browser closes. Nothing here is logged.
 *
 * What may reach chrome.storage.local (disk) is exactly one thing: the
 * recent-loads list in its reduced form (first name + surname initial, last
 * three digits of the DNI, the hour, the outcome) and never a whole name or
 * document. Whole DNIs live in session memory alone, so the popup's «Abrir en
 * HSI» can still copy them while the browser remembers — and says so after.
 */
importScripts("lib/shared.js");

var S = self.HRShared;
var HSI_SCRIPT_ID = "hr-hsi";
var HSI_FILES = ["lib/shared.js", "lib/hsi-dom.js", "lib/hsi-tidy.js", "content-hsi.js"];
var VIGENCIA = 15 * 60 * 1000;

function ajustes() {
  return chrome.storage.local.get(S.DEFAULTS).then(function (a) {
    a.hsiUrl = String(a.hsiUrl || S.DEFAULTS.hsiUrl).replace(/\/+$/, "");
    return a;
  });
}

function trabajos() {
  return chrome.storage.session.get({ trabajos: {} }).then(function (r) { return r.trabajos; });
}

function guardarTrabajos(t) {
  return chrome.storage.session.set({ trabajos: t });
}

// A load is in progress: a small neutral mark on the toolbar icon, nothing
// more, cleared the moment the job ends.
function insignia(texto) {
  var p = chrome.action.setBadgeText({ text: texto });
  if (texto) {
    p = p.then(function () { return chrome.action.setBadgeBackgroundColor({ color: "#5f6b76" }); });
  }
  return p.catch(function () {});
}

function horaLocal() {
  var d = new Date();
  function dos(n) { return n < 10 ? "0" + n : String(n); }
  return dos(d.getHours()) + ":" + dos(d.getMinutes());
}

// The one record that survives the browser, in the only shape that may:
// reduced, at most twenty, newest first.
function registrarReciente(datos, estadoNombre, detalle) {
  if (datos == null || datos.id == null) return Promise.resolve();
  var fila = {
    id: datos.id,
    quien: S.nombreCorto(datos.paciente),
    dni: S.dniCorto(datos.dni),
    hora: horaLocal(),
    estado: estadoNombre,
    detalle: S.sinDnis(detalle).slice(0, 140)
  };
  return chrome.storage.local.get({ recientes: [] }).then(function (r) {
    var lista = [fila].concat((r.recientes || []).filter(function (x) {
      return String(x.id) !== String(fila.id);
    })).slice(0, 20);
    return chrome.storage.local.set({ recientes: lista });
  });
}

// The whole DNI, in memory only, for the popup's «Abrir en HSI».
function recordarDni(id, dni) {
  if (id == null || !dni) return Promise.resolve();
  return chrome.storage.session.get({ dnis: {} }).then(function (r) {
    var m = r.dnis || {};
    m[String(id)] = String(dni);
    var claves = Object.keys(m);
    if (claves.length > 40) delete m[claves[0]];
    return chrome.storage.session.set({ dnis: m });
  });
}

// The HSI address is the operator's setting, so its content script is
// registered at runtime for exactly that origin and no other.
// One at a time: install and startup both ask for it, and two overlapping
// unregister/register pairs can end with nothing registered.
var registro = Promise.resolve();
function registrarHsi() {
  registro = registro.then(registrarAhora, registrarAhora);
  return registro;
}

function registrarAhora() {
  return ajustes().then(function (a) {
    var patron = S.patronDeOrigen(a.hsiUrl);
    if (!patron) return;
    return chrome.permissions.contains({ origins: [patron] }).then(function (tiene) {
      return chrome.scripting.getRegisteredContentScripts({ ids: [HSI_SCRIPT_ID] }).then(function (ya) {
        var quitar = ya.length ? chrome.scripting.unregisterContentScripts({ ids: [HSI_SCRIPT_ID] }) : Promise.resolve();
        return quitar.then(function () {
          if (!tiene) return;
          return chrome.scripting.registerContentScripts([{
            id: HSI_SCRIPT_ID, matches: [patron], js: HSI_FILES, css: ["content-hsi.css"],
            runAt: "document_idle", persistAcrossSessions: true
          }]);
        });
      });
    });
  });
}

// The Human Rounds sites the manifest does not name (S.TURNOS_OPCIONALES):
// the row script runs on one only after the operator allowed it, and a tab
// already open there gets it at once, without a reload.
var TURNOS_FILES = ["lib/shared.js", "content-turnos.js"];
var registroTurnos = Promise.resolve();
function registrarTurnos() {
  registroTurnos = registroTurnos.then(registrarTurnosAhora, registrarTurnosAhora);
  return registroTurnos;
}

// The operator's own address («Otra dirección» under «¿Dónde ves los turnos?»)
// rides the same way, as one more site with the id "propio". A site the
// manifest already names never gets a second copy of the row script.
function sitiosDeTurnos() {
  return chrome.storage.local.get({ turnosPropio: "" }).then(function (a) {
    var sitios = S.TURNOS_OPCIONALES.slice();
    var propio = S.origen(a.turnosPropio);
    var fijos = chrome.runtime.getManifest ? chrome.runtime.getManifest().content_scripts : [];
    var yaFijo = (fijos || []).some(function (cs) {
      return (cs.matches || []).some(function (m) { return propio && m.indexOf(propio + "/") === 0; });
    });
    var yaOpcional = sitios.some(function (s) { return S.origen(s.url) === propio; });
    sitios.push({ id: "propio", url: propio && !yaFijo && !yaOpcional ? propio : "" });
    return sitios;
  });
}

function registrarTurnosAhora() {
  return sitiosDeTurnos().then(function (sitios) {
    return Promise.all(sitios.map(registrarSitio));
  }).catch(function () {});
}

function registrarSitio(sitio) {
  var id = "hr-turnos-" + sitio.id;
  var admin = sitio.url ? S.origen(sitio.url) + "/admin/*" : "";
  var permiso = admin ? chrome.permissions.contains({ origins: [S.patronDeOrigen(sitio.url)] }) : Promise.resolve(false);
  return permiso.then(function (tiene) {
    return chrome.scripting.getRegisteredContentScripts({ ids: [id] }).then(function (ya) {
      var igual = ya.length && (ya[0].matches || []).join() === admin;
      if (tiene && igual) return;
      var quitar = ya.length ? chrome.scripting.unregisterContentScripts({ ids: [id] }) : Promise.resolve();
      if (!tiene) return quitar;
      return quitar.then(function () {
        return chrome.scripting.registerContentScripts([{
          id: id, matches: [admin], js: TURNOS_FILES, css: ["content-turnos.css"],
          runAt: "document_idle", persistAcrossSessions: true
        }]);
      }).then(function () {
        return chrome.tabs.query({ url: admin });
      }).then(function (tabs) {
        return Promise.all(tabs.map(function (tab) {
          return chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["content-turnos.css"] })
            .then(function () { return chrome.scripting.executeScript({ target: { tabId: tab.id }, files: TURNOS_FILES }); })
            .catch(function () {});
        }));
      });
    });
  });
}

// Every HSI tab, oldest first. A second HSI tab happens (the operator opens
// one): a job must be looked for in ALL of them before another one starts,
// and it belongs to the oldest free tab, so a stray page never splits a load.
function pestaniasHsi(a) {
  return chrome.tabs.query({ url: S.patronDeOrigen(a.hsiUrl) }).then(function (tabs) {
    return tabs.sort(function (x, y) { return x.id - y.id; });
  });
}

// HSI always comes to the front: its screens only advance while the tab is
// being drawn (a hidden tab freezes mid-step).
function enfocarHsi(a) {
  return pestaniasHsi(a).then(function (tabs) {
    var tab = tabs[0];
    if (tab) {
      return chrome.tabs.update(tab.id, { active: true }).then(function (t) {
        return chrome.windows.update(t.windowId, { focused: true }).then(function () { return t; });
      });
    }
    return chrome.tabs.create({ url: a.hsiUrl + "/" });
  });
}

function avisar(tabId, mensaje) {
  if (tabId == null) return Promise.resolve();
  return chrome.tabs.sendMessage(tabId, mensaje).catch(function () {});
}

function entregar(tabId, trabajo, a) {
  return chrome.tabs.sendMessage(tabId, { tipo: "trabajo", trabajo: trabajo, ajustes: a }).catch(function () {
    // No content script in that tab yet (it predates the extension or the
    // permission): inject it once; it asks for its job as soon as it loads.
    return chrome.scripting.insertCSS({ target: { tabId: tabId }, files: ["content-hsi.css"] })
      .then(function () { return chrome.scripting.executeScript({ target: { tabId: tabId }, files: HSI_FILES }); })
      .catch(function () {});
  });
}

function cargar(turno, origenTabId) {
  return ajustes().then(function (a) {
    // «Solo copiar el DNI» means exactly that: nothing is typed in HSI. A
    // page that still shows the old button is told, not obeyed.
    if (a.modo === "manual") return { ok: false, motivo: "Estás en modo «solo copiar el DNI». Cambialo en los ajustes." };
    var patron = S.patronDeOrigen(a.hsiUrl);
    return chrome.permissions.contains({ origins: [patron] }).then(function (tiene) {
      if (!tiene) {
        chrome.runtime.openOptionsPage();
        return { ok: false, motivo: "Falta permitir la dirección de HSI en los ajustes." };
      }
      return pestaniasHsi(a).then(function (tabs) {
        if (!tabs.length) return abrirYEntregar(null);
        // One turno at a time: a second job would take the first one's place
        // and its result would land on the wrong row. EVERY HSI tab answers —
        // the running job may sit in any of them, not just the first — and
        // the load lands on the oldest free one.
        return Promise.all(tabs.map(function (tab) {
          return chrome.tabs.sendMessage(tab.id, { tipo: "estas" }).catch(function () { return null; });
        })).then(function (respuestas) {
          if (respuestas.some(function (r) { return r && r.ocupado; })) {
            return { ok: false, motivo: "HSI está cargando otro turno. Terminá ese y tocá de nuevo." };
          }
          return abrirYEntregar(tabs[0]);
        });
      });

      function abrirYEntregar(tab) {
        // A new tab starts blank: the job must be on record BEFORE HSI
        // loads, or a fast page asks for its job and finds none.
        var abrir = tab
          ? chrome.tabs.update(tab.id, { active: true }).then(function (t) {
              return chrome.windows.update(t.windowId, { focused: true }).then(function () { return t; });
            })
          : chrome.tabs.create({ url: "about:blank", active: true });
        return abrir.then(function (t) {
          var trabajo = { turno: turno, origenTabId: origenTabId, hsiTabId: t.id, creado: Date.now() };
          return trabajos().then(function (todos) {
            todos[t.id] = trabajo;
            return guardarTrabajos(todos);
          }).then(function () {
            return Promise.all([insignia("↻"), recordarDni(turno.id, turno.dni)]);
          }).then(function () {
            if (tab) return entregar(t.id, trabajo, a);
            return chrome.tabs.update(t.id, { url: a.hsiUrl + "/" });
          }).then(function () { return { ok: true, nueva: !tab }; });
        });
      }
    });
  });
}

function terminar(hsiTabId) {
  return trabajos().then(function (todos) {
    delete todos[hsiTabId];
    return guardarTrabajos(todos);
  });
}

chrome.runtime.onMessage.addListener(function (msg, sender, responder) {
  var tabId = sender.tab && sender.tab.id;

  if (msg.tipo === "cargar") {
    cargar(msg.turno, tabId).then(responder, function () {
      responder({ ok: false, motivo: "No se pudo abrir HSI." });
    });
    return true;
  }

  // Manual mode: the DNI is already on the operator's clipboard (copied by
  // the row itself); here we only bring HSI up and remember the reduced row.
  if (msg.tipo === "manual") {
    ajustes().then(function (a) {
      return chrome.permissions.contains({ origins: [S.patronDeOrigen(a.hsiUrl)] }).then(function (tiene) {
        if (!tiene) {
          chrome.runtime.openOptionsPage();
          return { ok: false, motivo: "Falta permitir la dirección de HSI en los ajustes." };
        }
        return enfocarHsi(a).then(function () {
          return recordarDni(msg.turno.id, msg.turno.dni);
        }).then(function () {
          return registrarReciente(msg.turno, "manual", "");
        }).then(function () { return { ok: true }; });
      });
    }).then(responder, function () { responder({ ok: false, motivo: "No se pudo abrir HSI." }); });
    return true;
  }

  // The last «Pacientes de hoy» the operator had on screen, in memory only:
  // it is what auto mode looks a typed DNI up against.
  if (msg.tipo === "cola") {
    chrome.storage.session.set({ cola: { cuando: Date.now(), turnos: msg.turnos || [] } })
      .then(function () { responder({ ok: true }); }, function () { responder({ ok: false }); });
    return true;
  }

  if (msg.tipo === "turnos-conocidos") {
    chrome.storage.session.get({ cola: null }).then(function (r) {
      var cola = r.cola;
      responder({ ok: true, turno: cola ? S.turnoPorDni(cola.turnos, msg.dni) : null });
    }, function () { responder({ ok: true, turno: null }); });
    return true;
  }

  // Auto mode starts its own (lighter) job in the HSI tab the operator is
  // already driving; it is never resumed after a reload.
  if (msg.tipo === "auto") {
    trabajos().then(function (todos) {
      if (todos[tabId] && !todos[tabId].auto) return responder({ ok: false });
      todos[tabId] = { turno: msg.turno, origenTabId: null, hsiTabId: tabId, creado: Date.now(), auto: true };
      return guardarTrabajos(todos).then(function () {
        return Promise.all([insignia("↻"), recordarDni(msg.turno.id, msg.turno.dni)]);
      }).then(function () { responder({ ok: true }); });
    }).catch(function () { responder({ ok: false }); });
    return true;
  }

  // A job that is about to leave its tab behind (the patient alta navigates
  // away from the agenda) marks where it stands, so the loader that runs
  // after the navigation picks up from there instead of starting over. A
  // null etapa clears the mark: the patient exists and the turno is being
  // loaded again from the start. Every stage change also re-arms the job's
  // lifetime — the operator may take their ten minutes over the alta, and
  // the job must still be alive when the next page asks for it.
  if (msg.tipo === "etapa") {
    trabajos().then(function (todos) {
      if (!todos[tabId]) return null;
      todos[tabId].creado = Date.now();
      if (msg.etapa == null) delete todos[tabId].etapa;
      else todos[tabId].etapa = msg.etapa;
      return guardarTrabajos(todos);
    }).then(function () { responder({ ok: true }); });
    return true;
  }

  // What the operator answered in the panel joins the job's turno (memory
  // only), so the page HSI loads next already has it.
  if (msg.tipo === "datos") {
    trabajos().then(function (todos) {
      if (!todos[tabId]) return null;
      ["paciente", "sexo", "nacimiento"].forEach(function (k) {
        if (msg.datos && msg.datos[k]) todos[tabId].turno[k] = String(msg.datos[k]);
      });
      todos[tabId].creado = Date.now();
      return guardarTrabajos(todos);
    }).then(function () { responder({ ok: true }); });
    return true;
  }

  if (msg.tipo === "abrir-hsi") {
    ajustes().then(function (a) { return enfocarHsi(a); })
      .then(function () { responder({ ok: true }); }, function () { responder({ ok: false }); });
    return true;
  }

  // The HSI tab asks on every page load: a job survives the login screen and
  // a full reload.
  if (msg.tipo === "hsi-lista") {
    Promise.all([trabajos(), ajustes()]).then(function (r) {
      var trabajo = r[0][tabId] || null;
      if (trabajo && trabajo.auto) {
        // An auto job lives and dies with the dialog it is filling.
        trabajo = null;
        terminar(tabId);
      } else if (trabajo && Date.now() - trabajo.creado > VIGENCIA) {
        // A job nobody finished must not start by itself on a later visit.
        trabajo = null;
        terminar(tabId);
      }
      responder({ trabajo: trabajo, ajustes: r[1] });
    });
    return true;
  }

  if (msg.tipo === "estado") {
    trabajos().then(function (todos) {
      var trabajo = todos[tabId] || null;
      var datos = trabajo ? trabajo.turno : { id: msg.id, paciente: msg.paciente, dni: msg.dni };
      var promesa = Promise.resolve();
      if (trabajo && trabajo.origenTabId != null) {
        promesa = avisar(trabajo.origenTabId, { tipo: "estado", id: trabajo.turno.id, estado: msg.estado, detalle: msg.detalle || "" });
      }
      if (!msg.final) return promesa;
      // A final state ends the job for good — including the operator's
      // Cancelar (reposo): a job they stopped must never come back to life
      // on the next page load. Only a save or a failure is remembered.
      var termina = ["guardado", "error", "reposo"].indexOf(msg.estado) !== -1;
      return promesa
        .then(function () {
          if (["guardado", "error"].indexOf(msg.estado) !== -1) return registrarReciente(datos, msg.estado, msg.detalle || "");
        })
        .then(function () { return termina ? terminar(tabId) : null; })
        .then(function () {
          if (!termina) return null;
          insignia("");
          // The quick mode ends where it started: back on the list, with the
          // row saying «Guardado en HSI».
          if (msg.estado === "guardado" && trabajo && trabajo.origenTabId != null) {
            return ajustes().then(function (a) {
              if (a.guardarSolo && !a.mostrarPasos) return chrome.tabs.update(trabajo.origenTabId, { active: true }).catch(function () {});
            });
          }
        });
    }).then(function () { responder({ ok: true }); }, function () { responder({ ok: true }); });
    return true;
  }

  if (msg.tipo === "ajustes-cambiados") {
    Promise.all([registrarHsi(), registrarTurnos()]).then(function () { responder({ ok: true }); });
    return true;
  }

  if (msg.tipo === "abrir-ajustes") {
    chrome.runtime.openOptionsPage();
    responder({ ok: true });
  }
});

// A page of the HSI tab finished loading with a job pending: make sure the
// loader is there even if the registered script was not (first run, or the
// tab was opened before the permission was granted). It asks for its job.
chrome.tabs.onUpdated.addListener(function (tabId, cambio) {
  if (cambio.status !== "complete") return;
  trabajos().then(function (todos) {
    if (!todos[tabId]) return;
    return chrome.tabs.sendMessage(tabId, { tipo: "estas" }).catch(function () {
      return chrome.scripting.insertCSS({ target: { tabId: tabId }, files: ["content-hsi.css"] })
        .then(function () { return chrome.scripting.executeScript({ target: { tabId: tabId }, files: HSI_FILES }); })
        .catch(function () {});
    });
  });
});

chrome.tabs.onRemoved.addListener(function (tabId) {
  trabajos().then(function (todos) {
    var trabajo = todos[tabId];
    if (!trabajo) return;
    if (trabajo.origenTabId != null) {
      avisar(trabajo.origenTabId, { tipo: "estado", id: trabajo.turno.id, estado: "error", detalle: "Se cerró la pestaña de HSI." });
    }
    return terminar(tabId).then(function () { insignia(""); });
  });
});

chrome.runtime.onInstalled.addListener(function (info) {
  registrarHsi();
  registrarTurnos();
  if (info.reason === "install") chrome.tabs.create({ url: chrome.runtime.getURL("options.html") });
});
chrome.runtime.onStartup.addListener(function () {
  registrarHsi();
  registrarTurnos();
  insignia("");
});
// Allowed from the popup, or taken back in chrome://extensions: the row script
// follows the permission, whoever changed it.
chrome.permissions.onAdded.addListener(function () { registrarTurnos(); });
chrome.permissions.onRemoved.addListener(function () { registrarTurnos(); });
