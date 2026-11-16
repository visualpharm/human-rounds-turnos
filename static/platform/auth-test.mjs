/* Native owner for the opt-in identity simulator. No classic twin/global API. */
const mounts = new WeakMap();
const pageState = new WeakMap();

export function mount(document = globalThis.document, options = {}) {
  if (!document || !document.getElementById('tid-rows')) return null;
  if (mounts.has(document)) return mounts.get(document);
  const window = document.defaultView || globalThis.window;
  const location = window.location;
  const shared = pageState.get(document) || { pending: null, revision: 0, identities: [], handedOff: false };
  pageState.set(document, shared);
  let disposed = false, ready = false, listSequence = 0, formSequence = 0;
  let savingStatus = null;
  const listeners = [], timers = new Set();
  // Ownership guards: an async response may only touch the UI state that was
  // current when its request was issued. listEpoch advances on every list
  // request and every completed local mutation; dialogEpoch advances on every
  // draft opened; saveInFlight keeps exactly one save POST per form.
  let listEpoch = 0;
  let dialogEpoch = 0;
  let saveInFlight = false;
  function on(target, type, handler) {
    const guarded = function (event) { if (!disposed) return handler.call(this, event); };
    target.addEventListener(type, guarded);
    listeners.push(() => target.removeEventListener(type, guarded));
  }
  function schedule(callback, delay) {
    const timer = window.setTimeout(() => { timers.delete(timer); if (!disposed) callback(); }, delay);
    timers.add(timer);
  }
  function syncBusy() {
    if (disposed) return;
    const busy = !ready || !!shared.pending || shared.handedOff;
    document.querySelectorAll('.tid-login, #tid-random, #tid-save').forEach(button => { button.disabled = busy; });
    document.querySelectorAll('.tid-edit, #tid-new').forEach(button => { button.disabled = !ready; });
    document.querySelectorAll('.tid-person').forEach(row => row.setAttribute('aria-disabled', String(busy)));
  }
  function mutate(send) {
    if (disposed || shared.pending || shared.handedOff) return null;
    const ticket = {};
    shared.pending = ticket;
    shared.revision++;
    syncBusy();
    // Ownership includes decoding: receiving HTTP headers is not completion.
    // Dispatch immediately so teardown cannot leave an unstarted write queued.
    let response;
    try { response = send(); } catch (error) { response = Promise.reject(error); }
    ticket.promise = Promise.resolve(response).then(async response => ({
      ok: response.ok, json: await response.json(),
    })).finally(() => {
      if (shared.pending === ticket) { shared.pending = null; shared.revision++; }
    });
    return ticket.promise;
  }
  function fetch(url, init) { return window.fetch(url, init); }

  let I18n = null, Format = null, Dates = null;
  function dependency(name, loader) {
    return Promise.resolve().then(() => {
      if (loader) return loader();
      const url = window.SANA_STATIC && window.SANA_STATIC['platform/' + name + '.mjs'];
      if (!url) throw new Error('Missing ' + name + ' module');
      return import(url);
    });
  }
  const dependenciesReady = Promise.all([
    dependency('i18n', options.loadI18n),
    dependency('format', options.loadFormat),
    dependency('dates', options.loadDates),
  ]).then(([i18n, format, dates]) => {
    if (!disposed) { I18n = i18n; Format = format; Dates = dates; }
  });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  var params = new URLSearchParams(location.search);
  var provider = params.get("provider") === "renaper" ? "renaper" : "mi_argentina";
  // RENAPER y Mi Argentina son los nombres propios de los proveedores de
  // identidad del Estado — no se traducen (mismo criterio que DNI).
  var providerLabel = provider === "renaper" ? "RENAPER" : "Mi Argentina";
  var returnTo = safeReturn(params.get("return_to"));
  var rows = document.getElementById("tid-rows");
  var message = document.getElementById("tid-msg");
  var dialog = document.getElementById("tid-dialog");
  var form = document.getElementById("tid-form");
  var identities = shared.identities;
  var loginPending = false;

  document.getElementById("tid-provider").textContent = providerLabel;

  function safeReturn(value) {
    try {
      var target = new URL(value || "/account", location.origin);
      return target.origin === location.origin ? target.href : location.origin + "/account";
    } catch (e) {
      return location.origin + "/account";
    }
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>\"]/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char];
    });
  }

  function say(text, error) {
    if (disposed) return;
    message.textContent = text || "";
    message.className = "tid-msg" + (error ? " err" : "");
  }

  function render() {
    if (disposed) return;
    shared.identities = identities;
    if (!identities.length) {
      rows.innerHTML = '<tr><td class="tid-empty" colspan="12">' +
        T("account.mine.auth_test.empty", null, "Todavía no hay identidades de prueba.") + '</td></tr>';
      return;
    }
    rows.innerHTML = identities.map(function (person) {
      var nombreCompleto = Format.nombre(person.nombre + " " + person.apellido);
      var dniFmt = Format.dni(person.dni);
      return '<tr class="tid-person" tabindex="0" data-dni="' + esc(person.dni) +
        '" title="' + esc(T("account.mine.auth_test.login_as", { name: nombreCompleto }, "Ingresar como " + nombreCompleto)) +
        '" aria-label="' + esc(T("account.mine.auth_test.login_as_dni",
          { name: nombreCompleto, dni: dniFmt },
          "Ingresar como " + nombreCompleto + ", DNI " + dniFmt)) + '">' +
        '<td class="tid-mono">' + esc(Format.dni(person.dni)) + "</td>" +
        '<td class="tid-person-name">' + esc(Format.nombre(person.nombre)) + "</td>" +
        '<td class="tid-person-name">' + esc(Format.nombre(person.apellido)) + "</td>" +
        '<td><button class="tid-btn tid-btn--primary tid-login" type="button" data-dni="' + esc(person.dni) + '">' +
        T("account.mine.auth_test.login", null, "Ingresar") + '</button></td>' +
        '<td><button class="tid-link tid-edit" type="button" data-dni="' + esc(person.dni) + '">' +
        T("account.mine.auth_test.edit", null, "Editar") + '</button></td>' +
        '<td class="tid-mono">' + esc(Dates.nacimiento(person.nacimiento)) + "</td>" +
        "<td>" + esc(person.sexo) + "</td>" +
        '<td class="tid-mono">' + esc(Format.telTexto(person.contacto) || "—") + "</td>" +
        "<td>" + esc(person.email || "—") + "</td>" +
        "<td>" + esc(person.domicilio || "—") + "</td>" +
        "<td>" + esc(person.localidad || "—") + "</td>" +
        '<td class="tid-muted">' + esc(person.origen || "manual") + "</td>" +
        "</tr>";
    }).join("");
    syncBusy();
  }

  function load() {
    var epoch = ++listEpoch;
    const sequence = ++listSequence, revision = shared.revision;
    return fetch("/api/account/test-identities", { credentials: "same-origin" })
      .then(function (response) {
        return response.json().then(function (json) { return { ok: response.ok, json: json }; });
      })
      .then(function (result) {
        if (epoch !== listEpoch || disposed || sequence !== listSequence || revision !== shared.revision) return;
        if (!result.ok) throw new Error(result.json.error || T("account.mine.auth_test.err_load", null, "No se pudieron cargar las identidades."));
        identities = result.json.identidades || [];
        render();
      })
      .catch(function (error) {
        if (epoch !== listEpoch || disposed || sequence !== listSequence || revision !== shared.revision) return;
        if (!identities.length) rows.innerHTML = '<tr><td class="tid-empty" colspan="12">' +
          T("account.mine.auth_test.err_load_rows", null, "No pudimos cargar las identidades.") + '</td></tr>';
        say(error.message, true);
      });
  }

  function openForm(person) {
    if (!ready) return;
    dialogEpoch += 1;
    const owner = ++formSequence;
    person = person || {};
    document.getElementById("tid-form-title").textContent = person.dni
      ? T("account.mine.auth_test.edit_person", null, "Editar persona")
      : T("account.mine.auth_test.create_person", null, "Crear persona");
    document.getElementById("tid-original").value = person.dni || "";
    document.getElementById("tid-dni").value = Format.dni(person.dni);
    document.getElementById("tid-name").value = person.nombre || "";
    document.getElementById("tid-surname").value = person.apellido || "";
    document.getElementById("tid-birth").value = person.nacimiento || "";
    document.getElementById("tid-sex").value = person.sexo || "F";
    document.getElementById("tid-phone").value = Format.telTexto(person.contacto);
    document.getElementById("tid-email").value = person.email || "";
    document.getElementById("tid-address").value = person.domicilio || "";
    document.getElementById("tid-locality").value = person.localidad || "";
    document.getElementById("tid-form-msg").textContent = "";
    dialog.showModal();
    schedule(function () { if (owner === formSequence && dialog.open) document.getElementById("tid-dni").focus(); }, 0);
  }

  function formValue() {
    return {
      dni: document.getElementById("tid-dni").value,
      nombre: document.getElementById("tid-name").value,
      apellido: document.getElementById("tid-surname").value,
      nacimiento: document.getElementById("tid-birth").value,
      sexo: document.getElementById("tid-sex").value,
      contacto: document.getElementById("tid-phone").value,
      email: document.getElementById("tid-email").value,
      domicilio: document.getElementById("tid-address").value,
      localidad: document.getElementById("tid-locality").value,
      origen: document.getElementById("tid-original").value ? "editada" : "manual"
    };
  }

  function login(dni, button) {
    // Row and keyboard activation bypass a disabled button. One session write
    // owns the whole page until failure or the successful handoff completes.
    if (disposed || !ready || loginPending || shared.pending || shared.handedOff) return;
    loginPending = true;
    button.disabled = true;
    var dniFmt = Format.dni(dni);
    say(T("account.mine.auth_test.logging_in", { dni: dniFmt }, "Ingresando como DNI " + dniFmt + "…"));
    mutate(() => fetch("/api/account/test-login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dni: dni, provider: provider })
    })).then(function (result) {
      if (disposed) return;
      if (!result.ok || !result.json.usuario) throw new Error(result.json.error || T("account.mine.auth_test.err_login", null, "No se pudo iniciar la sesión."));
      shared.handedOff = true;
      syncBusy();
      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({ type: "sana-test-auth-complete", usuario: result.json.usuario }, location.origin);
          say(T("account.mine.auth_test.session_started", null, "Sesión iniciada. Volviendo a la aplicación…"));
          return;
        }
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage({ type: "sana-test-auth-complete", usuario: result.json.usuario }, location.origin);
          say(T("account.mine.auth_test.session_started", null, "Sesión iniciada. Volviendo a la aplicación…"));
          window.close();
          schedule(function () { location.href = returnTo; }, 400);
          return;
        }
      } catch (e) {}
      location.href = returnTo;
    }).catch(function (error) {
      if (disposed) return;
      loginPending = false;
      button.disabled = false;
      say(error.message, true);
      syncBusy();
    });
  }

  on(rows, "click", function (event) {
    var editButton = event.target.closest(".tid-edit");
    if (editButton) {
      var editDni = editButton.getAttribute("data-dni");
      openForm(identities.filter(function (person) { return person.dni === editDni; })[0]);
      return;
    }
    var loginButton = event.target.closest(".tid-login");
    if (loginButton) { login(loginButton.getAttribute("data-dni"), loginButton); return; }
    var personRow = event.target.closest(".tid-person");
    if (personRow) login(personRow.getAttribute("data-dni"), personRow.querySelector(".tid-login"));
  });

  on(rows, "keydown", function (event) {
    if ((event.key !== "Enter" && event.key !== " ") || event.target.closest("button")) return;
    var personRow = event.target.closest(".tid-person");
    if (!personRow) return;
    event.preventDefault();
    login(personRow.getAttribute("data-dni"), personRow.querySelector(".tid-login"));
  });

  on(document.getElementById("tid-new"), "click", function () { openForm(); });
  on(document.getElementById("tid-close"), "click", function () { formSequence++; dialog.close(); });
  on(dialog, 'cancel', function () { formSequence++; });
  on(document.getElementById("tid-random"), "click", function () {
    if (!ready || shared.pending || shared.handedOff) return;
    var button = this;
    button.disabled = true;
    say(T("account.mine.auth_test.creating_random", null, "Creando una persona aleatoria…"));
    mutate(() => fetch("/api/account/test-identities/random", { method: "POST", credentials: "same-origin" }))
      .then(function (result) {
        if (disposed) return;
        if (!result.ok) throw new Error(result.json.error || T("account.mine.auth_test.err_create", null, "No se pudo crear la persona."));
        listEpoch += 1;
        identities.push(result.json.identidad);
        render();
        say(T("account.mine.auth_test.random_added", null, "Persona aleatoria agregada a la lista."));
        return load();
      }).catch(function (error) { say(error.message, true); })
      .finally(function () { syncBusy(); });
  });

  on(form, "submit", function (event) {
    event.preventDefault();
    if (!ready || shared.pending || shared.handedOff || saveInFlight) return;
    var save = document.getElementById("tid-save");
    var formMessage = document.getElementById("tid-form-msg");
    if (!form.reportValidity()) return;
    var epoch = dialogEpoch;
    const owner = formSequence;
    saveInFlight = true;
    save.disabled = true;
    formMessage.className = "tid-msg";
    formMessage.textContent = T("account.mine.auth_test.saving", null, "Guardando…");
    savingStatus = { node: formMessage, text: formMessage.textContent };
    const payload = { original_dni: document.getElementById("tid-original").value, identidad: formValue() };
    mutate(() => fetch("/api/account/test-identities", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })).then(function (result) {
      if (disposed) return;
      if (!result.ok) throw new Error(result.json.error || T("account.mine.auth_test.err_save", null, "No se pudo guardar la persona."));
      if (epoch === dialogEpoch && owner === formSequence) {
        dialog.close();
        say(T("account.mine.auth_test.saved", null, "Persona guardada."));
      }
      return load();
    }).catch(function (error) {
      if (disposed || epoch !== dialogEpoch || owner !== formSequence) return;
      formMessage.className = "tid-msg err";
      formMessage.textContent = error.message;
    }).finally(function () { saveInFlight = false; savingStatus = null; save.disabled = false; syncBusy(); });
  });

  const controller = {
    ready: null,
    destroy() {
      if (disposed) return;
      disposed = true;
      // Retire only this owner's transient progress, never a draft or a
      // validation error. The unresolved server write keeps its shared lock.
      if (savingStatus && savingStatus.node.textContent === savingStatus.text)
        savingStatus.node.textContent = '';
      savingStatus = null;
      // A completed handoff is latched for this mount, not a future visit
      // restored from BFCache. In-flight requests retain their separate lock.
      shared.handedOff = false;
      listSequence++; formSequence++;
      listeners.forEach(remove => remove());
      timers.forEach(timer => window.clearTimeout(timer));
      timers.clear();
      mounts.delete(document);
    },
  };
  mounts.set(document, controller);
  on(window, 'pagehide', () => controller.destroy());
  syncBusy();
  const pendingAtMount = shared.pending;
  controller.ready = dependenciesReady.then(function () {
    if (disposed) return;
    ready = true;
    syncBusy();
    var simulatorTitle = T("account.mine.auth_test.title", { provider: providerLabel },
      "Simulador de autenticación " + providerLabel);
    document.getElementById("tid-title").textContent = simulatorTitle;
    document.title = simulatorTitle;
    if (pendingAtMount) {
      return pendingAtMount.promise.catch(() => {}).then(() => {
        if (disposed) return;
        syncBusy();
        return load();
      });
    }
    return load();
  }).catch(error => { controller.destroy(); throw error; });
  return controller;
}
