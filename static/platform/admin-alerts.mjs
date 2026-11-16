/* admin-alerts.mjs — campanita de alertas de triage ROJO con propiedad nativa
   del ciclo de vida (2026-09-07). Es el reemplazo lifecycle de
   admin-alerts.js: mismo widget, misma UI y copy, misma semántica de negocio
   (conteo barato ~60s, lista completa sólo al abrir, ack por fila) — pero
   como módulo ESM nativo. El único booteo es explícito: mountAlerts({role,
   root}) devuelve dispose(); nada se exporta por window y el import solo no
   toca el DOM. El cascarón es quien hace el cutover atómico de consumidor.

   Visible para admin y medico (mismo criterio que «Mis pacientes»): un chat
   de entrevista previa que el análisis califica ROJO es la única señal de
   este panel que no puede esperar a que alguien entre a mirar una tabla.
   Vive en .adm-h1row del root (por defecto el documento), el encabezado que
   persiste sea cual sea la pantalla activa.

   Contrato de API (modules/intake/intake_admin.py):
     GET  /api/admin/intake/alerts/count -> {count}
     GET  /api/admin/intake/alerts       -> {items:[{id,triage,paciente,dni,
       contacto,servicio,medico_id,medico,fecha,request_id}]}
     POST /api/admin/intake/alerts/<id>/ack -> {ok}
   Todas con admin_auth.require_admin(permitir_medico=True): un rol medico
   sólo ve/ackea las suyas (404 en una ajena), admin/operador ven todas.
   Ese gate del servidor es la autorización real; el chequeo de rol de acá
   sólo decide si el widget existe.
*/
const POLL_MS = 60000;
const mounts = new WeakMap();

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function css() {
  return ".alert-bell-wrap{position:relative;margin-left:auto;}"
    // A hidden bell leaves no empty flex item behind: its row gap pushed a
    // right-pinned title button 12px off the content's right edge.
    + ".alert-bell-wrap:has(> .alert-bell[hidden]){display:none;}"
    + ".alert-bell{position:relative;display:inline-flex;align-items:center;justify-content:center;"
    + "min-width:36px;height:36px;gap:7px;padding:0 11px;border-radius:8px;border:1px solid var(--danger);background:var(--danger);cursor:pointer;color:#fff;font-weight:650;font-size:var(--adm-btn-font,14px);}"
    + ".alert-bell:hover{background-image:var(--hover-fill);}"
    + ".alert-bell[hidden]{display:none;}"
    + ".alert-bell-label{font-size:var(--adm-btn-font,14px);white-space:nowrap;}"
    + ".alert-bell-badge{position:absolute;top:-6px;right:-6px;min-width:18px;height:18px;padding:0 4px;"
    + "border-radius:9px;background:var(--danger);color:#fff;font:700 13px/18px var(--font);text-align:center;}"
    + ".alert-bell-panel{position:absolute;top:44px;right:0;width:min(380px,90vw);max-height:70vh;overflow:auto;"
    + "background:var(--panel);border:1px solid var(--border);border-radius:10px;box-shadow:var(--shadow-pop);z-index:40;}"
    + ".alert-bell-head{padding:12px 14px;border-bottom:1px solid var(--border);font-size:14px;}"
    + ".alert-bell-body{padding:6px;}"
    + ".alert-row{padding:10px 8px;border-bottom:1px solid var(--border);}"
    + ".alert-row:last-child{border-bottom:0;}"
    + ".alert-row-top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;}"
    + ".alert-row-pac{font-weight:650;}"
    + ".alert-row-meta{color:var(--hint-ink);font-size:13px;margin-top:2px;}"
    + ".alert-row-actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;}"
    + ".alert-empty{padding:16px;color:var(--hint-ink);font-size:13px;}";
}

export function mountAlerts(options) {
  options = options || {};
  var role = options.role;
  var root = options.root !== undefined ? options.root
    : (typeof document !== "undefined" ? document : null);
  if (!root) throw new Error("Alerts root is required");
  // Propiedad idempotente por root: un remount siempre destrona al dueño
  // anterior antes de wiring propio.
  if (mounts.has(root)) mounts.get(root)();

  var host = root.querySelector ? root.querySelector(".adm-h1row") : null;

  function quitarWraps(hostEl) {
    if (!hostEl || !hostEl.querySelectorAll) return;
    var viejos = hostEl.querySelectorAll(".alert-bell-wrap");
    for (var i = 0; i < viejos.length; i += 1) viejos[i].remove();
  }

  if (role !== "admin" && role !== "medico") {
    // Mismo apagado silencioso del arranque legacy para todo rol que no es
    // admin/medico: sin markup, sin polling, sin listeners — y nunca el gate
    // de sesión por un widget secundario.
    quitarWraps(host);
    var apagado = false;
    function disposeInactivo() {
      if (apagado) return;
      apagado = true;
      if (mounts.get(root) === disposeInactivo) mounts.delete(root);
    }
    disposeInactivo.ready = Promise.resolve();
    mounts.set(root, disposeInactivo);
    return disposeInactivo;
  }

  var disposed = false;
  var pollTimer = null;
  var pollPendiente = false;
  var cargandoLista = false;
  var abierto = false;
  var ITEMS = [];
  var acksPendientes = {};
  var controllers = [];
  var wrap = null, bell = null, badge = null, panel = null, body = null;

  function alive() { return !disposed && (!host || host.isConnected !== false); }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (pollTimer !== null) { clearInterval(pollTimer); pollTimer = null; }
    document.removeEventListener("click", onOutsideClick);
    document.removeEventListener("click", onAckClick);
    document.removeEventListener("keydown", onKeydown);
    for (var i = 0; i < controllers.length; i += 1) {
      try { controllers[i].abort(); } catch (err) { /* ya abortado */ }
    }
    controllers.length = 0;
    if (wrap && wrap.remove) wrap.remove();
    wrap = null; bell = null; badge = null; panel = null; body = null;
    ITEMS = [];
    abierto = false;
    if (mounts.get(root) === dispose) mounts.delete(root);
  }

  function importStatic(name) {
    var url = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC[name]);
    return url ? import(url) : Promise.reject(new Error("Missing Alerts dependency: " + name));
  }

  var sessionReady = importStatic("platform/admin-session.mjs");
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
  var Format = null;
  var formatReady = importStatic("platform/format.mjs").then(function (m) { Format = m; return m; });
  var Dates = null;
  // dates degrada como en el arranque legacy: una fila sin «hace 20 minutos»
  // sigue siendo una fila válida, así que su falta nunca bloquea el widget.
  var datesReady = importStatic("platform/dates.mjs").then(function (m) { Dates = m; return m; })
    .catch(function () { return null; });

  function toast(m) {
    if (typeof window !== "undefined" && window.toast) window.toast(m);
  }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    if (ctl) { opts.signal = ctl.signal; controllers.push(ctl); }
    function liberar() {
      if (!ctl) return;
      var at = controllers.indexOf(ctl);
      if (at !== -1) controllers.splice(at, 1);
    }
    return fetch(path, opts).then(function (res) {
      if (!alive()) throw new Error("alerts owner gone");
      if (res.status === 401 || res.status === 403) {
        return sessionReady.then(function (mod) {
          if (!alive() || !mod) return false;
          // Se comparte sólo el chequeo de vigencia, nunca la autoridad para
          // navegar: una vez dispuesto, isCurrent() es false y confirmar no
          // redirige por este dueño.
          return mod.confirmar(res, undefined, alive);
        }).then(function (transitioned) {
          if (!alive()) throw new Error(String(res.status));
          // La frontera de sesión confirmó una caducidad (y ya redirigió):
          // este dueño se apaga solo — sin toast, sin markup zombi.
          if (transitioned) dispose();
          // Un rol sin acceso nunca debería llegar acá — mountAlerts no
          // arranca el polling para él — pero si pasa, apagamos en silencio
          // en vez de mostrar el gate por un widget secundario.
          if (transitioned || res.status !== 403) throw new Error(String(res.status));
          // La demo de solo lectura rechaza el «visto» con su código: el
          // catch lo lee con apiError (TASK-48) en vez de «No se pudo marcar».
          return res.json().catch(function () { return {}; }).then(function (j) {
            throw new Error(j && j.error === "demo_solo_lectura" ? j.error : "403");
          });
        });
      }
      return res.json().catch(function () { return {}; }).then(function (j) {
        if (!alive()) throw new Error("alerts owner gone");
        if (!res.ok) throw new Error(j.error || "error");
        return j;
      });
    }).then(function (j) { liberar(); return j; },
      function (err) { liberar(); throw err; });
  }

  function inyectarCss() {
    if (document.getElementById("alert-bell-css")) return;
    var tag = document.createElement("style");
    tag.id = "alert-bell-css";
    tag.textContent = css();
    document.head.appendChild(tag);
  }

  // El markup se construye con referencias propias (nunca se adopta un wrap
  // ajeno: el que haya, se saca) y se arma DESPUÉS de que i18n resolvió —
  // una etiqueta congelada antes de la traducción es el defecto de siempre.
  function construir() {
    quitarWraps(host);
    var etiqueta = T("admin.system.alerts.aria_label", null, "Alertas de triage rojo");
    wrap = document.createElement("div");
    wrap.className = "alert-bell-wrap";
    bell = document.createElement("button");
    bell.id = "alert-bell";
    bell.className = "alert-bell";
    bell.type = "button";
    bell.setAttribute("aria-haspopup", "dialog");
    bell.setAttribute("aria-expanded", "false");
    bell.setAttribute("aria-label", etiqueta);
    bell.hidden = true;
    var label = document.createElement("span");
    label.className = "alert-bell-label";
    label.textContent = T("admin.system.alerts.label", null, "Triage rojo");
    badge = document.createElement("span");
    badge.id = "alert-bell-badge";
    badge.className = "alert-bell-badge";
    badge.hidden = true;
    badge.textContent = "0";
    panel = document.createElement("div");
    panel.id = "alert-bell-panel";
    panel.className = "alert-bell-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", etiqueta);
    panel.hidden = true;
    var head = document.createElement("div");
    head.className = "alert-bell-head";
    var titulo = document.createElement("b");
    titulo.textContent = T("admin.system.alerts.panel_head", null, "Triage rojo sin ver");
    head.appendChild(titulo);
    body = document.createElement("div");
    body.id = "alert-bell-body";
    body.className = "alert-bell-body";
    bell.appendChild(label);
    bell.appendChild(badge);
    panel.appendChild(head);
    panel.appendChild(body);
    wrap.appendChild(bell);
    wrap.appendChild(panel);
    host.appendChild(wrap);
    bell.addEventListener("click", togglePanel);
  }

  function actualizarBadge(n) {
    if (!alive() || !badge) return;
    if (bell) bell.title = "";
    if (n > 0) { badge.textContent = n > 99 ? "99+" : String(n); badge.hidden = false; }
    else badge.hidden = true;
    if (bell) {
      bell.hidden = !(n > 0);
      bell.setAttribute("aria-label", n > 0
        ? Tn("admin.system.alerts.aria_label_count", n, null, "{n} alerta de triage rojo sin ver", "{n} alertas de triage rojo sin ver")
        : T("admin.system.alerts.aria_label", null, "Alertas de triage rojo"));
    }
    if (!(n > 0) && abierto) cerrarPanel();
  }

  function poll() {
    if (!alive() || pollPendiente) return;
    pollPendiente = true;
    // Coalescing: un conteo colgado no apila el tick siguiente encima.
    api("/api/admin/intake/alerts/count").then(function (j) {
      if (!j || !Number.isInteger(j.count) || j.count < 0) throw new Error("Invalid alert count response");
      if (alive()) actualizarBadge(j.count);
    }).catch(function (error) {
      // Unknown is not zero: retain the last count and keep list/retry access.
      if (error && (error.message === "401" || error.message === "403")) return;
      if (alive() && bell) {
        bell.hidden = false;
        bell.title = T("admin.system.alerts.load_error", null, "No se pudo cargar.");
      }
    }).then(function () { pollPendiente = false; });
  }

  function filaHtml(a) {
    var wa = Format.telWa(a.contacto);
    var telE164 = Format.telE164(a.contacto);
    var consultaHref = "/admin/consult?interview=" + encodeURIComponent(a.id);
    var acciones = "";
    if (wa) acciones += '<a class="btn btn-quiet btn-sm" href="https://wa.me/' + esc(wa) + '" target="_blank" rel="noopener">WhatsApp</a>';
    if (telE164) acciones += '<a class="btn btn-quiet btn-sm" href="tel:' + esc(telE164) + '">' + esc(T("admin.system.alerts.call", null, "Llamar")) + "</a>";
    if (a.request_id || a.id) acciones += '<a class="btn btn-quiet btn-sm" href="' + consultaHref + '">' + esc(T("admin.system.alerts.view_interview", null, "Ver entrevista")) + "</a>";
    acciones += '<button type="button" class="btn btn-quiet btn-sm" data-ack="' + esc(a.id) + '">' + esc(T("admin.system.alerts.mark_seen", null, "Marcar como visto")) + "</button>";
    var meta = [a.servicio, a.medico, (Dates && Dates.relativo(a.fecha)) || ""].filter(Boolean).join(" · ");
    return '<div class="alert-row" data-alert-row="' + esc(a.id) + '">'
      + '<div class="alert-row-top"><span class="queue-triage queue-triage--rojo">' + esc(T("admin.system.alerts.rojo", null, "Rojo")) + "</span>"
      + '<span class="alert-row-pac">' + esc(Format.nombre(a.paciente) || T("admin.system.alerts.sin_nombre", null, "Sin nombre")) + "</span></div>"
      + (meta ? '<div class="alert-row-meta">' + esc(meta) + "</div>" : "")
      + '<div class="alert-row-actions">' + acciones + "</div>"
      + "</div>";
  }

  function renderPanel() {
    if (!alive() || !body) return;
    if (!ITEMS.length) {
      body.innerHTML = '<p class="alert-empty">' + esc(T("admin.system.alerts.empty", null, "No hay alertas de triage rojo pendientes.")) + "</p>";
      return;
    }
    body.innerHTML = ITEMS.map(filaHtml).join("");
  }

  function cargarLista() {
    if (cargandoLista) return;
    cargandoLista = true;
    var destino = body;
    if (destino) destino.innerHTML = '<p class="alert-empty">' + esc(T("admin.system.common.loading", null, "Cargando…")) + "</p>";
    api("/api/admin/intake/alerts").then(function (j) {
      if (!alive()) return;
      if (!j || !Array.isArray(j.items)) throw new Error("Invalid alert list response");
      ITEMS = j.items;
      renderPanel();
    }).catch(function (error) {
      // Un dueño vivo reporta su fallo de lista; uno muerto ya no tiene
      // pantalla que informar — el rechazo tardío se traga en silencio.
      if (alive()) {
        if (typeof console !== "undefined" && console.error) console.error("[admin-alerts] list:", error);
        if (destino) destino.innerHTML = '<p class="alert-empty">' + esc(T("admin.system.alerts.load_error", null, "No se pudo cargar.")) + "</p>";
      }
    }).then(function () { cargandoLista = false; });
  }

  function abrirPanel() {
    if (!alive() || !panel || !bell) return;
    panel.hidden = false;
    bell.setAttribute("aria-expanded", "true");
    abierto = true;
    cargarLista();
  }

  function cerrarPanel() {
    if (panel) panel.hidden = true;
    if (bell) bell.setAttribute("aria-expanded", "false");
    abierto = false;
  }

  function togglePanel(e) {
    if (e && e.stopPropagation) e.stopPropagation();
    if (!alive()) return;
    if (abierto) cerrarPanel(); else abrirPanel();
  }

  function onOutsideClick(e) {
    if (!abierto || !alive()) return;
    var t = e.target;
    if (!(t && t.closest && t.closest(".alert-bell-wrap"))) cerrarPanel();
  }

  function onKeydown(e) {
    if (abierto && alive() && e.key === "Escape") cerrarPanel();
  }

  function onAckClick(e) {
    if (!alive()) return;
    var t = e.target;
    var b = t && t.closest ? t.closest("[data-ack]") : null;
    if (!b || !wrap || !wrap.contains(b)) return;
    var id = b.getAttribute("data-ack");
    var clave = String(id);
    // Coalescing de acks duplicados: un POST por alerta hasta que resuelva.
    if (acksPendientes[clave]) return;
    acksPendientes[clave] = true;
    b.disabled = true;
    api("/api/admin/intake/alerts/" + encodeURIComponent(id) + "/ack", { method: "POST" }).then(function (result) {
      if (!result || result.ok !== true) throw new Error("Invalid alert acknowledgement");
      delete acksPendientes[clave];
      if (!alive()) return;
      ITEMS = ITEMS.filter(function (a) { return String(a.id) !== clave; });
      renderPanel();
      poll();
      toast(T("admin.system.alerts.marked_seen", null, "Marcada como vista."));
    }).catch(function (err) {
      delete acksPendientes[clave];
      if (!alive()) return;
      if (b && b.isConnected !== false) b.disabled = false;
      var base = T("admin.system.alerts.mark_error", null, "No se pudo marcar. Reintentá.");
      var demo = err && err.message === "demo_solo_lectura";
      toast(demo && I18n && I18n.apiError ? I18n.apiError(err, base) : base);
    });
  }

  mounts.set(root, dispose);
  dispose.ready = Promise.all([i18nReady, formatReady, datesReady, sessionReady]).then(function () {
    if (!alive()) return;
    // Sin .adm-h1row no hay widget que mostrar: nada que escuchar ni
    // temporizar (el encabezado existe en todas las pantallas del cascarón).
    if (!host) return;
    inyectarCss();
    construir();
    document.addEventListener("click", onOutsideClick);
    document.addEventListener("click", onAckClick);
    document.addEventListener("keydown", onKeydown);
    poll();
    pollTimer = setInterval(poll, POLL_MS);
  }).catch(function (error) {
    // Una dependencia que no resolvió deja el dueño muerto y el informe
    // visible para quien llamó — nunca un widget mudo en pantalla.
    dispose();
    if (typeof console !== "undefined" && console.error) console.error("[admin-alerts] dependencies:", error);
    if (options.onError) options.onError(error);
    throw error;
  });
  return dispose;
}
