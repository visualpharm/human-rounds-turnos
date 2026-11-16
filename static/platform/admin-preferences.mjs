/* Server-backed admin list preferences. Load once after whoami, then modules
   can synchronously read the cache and write optimistically. */
import { t } from "./i18n.mjs";

var values = {};
var loaded = false;
var loading = null;
var timers = {};

/* A change still unacknowledged by the server, kept in sessionStorage until its
   PUT answers. The write flushed on pagehide races the next page's GET, which
   could answer first and show the old value (form audit 2026-10-25). The next
   page lays these over what it read and sends them again; the server stays the
   only store, this is just the outbox. Entries older than a minute are dropped. */
var PENDING = "sana.admin.preferences.pending";
var PENDING_TTL = 60000;

function readPending() {
  try {
    var parsed = JSON.parse(sessionStorage.getItem(PENDING) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (e) { return {}; }
}

function writePending(pending) {
  try {
    if (Object.keys(pending).length) sessionStorage.setItem(PENDING, JSON.stringify(pending));
    else sessionStorage.removeItem(PENDING);
  } catch (e) { /* storage unavailable: the PUT alone, as before */ }
}

function markPending(key, value) {
  var pending = readPending();
  pending[key] = { value: value, at: Date.now() };
  writePending(pending);
}

function clearPending(key, value) {
  var pending = readPending();
  if (pending[key] && JSON.stringify(pending[key].value) === JSON.stringify(value)) {
    delete pending[key];
    writePending(pending);
  }
}

/* The public demo's session is read-only: the server refuses every write with
   403 demo_solo_lectura (admin_auth.py). A preference is how a screen
   remembers a column or a filter, not something the visitor asked to save, so
   that refusal is not a failure to report: from the first one on, this tab
   keeps its preferences here and stops asking the server (demo 2026-10-26,
   «No se pudo guardar tu preferencia» on opening a patient's card). */
var LOCAL = "sana.admin.preferences.local";
var readOnly = false;

function readLocal() {
  try {
    var parsed = JSON.parse(sessionStorage.getItem(LOCAL) || "null");
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (e) { return null; }
}

function keepLocal(key, value) {
  readOnly = true;
  var local = readLocal() || {};
  local[key] = value;
  try { sessionStorage.setItem(LOCAL, JSON.stringify(local)); } catch (e) { /* this page only */ }
}

function persist(key, value, keepalive, previous) {
    function attempt() {
      return fetch("/api/admin/preferences", {
        method: "PUT",
        credentials: "same-origin",
        keepalive: !!keepalive,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key, value: value })
      }).then(function (res) {
        if (res.status === 403) {
          return Promise.resolve(res.json()).catch(function () { return null; }).then(function (body) {
            var err = new Error("preferences-403");
            err.readOnly = !!(body && body.error === "demo_solo_lectura");
            throw err;
          });
        }
        if (!res.ok) throw new Error("preferences-" + res.status);
        clearPending(key, value);
        return res;
      });
    }
    var saving = attempt();
    if (!keepalive) {
      // Un PUT caído era invisible: la pantalla quedaba con el cambio aplicado
      // y al recargar volvía al valor anterior sin decir nada. Un reintento
      // tapa el 503 transitorio; si tampoco sale, se avisa y el caché vuelve
      // al último valor que el servidor aceptó.
      saving = saving.catch(function (err) {
        if (err && err.readOnly) throw err;
        return new Promise(function (resolve) { setTimeout(resolve, 800); })
          .then(attempt);
      });
    }
    return saving.catch(function (err) {
      if (err && err.readOnly) {
        clearPending(key, value);
        // The newest choice wins if another one came while this was refused.
        keepLocal(key, Object.prototype.hasOwnProperty.call(values, key) ? values[key] : value);
        return;
      }
      if (!keepalive) clearPending(key, value);
      if (values[key] === value) {
        if (previous === undefined) delete values[key];
        else values[key] = previous;
      }
      if (!keepalive && typeof window !== "undefined" && window.toast) {
        window.toast(t("admin.system.preferences.save_error", null,
          "No se pudo guardar tu preferencia; puede volver al valor anterior."));
      }
      console.warn("admin preferences: no se pudo guardar", key);
    });
}

export function load(force) {
    if (loaded && !force) return Promise.resolve(values);
    if (loading && !force) return loading;
    loading = fetch("/api/admin/preferences", { credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("preferences-" + res.status);
        return res.json();
      }).then(function (data) {
        values = data.items || {};
        var local = readLocal(), pending = readPending(), now = Date.now();
        if (local) {
          readOnly = true;
          Object.keys(local).forEach(function (key) { values[key] = local[key]; });
        }
        Object.keys(pending).forEach(function (key) {
          var entry = pending[key];
          if (!entry || !(now - entry.at < PENDING_TTL)) { delete pending[key]; return; }
          values[key] = entry.value;
          if (readOnly) { keepLocal(key, entry.value); delete pending[key]; }
          else persist(key, entry.value, false, undefined);
        });
        writePending(pending);
        loaded = true;
        return values;
      }).catch(function () {
        loaded = true;
        return values;
      }).then(function (result) {
        loading = null;
        return result;
      });
    return loading;
}

export function get(key, fallback) {
    return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : fallback;
}

export function set(key, value) {
    var previous = Object.prototype.hasOwnProperty.call(values, key)
      ? values[key] : undefined;
    values[key] = value;
    if (readOnly) { keepLocal(key, value); return; }
    markPending(key, value);
    clearTimeout(timers[key]);
    timers[key] = setTimeout(function () {
      delete timers[key];
      persist(key, value, false, previous);
    }, 180);
}

  // El debounce evita una escritura por clic rápido, pero pagehide descarga lo
  // pendiente para que cerrar la pestaña justo después de navegar no pierda el
  // último estado del servidor. Sin reintento ni aviso: la pestaña ya se va.
window.addEventListener("pagehide", function () {
  Object.keys(timers).forEach(function (key) {
    clearTimeout(timers[key]);
    delete timers[key];
    persist(key, values[key], true);
  });
});
