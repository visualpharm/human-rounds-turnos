/* admin-content.mjs — edición de contenido del sitio (Servicios, Centros, Médicos)
   dentro del admin de Pinamar Turnos. Hash-routing (2026-07-17, en inglés — el
   tipo interno sigue en español, ver TIPO_URL/URL_TIPO más abajo): #services,
   #services/<id>, #services/nuevo, #centers, #centers/<slug>, #doctors,
   #doctors/<id>, y #solicitudes (delegado a admin.html, que ya tenía esa
   vista — no es un tipo de catálogo, se queda como estaba).

   Contrato de API (admin_content.py, mismo host, cookie de sesión ya
   resuelta por el gate de admin.html):
     GET    /api/admin/content/<tipo>            -> {items:[{...doc,_origen,_borrado}]}
     POST   /api/admin/content/<tipo>             body=doc -> {ok,item}
     PUT    /api/admin/content/<tipo>/<id>        body=doc -> {ok,item}
     DELETE /api/admin/content/<tipo>/<id>        -> {ok}
     POST   /api/admin/content/<tipo>/<id>/restaurar -> {ok,item}
   tipo en la URL ∈ centers|services|doctors (TIPO_URL traduce el tipo interno,
   español, al segmento de URL, inglés — igual que TYPE_MAP en admin_content.py).
   El "doc original" para el <details> de comparación se lee directo de
   /data/<archivo>.json (compat route pack-chain aware de server.py, estático,
   sin auth — ver docs/architecture/data-packs.md).
*/
const contentOwners = new WeakMap();

// This module deliberately does nothing until its direct-screen owner calls mount.
// All state below is per mount; replacing a screen first disposes its predecessor.
export function mount(root, options = {}) {
  root = root || document;
  contentOwners.get(root)?.();
  return (function () {
  "use strict";

  var document = root;
  var window = options.window || document.defaultView;
  var location = options.location || window.location;
  var history = options.history || window.history;
  var nativeFetch = options.fetch || window.fetch.bind(window);
  var disposed = false;
  var cleanups = new Set();
  var routeCleanups = new Set();
  var requests = new Set();
  var mutationRequests = new Set();
  var pendingMutations = new Set();
  var mountVersion = 0;
  function CancelledOwnerWork() {
    this.name = "CancelledOwnerWork";
    this.message = "Catalog editor owner is no longer current";
  }
  CancelledOwnerWork.prototype = Object.create(Error.prototype);
  function MutationOutcomeUnconfirmed() {
    this.name = "MutationOutcomeUnconfirmed";
    this.message = T("admin.content.catalog.common.mutation_outcome_unconfirmed", null, "No se pudo confirmar el guardado. Revisá el registro antes de volver a intentar.");
  }
  MutationOutcomeUnconfirmed.prototype = Object.create(Error.prototype);
  function cancelled() { return new CancelledOwnerWork(); }
  function isCancelled(error) { return error && error.name === "CancelledOwnerWork"; }
  function decodeFailure(error) { if (isCancelled(error)) throw error; return {}; }
  function ownCleanup(cleanup) {
    function release() {
      if (!cleanups.delete(release)) return;
      cleanup();
    }
    cleanups.add(release);
    return release;
  }
  function ownRouteCleanup(cleanup) {
    function release() {
      if (!routeCleanups.delete(release)) return;
      cleanup();
    }
    routeCleanups.add(release);
    return release;
  }
  function listen(target, type, handler) {
    target.addEventListener(type, handler);
    var owner = target === document || target === window ? ownCleanup : ownRouteCleanup;
    return owner(function () { target.removeEventListener(type, handler); });
  }
  function claimRouteBinding(target, key) {
    if (target[key]) return false;
    var token = {};
    target[key] = token;
    ownRouteCleanup(function () {
      if (target[key] === token) delete target[key];
    });
    return true;
  }
  function releaseRouteResources() {
    if (releaseCreateAppointment) releaseCreateAppointment();
    releaseCreateAppointment = null;
    Array.from(routeCleanups).forEach(function (release) { release(); });
    Object.keys(CATALOG_TABLE_FIT || {}).forEach(function (key) {
      var table = CATALOG_TABLE_FIT[key];
      if (table && table.dispose) table.dispose();
      delete CATALOG_TABLE_FIT[key];
    });
    // Calendar state belongs to one doctor route, not the document mount.
    MEDCAL_DATA = null;
    MEDCAL_SELECTED_DAY = "";
    MEDCAL_SORTS = {};
    MEDCAL_FILTERS = { q: "", servicio: "", centro: "" };
  }
  function current(version) { return !disposed && version === mountVersion; }
  function invalidateMutationCaches() {
    Object.keys(CACHE || {}).forEach(function (key) { delete CACHE[key]; });
    MED_STATS = null;
    MED_ACCOUNTS = null;
    MED_ACCOUNTS_READ = false;
    SOLICITUDES_CACHE = null;
  }
  function mutationRequest(url, init) {
    var version = mountVersion;
    var controller = new AbortController();
    var acknowledged = init && init.onAcknowledged;
    var config = Object.assign({}, init || {});
    delete config.onAcknowledged;
    var deadline;
    var deadlineTimer;
    var ended = false;
    var bodySettled = false;
    var releaseMutation;
    mutationRequests.add(controller);
    invalidateMutationCaches();
    // A route change cancels readers, not the result of an already submitted
    // write. Consume the acknowledgement once, then allow the replacement
    // route to read fresh server state. Full unmount still releases transport.
    var transport = nativeFetch(url, Object.assign({}, config, { signal: controller.signal }))
      .then(function (response) {
        return response.json().then(function (body) { return { body: body }; },
          function (error) { return { error: error }; }).then(function (result) {
          if (ended || disposed) throw cancelled();
          // This deadline bounds the write acknowledgement, not a dependent
          // photo upload. That upload owns a separate bounded transport.
          bodySettled = true;
          window.clearTimeout(deadlineTimer);
          function rawJson() {
            return result.error ? Promise.reject(result.error) : Promise.resolve(result.body);
          }
          var rawResponse = { status: response.status, ok: response.ok, headers: response.headers,
            json: rawJson, clone: function () { return { json: rawJson }; } };
          return Promise.resolve(acknowledged ? acknowledged({ status: response.status, ok: response.ok, body: result.body, error: result.error }) : null)
            .then(function () { return rawResponse; });
        });
      });
    deadline = new Promise(function (_, reject) {
      releaseMutation = ownCleanup(function () {
        window.clearTimeout(deadlineTimer);
        if (ended) return;
        ended = true;
        reject(cancelled());
        controller.abort();
      });
      deadlineTimer = window.setTimeout(function () {
        if (bodySettled || ended) return;
        ended = true;
        reject(new MutationOutcomeUnconfirmed());
        controller.abort();
      }, 15000);
    });
    var work = Promise.race([transport, deadline]).finally(function () {
        ended = true;
        window.clearTimeout(deadlineTimer);
        releaseMutation();
        mutationRequests.delete(controller);
        pendingMutations.delete(work);
        invalidateMutationCaches();
      });
    pendingMutations.add(work);
    var caller = work.then(function (response) {
      if (!current(version)) throw cancelled();
      function json() {
        if (!current(version)) return Promise.reject(cancelled());
        return response.json();
      }
      return { status: response.status, ok: response.ok, headers: response.headers,
        json: json, clone: function () { return { json: json }; } };
    }, function (error) {
      if (!current(version)) throw cancelled();
      throw error;
    });
    caller.settled = work;
    return caller;
  }
  // Every request and body belongs to this mounted screen. A browser that
  // ignores abort still settles the caller with a typed cancellation; owners
  // handle that explicitly and never leave a permanently pending promise.
  function request(url, init) {
    if (init && init.method && !/^(GET|HEAD)$/i.test(init.method)) return mutationRequest(url, init);
    var controller = new AbortController();
    var version = mountVersion;
    requests.add(controller);
    var config = Object.assign({}, init || {}, { signal: controller.signal });
    return nativeFetch(url, config).then(function (response) {
      if (!current(version)) throw cancelled();
      // Response getters/methods require a real Response receiver in browsers;
      // do not proxy/Object.create it. This small bound interface is the one
      // this controller and admin-session consume.
      var guarded = {
        status: response.status,
        ok: response.ok,
        headers: response.headers,
        clone: response.clone ? response.clone.bind(response) : undefined
      };
      guarded.json = function () {
        return response.json().then(function (data) {
          requests.delete(controller);
          if (!current(version)) throw cancelled();
          return data;
        }, function (error) {
          requests.delete(controller);
          if (!current(version)) throw cancelled();
          throw error;
        });
      };
      return guarded;
    }, function (error) {
      requests.delete(controller);
      if (!current(version) || error && error.name === "AbortError") throw cancelled();
      throw error;
    });
  }
  var fetch = request;

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var I18n = null;
    var i18nReady = (window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; }).catch(function (error) {
        if (isCancelled(error)) return null;
        throw error;
      })
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
  // calendar-grid.mjs (la grilla mensual del tab Calendario del editor de
  // médicos) es un módulo ES canónico servido con huella (core/static_fingerprint.py,
  // CLIENT_LOADED): un archivo servido no puede conocer su URL vigente, así
  // que el shell la publica como window.SANA_STATIC y staticUrl() resuelve a
  // través de ese mapa — fail-closed (null + console.error, nunca una URL
  // fallback hardcodeada), la misma forma que islands.mjs's resolveUrl().
  // importStatic() rechaza ante una entrada ausente exactamente igual que un
  // import() que falló en cargar. La isla MedCal importa el MISMO módulo por
  // el bare specifier "sana/calendar-grid" del import map (y vuelve a inyectar
  // el css por su cuenta, idempotente); acá el css de la grilla se inyecta
  // apenas resuelve el import, para el resto de la pantalla.
  function staticUrl(name) {
    var m = (typeof window !== "undefined") && window.SANA_STATIC;
    var u = m && m[name];
    if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
    return u || null;
  }
  function importStatic(name) {
    if (options.importStatic) return Promise.resolve().then(function () { return options.importStatic(name); }).then(function (m) {
      if (disposed) throw cancelled();
      return m;
    });
    var u = staticUrl(name);
    return u ? import(u).then(function (m) {
      if (disposed) throw cancelled();
      return m;
    }) : Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
  }
  var Cal = null;
  var Islands = null;
  var islandsReady = Promise.resolve(null);
  var Hours = null;
  var hoursReady = importStatic("platform/hours.mjs").then(function (m) {
    Hours = m;
    return m;
  }).catch(function () { return null; });
  // The schedule renderer is a native module, not a classic global. Keep its
  // namespace local so a delayed import cannot be replaced by a window facade.
  var HoursEditor = null;
  var needsHoursEditor = window.SANA_ADMIN_SCREEN === "servicios" || window.SANA_ADMIN_SCREEN === "medicos";
  var hoursEditorReady = needsHoursEditor
    ? importStatic("platform/admin-hours-editor.mjs").then(function (m) {
      HoursEditor = m;
      return m;
    }).catch(function () { return null; })
    : Promise.resolve(null);
  function hoursEditor() {
    if (!HoursEditor) throw new Error("admin-content.mjs: the hours editor could not be loaded; reload to retry");
    return HoursEditor;
  }
  var Dates = null;
  var datesReady = importStatic("platform/dates.mjs").then(function (m) {
    Dates = m;
    return m;
  }).catch(function () { return null; });
  var Format = null;
  var formatReady = importStatic("platform/format.mjs").then(function (m) {
    Format = m;
    return m;
  }).catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  var Combo = null;
  var comboReady = importStatic("platform/admin-combo.mjs").then(function (m) {
    Combo = m;
    return m;
  }).catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  var Prefs = null;
  var prefsReady = importStatic("platform/admin-preferences.mjs").then(function (m) {
    Prefs = m;
    return m.load();
  }).catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  var sessionReady = importStatic("platform/admin-session.mjs").catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  var Table = null;
  var tableReady = importStatic("platform/admin-table.mjs").then(function (m) { Table = m; return m; }).catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  // The panel's one progressive-disclosure rule (empty / no-match / data).
  var Filters = null;
  var filtersReady = importStatic("platform/admin-filter-controls.mjs").then(function (m) { Filters = m; return m; }).catch(function (error) {
    if (isCancelled(error)) return null;
    throw error;
  });
  var Users = null;
  var needsUsers =window.SANA_ADMIN_SCREEN === "medicos";
  function loadUsers() {
    return importStatic("platform/admin-users.mjs").then(function (m) {
      Users = m;
      return m;
    });
  }
  var usersReady = needsUsers
    ? loadUsers().catch(function () { return null; })
    : Promise.resolve(null);
  var createAppointmentReady = null;
  var createAppointmentSeq = 0;
  var createAppointmentIntent = {};
  var createAppointmentHost = null;
  var createAppointmentOwner = null;
  var releaseCreateAppointment = null;
  var currentCalendar = null;
  function loadCreateAppointment() {
    if (!createAppointmentReady) {
      createAppointmentReady = importStatic("platform/admin-create-appointment.mjs").then(function (m) {
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
  function loadErrorWithRetry(message, attribute) {
    return '<p class="adm-empty" role="alert">' + esc(message) + ' ' +
      '<button type="button" class="btn btn-quiet cnt-load-retry" ' + attribute + '="1">' +
      esc(T("admin.content.common.reload", null, "Recargar")) + '</button></p>';
  }
  function showCreateAppointmentLoadError() {
    var msg = T("admin.queue.create_appt.load_failed", null, "No se pudo cargar. Reintentá.");
    if (window.toast) window.toast(msg);
    var host = createAppointmentHost && createAppointmentHost.isConnected ? createAppointmentHost : null;
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
    var calendar = currentCalendar;
    createAppointmentIntent = pre || {};
    createAppointmentHost = host || null;
    createAppointmentOwner = host && ((host.closest && host.closest('[id^="sec-"]')) || host.parentElement);
    return loadCreateAppointment().then(function (m) {
      if (disposed || seq !== createAppointmentSeq) return;
      if (createAppointmentOwner && !createAppointmentOwner.isConnected) return;
      if (createAppointmentHost && !createAppointmentHost.isConnected) return;
      clearCreateAppointmentLoadError();
      var release = m.abrir(createAppointmentIntent, {
        onCreated: function () {
          if (disposed || seq !== createAppointmentSeq || !createAppointmentOwner || !createAppointmentOwner.isConnected) return;
          if (calendar && currentCalendar === calendar) return calendar.refresh();
        }
      });
      if (typeof release === "function") releaseCreateAppointment = release;
    }).catch(function () {
      if (disposed || seq !== createAppointmentSeq) return;
      if (createAppointmentOwner && !createAppointmentOwner.isConnected) return;
      showCreateAppointmentLoadError();
    });
  }
  var CAL_CSS_ID = "sana-medcal-css";
  function injectCalendarCss() {
    if (!Cal || !document.getElementById || document.getElementById(CAL_CSS_ID)) return;
    var tag = document.createElement("style");
    tag.id = CAL_CSS_ID;
    tag.textContent = Cal.css;
    document.head.appendChild(tag);
  }
  if (window.SANA_ADMIN_SCREEN === "medicos") {
    islandsReady = importStatic("platform/islands.mjs").then(function (m) {
      Islands = m;
      return m;
    }).catch(function () { return null; });
    importStatic("platform/calendar-grid.mjs").then(function (m) {
      Cal = m;
      injectCalendarCss();
    }).catch(function () { /* fail-open: sin grilla, el tab Calendario muestra su propio error */ });
  }
  // Rol de la sesión (lo fija applyRol() en admin.html ANTES de que se rutee
  // una sección de contenido). El operador edita servicios/médicos (agendas,
  // vacaciones — reunión 2026-07-17) pero crear/eliminar/restaurar es de
  // admin: acá sólo se esconden esos controles; el gate real es el server.
  var esAdmin = function () { return window.CURRENT_ROLE === "admin"; };
  // Lo que pide un endpoint de otro permiso se decide por los PERMISOS
  // efectivos que el server ya verificó para esta sesión
  // (window.CURRENT_PERMISSIONS, base.html), igual que el gate — nunca por el
  // nombre del rol: el gestor de agendas no es admin pero tiene `medicos`, y
  // no tiene `usuarios`/`asistente`/`servicios` (hallazgo K8, 2026-10-23).
  // Sin la lista (un host viejo) se cae al criterio anterior.
  var puede = function (perm) {
    var held = window.CURRENT_PERMISSIONS;
    return Array.isArray(held) ? held.indexOf(perm) !== -1 : esAdmin();
  };
  // Alta, deshabilitar y restaurar del catálogo: un médico va por
  // /api/admin/content/doctors, que pide `medicos`; los otros tipos siguen
  // siendo de admin como antes.
  var puedeAltaBaja = function (tipo) { return tipo === "medicos" ? puede("medicos") : esAdmin(); };
  function prefsGet(key, fallback) {
    return Prefs ? Prefs.get(key, fallback) : fallback;
  }
  function prefsSet(key, value) {
    if (Prefs) Prefs.set(key, value);
  }

  // admin.html trae su propio <style> inline (no lo tocamos desde acá — otro
  // frente lo tiene en uso) — todo el CSS que este archivo necesita (foto del
  // médico, tabs del formulario médico, ajustes de .cnt-row) se inyecta acá,
  // una sola vez al cargar el script, en un <style> agregado AL FINAL de
  // <head> — con igual especificidad, el último en el DOM gana el empate de
  // cascada, así que estas reglas pisan a las de admin.html sin tocarlo.
  // Paleta de colores del avatar de iniciales = la misma que .mp-av--0..5
  // (site_shell._PAGE_CSS) para que el círculo del admin coincida con el
  // del sitio público.
  function injectContentFormStyles() {
    // El css de la grilla mensual (medcal-*) ya no viene acá: lo inyecta
    // injectCalendarCss() apenas resuelve el import de calendar-grid.mjs
    // (mismo css, hoja propia con guard de id — la isla MedCal también lo
    // inyecta; los selectores son disjuntos de los de acá).
    var css =
      // Local status boundary for the direct screen (#content-status):
      // an error (expired session) is shown in red, not gray.
      ".cnt-status--error{color:var(--danger,#b3261e);}" +
      ".cnt-load-retry{min-height:44px;font-size:16px;}" +
      // Responsable del producto, 2026-07-16 (revisión del form médico): admin.html tiene
      // `.cnt-row{align-items:baseline}` — con campos altos (Foto, Palabras,
      // Agenda) la baseline sintética de un <textarea>/<div> es su borde
      // inferior, así que el label termina pegado ABAJO del campo ("labels
      // aligned to the bottom for some reason"). El padding-top:9px del
      // label en admin.html ya estaba pensado para alinear con la primera
      // línea del campo — sólo hacía falta este override.
      ".cnt-label-help{position:relative;border-bottom:1px dashed var(--text-3);cursor:help;}" +
      ".cnt-label-help:focus-visible{outline:2px solid var(--focus-border);outline-offset:3px;border-radius:2px;}" +
      ".cnt-label-help:hover:after,.cnt-label-help:focus:after{content:attr(data-tip);position:absolute;z-index:50;right:0;top:calc(100% + 8px);width:min(300px,70vw);padding:9px 11px;border-radius:8px;background:var(--text);color:var(--panel);font-size:13px;line-height:1.4;text-align:left;box-shadow:var(--shadow-pop);}" +
      ".svc-field-group{margin:0 0 30px;}" +
      ".svc-field-group>h2{margin:0 0 16px;font-size:16px;line-height:1.3;}" +
      // A folded group of rarely used fields: the heading is the control
      // (native disclosure marker, a 44px line to hit), the fields under it
      // stay in the DOM.
      ".cnt-fold>summary{cursor:pointer;min-height:44px;line-height:44px;color:var(--text);}" +
      ".cnt-fold>summary>h2{display:inline;margin:0;font-size:16px;line-height:1.3;}" +
      ".cnt-fold[open]>summary{margin-bottom:8px;}" +
      ".cnt-fold>summary:focus-visible{outline:2px solid var(--focus-border);outline-offset:2px;border-radius:4px;}" +
      ".adm-sel.svc-feature-order{width:9ch;}" +
      ".svc-announcement-fields{display:grid;gap:9px;}" +
      ".svc-announcement-fields>.cnt-check{margin-bottom:1px;}" +
      // Tabs del form médico (Calendario / Horarios de atención /
      // Información básica), mismo look que los filter-tabs de la cola
      // (.adm-filters/.adm-filter en admin.html) — un único lenguaje visual
      // de "tab de texto" en todo el admin. Sin línea divisoria bajo la fila
      // de tabs (responsable del producto, 2026-07-16): el espacio de abajo ya separa la fila del
      // contenido, la línea era ruido sobre el mismo gris del fondo del admin.
      // One row at every width: on a phone the row scrolls sideways instead
      // of wrapping into two (admin-direct.css says the same; this sheet
      // comes later and used to undo it). A tab is a <button>: without
      // background:none the browser paints its grey face behind the label.
      ".cnt-tabs{display:flex;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;align-items:stretch;gap:2px;margin:0 0 16px;min-height:36px;scroll-snap-type:x mandatory;}" +
      // The side of the row that has more tabs fades out (wireTabEdges).
      ".cnt-tabs[data-more~=end]{-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 48px),transparent);mask-image:linear-gradient(to right,#000 calc(100% - 48px),transparent);}" +
      ".cnt-tabs[data-more~=start]{-webkit-mask-image:linear-gradient(to left,#000 calc(100% - 48px),transparent);mask-image:linear-gradient(to left,#000 calc(100% - 48px),transparent);}" +
      ".cnt-tabs[data-more~=start][data-more~=end]{-webkit-mask-image:linear-gradient(to right,transparent,#000 48px,#000 calc(100% - 48px),transparent);mask-image:linear-gradient(to right,transparent,#000 48px,#000 calc(100% - 48px),transparent);}" +
      ".cnt-tab{scroll-snap-align:start;display:flex;align-items:center;flex:0 0 auto;white-space:nowrap;min-height:44px;font:inherit;font-size:16px;color:var(--hint-ink,var(--text-2));text-decoration:none;padding:0 10px;background:none;border:0;border-bottom:2px solid transparent;cursor:pointer;transition:color .12s;}" +
      ".cnt-tab:hover{color:var(--hover-ink);}" +
      ".cnt-tab.on{color:var(--text);font-weight:500;border-bottom-color:var(--accent);background:none;}" +
      ".cnt-tab.has-appointments:after{content:attr(data-count);display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;margin-left:6px;padding:0 5px;border-radius:999px;background:var(--info);color:#fff;font:600 10.5px/1 var(--font);}" +
      ".cnt-tab.is-empty{background:transparent;}" +
      // El texto y el ejemplo los pone platform/intake-explainer.mjs (su css).
      ".med-intake{max-width:1100px;}" +
      ".med-intake h2{font-size:18px;margin:0 0 12px;}" +
      // Agrupa "Horarios en este centro" (intro + filas + botón) y
      // "Excepciones puntuales" (intro + filas + botón) como dos bloques
      // simétricos con límite propio: el texto de excepciones queda pegado
      // a SU botón, no leído como pie de "+ Agregar horario" (responsable del producto,
      // 2026-07-16: "reads as if it described THAT button"). El límite es
      // sólo espacio (sin línea, responsable del producto 2026-07-16: la separación entre
      // horarios y excepciones la da el aire, no un hr).
      ".cnt-fgroup{margin-top:14px;}" +
      ".cnt-fgroup:first-child{margin-top:0;}" +
      ".cnt-fgroup--exc{margin-top:26px;}" +
      // Toolbar del listado de médicos (2026-07-16): buscador + filtros a la
      // izquierda, engranaje de columnas + "Nuevo médico" a la derecha — el
      // .cnt-toolbar de admin.html ya hace space-between entre dos mitades,
      // acá cada mitad agrupa sus propios controles con su propio gap.
      ".cnt-toolbar-filters{display:flex;flex-wrap:wrap;gap:9px;align-items:center;}" +
      ".cnt-toolbar-right{display:flex;align-items:center;gap:10px;}" +
      // Las entidades del catálogo usan el mismo lenguaje que Solicitudes:
      // verde, sin subrayado. El color ya comunica que son navegables.
      ".cnt-table a{color:var(--accent);text-decoration:none;}" +
      ".cnt-table a:hover{color:var(--accent-hover);text-decoration:none;}" +
      // En los catálogos las fechas de alta y última edición forman parte de
      // la vista de trabajo. El layout fijo reserva su lugar y recorta sólo
      // los textos descriptivos largos; así Horario no empuja las fechas
      // fuera del viewport en una notebook.
      // Keep the active catalog section shrinkable inside both the legacy
      // sidebar and the direct shell. On wide screens the work table keeps
      // compact columns; at <=1024px it becomes a full-width stacked list so
      // its 760px catalog minimum never survives after SanaTable stops fitting.
      ".adm-content-wrap:has(> #sec-servicios:not([style*='none'])),.adm-content-wrap:has(> #sec-centros:not([style*='none'])),.adm-content-wrap:has(> #sec-medicos:not([style*='none'])){width:100%;min-width:0;}" +
      ".cnt-table-wrap{width:100%;min-width:0;}" +
      ".cnt-table--catalog{table-layout:fixed;min-width:760px;}" +
      ".cnt-table--catalog th[data-col],.cnt-table--catalog td[data-col]{overflow:hidden;text-overflow:ellipsis;}" +
      ".cnt-table--catalog td[data-col=nombre] a,.cnt-table--catalog td[data-col=centro] a{display:block;overflow:hidden;text-overflow:ellipsis;}" +
      ".cnt-table--servicios [data-col=nombre]{width:19%;}.cnt-table--servicios [data-col=centro]{width:14%;}" +
      ".cnt-table--servicios [data-col=modo]{width:11%;}.cnt-table--servicios [data-col=horario]{width:18%;}" +
      ".cnt-table--servicios [data-col=medicos]{width:18%;}" +
      ".cnt-table--servicios [data-col=created],.cnt-table--servicios [data-col=updated]{width:10%;}" +
      ".cnt-table--centros [data-col=nombre]{width:26%;}.cnt-table--centros [data-col=address]{width:30%;}" +
      ".cnt-table--centros [data-col=phone]{width:16%;}.cnt-table--centros [data-col=created],.cnt-table--centros [data-col=updated]{width:14%;}" +
      // Primary catalog text must remain readable. Dates and phone values stay
      // compact; names, locations, modes, and schedules wrap inside their own
      // columns instead of being silently replaced with ellipses.
      ".cnt-table--servicios th[data-col=nombre],.cnt-table--servicios th[data-col=centro],.cnt-table--servicios th[data-col=modo],.cnt-table--servicios th[data-col=horario],.cnt-table--servicios th[data-col=medicos],.cnt-table--centros th[data-col=nombre],.cnt-table--centros th[data-col=address],#sec-medicos .cnt-table th[data-col]{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;}" +
      ".cnt-table--servicios td[data-col=nombre],.cnt-table--servicios td[data-col=centro],.cnt-table--servicios td[data-col=modo],.cnt-table--servicios td[data-col=horario],.cnt-table--servicios td[data-col=medicos],.cnt-table--centros td[data-col=nombre],.cnt-table--centros td[data-col=address],#sec-medicos .cnt-table td[data-col=nombre],#sec-medicos .cnt-table td[data-col=especialidad],#sec-medicos .cnt-table td[data-col=centro]{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:anywhere;}" +
      ".cnt-table--servicios td[data-col=nombre] a,.cnt-table--servicios td[data-col=centro] a,.cnt-table--servicios td[data-col=medicos] a,.cnt-table--centros td[data-col=nombre] a,#sec-medicos .cnt-table td[data-col=especialidad] .cnt-inline-action{white-space:normal;overflow:visible;text-overflow:clip;text-align:left;}" +
      "@media(max-width:1024px){" +
        "#sec-servicios .cnt-table-wrap,#sec-centros .cnt-table-wrap,#sec-medicos .cnt-table-wrap:has(>.cnt-table--medicos){overflow:visible;contain:none;}" +
        // A chooser redraw can leave SanaTable's previous desktop inline
        // width queued while the viewport crosses this breakpoint.  The
        // stacked mobile layout owns its width even for that one frame.
        "#sec-servicios .cnt-table,#sec-centros .cnt-table,#sec-medicos .cnt-table--medicos{display:block;width:100%!important;min-width:0!important;table-layout:auto!important;}" +
        "#sec-servicios .cnt-table thead,#sec-centros .cnt-table thead,#sec-medicos .cnt-table--medicos thead{display:none;}" +
        "#sec-servicios .cnt-table tbody,#sec-centros .cnt-table tbody,#sec-medicos .cnt-table--medicos tbody{display:block;width:100%;}" +
        // Business-card layout (the product owner review, fewer boxes/one label style):
        // el nombre es la única línea propia; el resto son datos que siguen
        // en renglones, separados por un punto medio, sin rótulo — salvo donde
        // un dato solo sería ambiguo (fechas de alta y cambio, servicios).
        // UNA regla para las tres tablas en tarjeta (Servicios, Centros,
        // Médicos; pinamar-turnos 07.11): la fila es flex que se parte en
        // renglones; cada dato es un bloque y su punto va en el hueco de 16px
        // a su izquierda (::after absoluto). El hueco de flex no existe al
        // empezar un renglón, así que el punto del primer dato de cada renglón
        // cae fuera de la fila y la fila lo recorta (overflow:clip, con 4px de
        // margen para los anillos de foco): nunca queda «20 h ·» al final de
        // un renglón ni «· Próximo día» al principio del siguiente, y el
        // último dato no tiene nada después. La section ya está sobre el
        // margen del panel (--adm-gutter): la fila no suma sangría propia.
        "#sec-servicios .cnt-table tr,#sec-centros .cnt-table tr,#sec-medicos .cnt-table--medicos tr{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:16px;row-gap:2px;width:100%;box-sizing:border-box;padding:12px 0;border-bottom:1px solid var(--border);overflow:hidden;overflow:clip;overflow-clip-margin:4px;}" +
        // Corte de renglón después del nombre (y de la casilla del médico).
        "#sec-servicios .cnt-table tr::before,#sec-centros .cnt-table tr::before,#sec-medicos .cnt-table--medicos tr::before{content:\"\";order:1;flex:0 0 100%;height:0;}" +
        // Los datos son la información de la fila: tinta oscura; el rótulo
        // («Horas por semana») va en --hint-ink, como los del resto del panel.
        "#sec-servicios .cnt-table td[data-col],#sec-centros .cnt-table td[data-col],#sec-medicos .cnt-table--medicos td[data-col]{order:2;position:relative;display:block;flex:0 1 auto;min-width:0;padding:0;box-sizing:border-box;width:auto!important;border:0;white-space:normal;overflow:visible;overflow-wrap:anywhere;font-size:13px;color:var(--text);}" +
        "#sec-servicios .cnt-table td[data-col=nombre],#sec-centros .cnt-table td[data-col=nombre],#sec-medicos .cnt-table--medicos td[data-col=nombre]{display:block;order:0;flex:1 1 0;padding:4px 0;font-size:14px;font-weight:650;color:var(--text);}" +
        "#sec-servicios .cnt-table td[data-col]:not([data-col=nombre])::after,#sec-centros .cnt-table td[data-col]:not([data-col=nombre])::after,#sec-medicos .cnt-table--medicos td[data-col]:not([data-col=nombre])::after{content:\"\\00b7\";position:absolute;top:0;right:100%;width:16px;text-align:center;color:var(--hint-ink,var(--text-2));}" +
        "#sec-servicios .cnt-table td[data-label]:before,#sec-centros .cnt-table td[data-label]:before,#sec-medicos .cnt-table--medicos td[data-label]:before{content:none;}" +
        "#sec-servicios .cnt-table td[data-col=created]:before,#sec-servicios .cnt-table td[data-col=updated]:before," +
        "#sec-centros .cnt-table td[data-col=created]:before,#sec-centros .cnt-table td[data-col=updated]:before," +
        // Hours, next day and occupancy carry no label on the card: the
        // same three words in every card were noise (the product owner, 02.11: what repeats
        // on every row goes outside the brackets): the three names are said
        // once, on the line over the list (below), and stay in the tooltip.
        "#sec-medicos .cnt-table--medicos td[data-col=created]:before,#sec-medicos .cnt-table--medicos td[data-col=updated]:before," +
        "#sec-medicos .cnt-table--medicos td[data-col=servicios]:before{content:attr(data-label) \" \";color:var(--hint-ink,var(--text-2));}" +
        // A text link reads as words in the card; a contact icon (phone,
        // WhatsApp, mail — format.mjs) keeps its own 44px box. Made inline,
        // its block glyph broke the cell into one icon per line above the
        // address, with the dot hanging beside the first (ui-expert, Centros
        // at 390, 2026-11-08). The pair never splits across lines either.
        "#sec-servicios .cnt-table td[data-col] a:not(.cnt-ico-link):not(.cnt-wa-link),#sec-centros .cnt-table td[data-col] a:not(.cnt-ico-link):not(.cnt-wa-link),#sec-medicos .cnt-table--medicos td[data-col] a:not(.cnt-ico-link):not(.cnt-wa-link){display:inline;overflow:visible;text-overflow:clip;white-space:normal;}" +
        "#sec-servicios .cnt-table td[data-col]:has(>.cnt-ico-link),#sec-centros .cnt-table td[data-col]:has(>.cnt-ico-link),#sec-medicos .cnt-table--medicos td[data-col]:has(>.cnt-ico-link){white-space:nowrap;}" +
        // The 20px glyph sits centred in its tap box (32px, 44px on a phone or
        // touch screen — format.mjs): the first icon steps back by that inset
        // into the dot's gap, so the dot stands as far from the phone as from
        // the address, and an icon opening a line lines up with the card edge.
        "#sec-servicios .cnt-table td[data-col]>.cnt-ico-link:first-child,#sec-centros .cnt-table td[data-col]>.cnt-ico-link:first-child,#sec-medicos .cnt-table--medicos td[data-col]>.cnt-ico-link:first-child{margin-left:-6px;}" +
        "@media(max-width:640px),(pointer:coarse){#sec-servicios .cnt-table td[data-col]>.cnt-ico-link:first-child,#sec-centros .cnt-table td[data-col]>.cnt-ico-link:first-child,#sec-medicos .cnt-table--medicos td[data-col]>.cnt-ico-link:first-child{margin-left:-12px;}}" +
        // The doctor's checkbox sits on the name's line; the data lines under
        // it start back on the card's edge.
        "#sec-medicos .cnt-table--medicos .cnt-td-check{order:0;flex:0 0 auto;align-self:flex-start;width:auto!important;min-width:0;margin-right:-6px;padding:6px 0 0;border:0;line-height:0;}" +
        "#sec-servicios .cnt-table .cnt-td-acts:not(:empty),#sec-centros .cnt-table .cnt-td-acts:not(:empty),#sec-medicos .cnt-table--medicos .cnt-td-acts:not(:empty){order:3;flex:0 0 100%;display:grid;justify-items:start;padding:4px 0;}" +
        "#sec-servicios .cnt-table .cnt-td-acts:empty,#sec-centros .cnt-table .cnt-td-acts:empty,#sec-medicos .cnt-table--medicos .cnt-td-acts:empty{display:none;}" +
        "#sec-servicios .cnt-table .adm-empty,#sec-centros .cnt-table .adm-empty,#sec-medicos .cnt-table--medicos .adm-empty{display:block;flex:1 1 100%;padding:4px 0;white-space:normal;overflow:visible;text-overflow:clip;}" +
        // Information diet: a line that only says "no data" is noise on the
        // stacked card. Every unset field renders inside its own cnt-muted
        // span (table-design: null distinguished from a real value), and a
        // field with nothing to say (Cuenta before the users read) renders
        // empty — whichever it is, the card drops it instead of showing a
        // lone "—" or a dot with nothing after it.
        "#sec-servicios .cnt-table td[data-col]:has(.cnt-muted),#sec-centros .cnt-table td[data-col]:has(.cnt-muted),#sec-medicos .cnt-table--medicos td[data-col]:has(.cnt-muted)," +
        "#sec-servicios .cnt-table td[data-col]:empty,#sec-centros .cnt-table td[data-col]:empty,#sec-medicos .cnt-table--medicos td[data-col]:empty{display:none;}" +
        // The cards' unlabelled «40 h · Mañana · ▮▮▯» are read through one
        // line over the list: the three column names, in the cards' order,
        // with the same dot (ui-expert 08.11 — say it once, never leave it
        // unsaid). The header keeps its sort buttons; every other column
        // name and the select-all box stay hidden on the phone. 14px; the ink is
        // the panel's label ink like every table header (the product owner 04.11).
        "#sec-medicos .cnt-table--medicos thead{display:block;width:100%;}" +
        "#sec-medicos .cnt-table--medicos thead tr{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:16px;row-gap:2px;padding:0 0 6px;border-bottom:1px solid var(--border);overflow:hidden;overflow:clip;overflow-clip-margin:4px;}" +
        "#sec-medicos .cnt-table--medicos thead tr::before{content:none;}" +
        "#sec-medicos .cnt-table--medicos thead th{display:none;}" +
        "#sec-medicos .cnt-table--medicos thead th[data-col=horas_semana],#sec-medicos .cnt-table--medicos thead th[data-col=proximo_dia],#sec-medicos .cnt-table--medicos thead th[data-col=ocupacion_14d]{display:block;position:relative;flex:0 1 auto;width:auto!important;min-width:0;padding:0;border:0;background:none;font-size:14px;font-weight:400;text-align:left;white-space:normal;color:var(--hint-ink,var(--text-2));}" +
        "#sec-medicos .cnt-table--medicos thead th[data-col=horas_semana]::after,#sec-medicos .cnt-table--medicos thead th[data-col=proximo_dia]::after,#sec-medicos .cnt-table--medicos thead th[data-col=ocupacion_14d]::after{content:\"\\00b7\";position:absolute;top:0;right:100%;width:16px;text-align:center;}" +
      "}" +
      // El engranaje de columnas (.cnt-gear-*) lo dibuja y lo estila
      // admin-table.js, que es el motor de tablas de toda la consola —
      // estaba escrito acá cuando Médicos era la única tabla que lo tenía.
      // Celda de métrica sin dato: atenuada, nunca un "0"/"—" con el mismo
      // peso que un dato real (table-design: null se distingue de un valor).
      ".cnt-muted{color:var(--hint-ink);}" +
      // SanaTable assigns the selection column a 40px track. The legacy
      // 38px content width plus cell padding overran that track at 1280px.
      "#sec-medicos .cnt-th-check,#sec-medicos .cnt-td-check{width:auto;}" +
      // Los filtros de médicos son UN renglón que no se parte a medias
      // (related-control hook, product owner 2026-07-20: "the filters break into
      // lines") — el buscador cede ancho antes de que el grupo envuelva.
      ".cnt-toolbar-filters{flex-wrap:nowrap;min-width:0;}" +
      ".cnt-toolbar-filters .cnt-search{flex:0 1 240px;min-width:120px;}" +
      // Un select nativo conoce el ancho de su opción más larga. No se lo
      // deja encoger debajo de ese ancho: si falta lugar, cede la búsqueda o
      // baja el grupo completo en el breakpoint.
      ".cnt-toolbar-filters .adm-sel{width:var(--control-fit-width,auto);min-width:var(--control-fit-width,max-content);flex:0 0 auto;}" +
      ".queue-doctor-filter,.queue-service-filter{width:var(--control-fit-width,auto);max-width:100%;flex-shrink:0;}" +
      // Pacientes tiene cuatro filtros y Cobertura puede ser larga. El
      // buscador ocupa su fila; abajo cada filtro conserva su ancho real.
      ".cnt-toolbar-filters:has(#pac-q){display:grid;grid-template-columns:107px 126px 179px minmax(305px,1fr);width:min(760px,100%);}" +
      ".cnt-toolbar-filters:has(#pac-q) #pac-q{grid-column:1/-1;width:100%;min-width:0;box-sizing:border-box;}" +
      ".cnt-toolbar-filters:has(#pac-q) .adm-sel{width:100%;min-width:0;}" +
      // En tablet el grupo pasa a una grilla pareja; en teléfono, a una pila.
      // Así ningún filtro queda solo ni recorta una opción larga.
      // A direct desktop shell loses 208px to navigation: stack catalog
      // filters before its usable content width becomes narrower than their
      // measured native options.
      "@media(max-width:1024px){.cnt-toolbar-filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;}" +
        ".cnt-toolbar-filters:has(#pac-q){grid-template-columns:repeat(2,minmax(0,1fr));}" +
        ".cnt-toolbar-filters .cnt-search{grid-column:1/-1;width:100%;min-width:0;box-sizing:border-box;}" +
        ".cnt-toolbar-filters .adm-sel{width:100%;min-width:0;}" +
        ".cnt-toolbar-filters>.adm-sel:last-child:nth-child(even){grid-column:1/-1;}}" +
      "@media(max-width:520px){.cnt-toolbar-filters{grid-template-columns:minmax(0,1fr);}" +
        ".cnt-toolbar-filters:has(#pac-q){grid-template-columns:minmax(0,1fr);}" +
        ".cnt-toolbar-filters>.adm-sel:last-child:nth-child(even){grid-column:auto;}}" +
      ".cnt-owner-status{padding:12px 14px;background:var(--state-done-bg);border:1px solid var(--border);border-radius:10px;color:var(--text);font-size:15px;}" +
      ".cnt-agenda .cnt-daygrid{display:flex;flex-wrap:wrap;gap:6px;}.cnt-agenda .cnt-daychk span{min-width:44px;height:40px;border-radius:8px;font-size:15px;}" +
      ".cnt-agenda .cnt-agenda-times{display:flex;gap:12px;flex-wrap:wrap;}.cnt-agenda .cnt-in{min-height:40px;height:40px;border-radius:8px;font-size:16px;}" +
      "@media(max-width:480px){.cnt-agenda .cnt-agenda-times{display:grid;grid-template-columns:minmax(0,1fr);}.cnt-agenda-times label{display:flex;justify-content:space-between;align-items:center;}}" +
      ".cnt-schedule-confirm{width:min(440px,calc(100vw - 32px));max-width:100%;box-sizing:border-box;border:1px solid var(--border);border-radius:12px;background:var(--panel);color:var(--text);font-size:16px;padding:24px;text-wrap:pretty;}" +
      ".cnt-schedule-confirm::backdrop{background:rgba(0,0,0,.4);}.cnt-schedule-confirm h3{font-size:18px;margin:0 0 6px;}.cnt-schedule-confirm p{color:#3D443E;}.cnt-schedule-confirm form{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:10px;margin-top:20px;}.cnt-service-schedule{font-size:16px;color:var(--text);}.cnt-service-note{margin:6px 0 0;line-height:1.4;}" +
      ".medcal-filters{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin:0 0 14px;}" +
      ".medcal-filters .cnt-search{width:min(28ch,100%);}" +
      ".mt-day-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin:0 0 10px;}" +
      ".mt-day-head .mt-day-h{margin:0;}" +
      ".medcal-create{white-space:nowrap;}" +
      ".mt-day-acts{display:flex;align-items:center;gap:14px;}" +
      ".medcal-hsi{white-space:nowrap;font-size:13px;}" +
      ".medcal-time{font:inherit;font-weight:650;background:none;border:0;padding:0;color:var(--accent);cursor:pointer;}" +
      ".medcal-sort{border:0;background:transparent;padding:0;color:inherit;font:inherit;text-transform:inherit;letter-spacing:inherit;cursor:pointer;}" +
      ".medcal-sort:hover{color:var(--hover-ink);}" +
      // En la compu la edad tiene su columna; la copia pegada al nombre es
      // sólo para la fila del teléfono.
      ".medcal-edad-junto{display:none;}" +
      // En la compu el nombre del paciente y el servicio pasan a un segundo
      // renglón antes que cortarse con «…», igual que el centro.
      "#med-cal .medcal-table td[data-col=paciente],#med-cal .medcal-table td[data-col=servicio]{white-space:normal;overflow:visible;text-overflow:clip;overflow-wrap:break-word;}" +
      // La tabla de un día del Calendario, debajo de 1024 px: la misma fila de
      // dos renglones que «Mis turnos» (admin-direct.css) — hora y paciente
      // arriba; edad, DNI, contacto, servicio y centro abajo, bajo el nombre;
      // una celda sin dato no ocupa lugar. La tabla ancha estiraba la página
      // a 1066 px en un teléfono de 390 (pinamar-turnos 04.11).
      "@media(max-width:1024px){" +
        "#med-cal .medcal-table{display:block;width:100%!important;min-width:0!important;table-layout:auto!important;}" +
        "#med-cal .medcal-table thead,#med-cal .medcal-table colgroup{display:none;}" +
        "#med-cal .medcal-table tbody{display:block;width:100%;}" +
        "#med-cal .medcal-table tr{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:16px;row-gap:4px;padding:12px 0 12px 68px;border-bottom:1px solid var(--border);box-sizing:border-box;}" +
        "#med-cal .medcal-table tr:has(>.adm-empty){padding-left:0;}" +
        "#med-cal .medcal-table td{display:block;width:auto!important;min-width:0!important;max-width:none!important;border:0;padding:0;white-space:nowrap;overflow:visible;text-overflow:clip;order:6;font-size:15px;color:var(--text);}" +
        "#med-cal .medcal-table td.medcal-vacio{display:none;}" +
        "#med-cal .medcal-table td[data-col=hora]{order:1;flex:0 0 auto;min-width:52px!important;margin-left:-68px;font-weight:650;}" +
        "#med-cal .medcal-table td[data-col=paciente]{order:2;flex:1 1 0;white-space:normal;font-weight:600;}" +
        "#med-cal .medcal-table tr::after{content:\"\";order:4;flex:0 0 100%;height:0;}" +
        // La edad va pegada al nombre («Sofía Herrera, 73») y su celda no se
        // dibuja; el DNI no lleva rótulo.
        "#med-cal .medcal-table td[data-col=edad]{display:none;}" +
        "#med-cal .medcal-table .medcal-edad-junto{display:inline;font-weight:400;}" +
        "#med-cal .medcal-table td[data-col=dni]{order:5;}" +
        "#med-cal .medcal-table td[data-col=consultorio]::before{content:attr(data-label) \" \";}" +
        "#med-cal .medcal-table td[data-col=contacto],#med-cal .medcal-table td[data-col=servicio],#med-cal .medcal-table td[data-col=centro]{white-space:normal;}" +
        "#med-cal .medcal-table td[data-col=contacto] br{display:none;}" +
        "#med-cal .medcal-table td[data-col=contacto] .cnt-contacto-de{margin-left:6px;}" +
        "#med-cal .medcal-table .adm-empty{white-space:normal;}" +
      "}" +
      // The request a calendar row opens is the panel's one overlay card
      // (.solmod, admin-direct.css): no copy of its rules here.
      // especialidad al lado del nombre en el editor — peso menor que el h1
      ".cnt-h1-sub{font-size:16px;font-weight:400;color:var(--hint-ink);margin-left:7px;overflow-wrap:anywhere;}" +
      // On a phone the specialty is its own line under the name, on the
      // same 16px edge — inline it wrapped and kept the 7px indent (x≈23).
      "@media(max-width:640px){.cnt-h1-sub{display:block;margin-left:0;}}" +
      // A row action is one unbroken label, so the fitter measures its
      // whole width for the actions track.
      ".cnt-td-acts .btn{white-space:nowrap;}" +
      ".medcal-sobre{font:600 10px/1 var(--font);letter-spacing:.04em;text-transform:uppercase;color:var(--warn);background:var(--warn-soft);padding:2px 5px;border-radius:5px;vertical-align:1px;}";
    var tag = document.createElement("style");
    tag.textContent = css;
    document.head.appendChild(tag);
  }
  injectContentFormStyles();

  // Getters, not values: this body runs before i18nReady settles, so a value
  // computed here stayed Spanish in the English panel (the «Médicos» crumb).
  // Each read happens at render, after the boot awaited the catalog.
  var TITLES = {
    get solicitudes() { return T("admin.content.catalog.titles.requests", null, "Solicitudes de turno"); },
    get servicios() { return T("admin.content.catalog.titles.services", null, "Servicios"); },
    get centros() { return T("admin.content.catalog.titles.centers", null, "Centros"); },
    get medicos() { return T("admin.content.catalog.titles.doctors", null, "Médicos"); }
  };

  // días lunes→domingo en el orden en que se muestran los checkboxes; el VALOR
  // que viaja en `dias` es getDay() (0=domingo…6=sábado — confirmado contra
  // horarios.js/semana.js y el médico demo `demo-aguirre-marcelo`, agenda_demo
  // {dias:[1,4]} = lunes y jueves).
  // `l` is a getter for the same reason as TITLES above.
  var DIAS_UI = [
    { v: 1, get l() { return T("admin.content.catalog.days.mon", null, "Lu"); } },
    { v: 2, get l() { return T("admin.content.catalog.days.tue", null, "Ma"); } },
    { v: 3, get l() { return T("admin.content.catalog.days.wed", null, "Mi"); } },
    { v: 4, get l() { return T("admin.content.catalog.days.thu", null, "Ju"); } },
    { v: 5, get l() { return T("admin.content.catalog.days.fri", null, "Vi"); } },
    { v: 6, get l() { return T("admin.content.catalog.days.sat", null, "Sá"); } },
    { v: 0, get l() { return T("admin.content.catalog.days.sun", null, "Do"); } }
  ];
  var DIA_LARGO = { 0: "domingo", 1: "lunes", 2: "martes", 3: "miércoles", 4: "jueves", 5: "viernes", 6: "sábado" };

  // archivo: /data/<f> (no /webdata/<f> directo) — compat route pack-chain
  // aware en server.py: resuelve dondequiera que el archivo esté HOY
  // (legacy data or the selected private town pack), so this fetch remains
  // que la migración por capas mueve un archivo más (data-packs.md).
  // singular/plural viajan como {tipo} dentro de mensajes traducidos (Buscar
  // {tipo}…, Nuevo {tipo}…) — si quedaran en castellano crudo, el mensaje en
  // inglés mezclaría los dos idiomas. Getters, como TITLES: este cuerpo corre
  // antes de que i18nReady resuelva.
  var TIPOS = {
    servicios: { archivo: "/data/servicios.json", campo: "servicios", idKey: "id",
      get singular() { return T("admin.content.catalog.common.type_service_singular", null, "servicio"); },
      get plural() { return T("admin.content.catalog.common.type_service_plural", null, "servicios"); } },
    centros: { archivo: "/data/centros-salud.json", campo: "centros", idKey: "slug",
      get singular() { return T("admin.content.catalog.common.type_center_singular", null, "centro"); },
      get plural() { return T("admin.content.catalog.common.type_center_plural", null, "centros"); } },
    medicos: { archivo: "/data/medicos.json", campo: "medicos", idKey: "id",
      get singular() { return T("admin.content.catalog.doctors.generic_label", null, "médico"); },
      get plural() { return T("admin.content.catalog.common.type_doctor_plural", null, "médicos"); } }
  };

  // tipo interno (español, = catalogo.tipo en Postgres, sin tocar) -> segmento
  // de URL (inglés, 2026-07-17). Usado para el fetch a /api/admin/content/;
  // el resto del
  // código sigue operando con el tipo interno en español.
  var TIPO_URL = { servicios: "services", centros: "centers", medicos: "doctors" };

  // Tier 5 direct screens (/admin/services, /admin/centers, /admin/doctors —
  // admin_panel/admin_content_screen.py): base.html declares
  // window.SANA_ADMIN_SCREEN with the manifest id; the SAME renderer paints
  // the screen with no hash. Item state there is the canonical query
  // `?item=<id>` (`?item=new` for the blank form), pushState + popstate;
  // Cross-editor links target their sibling direct screen.
  var DIRECT_PATH = { servicios: "/admin/services", centros: "/admin/centers", medicos: "/admin/doctors" };
  var DIRECT_NEW = "new";
  var openImportOnMount = false;
  function directScreen() {
    var id = (typeof window !== "undefined") && window.SANA_ADMIN_SCREEN;
    return DIRECT_PATH[id] ? id : "";
  }
  function directState() {
    var tipo = directScreen();
    if (!tipo) return null;
    var raw = new URLSearchParams(location.search).get("item") || "";
    var id = raw === DIRECT_NEW ? "nuevo" : raw;
    // /admin/users/import (the old roster page) lands on the doctors list with
    // ?import=1; read it before the address is made canonical.
    openImportOnMount = tipo === "medicos" && !raw && new URLSearchParams(location.search).get("import") === "1";
    var canonical = DIRECT_PATH[tipo] + (raw ? "?item=" + encodeURIComponent(raw) : "");
    if (location.pathname + location.search !== canonical) {
      history.replaceState({}, "", canonical);
    }
    return { sec: tipo, id: id || null };
  }
  function listHref(tipo) {
    return DIRECT_PATH[tipo];
  }
  function itemHref(tipo, id) {
    return DIRECT_PATH[tipo] + "?item=" + encodeURIComponent(id === "nuevo" ? DIRECT_NEW : id);
  }
  // Return to the list after save/disable/restore with history state.
  function goList(tipo) {
    history.pushState({}, "", listHref(tipo));
    readyRoute();
  }

  var CACHE = { servicios: null, centros: null, medicos: null };
  var RAW_CACHE = {}; // originales de webdata/*.json, para el <details> de comparación
  var SORT = { servicios: { key: "nombre", dir: 1 }, centros: { key: "nombre", dir: 1 }, medicos: { key: "nombre", dir: 1 } };
  var SEARCH = { servicios: "", centros: "", medicos: "" };
  var LIST_PREFS_READY = false;
  function hydrateListPrefs() {
    if (LIST_PREFS_READY) return;
    SORT = Object.assign(SORT, prefsGet("catalog.sort", {}));
    SEARCH = Object.assign(SEARCH, prefsGet("catalog.search", {}));
    MED_FILTERS = Object.assign(MED_FILTERS, prefsGet("doctors.filters", {}));
    SERVICE_FILTERS = Object.assign(SERVICE_FILTERS, prefsGet("services.filters", {}));
    LIST_PREFS_READY = true;
  }

  // ---- catálogo de especialidades -> cluster (webdata/especialidades.json) +
  // nombres de cluster (webdata/clusters.json), para el dropdown de
  // Especialidad del médico y su chip de cluster derivado (2026-07-11: ya no
  // hay campo "Cluster" editable a mano — se deriva SIEMPRE de la especialidad,
  // acá para preview instantáneo y en catalog.py como fuente de verdad). ----
  var ESPECIALIDADES = [];
  var CLUSTERS_NOMBRE = {};
  var _ESP_PROMISE = null;
  var ESP_UNAVAILABLE = false;

  function loadEspecialidadesYClusters() {
    if (_ESP_PROMISE) return _ESP_PROMISE;
    _ESP_PROMISE = Promise.all([
      fetch("/data/especialidades.json").then(function (r) { if (!r.ok) throw new Error("specialties-unavailable"); return r.json(); }),
      fetch("/data/clusters.json").then(function (r) { if (!r.ok) throw new Error("clusters-unavailable"); return r.json(); })
    ]).then(function (res) {
      if (!Array.isArray(res[0].specialties) || !Array.isArray(res[1].clusters)) throw new Error("invalid-catalog");
      ESP_UNAVAILABLE = false;
      ESPECIALIDADES = res[0].specialties;
      (res[1].clusters || []).forEach(function (c) { CLUSTERS_NOMBRE[c.id] = c.nombre; });
    }).catch(function (error) {
      _ESP_PROMISE = null;
      if (isCancelled(error)) throw error;
      ESP_UNAVAILABLE = true;
    });
    return _ESP_PROMISE;
  }

  function especialidadCluster(nombre) {
    var found = ESPECIALIDADES.filter(function (e) { return e.nombre === nombre; })[0];
    return found ? found.cluster : "";
  }

  function especialidadOptions(selected, placeholder) {
    var out = '<option value="">' + esc(placeholder || T("admin.content.catalog.doctors.specialty_placeholder", null, "Elegí una especialidad…")) + '</option>';
    if (selected && !ESPECIALIDADES.some(function (e) { return e.nombre === selected; })) {
      out += '<option value="' + esc(selected) + '" selected>' + esc(selected) + '</option>';
    }
    out += ESPECIALIDADES.map(function (e) {
      return '<option value="' + esc(e.nombre) + '"' + (e.nombre === selected ? " selected" : "") + ">" + esc(e.nombre) + "</option>";
    }).join("");
    return out;
  }

  // `especialidad`, when given, is the specialty chosen in the select above:
  // a general specialty of the same name («Pediatría» → «Pediatría») says
  // nothing new and is not written under it.
  function clusterChipHtml(clusterId, especialidad) {
    if (!clusterId) return T("admin.content.catalog.doctors.cluster_chip_empty", null, "Elegí una especialidad para ver su especialidad general.");
    var general = CLUSTERS_NOMBRE[clusterId] || clusterId;
    if (especialidad && String(especialidad).trim().toLowerCase() === String(general).trim().toLowerCase()) return "";
    return T("admin.content.catalog.doctors.cluster_chip_label", null, "Especialidad general:") + " <b>" + esc(general) + "</b>";
  }

  // ---- estado en memoria del editor de agenda multi-centro del médico
  // (2026-07-11: reemplaza la agenda semanal única — un médico puede atender
  // en más de un centro, con franjas y excepciones propias en cada uno). Vive
  // en JS, no en el DOM, para que agregar/quitar un centro/franja/excepción
  // pueda re-renderizar sólo ese sub-árbol sin perder lo tipeado en el resto
  // del form (y sin el bug de foco perdido de reconstruir todo el <section>). ----
  var MED_STATE = null;
  // Servicios que puede llevar cada franja del médico abierto, ya ordenados
  // por compatibilidad por /api/admin/doctors/<id>/compatible-services.
  var SVC_COMPAT = [];
  // Hasta que la lista llega, el combo conserva el servicio guardado: sin esto
  // el primer dibujo lo vaciaba y cualquier guardado lo borraba.
  var SVC_COMPAT_READY = false;
  // El servicio tiene una sola franja editable en esta pantalla, pero conserva
  // cualquier franja adicional ya importada. Las excepciones sí se editan acá
  // porque son el control diario para cerrar/bloquear la oferta web.
  var SVC_EXCEPCIONES = [];
  var SVC_FRANJAS_ADICIONALES = [];

  function idOf(tipo, item) { return item[TIPOS[tipo].idKey]; }

  // Default del sitio para "Máximo de reservas web por paciente" (booking_limits.site_default,
  // lo manda el listado de /api/admin/content). Es el placeholder de los dos
  // campos que pueden pisarlo, el del servicio y el del médico.
  var TURNOS_MAX_SITIO = 1;
  var DIAS_ANTICIPACION_SITIO = 31;
  // Upcoming site holidays (Settings › Feriados), shown read-only beside the
  // agenda's own closures; the same list closes every agenda server-side.
  var FERIADOS_SITIO = [];
  function feriadosHtml() {
    return hoursEditor().holidayRowsHtml(FERIADOS_SITIO, puede("configuracion") ? "/admin/settings" : "");
  }

  // ---- fetch de contenido (con caché; force=true tras guardar/borrar/restaurar) ----
  function fetchTipo(tipo, force) {
    if (!force && CACHE[tipo]) return Promise.resolve(CACHE[tipo]);
    return fetch("/api/admin/content/" + TIPO_URL[tipo], { credentials: "same-origin" }).then(function (res) {
      return afterSessionGate(res, function () {
        if (res.status === 401) throw new Error("401");
        if (res.status === 404) { CACHE[tipo] = { items: [], noBackend: true }; return CACHE[tipo]; }
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json().then(function (j) {
          if (j.turnos_max_sitio) TURNOS_MAX_SITIO = +j.turnos_max_sitio;
          if (j.dias_anticipacion_sitio) DIAS_ANTICIPACION_SITIO = +j.dias_anticipacion_sitio;
          if (Array.isArray(j.feriados_sitio)) FERIADOS_SITIO = j.feriados_sitio;
          CACHE[tipo] = { items: j.items || [], noBackend: false };
          return CACHE[tipo];
        });
      });
    });
  }

  function ensureCentros() { return fetchTipo("centros"); }

  function centrosVivos() {
    var c = CACHE.centros;
    if (!c || !c.items) return [];
    return c.items.filter(function (x) { return !x._borrado; });
  }

  function centroEncontrado(slug) {
    return centrosVivos().concat((CACHE.centros && CACHE.centros.items) || []).filter(function (c) { return c.slug === slug; })[0];
  }
  // A slug that names no center (deleted, or orphaned data) never reaches the
  // screen as a key (the product owner R14): once the centers are loaded it reads as a
  // nameless center; before that the slug is all there is.
  function centroNombre(slug) {
    var found = centroEncontrado(slug);
    if (found) return found.name;
    if (!slug) return "—";
    return CACHE.centros ? T("admin.doctor.unknown_center", null, "Centro sin nombre") : slug;
  }

  function ensureServicios() { return fetchTipo("servicios"); }

  // Nombre del servicio (= consultorio) etiquetado en una franja. Sin el
  // catálogo cargado devuelve "" en vez del id: un id crudo en la columna de
  // agenda no le dice nada a nadie.
  function servicioNombre(sid) {
    if (!sid) return "";
    var items = (CACHE.servicios && CACHE.servicios.items) || [];
    var found = items.filter(function (s) { return String(s.id) === String(sid); })[0];
    return found ? (found.nombre || "") : "";
  }

  function rawOriginal(tipo, id) {
    var key = tipo;
    var p = RAW_CACHE[key] ? Promise.resolve(RAW_CACHE[key]) : fetch(TIPOS[tipo].archivo).then(function (r) {
      if (!r.ok) throw new Error("no-raw");
      return r.json();
    }).then(function (j) { RAW_CACHE[key] = j; return j; });
    return p.then(function (j) {
      var arr = j[TIPOS[tipo].campo] || [];
      var idk = TIPOS[tipo].idKey;
      return arr.filter(function (x) { return x[idk] === id; })[0] || null;
    });
  }

  // ---- guardar / borrar / restaurar ----
  function confirmScheduleReset(conflict) {
    return new Promise(function (resolve) {
      var dialog = document.createElement("dialog");
      dialog.className = "cnt-schedule-confirm";
      var doctors = conflict.doctores || [];
      var names = doctors.length > 1
        ? '<ul>' + doctors.map(function (name) { return '<li>' + esc(name) + '</li>'; }).join('') + '</ul>'
        : '<p>' + esc(doctors[0] || '') + '</p>';
      var howMany = conflict.count || doctors.length;
      var title = Tn('admin.content.schedule.reset_title', howMany, null,
        'Esto reiniciará el horario de {n} médico', 'Esto reiniciará los horarios de {n} médicos');
      dialog.innerHTML = '<h3>' + esc(title) + '</h3><p>' +
        esc(T('admin.content.schedule.reset_hint', null, 'Cada médico pasa a usar el horario del servicio. ¿Continuar?')) + '</p>' + names +
        '<form method="dialog"><button class="btn btn-quiet" value="cancel">' + esc(T('admin.content.schedule.reset_cancel', null, 'Cancelar')) +
        '</button><button class="btn btn-accent" value="accept">' + esc(T('admin.content.schedule.continue', null, 'Continuar')) + '</button></form>';
      var finished = false;
      var version = mountVersion;
      var release = function () {
        if (finished) return;
        finished = true;
        var accepted = dialog.returnValue === "accept" && current(version);
        dialog.remove();
        routeCleanups.delete(release);
        resolve(accepted);
      };
      dialog.addEventListener("close", release, { once: true });
      routeCleanups.add(release);
      document.body.appendChild(dialog);
      dialog.showModal();
    });
  }

  function guardar(tipo, id, doc, onAcknowledged) {
    var path = id ? ("/api/admin/content/" + TIPO_URL[tipo] + "/" + encodeURIComponent(id)) : ("/api/admin/content/" + TIPO_URL[tipo]);
    return fetch(path, {
      method: id ? "PUT" : "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(doc), onAcknowledged: onAcknowledged
    }).then(function (res) {
      // Keep the draft visible on an expired session; the recovery link lets
      // the operator choose when to sign in again instead of losing edits.
      if (res.status === 401) throw new Error("401");
      return afterSessionGate(res, function () {
        return res.json().catch(decodeFailure).then(function (j) {
          if (res.status === 409 && j && j.error === "agenda_doctores_se_reinician" && !doc.confirmar_reinicio_agendas) {
            return confirmScheduleReset(j).then(function (accepted) {
              if (!accepted) throw new Error(T("admin.content.schedule.reset_cancelled", null, "No se guardaron cambios."));
              return guardar(tipo, id, Object.assign({}, doc, { confirmar_reinicio_agendas: true }), onAcknowledged);
            });
          }
          if (!res.ok || !j || j.ok !== true || !j.item || !idOf(tipo, j.item)) {
            throw new Error((j && (j.detalle || j.error)) || T("admin.content.catalog.common.save_failed", null, "No se pudo guardar."));
          }
          return j.item;
        });
      });
    });
  }

  function borrar(tipo, id) {
    return fetch("/api/admin/content/" + TIPO_URL[tipo] + "/" + encodeURIComponent(id), { method: "DELETE", credentials: "same-origin" })
      .then(function (res) {
        return afterSessionGate(res, function () {
          if (res.status === 401) throw new Error("401");
          return res.json().catch(decodeFailure).then(function (j) {
            if (!res.ok || !j.ok) {
              var err = new Error(j.error || T("admin.content.catalog.common.disable_failed", null, "No se pudo deshabilitar."));
              // 409 «Tiene N turnos por venir»: where the desk moves or cancels them.
              if (typeof j.enlace === "string" && j.enlace.indexOf("/admin/") === 0) err.enlace = j.enlace;
              throw err;
            }
            return j;  // {turnos, conservado} — cuántos turnos lo referencian
          });
        });
      });
  }

  function restaurar(tipo, id) {
    return fetch("/api/admin/content/" + TIPO_URL[tipo] + "/" + encodeURIComponent(id) + "/restaurar", { method: "POST", credentials: "same-origin" })
      .then(function (res) {
        return afterSessionGate(res, function () {
          if (res.status === 401) throw new Error("401");
          return res.json().catch(decodeFailure).then(function (j) {
            if (!res.ok || !j.ok) throw new Error(j.error || T("admin.content.catalog.common.restore_failed", null, "No se pudo restaurar."));
            return j.item;
          });
        });
      });
  }

  // ---- server validation failures associated with a field ----
  // The server remains the only authority (catalog.py): a rejected save does
  // not mutate data. Map only messages known to describe one form control;
  // duplicates, network failures, unknown results, and role/security text
  // keep using the toast.
  function catalogFieldError(tipo, message) {
    var msg = String(message || "");
    if (msg === "falta nombre") return { field: "name" };
    var centroFalta = msg === "falta centro" || msg.indexOf("el centro '") === 0;
    if (tipo === "servicios" && centroFalta) return { field: "center" };
    // The center's institution in HSI is a number (…/institucion/1086/…);
    // anything else the server rejects lands at the field, not in a toast.
    if (tipo === "centros" && msg.indexOf("la institución en HSI es un número") === 0) {
      return { field: "hsi_institution" };
    }
    if (tipo === "centros" && (msg.indexOf("las coordenadas deben ser dos números") === 0 || msg === "las coordenadas están fuera de rango")) {
      return { field: "coords" };
    }
    if (tipo !== "medicos") return null;
    if (msg === "falta especialidad") return { field: "specialty" };
    // The browser accepts «nombre@hospital»; the server wants a domain with
    // a dot. Say it at the field, not in a toast.
    if (msg === "email inválido") return { field: "email" };
    // The doctor's email is its account's login too (one email per doctor):
    // another active account already logs in with it.
    if (msg === "email_en_uso") return { field: "email", taken: true };
    // The server numbers submitted centros[] rows ("centro N: …") in the
    // same order collectMedico filters rows that have a center. Target that
    // real Nth row, never arbitrarily the first one. A plain missing-center
    // error means none was sent, so it targets the first row. Wrapped errors
    // about a schedule or exception remain a toast.
    var fila = null;
    if (centroFalta) fila = 1;
    else {
      var envuelto = msg.match(/^centro (\d+): (.+)$/);
      if (envuelto && (envuelto[2] === "falta centro" || envuelto[2].indexOf("el centro '") === 0)) fila = +envuelto[1];
    }
    return fila ? { field: "center-row", row: fila } : null;
  }

  function fieldErrorControl(tipo, sec, plan) {
    if (plan.field === "name") {
      if (tipo === "servicios") return $("#svc-nombre", sec);
      if (tipo === "centros") return $("#cen-nombre", sec);
      var pila = $("#med-nombre-pila", sec), apellido = $("#med-apellido", sec);
      // A name has two inputs; target the first one that needs correction.
      if (pila && !pila.value.trim()) return pila;
      if (apellido && !apellido.value.trim()) return apellido;
      return pila;
    }
    if (plan.field === "center") return $("#svc-centro", sec);
    if (plan.field === "hsi_institution") return $("#cen-hsi", sec);
    if (plan.field === "coords") return $("#cen-coords", sec);
    if (plan.field === "specialty") return $("#med-especialidad", sec);
    if (plan.field === "email") return $("#med-email", sec);
    // center-row: the Nth submitted row (a select with a center), in DOM
    // order, which is the order collectMedico sends.
    var selects = $$("[data-centro-select]", sec);
    var enviadas = selects.filter(function (sel) { return sel.value; });
    var target = enviadas[plan.row - 1] || (plan.row === 1 ? selects[0] : null);
    if (target && !target.id) target.id = "med-centro-sel-" + (selects.indexOf(target) + 1);
    return target;
  }

  function fieldErrorText(plan) {
    if (plan.field === "specialty") return T("admin.content.catalog.doctors.error_missing_specialty", null, "Elegí la especialidad.");
    if (plan.field === "email" && plan.taken) return T("admin.content.catalog.doctors.email_taken", null, "Ese email ya es de otra cuenta activa.");
    if (plan.field === "email") return T("admin.content.catalog.doctors.invite_email_invalid", null, "Ingresá un email válido o dejalo vacío.");
    if (plan.field === "hsi_institution") return T("admin.content.catalog.centers.error_hsi_number", null, "Poné un número, por ejemplo 1086.");
    if (plan.field === "coords") return T("admin.content.catalog.centers.error_coords", null, "Pegalas como las da Google Maps, por ejemplo -37.1009, -56.8731.");
    if (plan.field === "center" || plan.field === "center-row") return T("admin.content.catalog.common.error_missing_center", null, "Elegí el centro.");
    return T("admin.content.catalog.common.error_missing_name", null, "Escribí el nombre.");
  }

  // The state and one correction listener belong to the actual DOM control.
  // Weak ownership means a replaced form cannot retain either state or a
  // listener, while repeated invalid submits of the same rendered field do
  // not accumulate handlers.
  var fieldErrors = new WeakMap();
  var fieldErrorBound = new WeakSet();

  // A form may already have a same-id helper owned by another feature. Keep a
  // record of the exact node and aria-describedby token created here, instead
  // of treating #<control>-msg as ours merely because it happens to exist.
  function fieldErrorMessageId(sec, control) {
    var id = control.id + "-msg";
    if (!$("#" + id, sec)) return id;
    var suffix = 1;
    do {
      id = control.id + "-validation-msg" + (suffix === 1 ? "" : "-" + suffix);
      suffix++;
    } while ($("#" + id, sec));
    return id;
  }

  // Clears only the record this controller owns. Pre-existing helper nodes,
  // their text, and unrelated aria-describedby tokens survive correction.
  function clearFieldError(control) {
    if (!control) return;
    var error = fieldErrors.get(control);
    if (!error) return;
    if (error.node.isConnected) error.node.remove();
    // Do not roll back an ARIA value a different owner set after this error.
    if (control.getAttribute("aria-invalid") === "true") {
      if (error.invalid.present) control.setAttribute("aria-invalid", error.invalid.value);
      else control.removeAttribute("aria-invalid");
    }
    var desc = (control.getAttribute("aria-describedby") || "").split(/\s+/)
      .filter(function (token) { return token && token !== error.token; });
    if (desc.length) control.setAttribute("aria-describedby", desc.join(" "));
    else control.removeAttribute("aria-describedby");
    fieldErrors.delete(control);
  }

  function revealFieldErrorControl(sec, control) {
    // Reveal a field in a hidden doctor tab through the screen's tab mechanism:
    // it updates the panel, aria-selected state, and action row without
    // rebuilding the form and losing the operator's draft.
    var panel = control.closest ? control.closest("[data-tabpanel]") : null;
    if (panel && panel.style.display === "none") {
      var tab = $('.cnt-tab[data-tab="' + panel.getAttribute("data-tabpanel") + '"]', sec);
      if (tab) tab.click();
    }
    // A field inside a folded group («Configuración interna», the exact
    // location of a center) is shown before it takes the focus.
    var fold = control.closest ? control.closest("details") : null;
    if (fold && !fold.open) fold.open = true;
    control.scrollIntoView({ block: "center" });
    control.focus();
  }

  function showFieldError(tipo, sec, message) {
    var plan = catalogFieldError(tipo, message);
    if (!plan) return false;
    var control = fieldErrorControl(tipo, sec, plan);
    return showControlError(sec, control, fieldErrorText(plan));
  }

  function showControlError(sec, control, message) {
    if (!control || !control.id || !control.isConnected) return false;
    var error = fieldErrors.get(control);
    if (!error) {
      var token = fieldErrorMessageId(sec, control);
      var node = document.createElement("p");
      node.className = "cnt-msg cnt-msg--mal";
      node.id = token;
      control.insertAdjacentElement("afterend", node);
      error = {
        node: node,
        token: token,
        invalid: {
          present: control.hasAttribute("aria-invalid"),
          value: control.getAttribute("aria-invalid")
        }
      };
      fieldErrors.set(control, error);
      // Correcting the field clears its feedback. The listener is route-owned,
      // and one WeakSet claim keeps invalid retries from stacking listeners.
      var corregir = function () {
        if (String(control.value || "").trim()) clearFieldError(control);
      };
      if (!fieldErrorBound.has(control)) {
        fieldErrorBound.add(control);
        listen(control, "input", corregir);
        listen(control, "change", corregir);
      }
    }
    error.node.textContent = message;
    error.node.setAttribute("role", "alert");
    control.setAttribute("aria-invalid", "true");
    var desc = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    if (desc.indexOf(error.token) === -1) desc.push(error.token);
    control.setAttribute("aria-describedby", desc.join(" "));
    revealFieldErrorControl(sec, control);
    return true;
  }

  function validateClosures(sec) {
    var valid = true;
    $$(".cnt-exc-row", sec).forEach(function (row, index) {
      var from = $("[data-exc-desde-fecha]", row) || $("[data-exc-fecha]", row);
      var to = $("[data-exc-hasta-fecha]", row);
      [from, to].forEach(function (control) { if (control) clearFieldError(control); });
      var control = from && !from.value ? from : to && (!to.value || to.value < from.value) ? to : null;
      if (!control) return;
      if (!control.id) control.id = "cnt-closure-date-" + index + (control === to ? "-to" : "-from");
      // Show every incomplete row, but leave focus on the first correction.
      var previousFocus = document.activeElement;
      showControlError(sec, control, to && to.value && from.value && to.value < from.value
        ? T("admin.content.hours.exc.invalid_range", null, "Hasta debe ser igual o posterior a Desde.")
        : T("admin.content.hours.exc.missing_date", null, "Completá la fecha del cierre."));
      if (!valid && previousFocus) previousFocus.focus();
      valid = false;
    });
    return valid;
  }

  // The doctor's hours, checked where they are typed (on-site tester, 2026-10-21):
  // the center starts empty and must be chosen, and with Sobreturnos on two
  // rows of one center may not share hours — the message sits on the row
  // that collides and names the other one, instead of a "franjas 1 y 2"
  // error from the server after saving.
  function validateDoctorHours(sec) {
    var valid = true;
    function first(control, message) {
      var previousFocus = document.activeElement;
      showControlError(sec, control, message);
      if (!valid && previousFocus) previousFocus.focus();
      valid = false;
    }
    $$(".cnt-centro-block", sec).forEach(function (block, bi) {
      var sel = $("[data-centro-select]", block);
      var rows = $$(".cnt-franja-row", block).map(function (row) {
        return {
          row: row,
          dias: $$("[data-franja-dia]", row).filter(function (el) { return el.checked; }).map(function (el) { return +el.value; }),
          desde: ($("[data-franja-desde]", row) || {}).value || "",
          hasta: ($("[data-franja-hasta]", row) || {}).value || "",
          cada: ($("[data-franja-cada]", row) || {}).value || "",
          // same rule as the server: either band with a percentage counts
          percent: +(($("[data-overbooking-enabled]", row) || {}).value || 0) > 0 &&
            +(($("[data-overbooking-percent]", row) || {}).value || 0) > 0
        };
      });
      if (sel) {
        clearFieldError(sel);
        if (!sel.value && rows.some(function (r) { return r.dias.length; })) {
          if (!sel.id) sel.id = "cnt-center-" + bi;
          first(sel, T("admin.content.hours.center.required", null, "Elegí en qué centro atiende."));
        }
      }
      // The message goes under the whole row (a grid cell is too narrow).
      $$("[data-overlap-msg]", block).forEach(function (n) { n.remove(); });
      rows.forEach(function (r) { var d = $("[data-franja-desde]", r.row); if (d) d.removeAttribute("aria-invalid"); });
      rows.forEach(function (b, j) {
        var a = rows.slice(0, j).filter(function (x) {
          return (x.percent || b.percent) && x.dias.some(function (d) { return b.dias.indexOf(d) !== -1; }) && x.desde < b.hasta && b.desde < x.hasta;
        })[0];
        var control = a && $("[data-franja-desde]", b.row);
        if (!control) return;
        var note = document.createElement("p");
        note.className = "cnt-msg cnt-msg--mal";
        note.id = "cnt-overlap-" + bi + "-" + j;
        note.setAttribute("role", "alert");
        note.setAttribute("data-overlap-msg", "");
        note.textContent = T("admin.content.hours.overlap_row", { other: a.desde + "–" + a.hasta + ", " + a.cada + " min" },
          "Este horario comparte horas con el de {other}. Con Sobreturnos, cada horario necesita sus propias horas: cambiá estas horas o apagá Sobreturnos.");
        b.row.insertAdjacentElement("afterend", note);
        control.setAttribute("aria-invalid", "true");
        control.setAttribute("aria-describedby", note.id);
        if (valid) { control.scrollIntoView({ block: "center" }); control.focus(); }
        valid = false;
      });
    });
    return valid;
  }

  function saveMessage(error) {
    if (String(error.message) === "401" || String(error.message) === "redirect") return T("admin.content.catalog.common.save_sign_in", null, "No se guardó. Volvé a iniciar sesión y reintentá.");
    if (isCancelled(error)) return new MutationOutcomeUnconfirmed().message;
    return errText(error, T("admin.content.catalog.common.save_failed", null, "No se pudo guardar."));
  }

  function saveStatus(sec, message, loginTarget) {
    var status = $("#cnt-save-status", sec);
    if (!status) return;
    status.hidden = false;
    status.className = "cnt-msg";
    status.innerHTML = esc(message) + (loginTarget
      ? ' <a href="/admin/login?next=' + esc(encodeURIComponent(loginTarget)) + '">' + esc(T("admin.content.catalog.common.sign_in", null, "Iniciar sesión")) + '</a>' : "");
  }

  // A doctor with upcoming appointments is not disabled (409): the status line
  // says how many and links to them, so the desk moves or cancels each one
  // and then disables (remedies — the solution, not the error).
  function disableBlocked(sec, message, enlace) {
    var status = $("#cnt-save-status", sec);
    if (!status) return;
    status.hidden = false;
    // A refusal the desk must act on reads in the danger colour, never the
    // muted status grey (live demo 24.10).
    status.className = "cnt-msg cnt-msg--mal";
    status.innerHTML = esc(message) + ' <a href="' + esc(enlace) + '">'
      + esc(T("admin.content.catalog.common.disable_blocked_link", null, "Ver los turnos")) + "</a>";
  }

  // admin-shell.js provides the toast in both the legacy and direct shells.
  // Session expiry remains local to this controller because direct pages do
  // not load admin-app.js's gate.
  function toast(msg) {
    if (window.toast) { window.toast(msg); return; }
  }
  function directStatus(msg, isError) {
    var status = document.getElementById("content-status");
    if (!status) return;
    status.hidden = false;
    status.className = "adm-empty" + (isError ? " cnt-status--error" : "");
    status.textContent = msg;
  }
  function sessionGate(response) {
    if (!response) return Promise.resolve(false);
    var version = mountVersion;
    return sessionReady.then(function (mod) {
      if (!current(version)) throw cancelled();
      return mod.confirmar(response, undefined, function () { return current(version); });
    });
  }
  function afterSessionGate(response, continueWithResponse) {
    var boundary = (response.status === 401 || response.status === 403)
      ? sessionGate(response) : Promise.resolve(false);
    return boundary.then(function (transitioned) {
      // Only a confirmed expiry leaves silently ("redirect"): the session
      // module is already sending the page away. An unconfirmed 401 (site
      // Basic Auth, a proxy) falls through to the caller, which says so.
      if (transitioned) throw new Error("redirect");
      return continueWithResponse();
    });
  }

  // =====================================================================
  // helpers de UI genéricos
  // =====================================================================

  function origenBadge(item) {
    if (item._borrado) return '<span class="cnt-badge cnt-badge-borrado">Deshabilitado</span>';
    if (item._origen === "db") return '<span class="cnt-badge cnt-badge-nuevo">Nuevo</span>';
    if (item._origen === "json+db") return '<span class="cnt-badge cnt-badge-editado">Editado</span>';
    return "";
  }

  function fmtCatalogDate(iso) {
    return Format.fechaCorta(iso) || "—";
  }

  function modoLabel(m) {
    return {
      walkin: T("admin.content.catalog.services.mode_walkin", null, "Sin turno"),
      walkin_con_orden: T("admin.content.catalog.services.mode_walkin_order", null, "Sin turno, con orden"),
      solicitud: T("admin.content.catalog.services.mode_appointment", null, "Con turno"),
      solicitud_con_orden: T("admin.content.catalog.services.mode_appointment_order", null, "Con turno, con orden")
    }[m] || (m || "—");
  }

  function franjaResumen(fr, mostrarServicio) {
    if (!fr || !fr.dias || !fr.dias.length) return "—";
    var dias = fr.dias.filter(function (d, i, all) { return all.indexOf(d) === i; }).sort(function (a, b) {
      var oa = a === 0 ? 7 : a, ob = b === 0 ? 7 : b; return oa - ob;
    });
    var etq = dias.map(function (d) { return (DIAS_UI.filter(function (x) { return x.v === d; })[0] || {}).l || "?"; });
    // The same localized list as format.mjs's scheduleBand ("Lu, Ma y Mi" /
    // "Mo, Tu and We"): a Spanish «y» between English days was the bug.
    // Three or more days in a row read as a span («Lu a Vi»), not as a list
    // of five.
    var orden = dias.map(function (d) { return d === 0 ? 7 : d; });
    var seguidos = etq.length >= 3 && orden[orden.length - 1] - orden[0] === orden.length - 1;
    var diasTxt = seguidos
      ? T("admin.doctor.days_span", { first: etq[0], last: etq[etq.length - 1] }, "{first} a {last}")
      : etq.length > 1
        ? T("admin.doctor.days_join", { first: etq.slice(0, -1).join(", "), last: etq[etq.length - 1] }, "{first} y {last}")
        : etq[0];
    // El consultorio, cuando la franja lo tiene etiquetado: para el hospital
    // "qué tipo de consultorio es, es más importante que la especialidad"
    // (the reference-deployment counterpart, 2026-07-24) — el mismo médico hace anticoncepción los
    // lunes y medicina general los martes, en el mismo horario.
    var svc = mostrarServicio && fr.servicio ? servicioNombre(fr.servicio) : "";
    return diasTxt + " " + (fr.desde || "?") + "–" + (fr.hasta || "?") + (svc ? " " + svc : "");
  }

  // Una franja por combinación real de horario/capacidad. Importaciones
  // antiguas podían traer Lu 09–12, Ma 09–12, Mi 09–12 como tres filas;
  // para el operador eso es un solo horario con tres días seleccionados.
  function compactarFranjas(franjas) {
    var grupos = {}, orden = [];
    (franjas || []).forEach(function (fr) {
      var cupos = fr.cupos || {};
      // El servicio entra en la clave: dos franjas con el MISMO horario pero
      // distinto consultorio (anticoncepción los lunes, medicina general los
      // martes) son dos cosas distintas y no se pueden fusionar en una sola
      // fila de días — es justo el caso que planteó el hospital.
      var key = JSON.stringify([
        fr.desde || "09:00", fr.hasta || "12:00", +(fr.cada_min || 20),
        cupos.web == null ? 1 : +cupos.web,
        +(cupos.reservado || 0), +(cupos.sobreturnos || 0),
        cupos.overbooking_percent == null ? null : +cupos.overbooking_percent,
        String(fr.servicio || "")
      ]);
      if (!grupos[key]) {
        grupos[key] = Object.assign({}, fr, { dias: [] });
        if (fr.cupos) grupos[key].cupos = Object.assign({}, fr.cupos);
        orden.push(key);
      }
      (fr.dias || []).forEach(function (dia) {
        dia = +dia;
        if (grupos[key].dias.indexOf(dia) === -1) grupos[key].dias.push(dia);
      });
    });
    return orden.map(function (key) {
      grupos[key].dias.sort(function (a, b) {
        var oa = a === 0 ? 7 : a, ob = b === 0 ? 7 : b; return oa - ob;
      });
      return grupos[key];
    });
  }

  // resumen de TODOS los centros/franjas/excepciones de un médico, en una
  // sola línea corta (table-design: los datos cortos no se parten en dos
  // líneas) — el detalle completo va en el `title` del <span> que lo muestra.
  function agendaResumen(item) {
    var centrosArr = item.centros;
    if (!centrosArr || !centrosArr.length) {
      // compat: médicos sin `centros` todavía (no re-guardados con el editor nuevo)
      var legacy = item.agenda && item.agenda.dias
        ? [item.agenda]
        : compactarFranjas((item.agenda && item.agenda.franjas) || []);
      var legacyServicios = distinct(legacy, function (fr) { return servicioNombre(fr.servicio); });
      if (legacy.length) return legacy.map(function (fr) { return franjaResumen(fr, legacyServicios.length > 1); }).join("; ");
      return "—";
    }
    var todas = [];
    centrosArr.forEach(function (c) { todas = todas.concat(compactarFranjas(c.franjas || [])); });
    var servicios = distinct(todas, function (fr) { return servicioNombre(fr.servicio); });
    var mostrarServicio = servicios.length > 1;
    return centrosArr.map(function (c) {
      var franjasTxt = compactarFranjas(c.franjas || []).map(function (fr) {
        return franjaResumen(fr, mostrarServicio);
      }).join("; ") || "sin horario";
      var n = (c.excepciones || []).length;
      var excTxt = n ? " (" + n + " excepción" + (n > 1 ? "es" : "") + ")" : "";
      return centroNombre(c.centro) + ": " + franjasTxt + excTxt;
    }).join(" · ");
  }

  function distinct(items, getter) {
    var seen = {}, out = [];
    items.forEach(function (it) {
      var v = (getter(it) || "").trim();
      if (v && !seen[v]) { seen[v] = 1; out.push(v); }
    });
    out.sort(function (a, b) { return a.localeCompare(b, "es"); });
    return out;
  }

  function datalist(id, values) {
    return '<datalist id="' + id + '">' + values.map(function (v) { return '<option value="' + esc(v) + '">'; }).join("") + "</datalist>";
  }

  function idLabel(id) {
    var s = String(id || "").replace(/[-_]+/g, " ");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  }

  function selectOptions(values, selected, labels) {
    var vals = values.slice();
    if (selected && vals.indexOf(selected) === -1) vals.push(selected);
    vals.sort(function (a, b) { return String((labels && labels[a]) || idLabel(a)).localeCompare(String((labels && labels[b]) || idLabel(b)), "es"); });
    return vals.map(function (v) {
      var label = (labels && labels[v]) || idLabel(v);
      return '<option value="' + esc(v) + '"' + (v === selected ? " selected" : "") + '>' + esc(label) + "</option>";
    }).join("");
  }

  function row(label, inner, extraCls, controlId) {
    var help = FIELD_HELP[String(label || "")];
    var labelHtml = help
      ? '<span class="cnt-label-help" tabindex="0" data-tip="' + esc(help) + '">' + label + '</span>'
      : label;
    // Catalog fields pass their native control as `inner`. Connect the label
    // to the first visible one so clicking the left label and assistive
    // technology both reach it. Callers may explicitly associate a file
    // chooser; hidden inputs are never chosen as the label target.
    var control = String(inner || "").match(/<(?:input|select|textarea)\b[^>]*>/i);
    var hidden = control && /\btype\s*=\s*["']?hidden\b/i.test(control[0]);
    var inferred = control && !hidden && control[0].match(/\bid="([^"]+)"/i);
    var targetId = controlId || (inferred && inferred[1]);
    var forAttr = targetId ? ' for="' + esc(targetId) + '"' : "";
    return '<div class="cnt-row' + (extraCls ? " " + extraCls : "") + '"><label' + forAttr + '>' + labelHtml + "</label><div class=\"cnt-field\">" + inner + "</div></div>";
  }

  var FIELD_HELP = {
    "Descripción breve": "Resumen que aparece debajo del nombre del servicio en la página pública.",
    "Centro": "Centro de salud al que pertenece este servicio.",
    "Sede": "Ubicación específica cuando difiere del centro principal.",
    "Cómo se atiende": "Define si requiere turno, orden médica, confirmación o lista del mismo día.",
    "Horario": "Horario público habitual del servicio.",
    "Nota sobre el horario": "Aclaración pública que acompaña el horario.",
    "Cartel": "Mensaje principal que ve el paciente antes de pedir o consultar un turno.",
    "Aviso público": "Alerta temporal visible en la página del servicio, por ejemplo una vacuna sin stock.",
    "Categoría": "Agrupación interna que decide dónde se lista el servicio.",
    "Especialidad general": "Grupo amplio usado para búsqueda, filtros y navegación.",
    "Edad": "Rango de edades aceptado por este servicio.",
    "PAMI": "Instrucción pública específica para pacientes de PAMI.",
    "Palabras clave": "Términos adicionales que hacen aparecer este servicio en la búsqueda.",
    "No aparecer si buscan": "Consultas que deben excluir este servicio de los resultados."
  };

  // =====================================================================
  // editor de agenda semanal (médicos + servicios con turno)
  // =====================================================================

  function cuposEditor(cupos) {
    var c = cupos || {};
    var web = c.web === 0 ? 0 : 1;
    var reservado = c.reservado || 0;
    return '<fieldset class="cnt-cupos"><legend>' + esc(T("admin.content.catalog.services.slots_legend", null, "Cupos por horario")) + '</legend>' +
      hoursEditor().capacityDescriptionHtml() +
      '<div class="hor-c">' + esc(T("admin.content.catalog.services.slots_web", null, "Reserva por web")) + ' ' + hoursEditor().webChoiceHtml(web) + '</div>' +
      '<input type="hidden" data-cupo-reservado value="' + esc(reservado) + '">' +
      '<input type="hidden" data-cupo-sobreturnos value="' + esc(c.sobreturnos || 0) + '">' +
      (c.overbooking_percent == null ? '' : '<input type="hidden" data-preserved-overbooking-percent value="' + esc(c.overbooking_percent) + '">') +
      '</fieldset>';
  }

  function collectCupos(root) {
    function numero(sel, fallback) {
      var el = $(sel, root);
      if (el && el.type === "checkbox") return el.checked ? 1 : 0;
      return el ? Math.max(0, +(el.value || fallback)) : fallback;
    }
    // El sitio público tiene una sola reserva por horario. Guardar 0 permite
    // cerrar esa oferta sin inventar turnos dobles; los demás cupos quedan
    // exclusivamente para el personal hasta que el calendario los asigne.
    var quotas = {
      web: numero("[data-cupo-web]", 1),
      reservado: numero("[data-cupo-reservado]", 0),
      sobreturnos: numero("[data-cupo-sobreturnos]", 0)
    };
    if ($("[data-overbooking-enabled]", root)) {
      quotas.overbooking_percent = numero("[data-overbooking-enabled]", 0)
        ? numero("[data-overbooking-percent]", 20) : 0;
    } else if ($("[data-preserved-overbooking-percent]", root)) {
      quotas.overbooking_percent = numero("[data-preserved-overbooking-percent]", 0);
    }
    return quotas;
  }

  function renderAgendaEditor(prefix, agenda) {
    var ag = agenda || {};
    var dias = ag.dias || [];
    var boxes = DIAS_UI.map(function (d) {
      var checked = dias.indexOf(d.v) !== -1;
      return '<label class="cnt-daychk"><input type="checkbox" data-agenda-dia="' + prefix + '" value="' + d.v + '"' + (checked ? " checked" : "") + '><span>' + d.l + "</span></label>";
    }).join("");
    return (
      '<div class="cnt-agenda" data-agenda-prefix="' + prefix + '">' +
      '<div class="cnt-daygrid">' + boxes + "</div>" +
      '<div class="cnt-agenda-times">' +
      '<label>' + esc(T("admin.content.catalog.services.agenda_from", null, "De")) + ' <input type="time" class="cnt-in cnt-time" data-agenda-desde="' + prefix + '" value="' + esc(ag.desde || "09:00") + '"></label>' +
      '<label>' + esc(T("admin.content.catalog.services.agenda_to", null, "a")) + ' <input type="time" class="cnt-in cnt-time" data-agenda-hasta="' + prefix + '" value="' + esc(ag.hasta || "12:00") + '"></label>' +
      '<label>' + esc(T("admin.content.catalog.services.agenda_every", null, "cada")) + ' ' + hoursEditor().stepperHtml('min="5" step="5" data-agenda-cada="' + prefix + '"', ag.cada_min || 20, null, T('admin.content.hours.col.every_min', null, 'Cada (min)')) + ' ' + esc(T("admin.content.catalog.services.agenda_min", null, "min")) + '</label>' +
      "</div>" +
      cuposEditor(ag.cupos) +
      '<p class="cnt-hint">' + esc(T("admin.content.catalog.services.agenda_hint", null, "Tildá los días en que atiende; sin ningún día tildado, el servicio queda sin agenda de turnos.")) + '</p>' +
      "</div>"
    );
  }

  function collectAgenda(prefix) {
    var dias = $$('[data-agenda-dia="' + prefix + '"]:checked').map(function (el) { return +el.value; });
    if (!dias.length) return null;
    var desde = $('[data-agenda-desde="' + prefix + '"]').value || "09:00";
    var hasta = $('[data-agenda-hasta="' + prefix + '"]').value || "12:00";
    var cada = +($('[data-agenda-cada="' + prefix + '"]').value || 20);
    var host = $('[data-agenda-prefix="' + prefix + '"]');
    return { dias: dias, desde: desde, hasta: hasta, cada_min: cada || 20, cupos: collectCupos(host) };
  }

  // =====================================================================
  // editor de agenda MULTI-CENTRO del médico (centros[] -> franjas[] +
  // excepciones[]) — reemplaza la agenda semanal única de un solo centro.
  // =====================================================================

  // Servicio de la franja (responsable del producto, reunión hospital 2026-07-24): un mismo médico
  // hace DISTINTOS servicios en distintos días — Lazcano da anticoncepción los
  // lunes y medicina general los martes. Es opcional: sin servicio la franja
  // se comporta como siempre. La lista viene del backend ordenada por
  // compatibilidad (los que este médico ya usa primero) y el combo exige un id
  // del catálogo — texto libre revierte, como el "Derivar a" de la cola.
  function franjaServicioItems(centro) {
    return SVC_COMPAT.filter(function (s) {
      return !!centro && String(s.centro || "") === String(centro);
    }).map(function (s) {
      return {
        v: String(s.id),
        t: s.nombre || String(s.id),
        s: ""
      };
    });
  }

  // Resolve the one shared typeable combo implementation. This lets the
  // doctor editor run on a direct screen without loading admin-app.js. If the
  // dependency is missing, fail visibly rather than rendering a private
  // fallback or a silent, nonfunctional cell.
  function combo() {
    if (!Combo || typeof Combo.html !== "function") {
      throw new Error("admin-content.mjs: platform/admin-combo.mjs must load before the doctor editor renders");
    }
    return Combo;
  }

  // Sólo el combo: la etiqueta "Servicio" la escribe el encabezado de la
  // grilla una única vez, y la celda la pone platform/admin-hours-editor.mjs.
  function franjaServicioHtml(fr, centro, ci, fi) {
    var actual = String(fr.servicio || "");
    var items = franjaServicioItems(centro);
    var sel = items.filter(function (x) { return x.v === actual; })[0];
    if (!sel && SVC_COMPAT_READY) actual = "";
    return combo().html("fr-svc-sel", items, actual, sel ? sel.t : "",
      centro ? T("admin.content.catalog.doctors.slot_service_unset", null, "Sin especificar") : T("admin.content.catalog.doctors.slot_service_no_center", null, "Elegí un centro primero"),
      T("admin.content.catalog.doctors.slot_service_title", null, "Servicio de este centro que atiende el médico en este horario"));
  }

  // El dibujo del editor multi-centro vive en platform/admin-hours-editor.mjs:
  // una fila de grilla por horario, las etiquetas una sola vez arriba. Acá
  // quedan el estado, el guardado y los eventos.
  // A new doctor's or service's center starts EMPTY (on-site tester, 2026-10-21): a
  // preselected first center was easy to miss and not everyone works there.
  function centroOptionsEmpty(selected) {
    return (selected ? "" : '<option value="" selected disabled>' +
      esc(T("admin.content.hours.center.pick", null, "Elegí un centro")) + "</option>") + centroOptions(selected);
  }

  function hoursOpts() {
    return {
      centroOptions: centroOptionsEmpty,
      servicioHtml: franjaServicioHtml,
      turnosActivosMax: (MED_STATE || {}).turnos_activos_max,
      diasAnticipacion: (MED_STATE || {}).dias_anticipacion,
      aperturaTurnos: (MED_STATE || {}).apertura_turnos,
      patientDefault: (MED_STATE || {}).bookingDefaults?.patient || TURNOS_MAX_SITIO,
      daysDefault: (MED_STATE || {}).bookingDefaults?.days || DIAS_ANTICIPACION_SITIO,
      patientFrom: (MED_STATE || {}).bookingDefaults?.patient_from,
      daysFrom: (MED_STATE || {}).bookingDefaults?.days_from,
      // Los límites de reserva los define el administrador; el servidor no
      // acepta los de un médico, así que su ficha no los muestra editables.
      bookingLimits: window.CURRENT_ROLE !== "medico",
      feriados: FERIADOS_SITIO,
      feriadosHref: puede("configuracion") ? "/admin/settings" : ""
    };
  }

  function renderExcepcionRow(ci, ei, exc) {
    return hoursEditor().excRowHtml(ci, ei, exc);
  }

  function syncExcepcionRow(e, erow) {
    var tipoEl = $("[data-exc-tipo]", erow);
    e.tipo = tipoEl ? tipoEl.value : "cerrado";
    e.motivo = $("[data-exc-motivo]", erow).value;
    // Cambiar el tipo re-dibuja la fila con OTROS campos de fecha, y esta
    // función lee el DOM viejo: sin este arrastre, pasar unas vacaciones a
    // bloqueo parcial (o al revés) borraba la fecha ya cargada.
    if (e.tipo === "cerrado") {
      var desdeFecha = $("[data-exc-desde-fecha]", erow), hastaFecha = $("[data-exc-hasta-fecha]", erow);
      var unica = $("[data-exc-fecha]", erow);
      var desdeValor = desdeFecha ? desdeFecha.value : (unica ? unica.value : (e.desde_fecha || e.fecha || ""));
      var hastaValor = hastaFecha ? hastaFecha.value : (unica ? unica.value : (e.hasta_fecha || e.fecha || ""));
      delete e.desde; delete e.hasta; delete e.cada_min;
      delete e.desde_fecha; delete e.hasta_fecha;
      if (desdeValor && desdeValor === hastaValor) e.fecha = desdeValor;
      else {
        delete e.fecha;
        e.desde_fecha = desdeValor;
        e.hasta_fecha = hastaValor;
      }
    } else {
      var fechaEl = $("[data-exc-fecha]", erow), rangoEl = $("[data-exc-desde-fecha]", erow);
      e.fecha = fechaEl ? fechaEl.value
        : (rangoEl ? rangoEl.value : (e.fecha || e.desde_fecha || ""));
      delete e.desde_fecha; delete e.hasta_fecha;
    }
    if (e.tipo === "extra" || e.tipo === "bloqueado") {
      var d2 = $("[data-exc-desde]", erow), h2 = $("[data-exc-hasta]", erow), c2 = $("[data-exc-cada]", erow);
      if (d2) e.desde = d2.value || "09:00";
      if (h2) e.hasta = h2.value || "12:00";
      if (e.tipo === "extra" && c2) e.cada_min = +(c2.value || 20);
      if (e.tipo === "bloqueado") delete e.cada_min;
    }
  }

  function renderServiceExcepciones() {
    var rows = SVC_EXCEPCIONES.map(function (exc, ei) {
      return renderExcepcionRow("svc", ei, exc)
        .replace('data-remove-excepcion="svc:' + ei + '"', 'data-remove-svc-excepcion="' + ei + '"');
    }).join("");
    return '<div class="cnt-fgroup cnt-fgroup--exc" id="svc-excepciones">' +
      (rows ? hoursEditor().closureHeadingHtml() + hoursEditor().excColsHtml() + rows : "") +
      '<div class="hor-acts"><button type="button" class="hor-add" data-add-svc-excepcion>+ ' + esc(T("admin.content.catalog.services.add_closure", null, "Agregar cierre")) + '</button></div>' +
      feriadosHtml() +
      '</div>';
  }

  function syncServiceExcepciones(sec) {
    $$("#svc-excepciones .cnt-exc-row", sec).forEach(function (erow) {
      var ei = +erow.getAttribute("data-ei");
      if (SVC_EXCEPCIONES[ei]) syncExcepcionRow(SVC_EXCEPCIONES[ei], erow);
    });
  }

  function reRenderServiceExcepciones(sec) {
    $("#svc-excepciones", sec).outerHTML = renderServiceExcepciones();
  }

  function wireServiceAgenda(sec) {
    if (!claimRouteBinding(sec, "_svcAgendaWired")) return;
    var owner = $('#svc-schedule-owner', sec);
    var count = Number(owner && owner.getAttribute('data-doctor-count') || 0);
    function paintOwner() {
      if (!owner) return;
      $('#svc-schedule-editor', sec).hidden = !owner.checked;
      var hasAppointments = $('#svc-c-turno', sec).checked || ($('#svc-c-mismodia', sec) || {}).checked;
      $('#svc-closures', sec).hidden = hasAppointments && !owner.checked;
      $('#svc-owner-status', sec).textContent = owner.checked
        ? Tn('admin.content.schedule.owned_state', count, null, 'El médico de este servicio atiende en este horario y no lo puede cambiar desde su cuenta.', 'Los {n} médicos de este servicio atienden en este horario y no lo pueden cambiar desde su cuenta.')
        : T('admin.content.schedule.doctor_state', null, 'Cada médico define sus días y horarios.');
    }
    if (owner) { listen(owner, 'change', paintOwner); $$('#svc-modo-list input[type=checkbox]', sec).forEach(function(input) { listen(input, 'change', paintOwner); }); paintOwner(); }
    listen(sec, "click", function (e) {
      var add = e.target.closest("[data-add-svc-excepcion]");
      if (add) {
        e.preventDefault(); syncServiceExcepciones(sec);
        SVC_EXCEPCIONES.push({fecha: "", tipo: "cerrado"});
        reRenderServiceExcepciones(sec); return;
      }
      var remove = e.target.closest("[data-remove-svc-excepcion]");
      if (remove) {
        e.preventDefault(); syncServiceExcepciones(sec);
        SVC_EXCEPCIONES.splice(+remove.getAttribute("data-remove-svc-excepcion"), 1);
        reRenderServiceExcepciones(sec);
      }
    });
    listen(sec, "change", function (e) {
      if (e.target.matches && e.target.matches("#svc-excepciones [data-exc-tipo]")) {
        syncServiceExcepciones(sec);
        reRenderServiceExcepciones(sec);
      }
    });
  }

  function renderCentrosEditor() {
    return hoursEditor().editorHtml(MED_STATE.centros, hoursOpts());
  }

  function syncCentrosFromDom(sec) {
    if (!MED_STATE) return;
    ['turnos-max', 'dias-anticipacion', 'apertura-turnos'].forEach(function(key, index) {
      var field = $('#med-' + key, sec);
      if (field) MED_STATE[['turnos_activos_max','dias_anticipacion','apertura_turnos'][index]] = field.value;
    });
    $$(".cnt-centro-block", sec).forEach(function (block) {
      var ci = +block.getAttribute("data-ci");
      var c = MED_STATE.centros[ci];
      if (!c) return;
      var sel = $("[data-centro-select]", block);
      if (sel) c.centro = sel.value;
      var dias = $("[data-centro-dias]", block);
      if (dias) {
        if (String(dias.value).trim()) c.dias_anticipacion = +dias.value; else delete c.dias_anticipacion;
      }
      $$("[data-servicio-fi]", block).forEach(function (wrap) {
        var fi = +wrap.getAttribute("data-servicio-fi");
        var f = c.franjas[fi];
        var svc = $(".fr-svc-sel", wrap);
        if (!f || !svc) return;
        var sid = (svc.value || "").trim();
        if (sid) f.servicio = sid; else delete f.servicio;
      });
      $$(".cnt-franja-row", block).forEach(function (frow) {
        var fi = +frow.getAttribute("data-fi");
        var f = c.franjas[fi];
        if (!f) return;
        f.dias = $$("[data-franja-dia]", frow).filter(function (el) { return el.checked; }).map(function (el) { return +el.value; });
        f.desde = $("[data-franja-desde]", frow).value || "09:00";
        f.hasta = $("[data-franja-hasta]", frow).value || "12:00";
        f.cada_min = +($("[data-franja-cada]", frow).value || 20);
        f.cupos = collectCupos(frow);
      });
      $$(".cnt-exc-row", block).forEach(function (erow) {
        var ei = +erow.getAttribute("data-ei");
        var e = c.excepciones[ei];
        if (!e) return;
        syncExcepcionRow(e, erow);
      });
    });
  }

  function reRenderCentros(sec) {
    $("#med-centros-editor", sec).outerHTML = renderCentrosEditor();
  }

  function wireCentrosEditor(sec) {
    // Los listeners van delegados en `sec` (persiste entre navegaciones SPA
    // — renderForm sólo reemplaza sec.innerHTML) a propósito, para sobrevivir
    // al outerHTML de reRenderCentros. Pero eso significa que atarlos de
    // nuevo en CADA visita a un médico los acumula sin límite (un solo click
    // en "+ Agregar excepción" terminaba agregando una fila por cada médico
    // visitado en la sesión). Se atan una única vez por `sec`; como leen
    // siempre el MED_STATE módulo-global vigente, funcionan igual sin
    // importar cuándo se ataron (responsable del producto, 2026-07-16, hallado al revisar el
    // form médico).
    if (!claimRouteBinding(sec, "_cntCentrosWired")) return;
    listen(sec, "click", function (e) {
      if (!MED_STATE) return;
      var addCentro = e.target.closest("[data-add-centro]");
      if (addCentro) {
        e.preventDefault(); syncCentrosFromDom(sec);
        MED_STATE.centros.push({ centro: "", franjas: [], excepciones: [] });
        reRenderCentros(sec); return;
      }
      var remCentro = e.target.closest("[data-remove-centro]");
      if (remCentro) {
        e.preventDefault(); syncCentrosFromDom(sec);
        MED_STATE.centros.splice(+remCentro.getAttribute("data-remove-centro"), 1);
        reRenderCentros(sec); return;
      }
      var addFranja = e.target.closest("[data-add-franja]");
      if (addFranja) {
        e.preventDefault(); syncCentrosFromDom(sec);
        MED_STATE.centros[+addFranja.getAttribute("data-add-franja")].franjas.push({ dias: [], desde: "09:00", hasta: "12:00", cada_min: 20 });
        reRenderCentros(sec); return;
      }
      var remFranja = e.target.closest("[data-remove-franja]");
      if (remFranja) {
        e.preventDefault(); syncCentrosFromDom(sec);
        var pf = remFranja.getAttribute("data-remove-franja").split(":");
        MED_STATE.centros[+pf[0]].franjas.splice(+pf[1], 1);
        reRenderCentros(sec); return;
      }
      var addExc = e.target.closest("[data-add-excepcion]");
      if (addExc) {
        e.preventDefault(); syncCentrosFromDom(sec);
        MED_STATE.centros[+addExc.getAttribute("data-add-excepcion")].excepciones.push({ fecha: "", tipo: "cerrado" });
        reRenderCentros(sec); return;
      }
      var remExc = e.target.closest("[data-remove-excepcion]");
      if (remExc) {
        e.preventDefault(); syncCentrosFromDom(sec);
        var pe = remExc.getAttribute("data-remove-excepcion").split(":");
        MED_STATE.centros[+pe[0]].excepciones.splice(+pe[1], 1);
        reRenderCentros(sec); return;
      }
    });
    listen(sec, "change", function (e) {
      if (e.target.matches && e.target.matches("[data-exc-tipo]")) {
        syncCentrosFromDom(sec);
        reRenderCentros(sec);
        return;
      }
      if (e.target.matches && e.target.matches("[data-centro-select]")) {
        syncCentrosFromDom(sec);
        var block = e.target.closest(".cnt-centro-block");
        var ci = block ? +block.getAttribute("data-ci") : -1;
        var centro = ci >= 0 && MED_STATE.centros[ci] ? MED_STATE.centros[ci].centro : "";
        var permitidos = {};
        franjaServicioItems(centro).forEach(function (s) { permitidos[s.v] = true; });
        if (ci >= 0) {
          MED_STATE.centros[ci].franjas.forEach(function (fr) {
            if (fr.servicio && !permitidos[String(fr.servicio)]) delete fr.servicio;
          });
        }
        reRenderCentros(sec);
      }
    });
  }

  // =====================================================================
  // plantilla de cartel (ayuda "Regenerar del modo y horario")
  // =====================================================================

  function cartelTemplate(modo, horario, nombreCentro) {
    var h = (horario || "").trim();
    var hMin = h ? h.charAt(0).toLowerCase() + h.slice(1) : "";
    if (modo === "walkin") return "No necesitás turno. Se atiende por orden de llegada" + (hMin ? ": " + hMin + "." : ".");
    if (modo === "walkin_con_orden") return "Vení con tu orden médica. Se atiende por orden de llegada" + (hMin ? ": " + hMin + "." : ".");
    if (modo === "solicitud") return "Pedí tu turno acá y el hospital te lo confirma" + (hMin ? ". Se atiende " + hMin + "." : ".");
    if (modo === "solicitud_con_orden") return "Necesitás una orden de derivación. Subí la orden acá y el hospital te confirma el turno; también podés llevarla a la mesa de admisión del " + (nombreCentro || "centro") + ".";
    return "";
  }

  // =====================================================================
  // LISTAS
  // =====================================================================

  var COLS = {
    servicios: [
      { key: "nombre", label: T("admin.content.catalog.columns.service", null, "Servicio"), always: true, sort: function (it) { return (it.nombre || "").toLowerCase(); } },
      { key: "centro", label: T("admin.content.catalog.columns.center", null, "Centro"), "default": true, sort: function (it) { return centroNombre(it.centro).toLowerCase(); } },
      { key: "modo", label: T("admin.content.catalog.columns.mode", null, "Modo"), "default": true, sort: function (it) { return modoLabel(it.modo); } },
      // «Consultorios sin médico» (the hospital's technical contact, 2026-11-02): the booked
      // services nobody attends sort first, the walk-in ones (never assigned) right after.
      { key: "medicos", label: T("admin.content.catalog.columns.doctors", null, "Médicos"), "default": true, sort: function (it) { var n = (it._medicos || []).length; return n ? n + 1 : (servicioSinAgenda(it) ? 1 : 0); } },
      { key: "horario", label: T("admin.content.catalog.columns.schedule", null, "Horario"), "default": true, sort: function (it) { return (it.horario || "").toLowerCase(); } },
      // Creado / Actualizado wait behind the column gear: on a loaded catalog
      // every row reads the same import date (Opus UI review 2026-11-03).
      { key: "created", label: T("admin.content.catalog.columns.created", null, "Creado"), sort: function (it) { return it._created || null; } },
      { key: "updated", label: T("admin.content.catalog.columns.updated", null, "Actualizado"), sort: function (it) { return it._updated || null; } }
    ],
    centros: [
      // "nombre" stays the column KEY (shared generic dispatch below with
      // servicios/medicos' own "nombre" column) even though the underlying
      // centros field is now "name" — cellHtml reads item.nombre||item.name.
      { key: "nombre", label: T("admin.content.catalog.columns.center", null, "Centro"), always: true, sort: function (it) { return (it.nombre || it.name || "").toLowerCase(); } },
      { key: "address", label: T("admin.content.catalog.columns.address", null, "Dirección"), "default": true, sort: function (it) { return (it.address || "").toLowerCase(); } },
      { key: "phone", label: T("admin.content.catalog.columns.phone", null, "Teléfono"), "default": true, sort: function (it) { return (it.phone || ""); } },
      { key: "created", label: T("admin.content.catalog.columns.created", null, "Creado"), sort: function (it) { return it._created || null; } },
      { key: "updated", label: T("admin.content.catalog.columns.updated", null, "Actualizado"), sort: function (it) { return it._updated || null; } }
    ]
  };

  // La ficha de un centro (/admin/centers) pide el permiso `servicios`: quien
  // no lo tiene (el gestor de agendas) ve el nombre del centro como texto, no
  // un enlace que termina en «No tenés permiso» (verificación en vivo de MR 52,
  // 2026-10-24).
  function centroCellHtml(slug) {
    if (!slug) return '<span class="cnt-muted">—</span>';
    if (!puede("servicios") || (CACHE.centros && !centroEncontrado(slug))) return esc(centroNombre(slug));
    return '<a href="' + esc(itemHref("centros", slug)) + '">' + esc(centroNombre(slug)) + "</a>";
  }

  // A walk-in service without a same-day opening has no agenda, so nobody is
  // ever assigned to it: an empty doctor list there is not a gap to fix
  // (18 of the demo's 19 «Sin médico» were walk-in; Opus review of the panel,
  // 2026-11-04).
  function servicioSinAgenda(it) {
    return !!it && (it.modo === "walkin" || it.modo === "walkin_con_orden") && !it.apertura_mismo_dia;
  }

  // Who attends a service: the names (links to their records), «Sin médico»
  // where patients book one, nothing on a walk-in service (an absence is
  // shown by an empty cell, not a dash in every row).
  // Doctors' records need the `medicos` permission; without it, plain text.
  function medicosCellHtml(it) {
    var list = (it && it._medicos) || [];
    if (!list.length && servicioSinAgenda(it)) return "";
    if (!list.length) return '<strong class="cnt-no-doctor">' + esc(T("admin.content.catalog.services.no_doctor", null, "Sin médico")) + "</strong>";
    return list.map(function (m) {
      return puede("medicos") ? '<a href="' + esc(itemHref("medicos", m.id)) + '">' + esc(m.nombre) + "</a>" : esc(m.nombre);
    }).join(", ");
  }

  function cellHtml(tipo, col, item) {
    var idk = TIPOS[tipo].idKey, id = item[idk];
    if (col.key === "nombre") {
      return '<a href="' + esc(itemHref(tipo, id)) + '">' + esc(item.nombre || item.name || "(sin nombre)") + "</a>";
    }
    if (col.key === "centro") {
      return centroCellHtml(item.centro);
    }
    if (col.key === "modo") return esc(modoLabel(item.modo));
    if (col.key === "medicos") return medicosCellHtml(item);
    if (col.key === "horario") return item.horario ? esc(item.horario) : '<span class="cnt-muted">—</span>';
    // The address opens Google Maps, as on every other screen that shows it
    // (bc3284ee promised it, the panel list was left out — Opus UI review
    // 2026-11-03): the center's own maps_url, else a search on the address.
    if (col.key === "address") {
      if (!item.address) return "";
      var mapsUrl = item.maps_url || ("https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(item.address));
      return '<a href="' + esc(mapsUrl) + '" target="_blank" rel="noopener">' + esc(item.address) + "</a>";
    }
    // El teléfono del centro se escribe con la misma regla que el de un
    // paciente: sin +54, arrancando por el característico (format.mjs).
    if (col.key === "phone") {
      if (!item.phone) return '<span class="cnt-muted">—</span>';
      return Format.contactoIconoHtml(item.phone);
    }
    if (col.key === "created") return esc(fmtCatalogDate(item._created));
    if (col.key === "updated") return esc(fmtCatalogDate(item._updated));
    return "";
  }

  var CATALOG_TABLE_FIT = {};

  function catalogTableColumns(tipo, sec) {
    // The doctors directory carries several compact metrics beside two human
    // text columns. Give those text columns their working width before the
    // generic fitter distributes the remaining space.
    var sizing = tipo === "medicos" ? {
      nombre: { min: 180, max: 320, flex: 3 },
      // 168px holds «Otorrinolaringología» whole, without a mid-word break.
      especialidad: { min: 168, max: 260, flex: 2 },
      centro: { min: 130, max: 240, flex: 2 },
      horas_semana: { min: 80, max: 108, flex: 0 },
      proximo_dia: { min: 84, max: 112, flex: 0 },
      // a percent («52%») like the hours, no longer the 92px squares
      ocupacion_14d: { min: 80, max: 108, flex: 0 },
      seleccion: { min: 40, max: 40, flex: 0 }
    } : {};
    // A date or a phone is not free text: it never wraps and never takes an
    // ellipsis, so its track starts where the whole value fits. The label-based
    // minimum (67px for «Creado») cut «8 Jul 2026» at 1100px (the product owner, 2026-10-29).
    sizing = Object.assign({
      created: { min: 104, max: 160, flex: 0 },
      updated: { min: 104, max: 160, flex: 0 },
      phone: { min: 176, max: 220, flex: 0 }
    }, sizing);
    // The row action («Habilitar», «Restaurar original») keeps its whole
    // label in every catalog: its track is the widest button plus the cell
    // padding. A fixed 40px cut it to «Hab» on Médicos at 1440px, and the
    // auto-layout measurement of a clipped cell came back near zero.
    var acciones = rowActionsWidth(sec);
    if (acciones) sizing = Object.assign({}, sizing, { acciones: { min: acciones, max: acciones, flex: 0 } });
    var headers = sec.querySelectorAll(".cnt-table--" + tipo + " thead th");
    return Array.prototype.map.call(headers, function (th, index) {
      var key = th.getAttribute("data-col") || (index === 0 ? "seleccion" : "acciones");
      var label = (th.textContent || "").replace(/[▲▼]/g, "").replace(/\s+/g, " ").trim();
      return Object.assign({ key: key, min: Math.max(52, Math.min(150, 22 + label.length * 7.5)), max: 420, flex: label.length >= 6 ? 1 : 0,
        resizable: key !== "seleccion" && key !== "acciones" }, sizing[key] || {});
    });
  }

  function rowActionsWidth(sec) {
    var widest = 0;
    $$(".cnt-table .cnt-td-acts .btn", sec).forEach(function (button) {
      var width = button.getBoundingClientRect().width;
      if (!width) return;
      var cell = getComputedStyle(button.closest("td"));
      widest = Math.max(widest, Math.ceil(width + (parseFloat(cell.paddingLeft) || 0) + (parseFloat(cell.paddingRight) || 0)));
    });
    return widest;
  }

  // Solo la tabla del listado (cnt-table--<tipo>): la ficha de un médico
  // dibuja en la misma sección las tablas de los días de su Calendario, que
  // tienen otras columnas y quedan para el enganche automático del motor.
  // Tomarlas acá les ponía los anchos del listado (pinamar-turnos 04.11).
  function fitCatalogTable(tipo, sec) {
    if (!Table) return;
    if (!CATALOG_TABLE_FIT[tipo]) {
      CATALOG_TABLE_FIT[tipo] = Table.crear({
        clave: "catalog-" + tipo,
        selector: "#sec-" + tipo + " .cnt-table--" + tipo,
        tablas: function () { return sec.querySelectorAll(".cnt-table--" + tipo); },
        medio: function () {
          var tb = sec.querySelector(".cnt-table--" + tipo);
          return tb ? tb.closest(".cnt-table-wrap") : null;
        },
        host: function () { return sec; },
        cols: function () { return catalogTableColumns(tipo, sec); }
      });
    }
    CATALOG_TABLE_FIT[tipo].programarFit();
  }

  // ONE column picker for every list (the product owner 28.07, 05.09: «configurable
  // columns in all the tables; the gear in one place»): Servicios, Centros
  // and Médicos use the shared `columnas()` of admin-table.mjs, like
  // Pacientes and Usuarios. What someone chose before under the catalog's own
  // key (`catalog.columns.<tipo>`) is read once and kept (`legado`).
  var CATALOG_PICKERS = {};

  function catalogPicker(tipo) {
    if (CATALOG_PICKERS[tipo]) return CATALOG_PICKERS[tipo];
    var cols = tipo === "medicos" ? MED_COLS_ALL : COLS[tipo];
    if (!Table) {
      // admin-table.mjs never loaded: the default columns, no picker.
      return {
        gearHtml: function () { return ""; },
        activas: function () { return cols.filter(function (c) { return c.always || c["default"]; }); }
      };
    }
    CATALOG_PICKERS[tipo] = Table.columnas({
      clave: "catalog-" + tipo,
      cols: cols,
      legado: "catalog.columns." + tipo,
      migrar: tipo === "medicos" ? medMigrarLegado : null,
      onChange: function () {
        var sec = $("#sec-" + tipo);
        var keys = CATALOG_PICKERS[tipo].visibles();
        if (sec) $$("[data-tblcol-grupo] input[data-tblcol]", sec).forEach(function (box) {
          box.checked = keys.indexOf(box.getAttribute("data-tblcol")) !== -1;
        });
        renderListaRows(tipo);
      }
    });
    return CATALOG_PICKERS[tipo];
  }

  // Una columna puede traer su propia celda (`col.cell`, usado por el
  // registro de médicos abajo) o, si no, cae al switch genérico de arriba
  // (servicios/centros) — un solo punto de entrada para ambos estilos.
  function renderCell(tipo, col, item) {
    return col.cell ? col.cell(item) : cellHtml(tipo, col, item);
  }

  function cellText(tipo, col, item) {
    return renderCell(tipo, col, item).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  }

  // A cell that shows a picture and no words (a portrait,
  // the call/WhatsApp icons of a center's phone) is not empty.
  function cellEmpty(tipo, col, item) {
    var html = renderCell(tipo, col, item);
    return !/<img|role="img"|aria-label="[^"]/.test(html) && html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() === "";
  }

  function matchesSearch(tipo, item, q, cols) {
    if (!q) return true;
    q = q.toLowerCase();
    var hay = (cols || COLS[tipo]).map(function (c) { return renderCell(tipo, c, item).replace(/<[^>]+>/g, " "); }).join(" ").toLowerCase();
    return hay.indexOf(q) !== -1;
  }

  // =====================================================================
  // médicos: registro de columnas + stats de gestión (GET /api/admin/doctors/stats)
  // =====================================================================
  // El listado de médicos del admin NO es el del sitio público — un
  // operador necesita gestión (horas, ocupación, próximo día, foto, login),
  // no lo que ve un paciente (responsable del producto, 2026-07-16). Un registro más rico que
  // COLS de arriba: cada columna sabe su celda Y si es numérica (para el
  // sort con nulls al final) Y si es visible por defecto (el resto vive
  // detrás del engranaje de columnas).

  function medCentroPrincipal(it) {
    return (it.centros && it.centros[0] && it.centros[0].centro) || it.centro || "";
  }

  var MED_STATS = null;       // {mid: {...}} de /api/admin/doctors/stats
  var MED_STATS_ERROR = false;

  function fetchMedicoStats(force) {
    if (!force && MED_STATS) return Promise.resolve(MED_STATS);
    return fetch("/api/admin/doctors/stats", { credentials: "same-origin" }).then(function (res) {
      return afterSessionGate(res, function () {
        if (res.status === 401) throw new Error("401");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json();
      });
    }).then(function (j) {
      MED_STATS = j.medicos || {};
      MED_STATS_ERROR = false;
      return MED_STATS;
    }).catch(function (e) {
      if (String(e.message) === "redirect") throw e;
      // Degradación con gracia: el listado base se muestra igual, las
      // columnas de métricas quedan vacías + un aviso arriba de la tabla.
      MED_STATS = MED_STATS || {};
      MED_STATS_ERROR = true;
      return MED_STATS;
    });
  }

  function medStat(it, key) {
    var id = idOf("medicos", it);
    var st = id && MED_STATS ? MED_STATS[id] : null;
    return st ? st[key] : undefined;
  }

  function medNombre(it) {
    return (it && it.nombre) || T("admin.content.catalog.doctors.no_name", null, "Médico sin nombre");
  }

  // The doctor's account, from the admin-only users list (one read, mapped
  // by medico_id). Degrades to an empty column — never blocks the list.
  var MED_ACCOUNTS = null;   // {medico_id: account}
  // True only after the users list was really read: then a doctor missing
  // from the map has no account («Sin cuenta»); without the read (no
  // permission, a failed fetch) the cell stays unknown, i.e. blank.
  var MED_ACCOUNTS_READ = false;
  function fetchMedicoAccounts(force) {
    if (!puede("usuarios")) return Promise.resolve(MED_ACCOUNTS = {});
    if (!force && MED_ACCOUNTS) return Promise.resolve(MED_ACCOUNTS);
    return fetch("/api/admin/users", { credentials: "same-origin" }).then(function (res) {
      return afterSessionGate(res, function () {
        if (res.status === 401) throw new Error("401");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json();
      });
    }).then(function (j) {
      var map = {};
      ((j && j.items) || []).forEach(function (account) {
        if (account && (account.rol === "medico" || account.rol === "admin") && account.medico_id) map[String(account.medico_id)] = account;
      });
      MED_ACCOUNTS = map;
      MED_ACCOUNTS_READ = true;
      return map;
    }).catch(function (e) {
      if (String(e.message) === "redirect" || isCancelled(e)) throw e;
      MED_ACCOUNTS = MED_ACCOUNTS || {};
      return MED_ACCOUNTS;
    });
  }

  function medAccount(it) {
    var id = idOf("medicos", it);
    return id && MED_ACCOUNTS ? MED_ACCOUNTS[String(id)] || null : null;
  }

  // Hours and percentages through the panel's one number formatter
  // (i18n.mjs formatNumber/formatPercent): «1.466 h», «44,5 h», «52%».
  function fmtHoras(v) {
    var n = Number(v);
    return (I18n && I18n.formatNumber ? I18n.formatNumber(n, 1) : String(Math.round(n * 10) / 10)) + " h";
  }
  function fmtCount(v) {
    return I18n && I18n.formatNumber ? I18n.formatNumber(v, 0) : String(v);
  }

  function medMutedOr(v, html) {
    return v == null ? '<span class="cnt-muted">—</span>' : html;
  }

  // Occupancy of the next 14 days is the number itself, «52%», right-aligned
  // like the hours beside it (ui-expert 2026-11-15: the five-square bar read
  // as a rating and pushed the phone's stacked line 58px in). A doctor with
  // nothing booked, or with no usable capacity, gets a blank cell: the
  // number marks only who has turnos, and when nobody has any the table
  // engine drops the all-empty column (ui-expert 15.11).
  function occupancyCell(v) {
    if (v == null || !(Number(v) > 0)) return "";
    var pct = I18n && I18n.formatPercent ? I18n.formatPercent(v) : Math.round(Number(v)) + "%";
    return '<span title="' + esc(T("admin.content.catalog.doctors.col_occupancy_14d", null, "Ocupación 14 días")) + '">' + esc(pct) + "</span>";
  }

  // Orden = el orden canónico del registro; el gear elige el SUBSET visible,
  // nunca reordena (table-design: un solo registro de columnas por listado).
  var MED_COLS_ALL = [
    { key: "nombre", label: T("admin.content.catalog.doctors.col_doctor", null, "Médico"), always: true,
      sort: function (it) { return (it.nombre || "").toLowerCase(); },
      cell: function (it) {
        // A doctor invited empty has no name yet (panel only) and stays off
        // the public site until name, specialty and a center are all there.
        return '<a href="' + esc(itemHref("medicos", idOf("medicos", it))) + '">' + esc(medNombre(it)) + "</a>"
          + (it.incompleto ? ' <span class="med-incompleto">· ' + esc(T("admin.content.catalog.doctors.not_on_site", null, "Todavía no está en el sitio")) + "</span>" : "");
      } },
    // Especialidad clickeable = setea el FILTRO de especialidad (responsable del producto,
    // 2026-07-20: "make specialty clickable. In this case, it sets the
    // filter") — mismo gesto que centro→su página, pero acá el destino útil
    // es la misma tabla filtrada.
    { key: "especialidad", label: T("admin.content.catalog.doctors.col_specialty", null, "Especialidad"), "default": true,
      sort: function (it) { return (it.especialidad || "").toLowerCase(); },
      cell: function (it) {
        var e = it.especialidad || "";
        return e ? '<button type="button" class="cnt-inline-action" data-fesp-med="' + esc(e) + '" title="' + esc(T("admin.content.catalog.doctors.filter_by", { value: e }, "Filtrar por {value}")) + '">' + esc(e) + "</button>" : '<span class="cnt-muted">—</span>';
      } },
    { key: "centro", label: T("admin.content.catalog.columns.center", null, "Centro"), "default": true,
      sort: function (it) { return centroNombre(medCentroPrincipal(it)).toLowerCase(); },
      cell: function (it) {
        return centroCellHtml(medCentroPrincipal(it));
      } },
    { key: "horas_semana", label: T("admin.content.catalog.doctors.col_hours_week", null, "Horas por semana"), "default": true, numeric: true,
      // 0 h es un dato REAL (el médico existe en stats pero no tiene agenda
      // cargada) — se muestra igual que las otras columnas numéricas
      // (turnos/servicios): "—" atenuado sólo cuando el dato falta de
      // verdad (stats caído o médico ausente del mapa), nunca por ser cero.
      sort: function (it) { var v = medStat(it, "horas_semana"); return v == null ? null : v; },
      cell: function (it) {
        var v = medStat(it, "horas_semana");
        return medMutedOr(v, '<span title="' + esc(T("admin.content.catalog.doctors.col_hours_week", null, "Horas por semana")) + '">' + esc(fmtHoras(v)) + "</span>");
      } },
    { key: "proximo_dia", label: T("admin.content.catalog.doctors.col_next_day", null, "Próximo día"), "default": true,
      sort: function (it) { return medStat(it, "proximo_dia_iso") || null; },
      cell: function (it) {
        var v = medStat(it, "proximo_dia");
        return medMutedOr(v, '<span title="' + esc(T("admin.content.catalog.doctors.col_next_day", null, "Próximo día")) + '">' + esc(v) + "</span>");
      } },
    { key: "ocupacion_14d", label: T("admin.content.catalog.doctors.col_occupancy_14d", null, "Ocupación 14 días"), "default": true, numeric: true,
      sort: function (it) { var v = medStat(it, "ocupacion_14d"); return v == null ? null : v; },
      cell: function (it) { return occupancyCell(medStat(it, "ocupacion_14d")); } },
    { key: "matricula", label: T("admin.content.catalog.doctors.col_license", null, "Matrícula"),
      sort: function (it) { return medStat(it, "matricula") || null; },
      cell: function (it) { var v = medStat(it, "matricula"); return medMutedOr(v, esc(v)); } },
    { key: "foto", label: T("admin.content.catalog.doctors.col_photo", null, "Foto"),
      sort: function (it) { return medStat(it, "tiene_foto") ? 1 : 0; },
      cell: function (it) { return medStat(it, "tiene_foto") ? "✓" : '<span class="cnt-muted">—</span>'; } },
    // Médico = usuario (the product owner, 2026-10-19): every doctor has its account, and
    // this list is where it is seen — Sin invitar, Invitado, Ingresó, Perfil
    // completo or Inactiva. One read of /api/admin/users, mapped by medico_id.
    { key: "cuenta", label: T("admin.content.catalog.doctors.col_account", null, "Cuenta"), "default": true,
      sort: function (it) {
        var a = medAccount(it);
        if (!a) return MED_ACCOUNTS_READ ? -1 : null;
        return Users && Users.accountStateRank ? Users.accountStateRank(a) : null;
      },
      cell: function (it) {
        var a = medAccount(it);
        // A doctor without an account says so, in the same dark text as the
        // other states — the report counts them as «Sin cuenta de acceso»
        // and an all-blank column was dropped by the table engine (ui-expert
        // 2026-11-15). Accounts not read (no permission, a failed fetch):
        // blank, never a guess.
        if (!a) return MED_ACCOUNTS_READ ? esc(T("admin.system.users.account_state.sin_cuenta", null, "Sin cuenta")) : "";
        var label = Users && Users.accountStateLabel ? Users.accountStateLabel(a) : "";
        return label ? esc(label) : "";
      } },
    { key: "servicios", label: T("admin.content.catalog.columns.services", null, "Servicios"), numeric: true,
      sort: function (it) { var v = medStat(it, "servicios"); return v == null ? null : v; },
      cell: function (it) { var v = medStat(it, "servicios"); return medMutedOr(v, esc(fmtCount(v))); } },
    { key: "agenda_resumen", label: T("admin.content.catalog.doctors.col_schedule_detail", null, "Horarios (detalle)"),
      sort: function (it) { return agendaResumen(it); },
      cell: function (it) {
        var full = agendaResumen(it);
        return '<span class="cnt-truncate" title="' + esc(full) + '">' + esc(full) + "</span>";
      } },
    { key: "created", label: T("admin.content.catalog.columns.created", null, "Creado"),
      sort: function (it) { return it._created || null; },
      cell: function (it) { return esc(fmtCatalogDate(it._created)); } },
    { key: "updated", label: T("admin.content.catalog.columns.updated", null, "Actualizado"),
      sort: function (it) { return it._updated || null; },
      cell: function (it) { return esc(fmtCatalogDate(it._updated)); } },
    // Estado (editado/nuevo/eliminado) fuera de los defaults (responsable del producto,
    // 2026-07-20: la etiqueta de estado no le sirve al día a día) — sigue
    // disponible detrás del engranaje para el admin que audita el catálogo.
    { key: "estado", label: T("admin.content.catalog.doctors.col_status", null, "Estado"),
      sort: function (it) { return it._borrado ? 2 : (it._origen === "db" ? 0 : (it._origen === "json+db" ? 1 : -1)); },
      cell: function (it) { return origenBadge(it) || '<span class="cnt-muted">—</span>'; } }
  ];
  function localizeDoctorColumns() {
    var labels = {
      nombre: ["admin.content.catalog.doctors.col_doctor", "Médico"], especialidad: ["admin.content.catalog.doctors.col_specialty", "Especialidad"],
      centro: ["admin.content.catalog.columns.center", "Centro"], horas_semana: ["admin.content.catalog.doctors.col_hours_week", "Horas por semana"],
      proximo_dia: ["admin.content.catalog.doctors.col_next_day", "Próximo día"], ocupacion_14d: ["admin.content.catalog.doctors.col_occupancy_14d", "Ocupación 14 días"],
      matricula: ["admin.content.catalog.doctors.col_license", "Matrícula"], foto: ["admin.content.catalog.doctors.col_photo", "Foto"],
      cuenta: ["admin.content.catalog.doctors.col_account", "Cuenta"], servicios: ["admin.content.catalog.columns.services", "Servicios"],
      agenda_resumen: ["admin.content.catalog.doctors.col_schedule_detail", "Horarios (detalle)"], created: ["admin.content.catalog.columns.created", "Creado"],
      updated: ["admin.content.catalog.columns.updated", "Actualizado"], estado: ["admin.content.catalog.doctors.col_status", "Estado"]
    };
    MED_COLS_ALL.forEach(function (column) { var item = labels[column.key]; if (item) column.label = T(item[0], null, item[1]); });
  }

  // A doctors' column choice saved under the catalog's old key moves to the
  // shared picker once (catalogPicker). The account column replaced «Login»
  // and is shown once to everyone who had saved a choice before it existed;
  // hiding it again sticks.
  function medMigrarLegado(arr) {
    arr = arr.map(function (key) { return key === "login" ? "cuenta" : key; })
      .filter(function (key, i, all) { return all.indexOf(key) === i; });
    if (!prefsGet("catalog.columns.medicos.cuenta", false)) {
      if (arr.indexOf("cuenta") === -1) arr.push("cuenta");
      prefsSet("catalog.columns.medicos.cuenta", true);
    }
    return arr;
  }

  // ---- filtros de médicos: Centro / Especialidad / Agenda (selects, NUNCA
  // pills — el valor por defecto ES la etiqueta del campo, no "Todos"). ----

  var MED_FILTERS = { centro: "", especialidad: "", agenda: "" };
  var MED_SELECTED = {};
  var MED_LAST_SELECTED = null;
  var MED_SELECTION_VIEW = "";

  function medSelectedIds() {
    return Object.keys(MED_SELECTED).filter(function (id) { return MED_SELECTED[id]; });
  }

  function medBulkHtml() {
    var n = medSelectedIds().length;
    return '<div class="med-bulk" id="med-bulk" data-admin-repeated-mutation="true" data-admin-selection-visible="true" data-admin-group-action="true" data-admin-shift-range="true"' + (n ? "" : ' hidden') + '>'
      + '<span id="med-bulk-count">' + esc(T("admin.content.catalog.doctors.bulk_selected_count", { n: n }, "{n} seleccionados")) + "</span>"
      + '<div class="med-bulk-actions">'
      + '<label class="sr-only" for="med-bulk-action">' + esc(T("admin.content.catalog.doctors.bulk_action_label", null, "Acción para médicos seleccionados")) + '</label>'
      + '<select class="adm-sel" id="med-bulk-action" data-med-bulk-select>'
      + '<option value="">' + esc(T("admin.content.catalog.doctors.bulk_action_placeholder", null, "Acciones")) + '</option>'
      + '<option value="invite">' + esc(T("admin.content.catalog.doctors.bulk_invite", null, "Invitar")) + '</option>'
      + '<option value="enable-intake">' + esc(T("admin.content.catalog.doctors.bulk_enable_intake", null, "Activar captura de síntomas")) + '</option>'
      + '<option value="disable-intake">' + esc(T("admin.content.catalog.doctors.bulk_disable_intake", null, "Desactivar captura de síntomas")) + '</option>'
      + '<option value="disable">' + esc(T("admin.content.catalog.doctors.bulk_disable", null, "Desactivar")) + '</option>'
      + '<option value="reset-password">' + esc(T("admin.content.catalog.doctors.bulk_reset_password", null, "Restablecer contraseña")) + '</option>'
      + '<option value="reset-2fa">' + esc(T("admin.content.catalog.doctors.bulk_reset_2fa", null, "Restablecer 2FA")) + '</option>'
      + '</select>'
      + '<button type="button" class="btn btn-quiet btn-sm" data-med-bulk-apply>' + esc(T("admin.content.catalog.doctors.bulk_apply", null, "Aplicar")) + '</button>'
      + "</div></div>";
  }

  function updateMedBulk(sec) {
    var box = $("#med-bulk", sec), count = $("#med-bulk-count", sec);
    var n = medSelectedIds().length;
    if (box) box.hidden = !n;
    if (count) count.textContent = n === 1
      ? T("admin.content.catalog.doctors.bulk_selected_count_one", { n: n }, "{n} seleccionado")
      : T("admin.content.catalog.doctors.bulk_selected_count", { n: n }, "{n} seleccionados");
  }

  function runMedBulk(sec, action, button) {
    var version = mountVersion;
    var ids = medSelectedIds();
    if (!ids.length) return;
    button.disabled = true;
    var request;
    if (action === "enable-intake" || action === "disable-intake") {
      request = medIntakePost({ medico_ids: ids, enabled: action === "enable-intake" });
    } else {
      request = fetch("/api/admin/users/actions/" + action, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ medico_ids: ids })
      }).then(function (res) {
        return res.json().catch(decodeFailure).then(function (data) {
          if (!res.ok || !data.ok) throw new Error(data.error || T("admin.content.catalog.common.action_failed", null, "No se pudo completar la acción."));
          return data;
        });
      });
    }
    return request.then(function (data) {
      if (!current(version)) throw cancelled();
      if (!data.ok) throw new Error(T("admin.content.catalog.common.action_failed", null, "No se pudo completar la acción."));
      var completed = Array.isArray(data.medico_ids) ? data.medico_ids.length : data.completados;
      var msg = Tn("admin.content.catalog.doctors.bulk_completed", completed, null, "{n} completado", "{n} completados");
      if (data.errores) msg += T("admin.content.catalog.doctors.bulk_pending", { n: data.errores }, ", {n} con datos pendientes");
      toast(msg + ".");
      MED_SELECTED = {};
      MED_STATS = null;
      return refreshMedicosList();
    }).catch(function (err) {
      if (!current(version) || isCancelled(err)) return;
      toast(errText(err, T("admin.content.catalog.common.action_failed", null, "No se pudo completar la acción.")));
    }).then(function () { if (current(version)) { button.disabled = false; updateMedBulk(sec); } });
  }

  // ---- «Invitar médico» (responsable del producto, 2026-10-19: "all fields should be
  // non-obligatory, including the email ... when we put their email, make
  // sure you send an invitation"). The same create as «Nuevo médico» plus
  // `_invitar`: the doctor and its account are born together; the onboarding
  // asks for whatever was left empty. ----
  function medInviteHtml() {
    return '<section class="med-invite doctor-user-host" id="med-invite" hidden>'
      + '<form id="med-invite-form" class="med-invite-form" novalidate>'
      + row(esc(T("admin.content.catalog.common.field_name", null, "Nombre")), '<input class="cnt-in cnt-field-name" id="med-inv-nombre" name="nombre_pila" autocomplete="off">')
      + row(esc(T("admin.content.catalog.common.field_lastname", null, "Apellido")), '<input class="cnt-in cnt-field-name" id="med-inv-apellido" name="apellido" autocomplete="off">')
      + row(esc(T("admin.content.catalog.doctors.col_specialty", null, "Especialidad")), '<select class="adm-sel cnt-field-specialty" id="med-inv-especialidad" name="especialidad">' + especialidadOptions("") + "</select>")
      + row(esc(T("admin.content.catalog.common.field_email", null, "Email")), '<input class="cnt-in cnt-field-email" id="med-inv-email" name="email" type="email" autocomplete="off">'
        + '<p class="doctor-user-link-error" id="med-inv-error" role="alert" hidden></p>')
      + '<div class="doctor-user-link-actions med-invite-actions"><button type="submit" class="btn btn-ok">' + esc(T("admin.content.catalog.doctors.invite_submit", null, "Invitar")) + "</button>"
      + '<button type="button" class="btn btn-quiet" id="med-invite-cancel">' + esc(T("admin.content.catalog.common.cancel", null, "Cancelar")) + "</button></div>"
      + "</form>"
      + '<div id="med-invite-result" class="med-invite-result"></div>'
      + "</section>";
  }

  // ---- «Cargar lista» (Lira 13.10.2026, the hospital's technical contact: "creación masiva de
  // usuarios"). The one path for a roster since 2026-11-03 — /admin/users/import redirects
  // here. The server reads the list (pasted, CSV/TSV/TXT or Excel), shows what it would do
  // without writing, and on «Crear» makes each row with the same create as «Invitar médico»:
  // email → invitation sent, no email → a link to hand over. A person the hospital already
  // has is left alone, so loading the same file twice creates nobody twice. ----
  var IMPORT_MAX_BYTES = 2 * 1024 * 1024;
  var IMPORT_TIMEOUT_MS = 120000;
  var IMPORT_ACCEPT = ".csv,.tsv,.txt,.xlsx,text/csv,text/plain,"
    + "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

  // Its own toolbar button and panel (Opus review 2026-11-04: the list hid inside
  // «Invitar médico» under the one-doctor form, and the browser's own «Choose File /
  // No file chosen» showed in English). The file chooser is the control the portrait
  // uses: the native input stays hidden behind a labelled button, the chosen name beside it.
  function medImportHtml() {
    return '<section class="med-invite med-import" id="med-import" hidden>'
      + row(esc(T("admin.content.catalog.doctors.import_label", null, "Lista")),
        '<textarea class="cnt-in" id="med-import-text" rows="6" spellcheck="false" autocomplete="off" placeholder="'
        + esc(T("admin.content.catalog.doctors.import_placeholder", null, "Nombre;Apellido;Email;Especialidad;Centro")) + '"></textarea>'
        + '<div class="med-import-file-row">'
        + '<input type="file" id="med-import-file" class="cnt-foto-file" accept="' + IMPORT_ACCEPT + '" aria-label="'
        + esc(T("admin.content.catalog.doctors.import_file", null, "Elegir un archivo CSV o Excel")) + '">'
        + '<label class="btn btn-quiet btn-sm" for="med-import-file">'
        + esc(T("admin.content.catalog.doctors.import_pick", null, "Elegir archivo")) + "</label>"
        + '<span class="med-import-file-name" id="med-import-file-name"></span></div>', null, "med-import-text")
      + '<div class="doctor-user-link-actions med-invite-actions">'
      + '<button type="button" class="btn btn-ok" id="med-import-run">'
      + esc(T("admin.content.catalog.doctors.import_run", null, "Revisar lista")) + "</button>"
      + '<button type="button" class="btn btn-ok" id="med-import-create" hidden></button>'
      + '<button type="button" class="btn btn-quiet" id="med-import-cancel">'
      + esc(T("admin.content.catalog.common.cancel", null, "Cancelar")) + "</button></div>"
      + '<div id="med-import-result" class="med-import-result" role="status"></div>'
      + "</section>";
  }

  // POST the list to the server; `dryRun` previews. Its own deadline: creating 500
  // doctors and sending their invitations outlives the 15 s of a catalog write.
  function importRequest(source, dryRun) {
    var version = mountVersion;
    var controller = new AbortController();
    var done = false;
    var timedOut = false;
    var timer = window.setTimeout(function () { timedOut = true; controller.abort(); }, IMPORT_TIMEOUT_MS);
    var release = ownRouteCleanup(function () { if (!done) controller.abort(); });
    function finish() { done = true; window.clearTimeout(timer); release(); }
    var url = "/api/admin/doctors/import?filename=" + encodeURIComponent(source.name)
      + "&dry_run=" + (dryRun ? "true" : "false");
    return nativeFetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" }, body: source.blob, signal: controller.signal
    }).then(function (res) {
      if (!current(version)) throw cancelled();
      return afterSessionGate(res, function () {
        return res.json().catch(decodeFailure).then(function (j) {
          if (!current(version)) throw cancelled();
          if (!res.ok || !j || !Array.isArray(j.rows)) {
            throw new Error((j && j.error) || T("admin.content.catalog.doctors.import_unreadable", null, "No se pudo leer la lista."));
          }
          return j;
        });
      });
    }, function (error) {
      if (!current(version)) throw cancelled();
      if (timedOut) throw new Error(T("admin.content.catalog.doctors.import_timeout", null, "Tardó demasiado. Revisá la lista de nuevo para ver cómo quedó."));
      if (error && error.name === "AbortError") throw cancelled();
      throw error;
    }).then(function (j) { finish(); return j; }, function (error) { finish(); throw error; });
  }

  function importSummary(j) {
    var parts = [];
    if (j.dry_run) {
      if (j.validas) parts.push(Tn("admin.content.catalog.doctors.import_to_create", j.validas, null, "{n} médico para crear", "{n} médicos para crear"));
      if (j.existentes) parts.push(Tn("admin.content.catalog.doctors.import_existing", j.existentes, null, "{n} ya está entre los médicos", "{n} ya están entre los médicos"));
    } else {
      if (j.creadas) parts.push(Tn("admin.content.catalog.doctors.import_created", j.creadas, null, "{n} médico creado", "{n} médicos creados"));
      if (j.existentes) parts.push(Tn("admin.content.catalog.doctors.import_were", j.existentes, null, "{n} ya estaba", "{n} ya estaban"));
    }
    if (j.errores) parts.push(T("admin.content.catalog.doctors.import_failed", { n: j.errores }, "{n} con error"));
    return parts.length ? parts.join(", ") + "." : "";
  }

  // One row per line of the list; a column empty in every row is not drawn, the line
  // number only when a row has an error (to find it in the file).
  function importTable(rows) {
    var cols = [
      ["fila", T("admin.content.catalog.doctors.import_col_row", null, "Fila")],
      ["nombre", T("admin.content.catalog.common.field_name", null, "Nombre")],
      ["especialidad", T("admin.content.catalog.doctors.col_specialty", null, "Especialidad")],
      ["centro", T("admin.content.catalog.columns.center", null, "Centro")],
      ["email", T("admin.content.catalog.common.field_email", null, "Email")]
    ].filter(function (c) {
      if (c[0] === "fila") return rows.some(function (r) { return r.estado === "error"; });
      return rows.some(function (r) { return r[c[0]]; });
    });
    var result = T("admin.content.catalog.doctors.import_col_result", null, "Resultado");
    var head = cols.map(function (c) { return '<th scope="col">' + esc(c[1]) + "</th>"; }).join("")
      + '<th scope="col">' + esc(result) + "</th>";
    var body = rows.map(function (r) {
      var cells = cols.map(function (c) {
        return '<td data-label="' + esc(c[1]) + '">' + esc(r[c[0]] == null ? "" : r[c[0]]) + "</td>";
      }).join("");
      var outcome = esc(r.detalle || "");
      if (r.enlace) {
        outcome += '<input class="cnt-in doctor-user-link-input med-import-link" type="text" readonly autocomplete="off" aria-label="'
          + esc(T("admin.content.catalog.doctors.import_link", null, "Enlace de acceso")) + '" value="' + esc(r.enlace) + '">';
      }
      return "<tr" + (r.estado === "error" ? ' data-state="error"' : "") + ">" + cells
        + '<td data-label="' + esc(result) + '">' + outcome + "</td></tr>";
    }).join("");
    return '<table class="med-import-table"><thead><tr>' + head + "</tr></thead><tbody>" + body + "</tbody></table>";
  }

  function wireMedImport(sec) {
    // The opener lives on the title's line (outside sec), the panel in sec.
    var open = $(".adm-h1row #med-import-open") || $("#med-import-open", sec), panel = $("#med-import", sec);
    if (!open || !panel) return;
    var text = $("#med-import-text", panel), file = $("#med-import-file", panel), fileName = $("#med-import-file-name", panel);
    var run = $("#med-import-run", panel), create = $("#med-import-create", panel), result = $("#med-import-result", panel);
    if (!text || !run || !create) return;
    var excel = null;      // a chosen .xlsx: it cannot be shown in the text box
    var previewed = null;  // the source the preview on screen was made from
    function reset() {
      previewed = null;
      create.hidden = true;
      run.hidden = false;
      result.innerHTML = "";
    }
    function show(visible) {
      panel.hidden = !visible;
      open.setAttribute("aria-expanded", visible ? "true" : "false");
      if (visible) text.focus();
    }
    // /admin/users/import (the old roster page) lands here with ?import=1.
    if (openImportOnMount) {
      openImportOnMount = false;
      show(true);
    }
    listen(open, "click", function () { show(panel.hidden); });
    listen($("#med-import-cancel", panel), "click", function () {
      excel = null;
      file.value = "";
      text.value = "";
      fileName.textContent = "";
      reset();
      show(false);
    });
    function source() {
      if (excel) return { name: excel.name, blob: excel };
      var value = text.value.trim();
      return value ? { name: "lista.txt", blob: new Blob([value], { type: "text/plain" }) } : null;
    }
    function say(message, isError) {
      result.innerHTML = '<p class="' + (isError ? "doctor-user-link-error" : "med-import-summary") + '"'
        + (isError ? ' role="alert"' : "") + ">" + esc(message) + "</p>";
    }
    listen(text, "input", function () {
      if (excel) { excel = null; file.value = ""; }
      fileName.textContent = "";
      reset();
    });
    listen(file, "change", function () {
      var chosen = file.files && file.files[0];
      reset();
      excel = null;
      fileName.textContent = "";
      if (!chosen) return;
      if (chosen.size > IMPORT_MAX_BYTES) {
        file.value = "";
        say(T("admin.content.catalog.doctors.import_too_big", null, "El archivo supera 2 MB."), true);
        return;
      }
      fileName.textContent = chosen.name;
      if (/\.xlsx$/i.test(chosen.name)) {
        excel = chosen;
        text.value = "";
        return;
      }
      chosen.text().then(function (content) { text.value = content; file.value = ""; });
    });
    function send(dryRun) {
      var src = dryRun ? source() : previewed;
      if (!src) { reset(); return; }
      if (src.blob.size > IMPORT_MAX_BYTES) {
        say(T("admin.content.catalog.doctors.import_too_big", null, "El archivo supera 2 MB."), true);
        return;
      }
      var button = dryRun ? run : create;
      if (button.disabled) return;
      run.disabled = create.disabled = true;
      say(dryRun ? T("admin.content.catalog.doctors.import_reading", null, "Leyendo la lista…")
        : T("admin.content.catalog.doctors.import_creating", null, "Creando médicos…"));
      importRequest(src, dryRun).then(function (j) {
        run.disabled = create.disabled = false;
        result.innerHTML = '<p class="med-import-summary">' + esc(importSummary(j)) + "</p>" + importTable(j.rows);
        if (dryRun && j.validas) {
          previewed = src;
          create.textContent = Tn("admin.content.catalog.doctors.import_create", j.validas, null, "Crear {n} médico", "Crear {n} médicos");
          create.hidden = false;
          run.hidden = true;
        } else {
          previewed = null;
          create.hidden = true;
          run.hidden = false;
        }
        if (!dryRun && j.creadas) return refreshMedicosList();
      }, function (err) {
        if (isCancelled(err) || String(err && err.message) === "redirect") return;
        run.disabled = create.disabled = false;
        say(saveMessage(err), true);
      });
    }
    listen(run, "click", function () { send(true); });
    listen(create, "click", function () { send(false); });
    listen(result, "focusin", function (e) {
      if (e.target && e.target.classList && e.target.classList.contains("med-import-link")) e.target.select();
    });
  }

  function refreshMedicosList() {
    var version = mountVersion;
    // A save drops every catalog cache (invalidateMutationCaches): without
    // reloading centers and services, the Centro column fell back to the raw
    // slug and the agenda summary lost its consultorio names.
    return Promise.all([fetchTipo("medicos", true), fetchMedicoStats(true), fetchMedicoAccounts(true),
      ensureCentros(), ensureServicios()]).then(function () {
      if (current(version)) renderListaRows("medicos");
    });
  }

  function wireMedInvite(sec) {
    // The opener lives on the title's line (outside sec), the panel in sec.
    var open = $(".adm-h1row #med-invite-open") || $("#med-invite-open", sec), panel = $("#med-invite", sec), form = $("#med-invite-form", sec);
    if (!open || !panel || !form) return;
    var result = $("#med-invite-result", panel), error = $("#med-inv-error", panel);
    var disposeOutcome = null;
    function clearOutcome() {
      if (disposeOutcome) disposeOutcome();
      disposeOutcome = null;
      result.innerHTML = "";
    }
    ownRouteCleanup(clearOutcome);
    function show(visible) {
      panel.hidden = !visible;
      open.setAttribute("aria-expanded", visible ? "true" : "false");
      if (visible) $("#med-inv-nombre", panel).focus();
    }
    listen(open, "click", function () { show(panel.hidden); });
    listen($("#med-invite-cancel", panel), "click", function () { clearOutcome(); show(false); });
    listen(form, "submit", function (e) {
      e.preventDefault();
      var submit = $('button[type="submit"]', form);
      if (submit.disabled) return;
      var emailInput = $("#med-inv-email", form);
      var doc = { _invitar: true };
      [["nombre_pila", "#med-inv-nombre"], ["apellido", "#med-inv-apellido"], ["especialidad", "#med-inv-especialidad"], ["email", "#med-inv-email"]].forEach(function (pair) {
        var value = String($(pair[1], form).value || "").trim();
        if (value) doc[pair[0]] = value;
      });
      error.hidden = true;
      emailInput.removeAttribute("aria-invalid");
      if (doc.email && emailInput.checkValidity && !emailInput.checkValidity()) {
        emailInput.setAttribute("aria-invalid", "true");
        error.textContent = T("admin.content.catalog.doctors.invite_email_invalid", null, "Ingresá un email válido o dejalo vacío.");
        error.hidden = false;
        emailInput.focus();
        return;
      }
      var version = mountVersion;
      var cuenta = null;
      submit.disabled = true;
      clearOutcome();
      guardar("medicos", null, doc, function (ack) {
        cuenta = ack && ack.body && ack.body.cuenta || null;
        return null;
      }).then(function () {
        if (!current(version)) return;
        form.reset();
        submit.disabled = false;
        if (cuenta && Users && Users.showInviteOutcome) {
          disposeOutcome = Users.showInviteOutcome(result, cuenta, { isCurrent: function () { return current(version) && result.isConnected; } });
        } else {
          result.innerHTML = '<p class="doctor-user-outcome" role="status">' + esc(T("admin.content.catalog.common.saved", null, "Guardado.")) + "</p>";
        }
        return refreshMedicosList();
      }).catch(function (err) {
        if (!current(version) || isCancelled(err)) return;
        submit.disabled = false;
        error.textContent = saveMessage(err);
        error.hidden = false;
      });
    });
  }

  function wireMedBulk(sec) {
    var select = $("[data-med-bulk-select]", sec);
    var button = $("[data-med-bulk-apply]", sec);
    if (!select || !button) return;
    listen(button, "click", function () {
      var action = select.value;
      if (!action) { toast(T("admin.content.catalog.doctors.bulk_choose_action", null, "Elegí una acción.")); select.focus(); return; }
      runMedBulk(sec, action, button).then(function () { select.value = ""; });
    });
  }

  function medFiltroCentroOptions(items, selected) {
    var usados = {};
    items.forEach(function (it) { var s = medCentroPrincipal(it); if (s) usados[s] = 1; });
    var html = '<option value="">' + esc(T("admin.content.catalog.columns.center", null, "Centro")) + '</option>';
    centrosVivos().filter(function (c) { return usados[c.slug]; }).forEach(function (c) {
      html += '<option value="' + esc(c.slug) + '"' + (c.slug === selected ? " selected" : "") + ">" + esc(c.name) + "</option>";
    });
    return html;
  }

  function medFiltroEspecialidadOptions(items, selected) {
    var html = '<option value="">' + esc(T("admin.content.catalog.doctors.col_specialty", null, "Especialidad")) + '</option>';
    distinct(items, function (it) { return it.especialidad; }).forEach(function (e) {
      html += '<option value="' + esc(e) + '"' + (e === selected ? " selected" : "") + ">" + esc(e) + "</option>";
    });
    return html;
  }

  // The shared rule (Filters.offerFilter): a filter only on a list worth
  // filtering (Filters.FILTER_MIN_ROWS rows) and with two values to choose
  // between (one centre → no centre filter); a filter already set stays so
  // it can be cleared (progressive disclosure, product owner 2026-10-26).
  function filterOffered(count, current, rows) { return Filters.offerFilter(count, !!current, rows); }

  function medTieneHorarios(it) {
    var h = medStat(it, "horas_semana");
    return h != null && h > 0;
  }

  function medFiltersHtml(items) {
    var usados = {};
    items.forEach(function (it) { var s = medCentroPrincipal(it); if (s) usados[s] = 1; });
    var centros = centrosVivos().filter(function (c) { return usados[c.slug]; }).length;
    var especialidades = distinct(items, function (it) { return it.especialidad; }).length;
    // Agenda splits doctors with and without hours: a choice only when both exist.
    var agendas = (items.some(medTieneHorarios) ? 1 : 0) + (items.some(function (it) { return !medTieneHorarios(it); }) ? 1 : 0);
    var html = "";
    if (filterOffered(centros, MED_FILTERS.centro, items.length)) html += '<select class="adm-sel' + (MED_FILTERS.centro ? " on" : "") + '" id="med-f-centro">' + medFiltroCentroOptions(items, MED_FILTERS.centro) + "</select>";
    if (filterOffered(especialidades, MED_FILTERS.especialidad, items.length)) html += '<select class="adm-sel' + (MED_FILTERS.especialidad ? " on" : "") + '" id="med-f-especialidad">' + medFiltroEspecialidadOptions(items, MED_FILTERS.especialidad) + "</select>";
    if (filterOffered(agendas, MED_FILTERS.agenda, items.length)) html += '<select class="adm-sel' + (MED_FILTERS.agenda ? " on" : "") + '" id="med-f-agenda"><option value="">' + esc(T("admin.content.catalog.doctors.filter_schedule", null, "Agenda")) + '</option>' +
      '<option value="con"' + (MED_FILTERS.agenda === "con" ? " selected" : "") + '>' + esc(T("admin.content.catalog.doctors.filter_schedule_with", null, "Con horarios")) + "</option>" +
      '<option value="sin"' + (MED_FILTERS.agenda === "sin" ? " selected" : "") + '>' + esc(T("admin.content.catalog.doctors.filter_schedule_without", null, "Sin horarios")) + "</option></select>";
    return html;
  }

  function wireMedFiltros(sec) {
    ["centro", "especialidad", "agenda"].forEach(function (k) {
      var sel = $("#med-f-" + k, sec);
      if (!sel) return;
      listen(sel, "change", function () {
        MED_FILTERS[k] = sel.value;
        prefsSet("doctors.filters", MED_FILTERS);
        sel.classList.toggle("on", !!sel.value);
        renderListaRows("medicos");
      });
    });
  }

  function matchesFiltrosMed(it) {
    if (MED_FILTERS.centro && medCentroPrincipal(it) !== MED_FILTERS.centro) return false;
    if (MED_FILTERS.especialidad && (it.especialidad || "") !== MED_FILTERS.especialidad) return false;
    if (MED_FILTERS.agenda) {
      var h = medStat(it, "horas_semana");
      var con = h != null && h > 0;
      if (MED_FILTERS.agenda === "con" && !con) return false;
      if (MED_FILTERS.agenda === "sin" && con) return false;
    }
    return true;
  }

  // Servicios: los dos cortes operativos de la tabla son dónde se brinda y
  // si requiere turno/orden. Se mantienen como selects compactos, junto al
  // buscador, para no convertir cuatro valores cerrados en una hilera de pills.
  var SERVICE_FILTERS = { centro: "", modo: "" };

  function serviceFiltersHtml(items) {
    var centros = {}, modos = {};
    (items || []).forEach(function (it) {
      if (it.centro) centros[it.centro] = 1;
      if (it.modo) modos[it.modo] = 1;
    });
    var centroOpts = '<option value="">' + esc(T("admin.content.catalog.columns.center", null, "Centro")) + '</option>' + Object.keys(centros).sort(function (a, b) {
      return centroNombre(a).localeCompare(centroNombre(b), "es");
    }).map(function (slug) {
      return '<option value="' + esc(slug) + '"' + (SERVICE_FILTERS.centro === slug ? " selected" : "") + '>' + esc(centroNombre(slug)) + "</option>";
    }).join("");
    var modoOpts = '<option value="">' + esc(T("admin.content.catalog.columns.mode", null, "Modo")) + '</option>' + Object.keys(modos).sort(function (a, b) {
      return modoLabel(a).localeCompare(modoLabel(b), "es");
    }).map(function (modo) {
      return '<option value="' + esc(modo) + '"' + (SERVICE_FILTERS.modo === modo ? " selected" : "") + '>' + esc(modoLabel(modo)) + "</option>";
    }).join("");
    var rows = (items || []).length;
    return (filterOffered(Object.keys(centros).length, SERVICE_FILTERS.centro, rows) ? '<select class="adm-sel' + (SERVICE_FILTERS.centro ? " on" : "") + '" id="svc-f-centro">' + centroOpts + "</select>" : "") +
      (filterOffered(Object.keys(modos).length, SERVICE_FILTERS.modo, rows) ? '<select class="adm-sel' + (SERVICE_FILTERS.modo ? " on" : "") + '" id="svc-f-modo">' + modoOpts + "</select>" : "");
  }

  function wireServiceFiltros(sec) {
    ["centro", "modo"].forEach(function (key) {
      var sel = $("#svc-f-" + key, sec);
      if (!sel) return;
      listen(sel, "change", function () {
        SERVICE_FILTERS[key] = sel.value;
        prefsSet("services.filters", SERVICE_FILTERS);
        sel.classList.toggle("on", !!sel.value);
        renderListaRows("servicios");
      });
    });
  }

  function matchesFiltrosServicios(it) {
    if (SERVICE_FILTERS.centro && (it.centro || "") !== SERVICE_FILTERS.centro) return false;
    if (SERVICE_FILTERS.modo && (it.modo || "") !== SERVICE_FILTERS.modo) return false;
    return true;
  }

  // Nulls SIEMPRE al final, sin importar la dirección del sort — mismo
  // criterio que pidió el responsable del producto para las columnas numéricas del listado de
  // médicos, generalizado acá a cualquier columna (una fecha/matrícula sin
  // dato tampoco debería "ganar" el primer lugar sólo por ordenar desc).
  function compararCol(col, a, b, dir) {
    var va = col.sort(a), vb = col.sort(b);
    var na = va === null || va === undefined, nb = vb === null || vb === undefined;
    if (na || nb) { if (na && nb) return 0; return na ? 1 : -1; }
    if (col.numeric) return (va - vb) * dir;
    return va < vb ? -1 * dir : va > vb ? 1 * dir : 0;
  }

  // renderLista está partida en dos (2026-07-11, arregla foco perdido al
  // tipear en el buscador): renderListaShell dibuja el toolbar + <table>
  // UNA vez (ahí vive el <input> de búsqueda); renderListaRows sólo
  // redibuja <thead>/<tbody> — cada letra tipeada llama a renderListaRows,
  // que nunca toca el <input>, así que nunca pierde el foco ni el cursor.
  function renderLista(tipo) {
    return renderListaShell(tipo);
  }

  // The AI translation notice (ai.notice.translation) is the caption of the
  // action that hands the text to the AI: one line right above «Guardar» of
  // an editable item form, never a band over the screen, a list or an empty
  // catalog (the product owner 21.10: no little footnotes; 2026-11-08 review B5). The
  // notice itself stays (Decreto 742/2026, prior notice): saving translates
  // the text, a new item's included. A doctor's own session writes no
  // translated catalog text, so it never gets one.
  function translationNoticeHtml() {
    if (window.CURRENT_ROLE === "medico") return "";
    return '<p class="adm-ai-notice cnt-ai-notice" data-ai-notice-inline="translation">' + esc(T("ai.notice.translation", null, "La inteligencia artificial propone traducciones. Revisalas antes de publicar.")) + "</p>";
  }

  function renderListaShell(tipo) {
    hydrateListPrefs();
    var sec = $("#sec-" + tipo);
    sec.setAttribute("data-list-view", "1");
    return fetchTipo(tipo).then(function (data) {
      var needCentros = tipo === "servicios" || tipo === "medicos";
      return Promise.all([
        needCentros ? ensureCentros() : Promise.resolve(),
        tipo === "medicos" ? fetchMedicoStats() : Promise.resolve(),
        tipo === "medicos" ? fetchMedicoAccounts() : Promise.resolve(),
        // «Invitar médico» elige la especialidad del mismo catálogo cerrado
        tipo === "medicos" && puedeAltaBaja("medicos") ? loadEspecialidadesYClusters() : Promise.resolve(),
        // el resumen de agenda nombra el consultorio de cada franja
        tipo === "medicos" ? ensureServicios() : Promise.resolve()
      ]).then(function () { return data; });
    }).then(function (data) {
      if (data.noBackend) {
        sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.catalog.list.no_backend", null, "El backend de contenido no está desplegado todavía.")) + '</p>';
        return;
      }
      var addLbl = { servicios: T("admin.content.catalog.list.add_service", null, "Nuevo servicio"), centros: T("admin.content.catalog.list.add_center", null, "Nuevo centro"), medicos: T("admin.content.catalog.list.add_doctor", null, "Nuevo médico") }[tipo];
      // A genuinely empty catalogue is a creation task, not an error state.
      // Filtered zero-result views remain in the list for recovery instead.
      if (!data.items.length && puedeAltaBaja(tipo)) {
        sec.setAttribute("data-admin-empty-editable", "true");
        return renderForm(tipo, null);
      }
      // Empty and nothing this role can create: no toolbar, gear or header —
      // one sentence saying what goes here and who loads it.
      if (!data.items.length) {
        Filters.applyDisclosure(sec, "empty");
        sec.innerHTML = Filters.emptyStateHtml({
          title: T("admin.content.catalog.list.empty_readonly", { tipo: TIPOS[tipo].plural }, "Todavía no hay {tipo}: aparecen acá cuando un administrador los carga.")
        });
        return;
      }
      Filters.applyDisclosure(sec, "data");
      // A remembered search stays visible on a short list, so it can be cleared.
      var searchable = data.items.length > 15 || !!SEARCH[tipo];
      var esMedicos = tipo === "medicos";
      var esServicios = tipo === "servicios";

      var html = '<div class="cnt-toolbar">';
      html += '<div class="cnt-toolbar-filters">';
      html += searchable
        ? ('<input class="adm-search cnt-search" type="search" value="' + esc(SEARCH[tipo]) + '" placeholder="' + esc(T("admin.content.catalog.list.search_placeholder", null, "Buscar")) + '">')
        : "";
      if (esMedicos) html += medFiltersHtml(data.items);
      if (esServicios) html += serviceFiltersHtml(data.items);
      html += "</div>";
      html += '<div class="cnt-toolbar-right">';
      if (esMedicos && puede("usuarios")) html += medBulkHtml();
      html += catalogPicker(tipo).gearHtml();
      if (Table && Table.exportHtml) html += Table.exportHtml({ scope: "#sec-" + tipo + " .cnt-table-wrap" });
      html += "</div></div>";
      var createAction = puedeAltaBaja(tipo) ? titleAction(itemHref(tipo, "nuevo"), addLbl) : null;
      // One screen header (the product owner, 2026-11-08): inviting a doctor and loading a
      // list are ways of adding doctors, so they sit with «Nuevo médico» on
      // the title's line as bare icons (name = tooltip + accessible name); the
      // list row keeps only the search, the filters and the table's own
      // icons, which then fit one line on a phone.
      if (esMedicos && createAction && createAction.insertAdjacentHTML) {
        var inviteLbl = esc(T("admin.content.catalog.doctors.invite_doctor", null, "Invitar médico"));
        var importLbl = esc(T("admin.content.catalog.doctors.import_open", null, "Cargar lista"));
        createAction.insertAdjacentHTML("beforebegin",
          '<button type="button" class="cnt-gear-btn adm-tool adm-title-action" id="med-invite-open" data-title-owner="catalog" aria-expanded="false" aria-controls="med-invite" aria-label="' + inviteLbl + '" title="' + inviteLbl + '">'
          + '<span class="adm-icon adm-icon-invite" aria-hidden="true"></span></button>'
          + '<button type="button" class="cnt-gear-btn adm-tool adm-title-action" id="med-import-open" data-title-owner="catalog" aria-expanded="false" aria-controls="med-import" aria-label="' + importLbl + '" title="' + importLbl + '">'
          + '<span class="adm-icon adm-icon-import" aria-hidden="true"></span></button>');
        ownRouteCleanup(function () {
          $$('.adm-h1row > .adm-tool[data-title-owner="catalog"]').forEach(function (b) {
            if (b.parentNode) b.parentNode.removeChild(b);
          });
        });
      }
      if (esMedicos && puedeAltaBaja(tipo)) html += medInviteHtml() + medImportHtml();
      if (esMedicos && MED_STATS_ERROR) {
        html += '<p class="cnt-hint" style="margin:-10px 0 16px;">' + esc(T("admin.content.catalog.doctors.stats_error", null, "No se pudieron cargar las métricas de médicos (horas, ocupación, turnos) — el resto de la tabla sigue disponible.")) + '</p>';
      }
      var tableClass = "cnt-table" + (tipo === "medicos" ? " cnt-table--medicos" : " cnt-table--catalog cnt-table--" + tipo);
      html += '<div class="cnt-table-wrap"><table class="' + tableClass + '"><thead><tr id="cnt-thead-' + tipo + '"></tr></thead><tbody id="cnt-tbody-' + tipo + '"></tbody></table></div>';
      // Filters that hide every row: the table (header, select-all) gives way
      // to the shared no-match state; renderListaRows switches between them.
      html += '<div class="cnt-no-match" id="cnt-nomatch-' + tipo + '" hidden></div>';
      sec.innerHTML = html;

      if (searchable) {
        listen($(".cnt-search", sec), "input", function (e) {
          SEARCH[tipo] = e.target.value;
          prefsSet("catalog.search", SEARCH);
          renderListaRows(tipo);
        });
      }
      if (esMedicos) {
        wireMedFiltros(sec);
        wireMedBulk(sec);
        wireMedInvite(sec);
        wireMedImport(sec);
        // especialidad clickeada en una fila → setea el filtro (delegado y
        // atado UNA vez al section: las filas se redibujan a cada tecla)
        if (claimRouteBinding(sec, "_fespWired")) {
          listen(sec, "click", function (e) {
            var a = e.target.closest("[data-fesp-med]");
            if (!a) return;
            e.preventDefault();
            MED_FILTERS.especialidad = a.getAttribute("data-fesp-med") || "";
            prefsSet("doctors.filters", MED_FILTERS);
            var sel = $("#med-f-especialidad", sec);
            if (sel) { sel.value = MED_FILTERS.especialidad; sel.classList.toggle("on", !!sel.value); }
            renderListaRows("medicos");
          });
        }
      }
      if (esServicios) wireServiceFiltros(sec);
      renderListaRows(tipo);
    });
  }

  function renderListaRows(tipo) {
    var sec = $("#sec-" + tipo);
    var data = CACHE[tipo];
    if (!data || data.noBackend) return;
    var items = data.items;
    // A column with nothing in any row of the list is not drawn (the doctors'
    // «Cuenta» where nobody has an account yet). Judged on the whole list, not
    // on the filtered rows, so the columns do not jump while someone types.
    var cols = catalogPicker(tipo).activas().filter(function (c) {
      return c.always || c.key === SORT[tipo].key || items.some(function (it) { return !cellEmpty(tipo, c, it); });
    });
    var n = items.length;
    var sortable = n > 10;

    var s = SORT[tipo];
    var col = cols.filter(function (c) { return c.key === s.key; })[0] || cols[0];
    var q = SEARCH[tipo];
    var visible = items.filter(function (it) { return matchesSearch(tipo, it, q, cols); });
    if (tipo === "medicos") visible = visible.filter(matchesFiltrosMed);
    if (tipo === "servicios") visible = visible.filter(matchesFiltrosServicios);
    visible.sort(function (a, b) { return compararCol(col, a, b, s.dir); });
    // A blank trailing column is only useful when it contains a row action.
    // Restore is the sole list-row action, and only administrators can use it.
    var hasRowActions = puedeAltaBaja(tipo) && visible.some(function (it) { return !!it._borrado; });

    var theadRow = $("#cnt-thead-" + tipo, sec);
    var theadHtml = "";
    if (tipo === "medicos" && puede("usuarios")) {
      theadHtml += '<th class="cnt-th cnt-th-check"><input type="checkbox" id="med-select-all" aria-label="' + esc(T("admin.content.catalog.list.select_visible_doctors", null, "Seleccionar médicos visibles")) + '"></th>';
    }
    cols.forEach(function (c) {
      var on = c.key === s.key;
      theadHtml += '<th data-col="' + esc(c.key) + '"' + (sortable ? ' data-sort="' + c.key + '" class="cnt-th' + (c.numeric ? " cnt-numeric" : "") + ' cnt-th-sortable' + (on ? " on" : "") + '"' : ' class="cnt-th' + (c.numeric ? " cnt-numeric" : "") + '"') +
        ">" + esc(c.label) + (on ? (s.dir === 1 ? " ▲" : " ▼") : "") + "</th>";
    });
    if (hasRowActions) theadHtml += '<th class="cnt-th cnt-th-acts"></th>';
    theadRow.innerHTML = theadHtml;

    var tbody = $("#cnt-tbody-" + tipo, sec);
    var filtered = !!q || (tipo === "medicos" && !!(MED_FILTERS.centro || MED_FILTERS.especialidad || MED_FILTERS.agenda)) || (tipo === "servicios" && !!(SERVICE_FILTERS.centro || SERVICE_FILTERS.modo));
    // Progressive disclosure (the product owner, 2026-10-26): with nothing listed there is
    // no header, select-all or column picker — only the empty / no-match state.
    var view = Filters.applyDisclosure(sec, Filters.disclosure({ total: n, shown: visible.length, filtered: filtered }));
    var tableWrap = $(".cnt-table-wrap", sec), noMatch = $("#cnt-nomatch-" + tipo, sec), gear = $(".cnt-gear-wrap", sec);
    if (tableWrap) tableWrap.hidden = !visible.length;
    if (gear) gear.hidden = !visible.length;
    var download = $(".cnt-toolbar .tbl-export-btn", sec);
    if (download) download.hidden = !visible.length;
    if (noMatch) {
      noMatch.hidden = !!visible.length;
      noMatch.innerHTML = visible.length ? "" : view === "no-match"
        ? Filters.noMatchHtml({
          title: T("admin.content.catalog.list.no_search_matches", null, "Ninguno coincide con la búsqueda."),
          clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros"),
          clearAttrs: { "data-clear-catalog": "" }
        })
        : Filters.emptyStateHtml({ title: T("admin.content.catalog.list.empty_state", { tipo: TIPOS[tipo].plural }, "Todavía no hay {tipo}.") });
    }
    if (!visible.length) {
      theadRow.innerHTML = "";
      tbody.innerHTML = "";
    } else {
      var rowsHtml = "";
      visible.forEach(function (it) {
        rowsHtml += '<tr class="cnt-tr' + (it._borrado ? " cnt-tr-borrado" : "") + '">';
        if (tipo === "medicos" && puede("usuarios")) {
          var mid = String(idOf("medicos", it));
          rowsHtml += '<td class="cnt-td-check" data-label="' + esc(T("admin.content.catalog.columns.select", null, "Seleccionar")) + '"><input type="checkbox" data-med-select="' + esc(mid) + '" aria-label="' + esc(T("admin.content.catalog.list.select_doctor", { name: it.nombre || T("admin.content.catalog.doctors.generic_label", null, "médico") }, "Seleccionar {name}")) + '"' + (MED_SELECTED[mid] ? " checked" : "") + "></td>";
        }
        cols.forEach(function (c) {
          rowsHtml += '<td data-col="' + esc(c.key) + '" data-label="' + esc(c.label) + '"' + (c.numeric ? ' class="cnt-numeric"' : "") + ' title="' + esc(cellText(tipo, c, it)) + '">' + renderCell(tipo, c, it) + "</td>";
        });
        var restoreOriginal = it._origen === "json+db";
        var restoreLabel = restoreOriginal
          ? T("admin.content.catalog.common.restore_original", null, "Restaurar original")
          : T("admin.content.catalog.list.enable_link", null, "Habilitar");
        if (hasRowActions) rowsHtml += '<td class="cnt-td-acts" data-label="' + esc(T("admin.content.catalog.columns.actions", null, "Acciones")) + '">' + (it._borrado ? '<button type="button" class="btn btn-quiet btn-sm" data-restaurar="' + tipo + '" data-id="' + esc(idOf(tipo, it)) + '" data-restaurar-original="' + (restoreOriginal ? "1" : "") + '">' + esc(restoreLabel) + '</button>' : "") + "</td>";
        rowsHtml += "</tr>";
      });
      tbody.innerHTML = rowsHtml;
    }
    if (tipo === "medicos" && puede("usuarios")) {
      var viewSignature = visible.map(function (it) { return String(idOf("medicos", it)); }).join(",");
      if (MED_SELECTION_VIEW !== viewSignature) MED_LAST_SELECTED = null;
      MED_SELECTION_VIEW = viewSignature;
      $$("[data-med-select]", tbody).forEach(function (cb, index) {
        listen(cb, "click", function (event) { cb._medShift = !!event.shiftKey; });
        listen(cb, "change", function () {
          var id = cb.getAttribute("data-med-select");
          var previousIndex = visible.map(function (it) { return String(idOf("medicos", it)); }).indexOf(MED_LAST_SELECTED);
          if (cb._medShift && previousIndex !== -1) {
            var start = Math.min(previousIndex, index), end = Math.max(previousIndex, index);
            for (var i = start; i <= end; i++) MED_SELECTED[String(idOf("medicos", visible[i]))] = cb.checked;
            renderListaRows("medicos");
          } else {
            MED_SELECTED[id] = cb.checked;
          }
          MED_LAST_SELECTED = id;
          updateMedBulk(sec);
        });
      });
      var all = $("#med-select-all", theadRow);
      if (all) {
        all.checked = !!visible.length && visible.every(function (it) { return MED_SELECTED[String(idOf("medicos", it))]; });
        listen(all, "change", function () {
          visible.forEach(function (it) { MED_SELECTED[String(idOf("medicos", it))] = all.checked; });
          renderListaRows("medicos");
          updateMedBulk(sec);
        });
      }
      updateMedBulk(sec);
    }

    $$("[data-sort]", theadRow).forEach(function (th) {
      listen(th, "click", function () {
        var k = th.getAttribute("data-sort");
        if (SORT[tipo].key === k) SORT[tipo].dir *= -1; else { SORT[tipo].key = k; SORT[tipo].dir = 1; }
        prefsSet("catalog.sort", SORT);
        renderListaRows(tipo);
      });
    });
    $$("[data-restaurar]", tbody).forEach(function (a) {
      listen(a, "click", function (e) {
        e.preventDefault();
        if (a.getAttribute("data-restaurar-original") && !confirm(T("admin.content.catalog.common.confirm_restore", null, "¿Restaurar el original? Se pierde la edición hecha desde el admin."))) return;
        restaurar(tipo, a.getAttribute("data-id")).then(function () {
          toast(T("admin.content.catalog.common.restored", null, "Restaurado."));
          return fetchTipo(tipo, true);
        }).then(function () { renderListaRows(tipo); }).catch(function (e2) {
          if (!isCancelled(e2) && String(e2.message) !== "redirect") toast(errText(e2, T("admin.content.catalog.common.restore_failed", null, "No se pudo restaurar.")));
        });
      });
    });
    if (noMatch) $$("[data-clear-catalog]", noMatch).forEach(function (button) {
      listen(button, "click", function () {
        SEARCH[tipo] = "";
        prefsSet("catalog.search", SEARCH);
        if (tipo === "medicos") { MED_FILTERS = { centro: "", especialidad: "", agenda: "" }; prefsSet("doctors.filters", MED_FILTERS); }
        if (tipo === "servicios") { SERVICE_FILTERS = { centro: "", modo: "" }; prefsSet("services.filters", SERVICE_FILTERS); }
        renderLista(tipo);
      });
    });
    // Every list interaction replaces the header/body in place. The existing
    // SanaTable instance must refit that new DOM; its observer deliberately
    // skips this reserved selector to avoid creating a competing instance.
    fitCatalogTable(tipo, sec);
  }

  // =====================================================================
  // FORMULARIOS
  // =====================================================================

  function centroOptions(selected) {
    return centrosVivos().map(function (c) {
      return '<option value="' + esc(c.slug) + '"' + (c.slug === selected ? " selected" : "") + ">" + esc(c.name) + "</option>";
    }).join("");
  }

  // Los ids de campo llevan prefijo por tipo ("svc-", "cen-", "med-"): las tres
  // secciones conviven en el DOM a la vez (ocultas con display:none, no se
  // desmontan al navegar), así que ids compartidos como "f-centro" chocarían
  // entre sí y hasta con los selects de #sec-solicitudes (#f-esp/#f-medico/
  // #f-centro/#f-verdict ya existen ahí). Todas las consultas además van
  // scopeadas a `sec` para no depender sólo del prefijo.

  function formServicio(item, all) {
    var it = item || {};
    var agendaDoc = it.agenda || {};
    var agendaPrincipal = agendaDoc.franjas ? (agendaDoc.franjas[0] || {}) : agendaDoc;
    SVC_EXCEPCIONES = (agendaDoc.excepciones || it.excepciones || []).map(function (e) { return Object.assign({}, e); });
    SVC_FRANJAS_ADICIONALES = agendaDoc.franjas ? agendaDoc.franjas.slice(1) : [];
    var modo = it.modo || "walkin";
    var edadMin = it.edad && it.edad.min != null ? it.edad.min : "";
    var edadMax = it.edad && it.edad.max != null ? it.edad.max : "";
    var clusters = distinct(all, function (x) { return x.cluster; });
    Object.keys(CLUSTERS_NOMBRE).forEach(function (id) { if (clusters.indexOf(id) === -1) clusters.push(id); });
    var categorias = distinct(all, function (x) { return x.categoria; });

    // Tres perillas INDEPENDIENTES (responsable del producto, reunión hospital 2026-07-24): "el
    // modo" nunca fue un menú de cuatro opciones, son tres preguntas sueltas
    // — ¿se saca turno?, ¿hace falta la orden médica?, ¿lo confirma el
    // hospital? El `modo` del catálogo sale de las dos primeras (la
    // combinación que el server ya sabe validar) y `auto_confirmar` de la
    // tercera. Así se puede pedir la combinación que faltaba: turno, con o
    // sin orden, que queda confirmado solo (p. ej. control de salud).
    var perillas = [
      ["svc-c-turno", T("admin.content.catalog.services.knob_appointment_label", null, "Se saca turno"),
       T("admin.content.catalog.services.knob_appointment_hint", null, "Si no, se atiende por orden de llegada."),
       modo === "solicitud" || modo === "solicitud_con_orden"],
      ["svc-c-orden", T("admin.content.catalog.services.knob_order_label", null, "Requiere orden médica"),
       T("admin.content.catalog.services.knob_order_hint", null, "El paciente tiene que traer o subir la orden."),
       modo === "walkin_con_orden" || modo === "solicitud_con_orden"],
      ["svc-c-aprobacion", T("admin.content.catalog.services.knob_approval_label", null, "Lo confirma el hospital"),
       T("admin.content.catalog.services.knob_approval_hint", null, "Si lo destildás, el horario que elige el paciente queda confirmado al instante."),
       !it.auto_confirmar],
      // Cuarta perilla, sólo para los que NO sacan turno (the reference-deployment counterpart,
      // reunión 2026-07-24): el consultorio por orden de llegada abre su
      // listado el día de la atención, así quien ya sabe que va se guarda el
      // lugar desde la casa. Nunca para mañana: se abre hoy y se cierra hoy.
      ["svc-c-mismodia", T("admin.content.catalog.services.knob_sameday_label", null, "Abre el listado el mismo día"),
       T("admin.content.catalog.services.knob_sameday_hint", null, "Sólo si no se saca turno: ese día se puede reservar un lugar a distancia."),
       !!it.apertura_mismo_dia]
    ];
    var modoHtml = perillas.map(function (p) {
      return '<label class="cnt-modo-op' + (p[3] ? " on" : "") + '"><input type="checkbox" id="' + p[0] + '"'
        + (p[3] ? " checked" : "") + '><span class="cnt-modo-txt"><b>' + esc(p[1])
        + "</b><br><span class=\"cnt-hint\">" + esc(p[2]) + "</span></span></label>";
    }).join("");

    var html = '<section class="svc-field-group"><h2>' + esc(T("admin.content.catalog.services.group_public", null, "Visible para pacientes")) + '</h2>';
    html += row(T("admin.content.catalog.common.field_name", null, "Nombre"), '<input class="cnt-in cnt-field-name" id="svc-nombre" value="' + esc(it.nombre || "") + '">');
    html += row(T("admin.content.catalog.services.field_short_description", null, "Descripción breve"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-descripcion" rows="2" placeholder="' + esc(T("admin.content.catalog.services.short_description_placeholder", null, "Qué atención ofrece este servicio, en una frase")) + '">' + esc(it.descripcion || "") + '</textarea><p class="cnt-hint">' + esc(T("admin.content.catalog.services.short_description_hint", null, "Aparece debajo del título en la página del servicio.")) + '</p>');
    html += row(T("admin.content.catalog.columns.center", null, "Centro"), '<select class="adm-sel cnt-field-center" id="svc-centro">' + centroOptionsEmpty(it.centro) + "</select>");
    html += row(T("admin.content.catalog.services.field_venue", null, "Sede"), '<input class="cnt-in cnt-field-place" id="svc-sede" value="' + esc(it.sede || "") + '" placeholder="' + esc(T("admin.content.catalog.services.venue_placeholder", null, "opcional, si difiere del centro")) + '">');
    html += row(T("admin.content.catalog.services.field_how_attended", null, "Cómo se atiende"), '<div class="cnt-modo-list" id="svc-modo-list">' + modoHtml + "</div>");
    // Quién atiende este servicio (o «Sin médico» y dónde se asigna). Un servicio
    // nuevo todavía no tiene id: no hay nadie asignado que mostrar; uno sin turno
    // nunca tiene médico asignado, así que la fila no le pide nada (2026-11-04).
    var svcMedicos = it._medicos || [];
    if ((it.id || it.slug) && (svcMedicos.length || !servicioSinAgenda(it))) {
      var medicosLink = puede("medicos")
        ? '<a href="' + esc(listHref("medicos")) + '">' + esc(T("admin.content.catalog.columns.doctors", null, "Médicos")) + "</a>"
        : esc(T("admin.content.catalog.columns.doctors", null, "Médicos"));
      html += row(T("admin.content.catalog.services.field_doctors", null, "Médicos que lo atienden"),
        '<div id="svc-medicos">' + medicosCellHtml(it)
        + (svcMedicos.length ? "" : '<p class="cnt-hint">' + esc(T("admin.content.catalog.services.no_doctor_hint", { medicos: "\u0001" }, "Se asigna en la agenda de cada médico, en {medicos}.")).split("\u0001").join(medicosLink) + "</p>")
        + "</div>", "cnt-row-static");
    }
    // El horario NO entra en un renglón: en los datos reales llega a 161
    // caracteres ("Lunes de 8 a 11:30 h; Lunes de 13 a 16:30 h; Martes…") y
    // el p90 es 79 — con un input de 34ch se leía "Lunes de 11 a 13 h; Martes
    // de 9:30 a 11" y el resto quedaba invisible (responsable del producto, screenshot
    // 2026-07-24). Caja que crece con su contenido, igual que Cartel acá
    // abajo; los saltos de línea se limpian al guardar (sigue siendo UN texto).
    html += row(T("admin.content.catalog.columns.schedule", null, "Horario"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-horario" rows="2">' + esc(it.horario || "") + '</textarea><div class="cnt-preview" id="svc-horario-preview"></div>');
    html += row(T("admin.content.catalog.services.field_schedule_note", null, "Nota sobre el horario"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-horario-nota" rows="2" placeholder="' + esc(T("admin.content.catalog.services.schedule_note_placeholder", null, "Opcional: aclaración publicada por el centro")) + '">' + esc(it.horario_nota || "") + '</textarea>');
    html += row(T("admin.content.catalog.services.field_poster", null, "Cartel"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-cartel" rows="3">' + esc(it.cartel || "") + "</textarea><a href=\"#\" class=\"cnt-help\" id=\"svc-cartel-regen\">" + esc(T("admin.content.catalog.services.regenerate_poster_link", null, "Regenerar del modo y horario")) + "</a>");
    // One layout for the whole form (pinamar-turnos 03.11, decision 2a): the
    // schedule owner, the service hours and the closures are rows like every
    // other field — label left, control right — not sections of their own.
    html += '<div id="svc-agenda-wrap" style="display:' + (modo === "solicitud_con_orden" || modo === "solicitud" || it.apertura_mismo_dia ? "" : "none") + ';">' +
      row(T('admin.content.schedule.owner_heading', null, 'Quién define el horario'),
        '<label class="cnt-check"><input type="checkbox" id="svc-schedule-owner" data-service-schedule-owner data-doctor-count="' + (it._schedule_doctor_count || 0) + '"' + ((agendaDoc.dias?.length || agendaDoc.franjas?.length) ? " checked" : "") + '><span>' + esc(T('admin.content.schedule.owner_switch', null, 'Horario único del servicio')) + '</span></label>' +
        '<p class="cnt-hint">' + esc(T('admin.content.schedule.owner_hint', null, 'Un solo horario para todos sus médicos. Los horarios de cada médico se reemplazan.')) + '</p>' +
        '<p id="svc-owner-status" data-service-owner-status class="cnt-owner-status"></p>') +
      '<div class="cnt-row" id="svc-schedule-editor" data-service-schedule-editor><label>' + esc(T('admin.content.schedule.service_hours', null, 'Horario del servicio')) + '</label><div class="cnt-field">' + renderAgendaEditor("svc", agendaPrincipal) + '</div></div>' + "</div>";
    html += '<div class="cnt-row" id="svc-closures" data-service-closures><label>' + esc(T("admin.content.hours.closures.label", null, "Cierres")) + '</label><div class="cnt-field">' + renderServiceExcepciones() + '</div></div>';
    var anuncio = it.announcement || {};
    // The title and text of the notice only exist while «Mostrar aviso» is on:
    // switched off they say nothing to anyone (the product owner 27.10: what is not
    // switched on is not shown). Hidden, not removed: an inactive notice keeps
    // its text for the day it is switched back on.
    var anuncioOn = anuncio.active !== false && !!anuncio.body;
    html += row(T("admin.content.catalog.services.announcement_label", null, "Aviso público"), '<div class="svc-announcement-fields"><label class="cnt-check"><input type="checkbox" id="svc-announcement-active"' + (anuncioOn ? " checked" : "") + '><span>' + esc(T("admin.content.catalog.services.announcement_show", null, "Mostrar aviso en la página del servicio")) + '</span></label><div class="svc-announcement-dependent" id="svc-announcement-dependent"' + (anuncioOn ? "" : " hidden") + '><input class="cnt-in cnt-field-name" id="svc-announcement-title" value="' + esc(anuncio.title || "") + '" placeholder="' + esc(T("admin.content.catalog.services.announcement_title_placeholder", null, "Título del aviso")) + '"><textarea class="cnt-in cnt-wide cnt-textarea" id="svc-announcement-body" rows="2" placeholder="' + esc(T("admin.content.catalog.services.announcement_body_placeholder", null, "Qué cambió y qué tiene que hacer el paciente")) + '">' + esc(anuncio.body || "") + '</textarea></div></div>');
    html += row("PAMI", '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-pami" rows="2">' + esc(it.pami || "") + "</textarea>");
    // Category, age, limits, search words and «Destacado» are set once and
    // rarely touched: an existing service keeps them folded under their
    // heading, a new one shows them open (it still has to choose them). The
    // fields stay in the DOM, so saving reads them folded or not.
    html += '</section><details class="svc-field-group cnt-fold" id="svc-internal"' + (it.id || it.slug ? "" : " open") + '><summary><h2>' + esc(T("admin.content.catalog.services.group_internal", null, "Configuración interna")) + '</h2></summary>';
    html += row(T("admin.content.catalog.common.field_category", null, "Categoría"), '<select class="adm-sel cnt-field-taxonomy" id="svc-categoria"><option value="">' + esc(T("admin.content.catalog.common.choose_category_placeholder", null, "Elegí una categoría…")) + '</option>' + selectOptions(categorias, it.categoria || "") + "</select>");
    html += row(T("admin.content.catalog.services.field_specialty_general", null, "Especialidad general"), '<select class="adm-sel cnt-field-taxonomy" id="svc-cluster"><option value="">' + esc(T("admin.content.catalog.services.choose_specialty_general_placeholder", null, "Elegí una especialidad general…")) + '</option>' + selectOptions(clusters, it.cluster || "", CLUSTERS_NOMBRE) + "</select>");
    html += row(T("admin.content.catalog.services.field_age", null, "Edad"), '<div class="cnt-edad"><input class="cnt-in cnt-num" id="svc-edad-min" type="number" min="0" placeholder="' + esc(T("admin.content.catalog.common.age_min_placeholder", null, "mín")) + '" value="' + esc(edadMin) + '"> a <input class="cnt-in cnt-num" id="svc-edad-max" type="number" min="0" placeholder="' + esc(T("admin.content.catalog.common.age_max_placeholder", null, "máx")) + '" value="' + esc(edadMax) + '"></div>');
    // Booking limits only mean something where a patient books: shown and
    // hidden with the service schedule (#svc-agenda-wrap), by the same rule.
    html += '<div id="svc-limits-wrap" style="display:' + (modo === "solicitud_con_orden" || modo === "solicitud" || it.apertura_mismo_dia ? "" : "none") + ';">' + hoursEditor().bookingLimitsHtml('svc', it, {patient:TURNOS_MAX_SITIO, days:DIAS_ANTICIPACION_SITIO}, row) + '</div>';
    html += row(T("admin.content.catalog.common.field_keywords", null, "Palabras clave"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-palabras" rows="2"' + opcionalAttr() + '>' + esc((it.palabras || []).join(", ")) + '</textarea><p class="cnt-hint">' + esc(T("admin.content.catalog.common.comma_separated_hint", null, "Separadas por coma.")) + '</p>');
    html += row(T("admin.content.catalog.services.field_excluded_keywords", null, "No aparecer si buscan"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="svc-palabras-exc" rows="2"' + opcionalAttr() + '>' + esc((it.palabras_excluidas || []).join(", ")) + '</textarea><p class="cnt-hint">' + esc(T("admin.content.catalog.services.excluded_keywords_hint", null, "Separadas por coma. Búsquedas en las que este servicio no debe aparecer, por ejemplo una práctica que acá no se hace.")) + '</p>');
    var topOptions = "";
    for (var pos = 1; pos <= 20; pos++) topOptions += '<option value="' + pos + '"' + (+it.top === pos ? " selected" : "") + ">" + pos + "</option>";
    html += row(T("admin.content.catalog.services.field_featured", null, "Destacado"), '<label class="cnt-check"><input type="checkbox" id="svc-featured"' + (it.top ? " checked" : "") + '><span>' + esc(T("admin.content.catalog.services.featured_checkbox_label", null, "Mostrar entre los servicios destacados")) + '</span></label>');
    html += '<div id="svc-feature-dependent">' +
      row(T("admin.content.catalog.services.featured_position_label", null, "Posición").trim(), '<select class="adm-sel svc-feature-order" id="svc-top">' + topOptions + '</select><p class="cnt-hint">' + esc(T("admin.content.catalog.services.featured_position_hint", null, "La posición 1 aparece primero.")) + '</p>') +
      row(T("admin.content.catalog.common.field_tag", null, "Etiqueta"), '<input class="cnt-in cnt-field-short" id="svc-top-label" maxlength="40" value="' + esc(it.top_label || "") + '"' + opcionalAttr() + '>') +
      '</div>';
    return html + "</details>";
  }

  function collectServicio(it, sec) {
    var doc = Object.assign({}, it || {});
    doc.nombre = $("#svc-nombre", sec).value.trim();
    var descripcion = $("#svc-descripcion", sec).value.replace(/\s*\n\s*/g, " ").trim();
    if (descripcion) doc.descripcion = descripcion; else delete doc.descripcion;
    doc.centro = $("#svc-centro", sec).value;
    var sede = $("#svc-sede", sec).value.trim(); if (sede) doc.sede = sede; else delete doc.sede;
    doc.modo = modoActual(sec);
    // sigue siendo UN texto: el textarea es sólo para poder VERLO entero
    doc.horario = $("#svc-horario", sec).value.replace(/\s*\n\s*/g, " ").trim();
    var horarioNota = $("#svc-horario-nota", sec).value.replace(/\s*\n\s*/g, " ").trim();
    if (horarioNota) doc.horario_nota = horarioNota; else delete doc.horario_nota;
    doc.cartel = $("#svc-cartel", sec).value.trim();
    var announcementBody = $("#svc-announcement-body", sec).value.trim();
    if (announcementBody) {
      doc.announcement = {
        active: !!$("#svc-announcement-active", sec).checked,
        title: $("#svc-announcement-title", sec).value.trim(),
        body: announcementBody
      };
    } else delete doc.announcement;
    collectBookingWindow(doc, "svc");
    var svcTurnosMax = ($("#svc-turnos-max", sec) || {}).value;
    if (String(svcTurnosMax || "").trim()) doc.turnos_activos_max = +svcTurnosMax;
    else delete doc.turnos_activos_max;
    // Apertura el mismo día: sólo tiene sentido sin turno (el server valida lo
    // mismo). Se guarda ANTES de la agenda porque decide si hay agenda.
    var mismoDia = $("#svc-c-mismodia", sec);
    var abreMismoDia = !!(mismoDia && mismoDia.checked && !mismoDia.disabled
                          && (doc.modo === "walkin" || doc.modo === "walkin_con_orden"));
    if (abreMismoDia) doc.apertura_mismo_dia = true; else delete doc.apertura_mismo_dia;
    syncServiceExcepciones(sec);
    delete doc.excepciones;
    if ((doc.modo === "solicitud_con_orden" || doc.modo === "solicitud" || abreMismoDia) && ($("#svc-schedule-owner", sec) || {}).checked) {
      var ag = collectAgenda("svc");
      var franjas = (ag ? [ag] : []).concat(SVC_FRANJAS_ADICIONALES);
      if (SVC_EXCEPCIONES.length || SVC_FRANJAS_ADICIONALES.length) {
        doc.agenda = {franjas: franjas, excepciones: SVC_EXCEPCIONES};
      } else if (ag) doc.agenda = ag;
      else delete doc.agenda;
    } else {
      // Closures survive a mode change without creating a walk-in agenda.
      delete doc.agenda;
      if (SVC_EXCEPCIONES.length) {
        if (doc.modo === 'solicitud' || doc.modo === 'solicitud_con_orden') doc.agenda = {franjas:[], excepciones:SVC_EXCEPCIONES};
        else doc.excepciones = SVC_EXCEPCIONES;
      }
    }
    // La tercera perilla se guarda INVERTIDA: el catálogo tiene el flag de la
    // excepción (auto_confirmar), no el de lo habitual (que lo mire el
    // hospital). Un walk-in no tiene turno que confirmar — el server lo
    // rechazaría igual, pero acá ni se manda.
    var aprueba = $("#svc-c-aprobacion", sec);
    if (doc.modo !== "walkin" && doc.modo !== "walkin_con_orden" && aprueba && !aprueba.checked) {
      doc.auto_confirmar = true;
    } else delete doc.auto_confirmar;
    doc.categoria = $("#svc-categoria", sec).value.trim();
    doc.cluster = $("#svc-cluster", sec).value.trim();
    var min = $("#svc-edad-min", sec).value, max = $("#svc-edad-max", sec).value;
    doc.edad = { min: min !== "" ? +min : null, max: max !== "" ? +max : null };
    doc.pami = $("#svc-pami", sec).value.trim();
    var palabras = $("#svc-palabras", sec).value.split(/[,\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    if (palabras.length) doc.palabras = palabras; else delete doc.palabras;
    var palabrasExc = $("#svc-palabras-exc", sec).value.split(/[,\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    if (palabrasExc.length) doc.palabras_excluidas = palabrasExc; else delete doc.palabras_excluidas;
    if ($("#svc-featured", sec).checked) {
      doc.top = +$("#svc-top", sec).value || 1;
      var topLbl = $("#svc-top-label", sec).value.trim(); if (topLbl) doc.top_label = topLbl; else delete doc.top_label;
    } else {
      delete doc.top;
      delete doc.top_label;
    }
    return doc;
  }

  // Optional catalog fields say «opcional», never a gray sample: on a
  // screenshot a sample number, keyword or label reads as the entity's own
  // value (2026-11-10). A format the field needs goes in its
  // hint or its error text, not in the placeholder.
  function opcionalAttr() {
    return ' placeholder="' + esc(T("admin.content.catalog.common.optional_placeholder", null, "opcional")) + '"';
  }

  function formCentro(item) {
    var it = item || {};
    var html = "";
    html += row(T("admin.content.catalog.common.field_name", null, "Nombre"), '<input class="cnt-in cnt-wide" id="cen-nombre" value="' + esc(it.name || "") + '">');
    html += fotoRowHtml("cen", it);
    html += row(T("admin.content.catalog.columns.address", null, "Dirección"), '<input class="cnt-in cnt-wide" id="cen-direccion" value="' + esc(it.address || "") + '">');
    html += row(T("admin.content.catalog.columns.phone", null, "Teléfono"), '<input class="cnt-in" id="cen-telefono" style="width:16ch" value="' + esc(it.phone || "") + '">');
    // WhatsApp aparte del teléfono: el sitio lo muestra con su ícono y un
    // link a wa.me, nunca como tel: (un bot de WhatsApp no atiende llamadas).
    html += row(T("admin.content.catalog.centers.field_whatsapp", null, "WhatsApp"), '<input class="cnt-in" id="cen-whatsapp" type="tel" inputmode="tel" style="width:16ch" value="' + esc(it.whatsapp || "") + '">');
    html += row(T("admin.content.catalog.common.field_email", null, "Email"), '<input class="cnt-in cnt-field-email" id="cen-email" type="email" autocomplete="email" value="' + esc(it.email || "") + '">');
    html += row(T("admin.content.catalog.common.field_description", null, "Descripción"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="cen-descripcion" rows="2">' + esc(it.description || "") + "</textarea>");
    html += row(T("admin.content.catalog.centers.field_jtbd", null, "Para qué se usa"),'<textarea class="cnt-in cnt-wide cnt-textarea" id="cen-jtbd" rows="2">' + esc(it.jtbd || "") + "</textarea>");
    html += row(T("admin.content.catalog.centers.field_jobs", null, "Qué podés hacer ahí"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="cen-jobs" rows="4"' + opcionalAttr() + '>' + esc((it.jobs || []).join("\n")) + '</textarea><p class="cnt-hint">' + esc(T("admin.content.catalog.centers.jobs_hint", null, "Una por línea.")) + "</p>");
    html += row(T("admin.content.catalog.centers.field_maps", null, "Google Maps"), '<input class="cnt-in cnt-wide" id="cen-maps" value="' + esc(it.maps_url || "") + '">');
    html += row(T("admin.content.catalog.centers.field_bookable", null, "Reservable"), '<label class="cnt-check"><input type="checkbox" id="cen-bookable"' + (it.bookable !== false ? " checked" : "") + '><span>' + esc(T("admin.content.catalog.centers.bookable_checkbox_label", null, "Se pueden pedir turnos en este centro")) + '</span></label>');
    // The location note, the date the address expires, the zone, the
    // coordinates and the HSI number are filled for a few centers only: they
    // sit folded under one heading, open when the center carries any of them.
    // The fields stay in the DOM, so saving reads them folded or not.
    var conDetalle = !!(it.location_note || it.location_until || it.locality || (it.lat != null && it.lng != null) || it.hsi_institution);
    html += '<details class="cnt-fold" id="cen-more"' + (conDetalle ? " open" : "") + '><summary><h2>' + esc(T("admin.content.catalog.centers.group_more", null, "Ubicación exacta e HSI")) + '</h2></summary>';
    html += row(T("admin.content.catalog.centers.field_location_note", null, "Nota de ubicación"),'<input class="cnt-in cnt-wide" id="cen-nota-ubi" value="' + esc(it.location_note || "") + '"' + opcionalAttr() + '>');
    // Hasta cuándo vale la dirección (la unidad móvil en una escuela): pasado
    // ese día la dirección y el mapa dejan de mostrarse solos.
    html += row(T("admin.content.catalog.centers.field_location_until", null, "La dirección vale hasta"), '<input class="cnt-in" id="cen-hasta" type="date" style="width:16ch" value="' + esc(it.location_until || "") + '">');
    // Zona (Pinamar, Ostende…): the home groups the centers by it. Blank = the
    // site works it out from the name/address; coordinates make the route
    // button exact (lat, lng as Google Maps shows them).
    html += row(T("admin.content.catalog.centers.field_locality", null, "Zona"), '<input class="cnt-in" id="cen-zona" style="width:24ch" value="' + esc(it.locality || "") + '"' + opcionalAttr() + '>');
    html += row(T("admin.content.catalog.centers.field_coords", null, "Coordenadas"), '<input class="cnt-in" id="cen-coords" style="width:24ch" inputmode="decimal" value="' + esc(it.lat != null && it.lng != null ? it.lat + ", " + it.lng : "") + '"' + opcionalAttr() + '>');
    // Each center is its own institution in HSI: the desk's HSI extension
    // opens this number for the center's turnos (…/institucion/1086/…).
    html += row(T("admin.content.catalog.centers.field_hsi_institution", null, "Institución en HSI"), '<input class="cnt-in" id="cen-hsi" style="width:18ch" inputmode="numeric" value="' + esc(it.hsi_institution || "") + '"' + opcionalAttr() + '>');
    return html + "</details>";
  }

  function collectCentro(it, sec) {
    var doc = Object.assign({}, it || {});
    // Old-key leftovers (an item loaded before this rename, or copied via
    // Object.assign from the item passed in) must not survive into a new
    // write — the overlay's alias layer (catalog.py) only translates OLD
    // rows on READ; a write with BOTH keys would silently keep serving the
    // stale one wherever code still reads "nombre" for another type.
    delete doc.nombre; delete doc.direccion; delete doc.telefono;
    delete doc.descripcion; delete doc.nota_ubicacion;
    doc.name = $("#cen-nombre", sec).value.trim();
    doc.address = $("#cen-direccion", sec).value.trim();
    doc.phone = $("#cen-telefono", sec).value.trim();
    var wa = $("#cen-whatsapp", sec).value.trim(); if (wa) doc.whatsapp = wa; else delete doc.whatsapp;
    doc.email = $("#cen-email", sec).value.trim();
    doc.description = $("#cen-descripcion", sec).value.trim();
    doc.jtbd = $("#cen-jtbd", sec).value.trim();
    var jobs = $("#cen-jobs", sec).value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
    if (jobs.length) doc.jobs = jobs; else delete doc.jobs;
    doc.maps_url = $("#cen-maps", sec).value.trim();
    doc.bookable = $("#cen-bookable", sec).checked;
    var nota = $("#cen-nota-ubi", sec).value.trim(); if (nota) doc.location_note = nota; else delete doc.location_note;
    var hasta = $("#cen-hasta", sec).value.trim(); if (hasta) doc.location_until = hasta; else delete doc.location_until;
    var zona = $("#cen-zona", sec).value.trim(); if (zona) doc.locality = zona; else delete doc.locality;
    // Blank clears the pair; anything that is not «lat, lng» goes to the
    // server as typed so it is refused at the field — never a silent wipe of
    // the saved coordinates («-37,1009, -56,8731» used to erase them).
    var coords = $("#cen-coords", sec).value.trim();
    var xy = coords.split(",").map(function (s) { return s.trim(); });
    // «-37,1009, -56,8731»: the es-AR decimal keypad types commas; four parts
    // are read as two numbers with a decimal comma each.
    if (xy.length === 4 && xy.every(function (s) { return s; })) xy = [xy[0] + "." + xy[1], xy[2] + "." + xy[3]];
    if (!coords) { delete doc.lat; delete doc.lng; }
    else if (xy.length === 2 && xy[0] && xy[1]) { doc.lat = xy[0]; doc.lng = xy[1]; }
    else { doc.lat = coords; doc.lng = ""; }
    var hsi = $("#cen-hsi", sec).value.replace(/\s+/g, ""); if (hsi) doc.hsi_institution = hsi; else delete doc.hsi_institution;
    return doc;
  }

  // "Juan Pérez" (sólo nombre viejo, sin nombre_pila/apellido) -> ["Juan", "Pérez"]
  // best-effort, sólo para precargar los dos inputs — no toca datos hasta guardar.
  function splitNombreLegacy(nombre) {
    var partes = (nombre || "").trim().split(/\s+/);
    if (partes.length < 2) return { pila: nombre || "", apellido: "" };
    return { pila: partes.slice(0, -1).join(" "), apellido: partes[partes.length - 1] };
  }

  // ---- Foto del médico (upload del admin, POST/DELETE /api/admin/doctors/<id>/photo) ----
  // Un médico nuevo todavía no tiene id (el server lo necesita para el path del
  // endpoint) → la fila muestra sólo la leyenda hasta que se guarde una vez.
  // Foto de médico Y de centro (responsable del producto, 2026-07-24: "for new center, there
  // should be a photo. Same for doctors"). Una entidad TODAVÍA SIN GUARDAR
  // también puede elegir su foto: no hay id contra el cual subirla, así que
  // queda pendiente en memoria (FOTO_PENDIENTE) y se sube sola apenas el
  // POST devuelve el id — antes decía "guardá primero", que obligaba a
  // volver a entrar al editor.
  var FOTO_PENDIENTE = null;   // {prefix, data_b64, filename, mime} o null

  // One row of buttons beside the portrait, the control «Mi cuenta» already
  // uses (admin-account.mjs photoControlsHtml): a labelled button opens the
  // chooser (the native «Choose File / No file chosen» stays hidden), «Quitar
  // foto» is a button, not grey small print, and no hint lines under it
  // (Opus UI review 2026-11-03). The 5 MB guard still answers in place.
  function fotoRowHtml(prefix, it) {
    var portrait = prefix === 'med' && it.id;
    return row(T("admin.content.catalog.common.field_photo", null, "Foto"),
      '<div class="cnt-foto-wrap">' +
      '<div class="cnt-foto-av" id="' + prefix + '-foto-av" aria-hidden="true"></div>' +
      '<div class="cnt-foto-actions">' +
      '<input type="file" id="' + prefix + '-foto-input" class="cnt-foto-file" accept="image/jpeg,image/png,image/webp">' +
      '<label class="btn btn-quiet btn-sm" for="' + prefix + '-foto-input" id="' + prefix + '-foto-pick">' + esc(T("admin.system.account.photo.add", null, "Agregar foto")) + '</label>' +
      '<button type="button" id="' + prefix + '-foto-quitar" class="btn btn-quiet btn-sm" style="display:none">' + esc(T("admin.content.catalog.common.remove_photo_link", null, "Quitar foto")) + '</button>' +
      (portrait
        ? '<button type="button" class="btn btn-quiet btn-sm" data-portrait-action="restore" hidden>' + esc(T('admin.doctor.restore', null, 'Restaurar original')) + '</button>' +
          '<button type="button" class="btn btn-quiet btn-sm" data-portrait-action="process" hidden>' + esc(T('admin.doctor.reprocess', null, 'Volver a procesar')) + '</button>' +
          '<p id="med-foto-status" role="status"></p>'
        : '') +
      "</div></div>", null, prefix + "-foto-input");
  }

  // Sube la foto que quedó pendiente de una entidad recién creada. Nunca
  // rompe el alta: si la subida falla, la entidad ya existe y se avisa.
  function subirFotoSnapshot(ruta, id, f) {
    if (!f || !id) return Promise.resolve();
    var upload = fetch("/api/admin/" + ruta + "/" + encodeURIComponent(id) + "/photo", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: f.filename, mime: f.mime, data_b64: f.data_b64 })
    });
    // The stale-form facade may reject after navigation; the settled channel
    // below owns this dependent transport instead.
    upload.catch(function () {});
    return (upload.settled || upload).then(function (res) {
      return res.json().then(function (body) {
        if (!res.ok || !body || body.ok !== true) throw new Error("foto");
      });
    });
  }

  // Pinta el círculo con lo que devolvió GET /api/admin/doctors/<id>/photo:
  // {style} si hay foto (upload o sprite), si no {ini,color} para las iniciales.
  function paintFotoAv(sec, info, prefix) {
    var av = $("#" + (prefix || "med") + "-foto-av", sec);
    if (!av) return;
    if (info.style) {
      av.setAttribute("style", info.style);
      av.className = "cnt-foto-av";
      av.textContent = "";
    } else {
      av.removeAttribute("style");
      av.className = "cnt-foto-av cnt-foto-av--" + (info.color || 0);
      av.textContent = info.ini || "";
    }
  }

  function wireFotoEditor(sec, item, prefix, ruta) {
    prefix = prefix || "med";
    ruta = ruta || "doctors";
    // A center is keyed by `slug` (no `id`): reading only `id` made every
    // existing center look new, so its stored photo never showed in the editor.
    var mid = item && (item.id || item.slug);
    var av = $("#" + prefix + "-foto-av", sec);
    if (!av) return;
    var photoVersion = mountVersion;
    var readSequence = 0;
    function ownsRead(sequence) {
      return current(photoVersion) && sequence === readSequence && av.isConnected
        && $("#" + prefix + "-foto-av", sec) === av;
    }

    var input = $("#" + prefix + "-foto-input", sec);
    var quitarA = $("#" + prefix + "-foto-quitar", sec);
    var pick = $("#" + prefix + "-foto-pick", sec);
    var base = mid ? "/api/admin/" + ruta + "/" + encodeURIComponent(mid) + "/photo" : "";

    // Entidad nueva: no hay a quién subirle la foto todavía. Se muestra la
    // vista previa localmente y el archivo queda pendiente hasta el guardado.
    if (!mid) {
      listen(input, "change", function () {
        var sequence = ++readSequence;
        var file = input.files && input.files[0];
        if (!file) { FOTO_PENDIENTE = null; return; }
        if (file.size > 5 * 1024 * 1024) { toast(T("admin.content.catalog.common.photo_too_large", null, "La foto supera los 5 MB.")); input.value = ""; return; }
        var reader = new FileReader();
        reader.onload = function () {
          if (!ownsRead(sequence)) return;
          var url = String(reader.result || "");
          FOTO_PENDIENTE = { prefix: prefix, filename: file.name, mime: file.type, data_b64: url };
          paintFotoAv(sec, { style: "background-image:url('" + url + "')" }, prefix);
          if (pick) pick.textContent = T("admin.system.account.photo.change", null, "Cambiar foto");
        };
        reader.readAsDataURL(file);
      });
      return;
    }

    function toggleQuitar(on) {
      quitarA.style.display = on ? "" : "none";
    }

    function refrescar() {
      return fetch(base, { credentials: "same-origin" }).then(function (res) {
        return afterSessionGate(res, function () {
          if (res.status === 401) throw new Error("401");
          return res.ok ? res.json() : null;
        });
      }).then(function (j) {
        if (!j) return;
        paintFotoAv(sec, j, prefix);
        toggleQuitar(!!j.has_upload);
        // A portrait to replace says «Cambiar»; initials alone say «Agregar».
        if (pick) pick.textContent = j.style
          ? T("admin.system.account.photo.change", null, "Cambiar foto")
          : T("admin.system.account.photo.add", null, "Agregar foto");
        var state = $('#med-foto-status', sec);
        if (prefix === 'med' && state) {
          state.textContent = j.has_upload ? T(j.status === 'procesada' ? 'admin.doctor.processed' : 'admin.doctor.original', null, j.status === 'procesada' ? 'Procesada' : 'Original') : '';
          $$('[data-portrait-action]', sec).forEach(function (button) { button.hidden = button.getAttribute('data-portrait-action') === 'restore' ? !j.has_original : !j.has_upload; });
        }
      }).catch(function () {});
    }
    refrescar();
    if (prefix === 'med') $$('[data-portrait-action]', sec).forEach(function (button) {
      listen(button, 'click', function () {
        if (!current(photoVersion)) return;
        var controls = $$('[data-portrait-action]', sec);
        if (controls.some(function (control) { return control.disabled; })) return;
        controls.forEach(function (control) { control.disabled = true; });
        fetch(base, { method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json'}, body:JSON.stringify({action:button.getAttribute('data-portrait-action')}) })
          .then(function (response) { return afterSessionGate(response, function () {
            return response.json().then(function (body) {
              if (!response.ok || body.ok !== true) throw new Error(body.error || T('admin.doctor.failed', null, 'No se pudo guardar. Volvé a intentar.'));
            });
          }); })
          .then(refrescar).catch(function (error) { if (current(photoVersion) && !isCancelled(error)) { var state=$('#med-foto-status',sec); if(state)state.textContent=errText(error, T('admin.doctor.failed', null, 'No se pudo guardar. Volvé a intentar.')); } })
          .finally(function () { if(current(photoVersion))controls.forEach(function (control) { control.disabled=false; }); });
      });
    });

    listen(input, "change", function () {
      var sequence = ++readSequence;
      var file = input.files && input.files[0];
      if (!file) return;
      if (file.size > 5 * 1024 * 1024) {
        toast(T("admin.content.catalog.common.photo_too_large", null, "La foto supera los 5 MB."));
        input.value = "";
        return;
      }
      var reader = new FileReader();
      reader.onload = function () {
        if (!ownsRead(sequence)) return;
        input.disabled = true;
        fetch(base, {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, mime: file.type, data_b64: String(reader.result || "") })
        }).then(function (res) {
          return afterSessionGate(res, function () {
            if (res.status === 401) throw new Error("401");
            return res.json().catch(decodeFailure).then(function (j) {
              if (!res.ok || !j.ok) throw new Error(j.error || T("admin.content.catalog.common.photo_upload_failed", null, "No se pudo subir la foto."));
            });
          });
        }).then(function () {
          input.value = "";
          return refrescar();
        }).catch(function (e) {
          if (!isCancelled(e) && String(e.message) !== "redirect") toast(errText(e, T("admin.content.catalog.common.photo_upload_failed", null, "No se pudo subir la foto.")));
        }).then(function () { input.disabled = false; });
      };
      reader.readAsDataURL(file);
    });

    listen(quitarA, "click", function (e) {
      e.preventDefault();
      fetch(base, { method: "DELETE", credentials: "same-origin" }).then(function (res) {
        return afterSessionGate(res, function () {
          if (res.status === 401) throw new Error("401");
          return res.json().catch(decodeFailure).then(function (j) {
            if (!res.ok || !j.ok) throw new Error(j.error || T("admin.content.catalog.common.photo_remove_failed", null, "No se pudo quitar la foto."));
          });
        });
      }).then(function () {
        toast(T("admin.content.catalog.common.photo_removed", null, "Foto quitada."));
        return refrescar();
      }).catch(function (e2) {
        if (!isCancelled(e2) && String(e2.message) !== "redirect") toast(errText(e2, T("admin.content.catalog.common.photo_remove_failed", null, "No se pudo quitar la foto.")));
      });
    });
  }

  function collectBookingWindow(doc, prefix) {
    var days = $('#' + prefix + '-dias-anticipacion');
    var time = $('#' + prefix + '-apertura-turnos');
    if (days && days.value.trim()) doc.dias_anticipacion = +days.value;
    else delete doc.dias_anticipacion;
    if (time && time.value) doc.apertura_turnos = time.value;
    else delete doc.apertura_turnos;
  }
  function managedScheduleHtml(services) {
    // Una sola frase oscura bajo las horas de cada servicio, con «Abrir el
    // servicio» en la misma línea (R42: nada de texto gris para lo que
    // explica por qué no se puede editar acá).
    return services.map(function (service) {
      var editable = !!(service._puede_editar && service.id);
      var note = editable
        ? esc(T("admin.content.schedule.service_applies_admin", null, "El horario lo define el servicio para todos sus médicos.")) +
          ' <a href="' + itemHref("servicios", service.id) + '">' + esc(T("admin.content.schedule.open_service", null, "Abrir el servicio")) + '</a>'
        : esc(T("admin.content.schedule.service_applies", null, "El horario lo define el servicio. Lo cambia el administrador."));
      return '<section><h2>' + esc(service.nombre || '') + '</h2><div class="cnt-service-schedule">' + esc(agendaResumen(service)) + '</div><p class="cnt-service-schedule cnt-service-note">' + note + '</p></section>';
    }).join('') + feriadosHtml();
  }

  function formMedico(item, all) {
    var it = item || {};
    var nombrePila = it.nombre_pila || splitNombreLegacy(it.nombre).pila;
    var apellido = it.apellido || splitNombreLegacy(it.nombre).apellido;
    var especialidad = it.especialidad || "";
    var cluster = especialidadCluster(especialidad) || it.cluster || "";

    MED_STATE = {
      bookingDefaults: it._booking_defaults,
      turnos_activos_max: it.turnos_activos_max,
      dias_anticipacion: it.dias_anticipacion,
      apertura_turnos: it.apertura_turnos,
      centros: (it.centros && it.centros.length)
        ? JSON.parse(JSON.stringify(it.centros))
        : [{ centro: it.centro || "", franjas: (it.agenda && it.agenda.dias) ? [it.agenda] : (it.agenda && it.agenda.franjas) || [], excepciones: (it.agenda && it.agenda.excepciones) || [] }]
    };
    MED_STATE.centros.forEach(function (c) { c.franjas = compactarFranjas(c.franjas || []); });

    var infoHtml = "";
    infoHtml += row(T("admin.content.catalog.common.field_name", null, "Nombre"), '<input class="cnt-in cnt-field-name" id="med-nombre-pila" value="' + esc(nombrePila) + '">');
    infoHtml += row(T("admin.content.catalog.common.field_lastname", null, "Apellido"), '<input class="cnt-in cnt-field-name" id="med-apellido" value="' + esc(apellido) + '">');
    infoHtml += row(T("admin.content.catalog.common.field_email", null, "Email"), '<input class="cnt-in cnt-field-email" id="med-email" type="email" autocomplete="email" value="' + esc(it.email || "") + '">');
    infoHtml += fotoRowHtml("med", it);
    infoHtml += row(T("admin.content.catalog.doctors.col_specialty", null, "Especialidad"), '<select class="adm-sel cnt-field-specialty" id="med-especialidad">' + especialidadOptions(especialidad) + "</select>" +
      (ESP_UNAVAILABLE ? '<p class="cnt-msg" role="status">' + esc(T("admin.content.catalog.doctors.specialties_unavailable", null, "No se pudo cargar la lista de especialidades. Conservamos la actual; volvé a abrir la ficha para reintentar.")) + '</p>' : "") +
      '<p class="cnt-hint" id="med-cluster-chip">' + clusterChipHtml(cluster, especialidad) +"</p>");
    infoHtml += row(T("admin.content.catalog.doctors.field_specialty_2", null, "Segunda especialidad"), '<select class="adm-sel cnt-field-specialty" id="med-especialidad-2">' + especialidadOptions(it.especialidad_2 || "", T("admin.content.catalog.doctors.specialty_2_placeholder", null, "Sin segunda especialidad")) + "</select>" +
      '<p class="cnt-hint" id="med-cluster-chip-2">' + (it.especialidad_2 ? clusterChipHtml(especialidadCluster(it.especialidad_2) || it.cluster_2 || "", it.especialidad_2) : "") + "</p>");
    infoHtml += row(T("admin.content.catalog.doctors.field_search_keywords", null, "Palabras de búsqueda"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="med-palabras" rows="2"' + opcionalAttr() + '>' + esc((it.palabras || []).join(", ")) + '</textarea><p class="cnt-hint">' + esc(T("admin.content.catalog.doctors.search_keywords_hint", null, "Separadas por coma. Síntomas o formas habituales de buscar a este profesional.")) + '</p>');
    infoHtml += row(T("admin.content.catalog.doctors.field_notes", null, "Notas"), '<textarea class="cnt-in cnt-wide cnt-textarea" id="med-nota" rows="3" placeholder="' + esc(T("admin.content.catalog.doctors.notes_placeholder", null, "Notas internas sobre este profesional, para uso permanente del equipo")) + '">' + esc(it.nota || it.demo_nota || "") + "</textarea>");

    // Tabs: Horarios de atención (el editor multi-centro de horarios;
    // data-tab sigue siendo "agenda", sólo cambió el label — el responsable del producto,
    // 2026-07-16: "Agenda" no decía qué se edita ahí) PRIMERO y por defecto
    // — es lo que un operador viene a tocar más seguido —, Calendario
    // (turnos ya asignados a este médico, sólo si YA tiene id — un médico
    // nuevo todavía no tiene turnos que mostrar) segundo, Información básica
    // (nombre/apellido/foto/especialidad/palabras/notas) tercero (the product owner,
    // 2026-07-16, corrigió el orden inicial que arrancaba en Calendario). Un
    // solo <form>, las tabs sólo muestran/ocultan (nunca desmontan ni
    // re-renderizan) para no perder lo tipeado al cambiar de tab; el botón
    // Guardar vive fuera de todos los paneles (ver renderForm) y
    // collectMedico sigue leyendo todos los campos por id, estén o no
    // visibles. El fetch del Calendario sigue disparándose al entrar (ver
    // wireMedicoCalendario) aunque el panel arranque oculto — sólo cambia
    // el orden visual, no cuándo se pide.
    // Un médico NUEVO abre en Información básica (responsable del producto, 2026-07-24): todavía
    // no tiene nombre ni especialidad, y sin especialidad el combo de
    // servicios de cada franja no puede sugerir nada — cargar horarios antes
    // que el nombre es empezar por el final. Uno EXISTENTE sigue abriendo en
    // Horarios de atención, que es a lo que se vuelve todos los días.
    var tieneId = !!it.id;
    var inicial = tieneId ? "agenda" : "info";
    function tab(name, label) {
      var on = name === inicial;
      return '<button type="button" class="cnt-tab' + (on ? " on" : "") + '" data-tab="' + name
        + '" role="tab" aria-selected="' + (on ? "true" : "false") + '">' + label + "</button>";
    }
    function panel(name, contenido) {
      return '<div class="cnt-tabpanel" data-tabpanel="' + name + '"'
        + (name === inicial ? "" : ' style="display:none"') + ">" + contenido + "</div>";
    }
    var html = "";
    if (window.CURRENT_ROLE !== "medico") html += '<div class="cnt-tabs" role="tablist">';
    if (window.CURRENT_ROLE !== "medico") html += tab("agenda", T("admin.content.catalog.doctors.tab_schedule", null, "Horarios de atención"));
    if (tieneId && window.CURRENT_ROLE !== "medico") html += tab("calendario", T("admin.content.catalog.doctors.tab_calendar", null, "Calendario"));
    if (window.CURRENT_ROLE !== "medico") html += tab("info", T("admin.content.catalog.doctors.tab_basic_info", null, "Información básica"));
    if (tieneId && window.CURRENT_ROLE !== "medico" && puede("asistente")) html += tab("intake", T("admin.content.catalog.doctors.tab_symptoms", null, "Síntomas"));
    if (tieneId && window.CURRENT_ROLE !== "medico" && puede("usuarios")) html += tab("usuario", T("admin.content.catalog.doctors.tab_user", null, "Usuario"));
    if (window.CURRENT_ROLE !== "medico") html += "</div>";
    html += panel("agenda", (it._agendas_servicio || []).length
      ? managedScheduleHtml(it._agendas_servicio) + (window.CURRENT_ROLE !== "medico" ? '<section class="hor-limits"><h2>' + esc(T('admin.content.limits.heading', null, 'Límites de reserva')) + '</h2>' + hoursEditor().bookingLimitsHtml('med', it, it._booking_defaults ? {patient:it._booking_defaults.patient, days:it._booking_defaults.days, patientFrom:it._booking_defaults.patient_from, daysFrom:it._booking_defaults.days_from} : {patient:TURNOS_MAX_SITIO, days:DIAS_ANTICIPACION_SITIO}) + '</section>' : '')
      : renderCentrosEditor());
    if (tieneId && window.CURRENT_ROLE !== "medico") html += panel("calendario", medicoCalendarioHtml());
    html += panel("info", infoHtml);
    if (tieneId && window.CURRENT_ROLE !== "medico" && puede("asistente")) html += panel("intake", '<div id="med-intake-panel"><p class="cnt-hint">' + esc(T("admin.content.catalog.common.loading", null, "Cargando…")) + '</p></div>');
    if (tieneId && window.CURRENT_ROLE !== "medico" && puede("usuarios")) html += panel("usuario", '<div id="med-user-panel"><p class="cnt-hint">' + esc(T("admin.content.catalog.doctors.loading_user", null, "Cargando usuario…")) + '</p></div>');
    return html;
  }

  // ---- Calendario del médico (tab "Calendario", GET /api/admin/doctors/<id>/appointments) ----
  // Mismo lenguaje visual que admin-my-appointments.js ("Mis turnos" del médico
  // logueado): encabezado de día amigable (Hoy/Mañana/weekday+fecha vía
  // dates.mjs) + una tabla cnt-table por día con hora/paciente/dni/servicio/
  // centro/preparación. Se duplica el patrón acá en vez de importar
  // admin-my-appointments.js porque ese módulo es un IIFE propio con su propio
  // hash-routing (#mis-turnos) — no expone nada reusable y no corresponde
  // acoplar los dos módulos por esto.

  function medicoCalendarioHtml() {
    // Tier 5 slice 3 (docs/roadmap/tier5-frontend-design.md, "Interactive
    // screens as Preact+HTM islands"): la grilla mensual vive en un island
    // Preact aparte (static/islands/MedCal.mjs), no adentro de #med-cal —
    // así Preact posee su propio subárbol y no se lo pisa el
    // host.innerHTML=… de renderMedicoCalendario en cada filtro/orden/turno
    // creado. wireMedicoCalendario la monta una vez; cargarMedicoCalendario
    // la actualiza con .update() cuando llega data nueva.
    return '<div id="med-cal-grid" data-island="MedCal" data-props="{}"></div>'
      + '<div id="med-cal" class="cnt-hint">' + esc(T("admin.content.catalog.common.loading", null, "Cargando…")) + '</div>';
  }

  // ---- Captura de síntomas del médico -------------------------------------

  function medIntakePost(body) {
    return fetch("/api/admin/intake/settings", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(decodeFailure).then(function (j) {
        if (!r.ok) throw new Error(j.error || T("admin.content.catalog.common.save_failed", null, "No se pudo guardar."));
        return j;
      });
    });
  }

  // Qué es y qué gana el médico (responsable del producto, 2026-10-27): el bloque compartido
  // platform/intake-explainer.mjs con UN botón — «Activar» lleno cuando está
  // apagada; prendida, el estado al lado de «Desactivar» y el cartel.
  function renderMedicoIntake(host, item, payload, Explainer) {
    var medico = (payload.medicos || []).filter(function (m) { return String(m.id) === String(item.id); })[0];
    if (!medico) { host.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.catalog.doctors.intake_not_found", null, "No se encontró la configuración de este médico.")) + '</p>'; return; }
    var pdfUrl = medico.efectivo && (medico.pdf_url || (medico.cartel_url ? medico.cartel_url + "?format=pdf" : ""));
    var cartel = pdfUrl
      ? '<a href="' + esc(pdfUrl) + '" target="_blank" rel="noopener">' + esc(T("admin.content.catalog.doctors.intake_poster_link", null, "Cartel imprimible con QR")) + '</a>'
      : "";
    var action = medico.efectivo
      ? '<strong>' + esc(T("admin.content.catalog.doctors.intake_status_active", null, "Activa")) + '</strong>'
        + '<button type="button" class="btn btn-quiet" data-med-intake-enabled="0">' + esc(T("admin.content.catalog.doctors.intake_disable", null, "Desactivar")) + '</button>'
        + cartel
      : '<button type="button" class="btn btn-ok" data-med-intake-enabled="1">' + esc(T("admin.content.catalog.doctors.intake_toggle_label", null, "Activar captura de síntomas")) + '</button>';
    host.innerHTML = '<div class="med-intake"><h2>' + esc(T("admin.content.catalog.doctors.intake_title", null, "Captura de síntomas")) + '</h2>'
      + Explainer.html(T, { action: action }) + "</div>";
  }

  function wireMedicoIntake(sec, item) {
    var host = $("#med-intake-panel", sec);
    if (!host || !item || !item.id) return;
    var payload = null;
    var explainerReady = importStatic("platform/intake-explainer.mjs").then(function (m) { m.ensureCss(document); return m; });
    function cargar() {
      if (!host.querySelector("[data-intake-explainer]")) host.innerHTML = '<p class="cnt-hint">' + esc(T("admin.content.catalog.common.loading", null, "Cargando…")) + '</p>';
      return Promise.all([fetch("/api/admin/intake/settings", { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(); return r.json(); }), explainerReady])
        .then(function (res) { payload = res[0]; renderMedicoIntake(host, item, payload, res[1]); })
        .catch(function (error) {
          if (isCancelled(error) || disposed || !host.isConnected) return;
          host.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.catalog.doctors.intake_load_failed", null, "No se pudo cargar la captura de síntomas.")) + '</p>';
        });
    }
    listen(host, "click", function (e) {
      var toggle = e.target.closest("[data-med-intake-enabled]");
      if (!toggle || toggle.disabled) return;
      toggle.disabled = true;
      medIntakePost({ medico_id: item.id, override: toggle.getAttribute("data-med-intake-enabled") === "1" })
        // Sin tost: el botón ya cambia a «Activa · Desactivar» (o al revés).
        .then(function () { return cargar(); })
        .catch(function (err) { if (isCancelled(err)) return; toast(errText(err, T("admin.content.catalog.common.save_failed", null, "No se pudo guardar."))); return cargar(); });
    });
    cargar();
  }

  function medCalGrupoLabel(diaIso) {
    if (!Dates) return diaIso;
    var rel = Dates.dia(diaIso);
    if (rel === T("date.today", null, "Hoy") || rel === T("date.tomorrow", null, "Mañana")) {
      var abs = Dates.larga(diaIso, null);
      return abs ? rel + ", " + abs : rel;
    }
    return rel || diaIso;
  }

  // Paciente CLICKEABLE (responsable del producto, 2026-07-20): abre su solicitud — lo que
  // mandó, con la orden y lo extraído de ella. La columna Preparación murió
  // acá (es dato del paciente, no del médico que arma su día); vive en la
  // solicitud que se abre al click.
  // Número pelado: la columna ya se llama "Edad" (responsable del producto, 2026-08-05). Lo
  // escribe format.mjs, igual que en Pacientes de hoy y en Mis turnos.
  function medCalEdad(iso) {
    return Format.edadTexto(iso);
  }

  // Esta era la única de las cuatro versiones que separaba el característico;
  // ahora ese criterio vive en format.mjs y lo usa todo el panel. De quién es
  // cada teléfono (el de la solicitud y, si es otro, el de la cuenta) — la
  // misma celda que «Mis turnos» y «Mis pacientes» (core/patient_contact.py).
  function medCalContacto(t) {
    if (!t.contacto) return "";
    return Format.contactoQuienHtml(t, "");
  }

  var MEDCAL_DATA = null;
  var MEDCAL_SELECTED_DAY = "";
  var MEDCAL_SORTS = {};
  var MEDCAL_FILTERS = { q: "", servicio: "", centro: "" };

  function medCalSortValue(t, key) {
    // Se ordena por la edad en años (número), no por el texto de la celda: un
    // bebé de "8 meses" tiene que caer antes que uno de 1, no entre los de 8.
    if (key === "edad") {
      var a = Format.edad(t.nacimiento);
      return a == null ? "-1" : String(a);
    }
    return String(t[key] || "").toLocaleLowerCase("es");
  }

  function medCalDiaTable(turnos, diaIso) {
    var sort = MEDCAL_SORTS[diaIso];
    if (sort) {
      turnos = turnos.slice().sort(function (a, b) {
        var cmp = medCalSortValue(a, sort.key).localeCompare(medCalSortValue(b, sort.key), "es", { numeric: true });
        return sort.dir === "desc" ? -cmp : cmp;
      });
    }
    // A value shared by the whole list belongs to the list context, not in
    // every row. Keep the column only when it actually distinguishes slots.
    var consultorios = distinct(turnos, function (t) { return t.consultorio; });
    var conConsultorio = consultorios.length > 1;
    var L = {
      hora: esc(T("admin.content.catalog.doctors.calendar_col_time", null, "Hora")),
      paciente: esc(T("admin.content.catalog.doctors.calendar_col_patient", null, "Paciente")),
      dni: "DNI",
      edad: esc(T("admin.content.catalog.doctors.calendar_col_age", null, "Edad")),
      contacto: esc(T("admin.content.catalog.doctors.calendar_col_contact", null, "Contacto")),
      servicio: esc(T("admin.content.catalog.columns.service", null, "Servicio")),
      consultorio: esc(T("admin.content.catalog.doctors.calendar_col_room", null, "Consultorio")),
      centro: esc(T("admin.content.catalog.columns.center", null, "Centro"))
    };
    // Cada celda lleva su clave y su rótulo, como en «Mis turnos»: en el
    // teléfono la fila se rearma en dos renglones (hora y paciente arriba, el
    // resto abajo) y una celda sin dato no ocupa lugar (pinamar-turnos 04.11).
    // Una celda sin dato queda en blanco: una raya en cada fila no dice nada
    // y tapa las que sí traen dato (la misma regla que «Mis turnos»).
    function celda(key, html) {
      var vacia = !html;
      return '<td data-col="' + key + '" data-label="' + L[key] + '"' + (vacia ? ' class="medcal-vacio"' : "") + ">" + (vacia ? "" : html) + "</td>";
    }
    var rows = turnos.map(function (t) {
      var sinNombre = T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre");
      var pac = t.hsi_patient_url
        ? '<a href="' + esc(t.hsi_patient_url) + '" target="_blank" rel="noopener" title="' + esc(T("admin.content.catalog.doctors.calendar_patient_in_hsi", null, "Paciente en HSI")) + '">' + esc(t.paciente || sinNombre) + "</a>"
        : esc(t.paciente || sinNombre);
      // En la fila de dos renglones del teléfono la edad va pegada al nombre
      // («Sofía Herrera, 73»), sin rótulo, como en «Mis turnos»; su celda no
      // se muestra ahí. En la tabla ancha queda la columna Edad.
      var edad = esc(medCalEdad(t.nacimiento));
      if (edad) pac += '<span class="medcal-edad-junto" data-export-omit>, ' + edad + "</span>";
      var hora = t.id
        ? '<button type="button" class="medcal-time" data-solicitud="' + esc(t.id) + '">' + esc(t.hora) + "</button>"
        : esc(t.hora);
      if (t.sobreturno) hora += ' <span class="medcal-sobre">' + esc(T("admin.content.catalog.doctors.overbooked_label", null, "sobreturno")) + '</span>';
      return "<tr class=\"cnt-tr\" data-export-dni=\"" + esc(String(t.dni || "").replace(/\D/g, "")) + "\">"
        + celda("hora", hora)
        + celda("paciente", pac)
        + celda("dni", Format.dniHtml(t.dni))
        + celda("edad", edad)
        + celda("contacto", medCalContacto(t))
        + celda("servicio", esc(t.servicio))
        + (conConsultorio ? celda("consultorio", esc(t.consultorio)) : "")
        + celda("centro", esc(t.centro))
        + "</tr>";
    }).join("");
    if (!rows) rows = '<tr><td colspan="' + (conConsultorio ? 8 : 7) + '" class="adm-empty">' + esc(T("admin.content.catalog.doctors.calendar_no_appointments", null, "No hay turnos para esta fecha.")) + '</td></tr>';
    var sortable = turnos.length > 3;
    function th(label, key) {
      if (!sortable) return '<th class="cnt-th">' + label + "</th>";
      var active = sort && sort.key === key;
      return '<th class="cnt-th"><button type="button" class="medcal-sort" data-med-sort="' + key + '" data-dia="' + esc(diaIso) + '">' + label + (active ? (sort.dir === "asc" ? " ↑" : " ↓") : "") + "</button></th>";
    }
    // In the downloaded file the day of each table becomes its first column.
    var diaLargo = (Dates && Dates.larga && Dates.larga(diaIso, null)) || diaIso || "";
    return '<div class="cnt-table-wrap"><table class="cnt-table medcal-table" data-export-group="' + esc(diaLargo)
      + '" data-export-group-head="' + esc(T("admin.shell.export.day_column", null, "Día")) + '"><thead><tr>'
      + th(L.hora, "hora") + th(L.paciente, "paciente") + th(L.dni, "dni")
      + th(L.edad, "edad") + th(L.contacto, "contacto")
      + th(L.servicio, "servicio") + (conConsultorio ? th(L.consultorio, "consultorio") : "")
      + th(L.centro, "centro")
      + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  // ---- calendario coloreado por ocupación (responsable del producto, 2026-07-20) ----------------
  // Grilla mensual: los días en que el médico atiende llevan color de base
  // según cuánto lugar queda — rojo = lleno, amarillo = quedan ~3 o menos,
  // verde = lugar de sobra. Los demás días quedan planos.
  // Tier 5 slice 3: esto YA NO arma el HTML de la grilla — eso lo hace el
  // island (static/islands/MedCal.mjs), llamando a gridHtml() directo desde el
  // módulo canónico. Lo que queda acá es sólo la parte que dependía de T()/i18n del
  // panel: mapear día crudo (capacidad/ocupados) a la forma que gridHtml()
  // espera (dia/className/title). El island recibe este array ya armado
  // como prop `dias` — sigue siendo "wrap the existing renderer", la
  // clasificación por ocupación no es el renderer, es preparación de datos.
  function medCalGridDias(diasDetalle) {
    if (!diasDetalle || !diasDetalle.length) return [];
    return diasDetalle.map(function (day) {
      var free = day.capacidad - day.ocupados;
      return {
        dia: day.dia,
        // A day with appointments shows how many under its number, as in
        // Pacientes de hoy's month calendar: a free working day and a day
        // with two booked were the same green cell (pinamar-turnos 03.11).
        count: Math.max(0, parseInt(day.ocupados, 10) || 0),
        className: free <= 0 ? "medcal-lleno" : (free <= 3 ? "medcal-justo" : "medcal-libre"),
        title: day.capacidad
          ? Tn("admin.content.catalog.doctors.calendar_day_occupied", day.capacidad, { ocupados: day.ocupados, capacidad: day.capacidad }, "{ocupados} de {capacidad} turno ocupado", "{ocupados} de {capacidad} turnos ocupados")
          : Tn("admin.content.catalog.doctors.calendar_day_no_schedule", day.ocupados, { ocupados: day.ocupados }, "{ocupados} turno sin agenda cargada ese día", "{ocupados} turnos sin agenda cargada ese día")
      };
    });
  }

  // Agrupa preservando el orden que ya trae el server (turno_dt asc).
  function medCalAgrupar(turnos) {
    var grupos = [], porDia = {};
    turnos.forEach(function (t) {
      if (!porDia[t.dia_iso]) { porDia[t.dia_iso] = []; grupos.push(t.dia_iso); }
      porDia[t.dia_iso].push(t);
    });
    return grupos.map(function (diaIso) { return { diaIso: diaIso, turnos: porDia[diaIso] }; });
  }

  function medCalFiltered(turnos) {
    var q = String(MEDCAL_FILTERS.q || "").toLocaleLowerCase("es");
    return turnos.filter(function (t) {
      if (MEDCAL_FILTERS.servicio && t.servicio !== MEDCAL_FILTERS.servicio) return false;
      if (MEDCAL_FILTERS.centro && t.centro !== MEDCAL_FILTERS.centro) return false;
      if (!q) return true;
      return [t.paciente, t.dni, t.contacto, t.servicio, t.centro].some(function (v) {
        return String(v || "").toLocaleLowerCase("es").indexOf(q) !== -1;
      });
    });
  }

  // The search shows on a list worth filtering (or once typed);
  // each select follows the shared filter rule (Filters.offerFilter: the
  // list's length, two values, or already set) and is named by its field.
  function medCalFiltersHtml(turnos, extra) {
    var rows = turnos.length;
    var searchable = Filters.filtersWorth(rows) || !!MEDCAL_FILTERS.q;
    function values(key) {
      var vals = [];
      turnos.forEach(function (t) { if (t[key] && vals.indexOf(t[key]) === -1) vals.push(t[key]); });
      return vals.sort(function (a, b) { return String(a).localeCompare(String(b), "es"); });
    }
    function select(key, label) {
      var vals = values(key), selected = MEDCAL_FILTERS[key];
      if (!filterOffered(vals.length, selected, rows)) return "";
      return '<select class="adm-sel" data-med-filter="' + key + '"><option value="">' + esc(label) + '</option>'
        + vals.map(function (v) { return '<option value="' + esc(v) + '"' + (v === selected ? " selected" : "") + ">" + esc(v) + "</option>"; }).join("")
        + "</select>";
    }
    var html = (searchable ? '<input type="search" class="cnt-search" data-med-filter="q" placeholder="' + esc(T("admin.content.catalog.doctors.calendar_search_placeholder", null, "Paciente o DNI")) + '" value="' + esc(MEDCAL_FILTERS.q) + '">' : "")
      + select("servicio", T("admin.content.catalog.columns.service", null, "Servicio"))
      + select("centro", T("admin.content.catalog.columns.center", null, "Centro"));
    html += extra || "";
    return html ? '<div class="medcal-filters">' + html + "</div>" : "";
  }

  function medCalDayHtml(g, hsiUrl) {
    // Crear turno acá adentro (responsable del producto, 2026-07-24): el mismo modal de la mesa,
    // con el médico y el día ya puestos — es de quien están hablando. El link
    // a HSI queda como salida de emergencia (ahí se registra igual, vía push),
    // en texto: la acción de esta pantalla es la primera.
    var create = '<button type="button" class="btn btn-quiet btn-sm medcal-create" data-med-crear="' + esc(g.diaIso) + '">' + esc(T("admin.content.catalog.doctors.calendar_create_appointment", null, "Crear turno")) + '</button>';
    // The day on HSI's A4 sheet («Turnos programados», /admin/cola/print).
    var medId = MEDCAL_DATA && MEDCAL_DATA.medico && MEDCAL_DATA.medico.id;
    if (medId && (g.turnos || []).length) {
      create = '<a class="btn btn-quiet btn-sm medcal-print" data-med-imprimir="' + esc(g.diaIso) + '" href="/admin/cola/print?dia='
        + esc(encodeURIComponent(g.diaIso)) + '&amp;medico=' + esc(encodeURIComponent(medId)) + '&amp;auto=1" target="_blank" rel="noopener">'
        + esc(T("admin.queue.print.print", null, "Imprimir")) + '</a>' + create;
    }
    if (hsiUrl) {
      create += '<a class="medcal-hsi" href="' + esc(hsiUrl) + '" target="_blank" rel="noopener" title="' + esc(T("admin.content.catalog.doctors.calendar_hsi_title", null, "HSI es el sistema de registro")) + '">' + esc(T("admin.content.catalog.doctors.calendar_create_in_hsi", null, "Crear en HSI")) + '</a>';
    }
    return '<div class="mt-day" data-med-day="' + esc(g.diaIso) + '"><div class="mt-day-head"><h2 class="mt-day-h">' + esc(medCalGrupoLabel(g.diaIso)) + '</h2><span class="mt-day-acts">' + create + "</span></div>" + medCalDiaTable(g.turnos, g.diaIso) + "</div>";
  }

  function renderMedicoCalendario(host, data) {
    MEDCAL_DATA = data;
    var allTurnos = data.turnos || [];
    var turnos = medCalFiltered(allTurnos);
    var dias = data.dias || 30;
    // Tier 5 slice 3: la grilla ya no se arma acá (es el island #med-cal-grid,
    // actualizado por separado — ver medCalUpdateGrid) pero SIGUE contando
    // para el estado vacío: un médico con agenda pero sin turnos igual
    // muestra la grilla, así que "sin nada que mostrar" es "ni turnos NI
    // días con agenda", no sólo "sin turnos".
    var hayGrilla = !!(data.dias_detalle && data.dias_detalle.length);
    if (!turnos.length && !hayGrilla) {
      host.className = "adm-empty";
      host.textContent = T("admin.content.catalog.doctors.calendar_no_upcoming", { dias: dias }, "Sin turnos asignados en los próximos {dias} días.");
      return;
    }
    host.className = "";
    // «Descargar» sits in the filters' row: the days listed below with the
    // filters above, as a file.
    var download = turnos.length && Table && Table.exportHtml ? Table.exportHtml({ scope: "#med-cal" }) : "";
    var html = medCalFiltersHtml(allTurnos, download);
    if (!turnos.length) {
      html += '<p class="cnt-hint">' + esc(T("admin.content.catalog.doctors.calendar_no_upcoming", { dias: dias }, "Sin turnos asignados en los próximos {dias} días.")) + "</p>";
    }
    var groups = medCalAgrupar(turnos);
    if (MEDCAL_SELECTED_DAY && !groups.some(function (g) { return g.diaIso === MEDCAL_SELECTED_DAY; })) {
      groups.unshift({ diaIso: MEDCAL_SELECTED_DAY, turnos: [] });
    }
    groups.forEach(function (g) { html += medCalDayHtml(g, data.hsi_url); });
    host.innerHTML = html;
  }

  // ---- solicitud del paciente en un panel superpuesto (responsable del producto, 2026-07-20:
  // "make the patients clickable, and you can see their application") --------
  var SOLICITUDES_CACHE = null;

  function fetchSolicitudesTodas() {
    if (SOLICITUDES_CACHE) return Promise.resolve(SOLICITUDES_CACHE);
    return fetch("/api/admin/requests", { credentials: "same-origin" }).then(function (res) {
      return afterSessionGate(res, function () {
        if (res.status === 401) throw new Error("401");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json();
      });
    }).then(function (j) {
      SOLICITUDES_CACHE = j.solicitudes || [];
      return SOLICITUDES_CACHE;
    });
  }

  function solCampoRows(campos) {
    if (!campos) return "";
    var labels = { dni: "DNI", paciente: T("admin.content.catalog.doctors.request_field_patient", null, "Paciente"),
      prepaga: T("admin.content.catalog.doctors.request_field_insurance", null, "Prepaga"),
      afiliado: T("admin.content.catalog.doctors.request_field_member_number", null, "N° afiliado"),
      medico: T("admin.content.catalog.doctors.request_field_doctor", null, "Médico"),
      matricula: T("admin.content.catalog.doctors.request_field_license", null, "Matrícula"),
      especialidad: T("admin.content.catalog.doctors.col_specialty", null, "Especialidad"),
      diagnostico: T("admin.content.catalog.doctors.request_field_diagnosis", null, "Diagnóstico"),
      practicas: T("admin.content.catalog.doctors.request_field_procedures", null, "Prácticas"),
      fecha: T("admin.content.catalog.doctors.request_field_date", null, "Fecha") };
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

  function solicitudModalHtml(s) {
    var nombre = ((s.nombre || "") + " " + (s.apellido || "")).trim() || T("admin.content.catalog.common.no_name_fallback", null, "(sin nombre)");
    var ai = s.ai_json || {};
    var estado = { pendiente: T("admin.content.catalog.doctors.request_status_pending", null, "Pendiente"), aprobada: T("admin.content.catalog.doctors.request_status_approved", null, "Aprobada"), rechazada: T("admin.content.catalog.doctors.request_status_rejected", null, "Rechazada"), cancelada: T("admin.content.catalog.doctors.request_status_cancelled", null, "Cancelada") }[s.estado] || s.estado || "";
    var head = '<div class="solmod-head"><b>' + esc(nombre) + "</b>"
      + '<span class="cnt-muted">' + (Format.dniHtml(s.dni, "DNI ") || "DNI " + esc(s.dni || "—")) + "</span>"
      + '<span class="cnt-muted">' + esc(estado) + "</span>"
      + '<button type="button" class="solmod-x hr-x" aria-label="' + esc(T("admin.content.catalog.common.close_label", null, "Cerrar")) + '"></button></div>';
    var svcNom = window.svcNombre ? window.svcNombre(s.servicio_id) : (s.servicio_id || "");
    var svc = '<p class="solmod-svc">' + esc(svcNom)
      + (s.contacto ? ' · <span class="cnt-muted">' + Format.contactoIconoHtml(s.contacto) + "</span>" : "") + "</p>";
    var campos = solCampoRows(ai.campos);
    var resumen = !campos && ai.resumen ? '<p class="cnt-hint">' + esc(ai.resumen) + "</p>" : "";
    var orden = "";
    if (s.tiene_orden && s.orden_url) {
      orden = (s.orden_mime || "").toLowerCase().indexOf("pdf") !== -1
        ? '<embed class="solmod-orden" src="' + esc(s.orden_url) + '#toolbar=0&navpanes=0" type="application/pdf">'
        : '<img class="solmod-orden" src="' + esc(s.orden_url) + '" alt="' + esc(T("admin.content.catalog.doctors.request_order_alt", { nombre: nombre }, "Orden de {nombre}")) + '">';
    }
    return '<div class="solmod-back"><div class="solmod">' + head + svc
      + '<div class="solmod-body' + (orden ? "" : " solmod-body--solo") + '">'
      + (orden ? '<div class="solmod-side">' + orden + "</div>" : "")
      + '<div class="solmod-campos">' + (campos || resumen || "") + "</div></div></div></div>";
  }

  function abrirSolicitud(id) {
    var version = mountVersion;
    fetchSolicitudesTodas().then(function (rows) {
      if (!current(version)) return;
      var s = null;
      rows.forEach(function (r) { if (String(r.id) === String(id)) s = r; });
      if (!s) return;
      var wrap = document.createElement("div");
      wrap.innerHTML = solicitudModalHtml(s);
      var back = wrap.firstChild;
      document.body.appendChild(back);
      var releaseEsc, releaseClick;
      var cerrar = ownRouteCleanup(function () {
        back.remove();
        if (releaseEsc) releaseEsc();
        if (releaseClick) releaseClick();
      });
      function onEsc(e) { if (e.key === "Escape") cerrar(); }
      releaseClick = listen(back, "click", function (e) { if (e.target === back || e.target.closest(".solmod-x")) cerrar(); });
      releaseEsc = listen(document, "keydown", onEsc);
    }).catch(function () { /* red caída: nada que mostrar */ });
  }

  // Tier 5 slice 3: empuja el estado actual (días clasificados + selección)
  // al island de la grilla. Islands.update() bufferea sola si el
  // mount() (async — dynamic import) todavía no terminó, así que no hay
  // carrera entre "llegó la data" y "terminó de montar el island".
  function medCalUpdateGrid(gridEl, data) {
    if (!gridEl || !Islands) return;
    Islands.update(gridEl, {
      dias: medCalGridDias(data && data.dias_detalle),
      selected: MEDCAL_SELECTED_DAY,
    });
  }

  // Fetch ÚNICO al renderizar el detalle (llamado una vez desde
  // wireMedicoForm) — cambiar de tab sólo muestra/oculta el panel ya
  // pintado, nunca vuelve a pedir.
  function wireMedicoCalendario(sec, item) {
    var host = $("#med-cal", sec);
    if (!host || !item || !item.id) return;
    var gridEl = $("#med-cal-grid", sec);
    var calendarVersion = mountVersion;
    // El click de un día pasa a vivir DENTRO del island (su propio DOM,
    // #med-cal-grid ya no es hijo de #med-cal) — esta callback es lo que
    // hoy hacía el branch `[data-med-date]` de abajo: guarda el día,
    // re-pinta las tablas del día (host, legacy) y hace scroll.
    function onDaySelect(diaIso) {
      if (!current(calendarVersion) || !host.isConnected) return;
      MEDCAL_SELECTED_DAY = diaIso;
      if (MEDCAL_DATA) renderMedicoCalendario(host, MEDCAL_DATA);
      if (gridEl && Islands) Islands.update(gridEl, { selected: MEDCAL_SELECTED_DAY });
      var block = host.querySelector('[data-med-day="' + MEDCAL_SELECTED_DAY + '"]');
      if (block) block.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (gridEl && Islands) {
      // onSelect no es serializable en data-props (JSON) — se pasa como
      // override programático; ver islands.mjs:mount(el, {props}).
      // Register teardown before the asynchronous island import/mount settles.
      // The shared owner cancels a pending claim as well as a mounted tree.
      ownRouteCleanup(function () { Islands.unmount(gridEl); });
      Promise.resolve(Islands.mount(gridEl, { props: { onSelect: onDaySelect } })).catch(function (error) {
        if (current(calendarVersion) && !isCancelled(error)) console.error("[catalog] calendar island failed", error);
      });
    }
    listen(host, "click", function (e) {
      var reload = e.target.closest("[data-catalog-calendar-retry]");
      if (reload) {
        e.preventDefault();
        if (reload.disabled || !current(calendarVersion) || !host.isConnected) return;
        reload.disabled = true;
        // The grid is a sibling of this host; retain its owner and unsaved form fields.
        cargarMedicoCalendario(sec, item, host, gridEl);
        return;
      }
      var retry = e.target.closest("[data-crear-turno-retry]");
      if (retry) {
        e.preventDefault();
        openCreateAppointment(createAppointmentIntent, createAppointmentHost);
        return;
      }
      var crear = e.target.closest("[data-med-crear]");
      if (crear) {
        openCreateAppointment({ medico: item.nombre || "", dia: crear.getAttribute("data-med-crear") }, host);
        return;
      }
      var sortBtn = e.target.closest("[data-med-sort]");
      if (sortBtn) {
        var sortDay = sortBtn.getAttribute("data-dia"), key = sortBtn.getAttribute("data-med-sort");
        var prev = MEDCAL_SORTS[sortDay];
        MEDCAL_SORTS[sortDay] = { key: key, dir: prev && prev.key === key && prev.dir === "asc" ? "desc" : "asc" };
        renderMedicoCalendario(host, MEDCAL_DATA);
        return;
      }
      var a = e.target.closest("[data-solicitud]");
      if (a) { e.preventDefault(); abrirSolicitud(a.getAttribute("data-solicitud")); }
    });
    listen(host, "input", function (e) {
      var f = e.target.closest('input[data-med-filter="q"]');
      if (!f) return;
      MEDCAL_FILTERS.q = f.value;
      renderMedicoCalendario(host, MEDCAL_DATA);
      var next = host.querySelector('input[data-med-filter="q"]');
      if (next) { next.focus(); next.setSelectionRange(next.value.length, next.value.length); }
    });
    listen(host, "change", function (e) {
      var f = e.target.closest("select[data-med-filter]");
      if (!f) return;
      MEDCAL_FILTERS[f.getAttribute("data-med-filter")] = f.value;
      renderMedicoCalendario(host, MEDCAL_DATA);
    });
    // The mounted doctor editor owns its post-save refresh.  The appointment
    // overlay receives this callback directly; no compatibility window hook.
    var calendar = {
      refresh: function () {
        if (current(calendarVersion) && host.isConnected) return cargarMedicoCalendario(sec, item, host, gridEl);
      }
    };
    currentCalendar = calendar;
    ownRouteCleanup(function () {
      if (currentCalendar === calendar) currentCalendar = null;
    });
    cargarMedicoCalendario(sec, item, host, gridEl);
  }

  function cargarMedicoCalendario(sec, item, host, gridEl) {
    var url = "/api/admin/doctors/" + encodeURIComponent(item.id) + "/appointments?days=30";
    return fetch(url, { credentials: "same-origin" }).then(function (res) {
      return afterSessionGate(res, function () {
        if (res.status === 401) throw new Error("401");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json();
      });
    }).then(function (data) {
      var tab = sec.querySelector('[data-tab="calendario"]');
      if (tab) {
        tab.classList.toggle("has-appointments", !!data.total);
        tab.classList.toggle("is-empty", !data.total);
        tab.setAttribute("data-count", data.total || "");
        tab.title = data.total ? (data.total === 1
          ? T("admin.content.catalog.doctors.calendar_tab_count_one", { n: data.total }, "{n} turno")
          : T("admin.content.catalog.doctors.calendar_tab_count", { n: data.total }, "{n} turnos")) : T("admin.content.catalog.doctors.calendar_tab_empty", null, "Sin turnos asignados; abrí el calendario para crear uno");
      }
      renderMedicoCalendario(host, data);
      medCalUpdateGrid(gridEl, data);
    }).catch(function (e) {
      if (isCancelled(e) || disposed || !host.isConnected) return;
      if (String(e.message) === "redirect") return;
      host.className = "";
      host.innerHTML = loadErrorWithRetry(T("admin.content.catalog.doctors.calendar_load_failed", null, "No se pudo cargar el calendario. Reintentá."), "data-catalog-calendar-retry");
    });
  }

  // A tab row wider than the screen (a doctor's card on a phone: five tabs,
  // ~600px in 358) keeps one row and scrolls, but never shows a tab cut by
  // the screen edge as if it were whole (ui-expert 2026-11-15: «Informaci»):
  // the side that has more fades out (data-more), and the row snaps to whole
  // tabs. Desktop rows that fit get no attribute and no fade.
  function wireTabEdges(row) {
    if (!row) return;
    function update() {
      var max = row.scrollWidth - row.clientWidth;
      var more = [];
      if (max > 1 && row.scrollLeft > 1) more.push("start");
      if (max > 1 && row.scrollLeft < max - 1) more.push("end");
      if (more.length) row.setAttribute("data-more", more.join(" "));
      else row.removeAttribute("data-more");
    }
    listen(row, "scroll", update);
    if (typeof ResizeObserver === "function") {
      // The row and each tab: a count badge or the web font arriving widens
      // the content without resizing the row's own box.
      var ro = new ResizeObserver(update);
      ro.observe(row);
      Array.prototype.forEach.call(row.children, function (tab) { ro.observe(tab); });
      ownRouteCleanup(function () { ro.disconnect(); });
    }
    update();
  }

  function wireMedicoTabs(sec) {
    var tabs = $$(".cnt-tab", sec);
    var panels = $$(".cnt-tabpanel", sec);
    // Guardar/Eliminar/Restaurar (toda la fila .cnt-actions) pertenecen al
    // form (Horarios de atención / Información básica); bajo Calendario —
    // sólo lectura — no hay nada que guardar ni borrar, así que la fila
    // entera se esconde mientras ese tab está activo. Es la fila HIJA DIRECTA
    // del editor: la cuenta del médico (pestaña Usuario) trae su propia
    // .cnt-actions anidada, antes en el DOM, y elegir «la primera» escondía
    // los botones de la cuenta y dejaba Guardar a la vista (prod 2026-10-19).
    function acciones(name) {
      var soloLectura = name === "calendario" || name === "usuario" || name === "intake";
      var row = $(":scope > .cnt-actions", sec);
      if (row) row.style.display = soloLectura ? "none" : "";
      // The AI translation caption over «Guardar» goes with the one tab
      // that has a translated field (Información básica → specialty,
      // core/pack_l10n.FIELDS); below the form it moves no tab.
      var aviso = $(":scope > .cnt-ai-notice", sec);
      if (aviso) aviso.hidden = name !== "info";
    }
    tabs.forEach(function (tab) {
      listen(tab, "click", function (e) {
        e.preventDefault();
        var name = tab.getAttribute("data-tab");
        tabs.forEach(function (t) {
          var on = t === tab;
          t.classList.toggle("on", on);
          t.setAttribute("aria-selected", on ? "true" : "false");
        });
        panels.forEach(function (p) {
          p.style.display = p.getAttribute("data-tabpanel") === name ? "" : "none";
        });
        // The row scrolls sideways on a phone: the chosen tab comes fully
        // into view (the row snaps to whole tabs).
        if (tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
        acciones(name);
      });
    });
    if (tabs.length) wireTabEdges(tabs[0].parentNode);
    var activa = tabs.filter(function (t) { return t.classList.contains("on"); })[0];
    if (activa) acciones(activa.getAttribute("data-tab"));
  }

  // Repinta los combos de servicio ya dibujados sin re-renderizar el editor
  // entero: el fetch vuelve DESPUÉS del primer render y rehacer los centros
  // perdería lo que el operador esté tipeando en otra fila. Se reconstruye el
  // control (not only its data-items) because admin-combo.mjs sizes the input
  // según el contenido REAL, y en el primer render la lista todavía estaba
  // vacía: quedaba de 18ch y cortaba "Cardiología · Hospital de Pinamar"
  // (hook control-content-fits). La fila que el operador tenga enfocada se
  // deja como está, para no arrancarle el control de abajo del cursor.
  function pintarServiciosFranja(sec) {
    $$(".cnt-franja-svc", sec).forEach(function (fila) {
      if (fila.contains(document.activeElement)) return;
      var hid = fila.querySelector('input[type="hidden"]');
      var ci = +fila.getAttribute("data-servicio-ci");
      var fi = +fila.getAttribute("data-servicio-fi");
      var centro = MED_STATE && MED_STATE.centros[ci] ? MED_STATE.centros[ci] : null;
      var fr = centro && centro.franjas[fi] ? centro.franjas[fi] : {};
      var actual = hid && hid.value ? hid.value : (fr.servicio || "");
      // innerHTML, no outerHTML: la celda ES una columna de la grilla — si se
      // reemplaza entera, la fila pierde su lugar y su etiqueta en angosto.
      fila.innerHTML = franjaServicioHtml(
        { servicio: actual }, centro ? centro.centro : "", ci, fi);
    });
  }

  function cargarServiciosCompatibles(sec, item) {
    var it = item || {};
    if (window.CURRENT_ROLE === "medico") {
      SVC_COMPAT = it._servicios_compatibles || [];
      SVC_COMPAT_READY = true;
      pintarServiciosFranja(sec);
      return Promise.resolve();
    }
    var espEl = $("#med-especialidad", sec);
    var esp = (espEl && espEl.value) || it.especialidad || "";
    // Un médico nuevo todavía no tiene id: el backend resuelve la lista con
    // ?especialidad= y devuelve la misma forma.
    var url = "/api/admin/doctors/" + encodeURIComponent(it.id || "nuevo")
      + "/compatible-services?especialidad=" + encodeURIComponent(esp);
    return fetch(url, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { items: [] }; })
      .then(function (j) {
        SVC_COMPAT = (j && j.items) || [];
        SVC_COMPAT_READY = true;
        pintarServiciosFranja(sec);
      })
      .catch(function () { /* sin lista el combo queda vacío: la franja sigue guardándose */ });
  }

  function wireMedicoForm(sec, item) {
    wireCentrosEditor(sec);
    wireMedicoTabs(sec);
    wireMedicoCalendario(sec, item);
    wireMedicoIntake(sec, item);
    cargarServiciosCompatibles(sec, item);
    var userPanel = $("#med-user-panel", sec);
    var userReady = Promise.resolve();
    var userVersion = mountVersion;
    function userFailed() {
      if (!current(userVersion) || !userPanel || !userPanel.isConnected) return;
      userPanel.innerHTML = '<p class="adm-empty" role="alert">' +
        esc(T("admin.content.catalog.doctors.user_load_failed", null, "No se pudo cargar el acceso del médico.")) +
        ' <button class="btn btn-quiet" type="button" data-med-user-reload>' +
        esc(T("admin.content.catalog.doctors.user_reload", null, "Recargar")) +
        "</button></p>";
      listen($("[data-med-user-reload]", userPanel), "click", function () { location.reload(); });
    }
    if (userPanel && item && item.id && Users) {
      var userOwner = Users.mountDoctorUser(userPanel, item.id, item);
      MED_USER_OWNER = userOwner;
      ownRouteCleanup(function () { if (MED_USER_OWNER === userOwner) MED_USER_OWNER = null; });
      ownRouteCleanup(function () { userOwner.dispose(); });
      userReady = userOwner.ready.catch(userFailed);
    } else if (userPanel && item && item.id) {
      userFailed();
    }
    var esp = $("#med-especialidad", sec);
    var esp2 = $("#med-especialidad-2", sec);
    if (esp2) {
      listen(esp2, "change", function () {
        $("#med-cluster-chip-2", sec).innerHTML = esp2.value ? clusterChipHtml(especialidadCluster(esp2.value), esp2.value) : "";
        cargarServiciosCompatibles(sec, item);
      });
    }
    if (esp) {
      listen(esp, "change", function () {
        $("#med-cluster-chip", sec).innerHTML = clusterChipHtml(especialidadCluster(esp.value), esp.value);
        // La compatibilidad se calcula contra la especialidad: si cambia,
        // cambia el orden sugerido (y para un médico nuevo, la lista entera).
        cargarServiciosCompatibles(sec, item);
      });
    }
    wireFotoEditor(sec, item, "med", "doctors");
    return userReady;
  }

  var MED_USER_OWNER = null;

  // After «Nuevo médico» saves: the account outcome, inline under the actions.
  function showCreatedAccount(sec, medicoId, cuenta, version) {
    var host = $("#med-account-outcome", sec);
    if (!host || !Users || !Users.showInviteOutcome) return;
    function isCurrent() { return current(version) && host.isConnected; }
    // The host sits at the top of the editor; bring it into view and focus
    // it so the outcome is the first thing the admin (and a reader) gets.
    function reveal() {
      if (!isCurrent()) return;
      if (host.scrollIntoView) host.scrollIntoView({ block: "nearest" });
      host.focus({ preventScroll: true });
    }
    if (cuenta.email_enviado || cuenta.enlace) {
      ownRouteCleanup(Users.showInviteOutcome(host, cuenta, { isCurrent: isCurrent }));
      reveal();
      return;
    }
    var outcome = '<p class="doctor-user-outcome" role="status">'
      + esc(T("admin.content.catalog.doctors.account_created", { usuario: cuenta.usuario || "" }, "Se creó su cuenta {usuario}, todavía sin invitar."))
      + "</p>";
    // «Invitar» va por /api/admin/users (permiso `usuarios`): quien crea el
    // médico sin ese permiso ve el aviso, no un botón que respondería 403.
    if (!puede("usuarios")) { host.innerHTML = outcome; reveal(); return; }
    host.innerHTML = outcome + '<div class="doctor-user-link-actions"><button type="button" class="btn btn-ok" id="med-account-invite">'
      + esc(T("admin.content.catalog.doctors.invite_submit", null, "Invitar")) + "</button></div>";
    reveal();
    var button = $("#med-account-invite", host);
    listen(button, "click", function () {
      if (!isCurrent() || button.disabled) return;
      button.disabled = true;
      Users.inviteDoctor(medicoId).then(function (result) {
        if (!isCurrent()) return;
        ownRouteCleanup(Users.showInviteOutcome(host, result, { isCurrent: isCurrent }));
        if (MED_USER_OWNER && MED_USER_OWNER.render) MED_USER_OWNER.render();
      }).catch(function (err) {
        if (!isCurrent()) return;
        button.disabled = false;
        toast(errText(err, T("admin.content.catalog.common.action_failed", null, "No se pudo completar la acción.")));
      });
    });
  }

  function collectMedico(it, sec) {
    syncCentrosFromDom(sec);
    var doc = Object.assign({}, it || {});
    doc.nombre_pila = $("#med-nombre-pila", sec).value.trim();
    doc.apellido = $("#med-apellido", sec).value.trim();
    var email = $("#med-email", sec).value.trim();
    // An emptied email of a saved doctor travels as "": its account's email
    // empties with it (one email per doctor, 2026-11-03).
    if (email) doc.email = email; else if (it && it.id) doc.email = ""; else delete doc.email;
    doc.especialidad = $("#med-especialidad", sec).value;
    var esp2 = $("#med-especialidad-2", sec);
    if (esp2 && esp2.value) doc.especialidad_2 = esp2.value; else delete doc.especialidad_2;
    delete doc.cluster_2;   // idem: el server lo deriva de la segunda especialidad
    var palabras = $("#med-palabras", sec).value.split(/[,\n]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    if (palabras.length) doc.palabras = palabras; else delete doc.palabras;
    delete doc.cluster;   // el server lo deriva SIEMPRE de la especialidad
    doc.centros = (MED_STATE.centros || [])
      .filter(function (c) { return c.centro; })
      .map(function (c) {
        var out = {
          centro: c.centro,
          franjas: (c.franjas || []).filter(function (f) { return f.dias && f.dias.length; }),
          excepciones: (c.excepciones || []).map(function (e) { return Object.assign({}, e); })
        };
        if (c.dias_anticipacion) out.dias_anticipacion = c.dias_anticipacion;
        return out;
      });
    delete doc.centro;    // el server los re-deriva de centros[0] (centro principal)
    delete doc.agenda;
    // Service-managed schedule: the form shows it read-only, so the server
    // must keep the stored one rather than judge what this form rebuilt.
    if (it && it._agendas_servicio && it._agendas_servicio.length) doc._agenda_sin_cambios = true;
    else delete doc._agenda_sin_cambios;
    delete doc.demo;   // los médicos de demostración se dieron de baja (2026-07-16); el admin ya no ofrece marcar uno
    var nota = $("#med-nota", sec).value.trim();
    if (nota) doc.nota = nota; else delete doc.nota;
    delete doc.demo_nota;
    collectBookingWindow(doc, "med");
    var medTurnosMax = ($("#med-turnos-max", sec) || {}).value;
    if (String(medTurnosMax || "").trim()) doc.turnos_activos_max = +medTurnosMax;
    else delete doc.turnos_activos_max;
    return doc;
  }

  var FORM = {
    servicios: { render: formServicio, collect: collectServicio },
    centros: { render: formCentro, collect: collectCentro },
    medicos: { render: formMedico, collect: collectMedico }
  };

  function wireHorarioPreview(sec) {
    var input = $("#svc-horario", sec);
    var out = $("#svc-horario-preview", sec);
    if (!input || !out) return;
    var mostrarError = false;
    function update() {
      var H = Hours;
      if (!H) { out.textContent = ""; return; }
      var valor = input.value.trim();
      if (!valor) {
        out.textContent = T("admin.content.catalog.services.schedule_preview_empty", null, "Escribí cuándo atiende. Ejemplo: Lunes a viernes de 8 a 20 h.");
        out.className = "cnt-preview";
        return;
      }
      var e = H.estado(valor);
      var frase = e ? H.frase(e) : null;
      if (frase) { out.textContent = T("admin.content.catalog.services.schedule_preview_label", { frase: frase }, "Vista previa: {frase}"); out.className = "cnt-preview"; }
      else if (mostrarError) {
        out.textContent = T("admin.content.catalog.services.schedule_preview_error", null, "No pude interpretar el horario. Probá con: Lunes a viernes de 8 a 20 h.");
        out.className = "cnt-preview cnt-preview-warn";
      } else {
        out.textContent = T("admin.content.catalog.services.schedule_preview_incomplete", null, "Completá los días y las horas.");
        out.className = "cnt-preview";
      }
    }
    listen(input, "input", function () { mostrarError = false; update(); });
    listen(input, "blur", function () { mostrarError = !!input.value.trim(); update(); });
    update();
  }

  // El `modo` del catálogo es la combinación de las dos primeras perillas
  // (ver formServicio). Una sola función lo deriva para todos los que lo
  // necesitan (guardar, regenerar el cartel), así el mapeo vive en un lugar.
  function modoActual(sec) {
    var t = $("#svc-c-turno", sec), o = $("#svc-c-orden", sec);
    var conTurno = !!(t && t.checked), conOrden = !!(o && o.checked);
    return conTurno ? (conOrden ? "solicitud_con_orden" : "solicitud")
                    : (conOrden ? "walkin_con_orden" : "walkin");
  }

  function wireModoToggle(sec) {
    var wrap = $("#svc-agenda-wrap", sec);
    if (!wrap) return;
    var aprob = $("#svc-c-aprobacion", sec);
    var mismo = $("#svc-c-mismodia", sec);
    function apagar(input, off) {
      if (!input) return;
      input.disabled = off;
      var lbl = input.closest(".cnt-modo-op");
      if (lbl) {
        lbl.classList.toggle("cnt-modo-op--off", off);
        lbl.style.display = off ? "none" : "";
      }
    }
    function sync() {
      var conTurno = $("#svc-c-turno", sec).checked;
      // Hide unavailable properties without losing the user's stored choice.
      apagar(aprob, !conTurno);
      // Y al revés: con turno, abrir "el mismo día" no significa nada — el
      // turno ya se saca con anticipación.
      apagar(mismo, conTurno);
      // Compute from this mode's availability, not the previous disabled state.
      var reserva = conTurno || (mismo && mismo.checked && !mismo.disabled);
      wrap.style.display = reserva ? "" : "none";
      var limites = $("#svc-limits-wrap", sec);
      if (limites) limites.style.display = reserva ? "" : "none";
      $$(".cnt-modo-op", sec).forEach(function (l) {
        var i = l.querySelector("input");
        if (i) l.classList.toggle("on", i.checked && !i.disabled);
      });
    }
    $$("#svc-modo-list input[type=checkbox]", sec).forEach(function (c) {
      listen(c, "change", sync);
    });
    sync();
  }

  function wireCartelRegen(sec) {
    var link = $("#svc-cartel-regen", sec);
    if (!link) return;
    listen(link, "click", function (e) {
      e.preventDefault();
      var modo = modoActual(sec);
      var horario = $("#svc-horario", sec).value;
      var slug = $("#svc-centro", sec).value;
      var cartel = $("#svc-cartel", sec);
      cartel.value = cartelTemplate(modo, horario, centroNombre(slug));
      autoGrowTextarea(cartel);
    });
  }

  function autoGrowTextarea(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.max(el.scrollHeight, 62) + "px";
  }

  function wireAutoGrowTextareas(sec) {
    $$("textarea.cnt-textarea", sec).forEach(function (el) {
      autoGrowTextarea(el);
      listen(el, "input", function () { autoGrowTextarea(el); });
    });
  }

  function wireServiceAnnouncement(sec) {
    var check = $("#svc-announcement-active", sec);
    if (!check) return;
    function update() {
      var dependent = $("#svc-announcement-dependent", sec);
      if (dependent) dependent.hidden = !check.checked;
    }
    listen(check, "change", update);
    update();
  }

  function wireServiceFeature(sec) {
    var check = $("#svc-featured", sec);
    if (!check) return;
    function update() {
      var dependent = $("#svc-feature-dependent", sec);
      if (dependent) dependent.hidden = !check.checked;
    }
    listen(check, "change", update);
    update();
  }

  // The raw webdata JSON is a support tool: only the admin sees it, a doctor
  // or an editor never does (pinamar-turnos 27.10, m1101291).
  function renderOriginal(sec, tipo, item) {
    if (!esAdmin() || !item || item._origen !== "json+db") return;
    var box = document.createElement("details");
    box.className = "cnt-original";
    var summary = "<summary>" + esc(T("admin.content.catalog.common.view_original_webdata", null, "Ver el original de webdata")) + "</summary>";
    box.innerHTML = summary + '<p class="cnt-hint">' + esc(T("admin.content.catalog.common.loading", null, "Cargando…")) + '</p>';
    sec.appendChild(box);
    rawOriginal(tipo, idOf(tipo, item)).then(function (orig) {
      box.innerHTML = summary +
        (orig ? "<pre class=\"cnt-pre\">" + esc(JSON.stringify(orig, null, 2)) + "</pre>" : '<p class="cnt-hint">' + esc(T("admin.content.catalog.common.original_not_found", null, "No se encontró el original.")) + '</p>');
    }).catch(function () {
      box.innerHTML = summary + '<p class="cnt-hint">' + esc(T("admin.content.catalog.common.original_load_failed", null, "No se pudo cargar el original.")) + '</p>';
    });
  }

  // A medico opening their own ficha whose schedule is owned by the service
  // gets a fully read-only form (tabs suppressed, agenda rendered by
  // managedScheduleHtml, info panel hidden): there is nothing Guardar could
  // save. wireMedicoTabs.acciones() already tolerates a missing row.
  function medicoFichaReadOnly(tipo, isNew, item) {
    return tipo === "medicos" && window.CURRENT_ROLE === "medico"
      && !isNew && item && (item._agendas_servicio || []).length;
  }

  function renderForm(tipo, id) {
    FOTO_PENDIENTE = null;   // no arrastrar la foto elegida en otro formulario
    var sec = $("#sec-" + tipo);
    sec.removeAttribute("data-list-view");
    sec.removeAttribute("data-disclosure");
    var isNew = id == null;
    return fetchTipo(tipo).then(function (data) {
      var needCentros = tipo === "servicios" || tipo === "medicos" || tipo === "centros";
      var needEsp = tipo === "medicos" || tipo === "servicios";
      return Promise.all([
        needCentros ? ensureCentros() : Promise.resolve(),
        needEsp ? loadEspecialidadesYClusters() : Promise.resolve()
      ]).then(function () { return data; });
    }).then(function (data) {
      if (data.noBackend) {
        sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.catalog.list.no_backend", null, "El backend de contenido no está desplegado todavía.")) + '</p>';
        return;
      }
      var item = isNew ? null : data.items.filter(function (x) { return String(idOf(tipo, x)) === String(id); })[0];
      if (!isNew && !item) {
        sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.catalog.common.not_found", { tipo: TIPOS[tipo].singular }, "No se encontró ese {tipo}.")) + '</p>';
        return;
      }
      var titulo = isNew ? T("admin.content.catalog.common.new_entity_title", { tipo: TIPOS[tipo].singular }, "Nuevo {tipo}")
        : (tipo === "medicos" ? medNombre(item) : (item.nombre || item.name || T("admin.content.catalog.common.no_name_fallback", null, "(sin nombre)")));
      var fieldsHtml = FORM[tipo].render(item, data.items);

      // Breadcrumb ÚNICO + el nombre de la entidad como el h1 de esta página
      // (reusa la clase .adm-h1; el h1 compartido de la sección se oculta,
      // ver route()/setTitle()) — reemplaza el stack de antes (h1 "Médicos" +
      // crumb + h2 + "id: …") por una sola línea de contexto (responsable del producto, 2026-07-16).
      // El título de la ficha vive en la barra de la pantalla (#adm-h1), la
      // misma banda de 44px que usan las listas — no repetido adentro del
      // contenido (rediseño 2026-08-04). La especialidad acompaña al nombre
      // (the product owner, 2026-07-20: "show the breadcrumbs, but also add the
      // specialization here") — contexto que el crumb "Médicos" solo no da.
      var sub = tipo === "medicos" && item && item.especialidad
        ? ' <span class="cnt-h1-sub">' + esc(item.especialidad) + "</span>" : "";
      var h1 = $("#adm-h1");
      if (h1) {
        h1.style.display = "";
        h1.innerHTML = '<a class="adm-crumb" href="' + esc(listHref(tipo)) + '">' +
          esc(TITLES[tipo]) + '</a><span class="adm-crumb-sep">/</span>' +
          esc(titulo) + sub;
      }
      // What creating the doctor did with its account (Médico = usuario): at
      // the top, under the title, where the admin is looking after «Guardar»
      // — below the tabs it landed ~1,370px down (prod 2026-10-19).
      var html = tipo === "medicos" ? '<div id="med-account-outcome" class="doctor-user-host" tabindex="-1"></div>' : "";
      html += '<form id="cnt-form">' + fieldsHtml + "</form>";
      html += '<p id="cnt-save-status" class="cnt-msg" role="status" aria-live="polite" hidden></p>';
      var readOnlyFicha = medicoFichaReadOnly(tipo, isNew, item);
      // Guardar (izquierda) y Eliminar/Restaurar (derecha) en la MISMA fila
      // de acciones (responsable del producto, 2026-07-16: Eliminar era un link rojo suelto
      // abajo a la izquierda). Guardar vive FUERA de <form> pero sigue
      // enviándolo vía el atributo form="cnt-form" — así puede compartir fila
      // con Eliminar/Restaurar, que nunca deben mandar el form (Eliminar es
      // type=button: muta estado por su cuenta, no es un submit).
      if (!readOnlyFicha) {
        html += translationNoticeHtml();
        html += '<div class="cnt-actions">';
        // Guardar es la acción principal de la pantalla: el mismo control verde
        // que "Crear turno" en Solicitudes (hook: un control nuevo copia al que
        // ya hace ese trabajo). Antes era negro y no se parecía a ningún otro
        // botón principal del panel.
        html += '<button class="btn btn-accent" type="submit" form="cnt-form">' + esc(T("admin.content.catalog.common.save", null, "Guardar")) + '</button>';
        if (!isNew && puedeAltaBaja(tipo)) {
          html += '<span class="cnt-actions-danger">';
          if (item._origen === "json+db") html += '<button type="button" class="btn btn-quiet" id="cnt-restore">' + esc(T("admin.content.catalog.common.restore_original", null, "Restaurar original")) + '</button>';
          // "Deshabilitar", nunca "Eliminar": nada con turnos históricos se
          // borra (reunión hospital 2026-07-24). Una sola palabra para los tres
          // tipos — antes médicos decía "Desactivar" y el resto "Eliminar".
          html += '<button type="button" class="btn btn-quiet" id="cnt-del">' + esc(T("admin.content.catalog.common.disable", null, "Deshabilitar")) + '</button>';
          html += "</span>";
        }
        html += "</div>";
      }
      sec.innerHTML = html;

      wireHorarioPreview(sec);
      wireAutoGrowTextareas(sec);
      if (tipo === "servicios") { wireModoToggle(sec); wireCartelRegen(sec); wireServiceAgenda(sec); wireServiceAnnouncement(sec); wireServiceFeature(sec); }
      var childReady = tipo === "medicos" ? wireMedicoForm(sec, item) : Promise.resolve();
      if (tipo === "centros") wireFotoEditor(sec, item, "cen", "centers");
      if (!isNew) renderOriginal(sec, tipo, item);

      // The browser cannot point at an out-of-range number inside a closed
      // fold: «Guardar» would do nothing. `invalid` does not bubble, so each
      // folded control opens its own group.
      $$("details.cnt-fold input, details.cnt-fold select, details.cnt-fold textarea", sec).forEach(function (control) {
        listen(control, "invalid", function () {
          var fold = control.closest("details");
          if (fold && !fold.open) fold.open = true;
        });
      });

      listen($("#cnt-form", sec), "submit", function (e) {
        e.preventDefault();
        var btn = $('button[type="submit"]', sec);
        if (!btn || btn.disabled) return;
        if (!validateClosures(sec)) return;
        if (tipo === "medicos" && !validateDoctorHours(sec)) return;
        var doc = FORM[tipo].collect(item, sec);
        var pendingPhoto = FOTO_PENDIENTE;
        var photoFailed = false;
        var acknowledgement = null;
        var submitTarget = location.pathname + (location.search || "");
        btn.disabled = true;
        // A late rejection maps to a field only while this form remains the
        // current route. Navigation or re-rendering can replace it, and
        // writing feedback into that obsolete controller would be unsafe.
        var submitVersion = mountVersion;
        guardar(tipo, isNew ? null : idOf(tipo, item), doc, function (ack) {
          acknowledgement = ack;
          // This continuation belongs to the accepted create, not the form.
          // It remains owned while navigation replaces the stale UI.
          var ruta = tipo === "centros" ? "centers" : tipo === "medicos" ? "doctors" : null;
          var saved = ack.body && ack.body.item;
          if (!pendingPhoto || !ruta || !ack.ok || !ack.body || ack.body.ok !== true || !saved || !idOf(tipo, saved)) return null;
          if (FOTO_PENDIENTE === pendingPhoto) FOTO_PENDIENTE = null;
          return subirFotoSnapshot(ruta, idOf(tipo, saved), pendingPhoto).catch(function () {
            photoFailed = true;
          });
        }).then(function (saved) {
          // Acknowledging the submitted snapshot must not replace later
          // typing. Read the real controls again, including autofill which
          // need not dispatch an input/change event.
          var changedDuringSave = JSON.stringify(FORM[tipo].collect(item, sec)) !== JSON.stringify(doc);
          // Use only the acknowledged item, including server normalization.
          // A second GET failure must not turn a committed save into a failure.
          var items = data.items.filter(function (entry) { return String(idOf(tipo, entry)) !== String(idOf(tipo, saved)); });
          items.push(saved);
          CACHE[tipo] = { items: items, noBackend: false };
          var message = photoFailed
            ? T("admin.content.catalog.common.photo_upload_failed_after_save", null, "Se guardó, pero la foto no se pudo subir. Probá de nuevo desde el editor.")
            : T("admin.content.catalog.common.saved", null, "Guardado.");
          if (changedDuringSave) {
            // Even a create with a newer draft now owns a persisted identity:
            // retry updates that row rather than creating another document.
            item = saved;
            if (isNew) {
              id = idOf(tipo, saved);
              isNew = false;
              history.replaceState({}, "", itemHref(tipo, id));
            }
            btn.disabled = false;
            saveStatus(sec, T("admin.content.knowledge.cambios_sin_guardar", null, "Cambios sin guardar"));
            if (photoFailed) toast(message);
            return;
          }
          toast(message);
          var cuenta = acknowledgement && acknowledgement.body && acknowledgement.body.cuenta;
          if (isNew && tipo === "medicos" && cuenta) {
            // Médico = usuario: stay on the doctor just created and say what
            // happened to its account (email sent, link to copy, or created
            // «Sin invitar» with Invitar right there).
            var createdId = idOf(tipo, saved);
            history.replaceState({}, "", itemHref(tipo, createdId));
            var createdVersion = mountVersion + 1;
            return readyRoute().then(function () {
              if (current(createdVersion)) showCreatedAccount(sec, createdId, cuenta, createdVersion);
            });
          }
          if (isNew) {
            goList(tipo);
            return;
          }
          var savedVersion = mountVersion + 1;
          return readyRoute().then(function () {
            if (current(savedVersion)) saveStatus(sec, message);
          });
        }).catch(function (e2) {
          btn.disabled = false;
          var message = saveMessage(e2);
          if (isCancelled(e2) && acknowledgement) {
            var ack = acknowledgement;
            message = ack.ok && ack.body && ack.body.ok === true && ack.body.item && idOf(tipo, ack.body.item)
              ? (photoFailed ? T("admin.content.catalog.common.photo_upload_failed_after_save", null, "Se guardó, pero la foto no se pudo subir. Probá de nuevo desde el editor.") : T("admin.content.catalog.common.saved", null, "Guardado."))
              : saveMessage(new Error(ack.status === 401 ? "401" : (ack.body && ack.body.error) || ""));
          }
          if (current(submitVersion) && btn.isConnected) {
            if (showFieldError(tipo, sec, String(e2.message))) return;
            // A rejection this screen cannot pin to one control may be about
            // a folded field: put them all on screen next to the message.
            $$("details.cnt-fold", sec).forEach(function (fold) { fold.open = true; });
            saveStatus(sec, message, String(e2.message) === "401" || String(e2.message) === "demo_solo_lectura" ? submitTarget : null);
          }
          toast(message);
        });
      });

      var delA = $("#cnt-del", sec);
      if (delA) listen(delA, "click", function (e) {
        e.preventDefault();
        var pregunta = T("admin.content.catalog.common.confirm_disable", { tipo: TIPOS[tipo].singular }, "¿Deshabilitar este {tipo}? Deja de "
          + "aparecer para sacar turnos nuevos. Los turnos ya dados no se tocan.");
        if (!confirm(pregunta)) return;
        borrar(tipo, idOf(tipo, item)).then(function (r) {
          var n = (r && r.turnos) || 0;
          var msg = T("admin.content.catalog.common.disabled", null, "Deshabilitado.") + (n ? " " + Tn("admin.content.catalog.common.disabled_kept", n, null, "Se conserva {n} turno ya registrado.", "Se conservan {n} turnos ya registrados.") : "");
          toast(msg);
          return fetchTipo(tipo, true);
        }).then(function () { goList(tipo); }).catch(function (e2) {
          if (isCancelled(e2) || String(e2.message) === "redirect") return;
          var msg = errText(e2, T("admin.content.catalog.common.disable_failed", null, "No se pudo deshabilitar."));
          if (e2.enlace && sec.isConnected) disableBlocked(sec, msg, e2.enlace);
          toast(msg);
        });
      });
      var resA = $("#cnt-restore", sec);
      if (resA) listen(resA, "click", function (e) {
        e.preventDefault();
        if (!confirm(T("admin.content.catalog.common.confirm_restore", null, "¿Restaurar el original? Se pierde la edición hecha desde el admin."))) return;
        restaurar(tipo, idOf(tipo, item)).then(function () {
          toast(T("admin.content.catalog.common.restored", null, "Restaurado."));
          return fetchTipo(tipo, true);
        }).then(function () { goList(tipo); }).catch(function (e2) {
          if (!isCancelled(e2) && String(e2.message) !== "redirect") toast(errText(e2, T("admin.content.catalog.common.restore_failed", null, "No se pudo restaurar.")));
        });
      });
      return childReady;
    });
  }

  // =====================================================================
  // routing
  // =====================================================================

  function setNav(sec) {
    $$("#adm-nav a").forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-nav") === sec); });
  }

  // El h1 compartido (#adm-h1) es el título de una LISTA ("Médicos"). En una
  // vista de detalle (#medicos/<id>, #medicos/nuevo) el detalle se pone su
  // propio h1 con el nombre de la entidad (ver renderForm) — el compartido se
  // oculta para que la página tenga UN solo h1, nunca los dos apilados
  // (the product owner, 2026-07-16).
  // One screen header (the product owner, 2026-11-08): a list's create action sits at the
  // right end of the title's own line (.adm-title-action, admin-direct.css),
  // like «Nuevo paciente». The route that puts it there takes it away again.
  function titleAction(href, label) {
    var row = $(".adm-h1row");
    if (!row || !row.insertBefore) return;
    $$('.adm-h1row > [data-title-owner="catalog"]').forEach(function (old) {
      if (old.parentNode) old.parentNode.removeChild(old);
    });
    var a = document.createElement("a");
    a.className = "btn btn-accent adm-title-action";
    a.setAttribute("data-title-owner", "catalog");
    a.setAttribute("href", href);
    a.textContent = label;
    row.insertBefore(a, $(".alert-bell-wrap", row) || null);
    ownRouteCleanup(function () { if (a.parentNode) a.parentNode.removeChild(a); });
    return a;
  }
  function setTitle(sec, isDetail) {
    var h1 = $("#adm-h1");
    // En una ficha el título lo reescribe renderForm cuando llegan los datos
    // (crumb / nombre · especialidad). Hasta entonces queda el nombre de la
    // lista: la barra nunca aparece vacía.
    h1.style.display = "";
    h1.textContent = TITLES[sec] || TITLES.servicios;
    var sub = $("#adm-sub");
    if (sub && (sec !== "solicitudes" || isDetail)) sub.style.display = "none";
  }

  function showSection(sec) {
    $$('.adm-content-wrap > [id^="sec-"]').forEach(function (el) {
      el.style.display = el.id === "sec-" + sec ? "" : "none";
    });
  }

  function route(routeVersion) {
    if (!current(routeVersion)) return Promise.resolve();
    var parsed = directState();
    if (!parsed) return;
    var sec = parsed.sec;
    var isDetail = sec !== "solicitudes" && parsed.id != null;
    setNav(sec); setTitle(sec, isDetail); showSection(sec);
    if (sec === "solicitudes") {
      if (window.sanaShowSolicitudes) window.sanaShowSolicitudes();
      return Promise.resolve();
    }
    var p;
    if (parsed.id === "nuevo") p = renderForm(sec, null);
    else if (parsed.id) p = renderForm(sec, parsed.id);
    else p = renderLista(sec);
    return p.catch(function (e) {
      if (!current(routeVersion) || isCancelled(e)) return;
      if (String(e.message) === "redirect") return;
      $("#sec-" + sec).innerHTML = loadErrorWithRetry(T("admin.content.catalog.route.load_failed", null, "No se pudo cargar. Reintentá."), "data-catalog-route-retry");
    });
  }
  function screenDependencies() {
    var deps = [i18nReady, hoursReady, datesReady, formatReady, comboReady, prefsReady, tableReady, filtersReady];
    if (needsHoursEditor) deps.push(hoursEditorReady);
    if (directScreen() === "medicos") deps.push(islandsReady);
    if (needsUsers) deps.push(usersReady);
    return deps;
  }
  function readyRoute() {
    // A same-owner navigation replaces the rendered screen just as surely as
    // an unmount does.  Supersede all pending reads and lazy overlay intent
    // before awaiting dependencies, so stale success and stale failure stay inert.
    mountVersion++;
    createAppointmentSeq++;
    releaseRouteResources();
    requests.forEach(function (controller) { controller.abort(); });
    requests.clear();
    var routeVersion = mountVersion;
    var deps = screenDependencies();
    pendingMutations.forEach(function (work) { deps.push(work.catch(function () {})); });
    return Promise.all(deps).then(function () {
      if (!current(routeVersion)) return;
      localizeDoctorColumns();
      return route(routeVersion);
    });
  }
  listen(window, "beforeunload", function (event) {
    if (!pendingMutations.size) return;
    event.preventDefault();
    event.returnValue = "";
  });
  // Direct-screen links for an item, New, or the list crumb use pushState +
  // route(). Back/Forward restores the item or list through popstate; a link
  // to another direct screen (such as a service's centre) performs a normal
  // navigation.
  if (directScreen()) {
    var ownPath = DIRECT_PATH[directScreen()];
    listen(document, "click", function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var retry = e.target.closest && e.target.closest("[data-catalog-route-retry]");
      if (retry) {
        e.preventDefault();
        if (retry.disabled || !retry.isConnected) return;
        retry.disabled = true;
        readyRoute();
        return;
      }
      var a = e.target.closest && e.target.closest("a[href]");
      if (!a || a.target === "_blank") return;
      var href = a.getAttribute("href") || "";
      if (href.indexOf(ownPath) !== 0 || (href.length > ownPath.length && href.charAt(ownPath.length) !== "?")) return;
      e.preventDefault();
      if (href !== location.pathname + location.search) history.pushState({}, "", href);
      readyRoute();
    });
    listen(window, "popstate", readyRoute);
  }
  // The shell owns route registration.  This controller owns only a mounted
  // direct-screen instance and never publishes a compatibility global.
  var dispose = function () {
    if (disposed) return;
    disposed = true;
    mountVersion++;
    createAppointmentSeq++;
    mutationRequests.forEach(function (controller) { controller.abort(); });
    mutationRequests.clear();
    requests.forEach(function (controller) { controller.abort(); });
    requests.clear();
    if (releaseCreateAppointment) releaseCreateAppointment();
    releaseCreateAppointment = null;
    releaseRouteResources();
    Array.from(cleanups).forEach(function (release) { release(); });
    if (contentOwners.get(root) === dispose) contentOwners.delete(root);
  };
  contentOwners.set(root, dispose);
  listen(window, "pagehide", dispose);
  // The page's load deadline ends when the dependencies are in; the first
  // read after that is the screen's own waiting and failure state.
  dispose.loaded = directScreen()
    ? Promise.all(screenDependencies()).then(function () { return true; }, function () { return false; })
    : Promise.resolve(true);
  dispose.ready = directScreen() ? readyRoute().catch(function (error) { dispose(); throw error; }) : Promise.resolve();
  return dispose;
})();
}
