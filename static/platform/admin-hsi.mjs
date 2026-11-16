/* admin-hsi.mjs — native lifecycle-owned controller for the direct
 * /admin/hsi screen (HSI synchronization: synced share + conflicts the
 * resolver left to a person).
 *
 * Import is inert. The template (owned by root) boots an explicit host and
 * status element and owns page-exit disposal. Each mount owns its reads,
 * mutations and listeners; disposing or mounting over the same host aborts
 * in-flight work and retires every callback. A late fetch, JSON decode,
 * session confirmation, import settlement or render/mutation continuation from
 * an old owner cannot affect a disposed or remounted UI.
 *
 * The first read is the installation's HSI state (read-only, slice 7): how a
 * turno reaches HSI here, why, and each center's institution number. Without
 * the HSI connector that state IS the screen and the journal API (a 404 there)
 * is never asked; with it the journal follows and the centers close the page.
 *
 * Data contract:
 *   GET  /api/admin/system/hsi   {modo, conector, modo_texto, motivo_texto, centros:[{slug,nombre,institucion}]}
 *   GET  /api/admin/hsi-sync
 *   POST /api/admin/hsi-sync/resolver
 *   POST /api/admin/hsi-sync/conflicts  {accion, conflictos:[id]}
 * HTTP 200 is not itself a successful resolution: panel/summary/result rows
 * must be well-formed, and an apply must carry a matching per-conflict ack.
 */

const mounts = new WeakMap();

export function mount(host, options) {
  if (!host) throw new Error("HSI host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};
  var statusHost = options.status || null;

  var disposed = false;
  var generation = 0;
  var loadCtl = null;
  var flight = null;
  var panel = null;
  var loadFailed = false;
  // The installation's HSI state, or null while unread / when its read failed
  // (the journal is then asked as before, so a broken state read never hides it).
  var estado = null;

  var Session = null;
  var I18n = null;
  var Dates = null;
  var Format = null;

  function alive() { return !disposed && host.isConnected !== false; }
  function owns(gen) { return alive() && gen === generation; }

  function doc() {
    return (typeof window !== "undefined" && window.document) || globalThis.document;
  }
  function loc() {
    return (typeof window !== "undefined" && window.location) || globalThis.location;
  }
  function hist() {
    return (typeof window !== "undefined" && window.history) || globalThis.history;
  }
  function ask(msg) {
    var fn = (typeof window !== "undefined" && window.confirm) || globalThis.confirm;
    return typeof fn === "function" ? fn(msg) : false;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    generation += 1;
    if (loadCtl) loadCtl.abort();
    if (flight && flight.controller) flight.controller.abort();
    flight = null;
    host.removeEventListener("click", onClick);
    host.removeEventListener("change", onChange);
    if (mounts.get(host) === dispose) mounts.delete(host);
  }

  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing HSI dependency: " + name));
  }

  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }

  function $(sel, root) { return (root || host).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || host).querySelectorAll(sel)); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function validResolutions(list) {
    if (list == null) return true;
    if (!Array.isArray(list)) return false;
    for (var i = 0; i < list.length; i += 1) {
      var row = list[i];
      if (!isObject(row) || row.accion == null || String(row.accion) === "") return false;
    }
    return true;
  }

  function validPanel(value) {
    if (!isObject(value)) return false;
    if (!Array.isArray(value.conflictos)) return false;
    if (value.resueltos != null && !Array.isArray(value.resueltos)) return false;
    var metrics = value.metricas;
    if (!isObject(metrics) || !Number.isSafeInteger(metrics.futuras) || metrics.futuras < 0) return false;
    if (metrics.futuras > 0 && (!Number.isFinite(metrics.pct_futuras)
        || metrics.pct_futuras < 0 || metrics.pct_futuras > 100)) return false;
    for (var i = 0; i < value.conflictos.length; i += 1) {
      var c = value.conflictos[i];
      if (!isObject(c) || c.conflicto_id == null || String(c.conflicto_id) === "") return false;
      if (!validResolutions(c.resoluciones)) return false;
    }
    return true;
  }

  function parseSyncBody(json) {
    if (!isObject(json)) return { kind: "malformed" };
    if (json.panel == null) return { kind: "empty" };
    if (!validPanel(json.panel)) return { kind: "malformed" };
    return { kind: "ok", panel: json.panel };
  }

  function validState(value) {
    if (!isObject(value) || typeof value.modo !== "string" || !value.modo) return false;
    // The words the screen prints, and the connector `stateOnly()` decides on:
    // a payload without the key would read as «no HSI connector» and hide the
    // journal of an installation whose server does send.
    if (typeof value.modo_texto !== "string" || !value.modo_texto) return false;
    if (!("conector" in value) || (value.conector !== null && typeof value.conector !== "string")) return false;
    if (value.centros != null && !Array.isArray(value.centros)) return false;
    var centros = value.centros || [];
    for (var i = 0; i < centros.length; i += 1) {
      if (!isObject(centros[i])) return false;
    }
    return true;
  }

  // Without the HSI connector there is no journal to read: the state is the screen.
  function stateOnly() { return !!estado && estado.conector !== "hsi"; }

  function matchingAck(json, conflictoId) {
    if (!isObject(json) || json.ok !== true || !Array.isArray(json.resultados)) return null;
    var id = Number(conflictoId);
    for (var i = 0; i < json.resultados.length; i += 1) {
      var row = json.resultados[i];
      if (isObject(row) && Number(row.conflicto_id) === id && typeof row.ok === "boolean") return row;
    }
    return null;
  }

  function injectCss() {
    var d = doc();
    if (!d || d.getElementById("hsi-css")) return;
    var st = d.createElement("style");
    st.id = "hsi-css";
    st.textContent = [
      ".hsi-tabla .hsi-alt{min-height:44px;font-size:16px;}",
      ".hsi-tabla .hsi-alt{width:max-content;min-width:220px;}",
      ".hsi-tabla tbody td{border-bottom:0;}",
      "@media(max-width:600px){#sec-hsi>.hsi-hero,#sec-hsi>.hsi-title{margin-left:16px;margin-right:16px;}}",
      ".hsi-tabla [data-hsi-apply],#hsi-rescan-btn,#hsi-retry-btn{min-height:44px;min-width:44px;font-size:16px;}",
      ".hsi-load-error{display:flex;align-items:center;gap:12px;flex-wrap:wrap;color:var(--text);}",
      ".hsi-status--error{color:var(--danger,#b3261e);}",
      ".hsi-tabla-wrap{container-type:inline-size;container-name:hsi-table;margin-left:0;margin-right:0;width:100%;max-width:100%;box-sizing:border-box;}",
      "@container hsi-table (max-width:900px){",
      ".hsi-tabla,.hsi-tabla tbody,.hsi-tabla tr,.hsi-tabla td{display:block;width:100%;box-sizing:border-box;}",
      ".hsi-tabla thead{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);border:0;}",
      ".hsi-tabla tr{display:flex;flex-direction:column;padding:10px 16px 14px;}",
      ".hsi-tabla td{border:0;padding:4px 0;white-space:normal;max-width:none;}",
      ".hsi-tabla td::before{content:attr(data-label);display:block;font-size:16px;font-weight:600;color:var(--text);margin-bottom:2px;}",
      ".hsi-tabla td.hsi-td-pac{order:-1;font-size:16px;font-weight:700;padding-top:0;}",
      ".hsi-tabla td.hsi-td-pac::before{content:none;}",
      ".hsi-tabla td.hsi-td-prob{max-width:none;}",
      ".hsi-td-rec [data-hsi-apply]{max-width:100%;width:auto;justify-content:center;}",
      ".hsi-title{flex-wrap:wrap;}",
      "}",
    ].join("");
    if (d.head) d.head.appendChild(st);
  }

  function setStatus(msg, isError) {
    if (!statusHost || statusHost.isConnected === false) return;
    statusHost.hidden = !msg;
    statusHost.className = "adm-empty" + (isError && msg ? " hsi-status--error" : "");
    statusHost.textContent = msg || "";
  }

  function clearStatus() { setStatus("", false); }

  function toast(msg) { setStatus(msg || "", false); }

  function errorText(r, fallback) {
    var code = r.json && r.json.error;
    if (code === "hsi_desactivado") return T("admin.hsi.err_hsi_disabled", null, "Este entorno no envía turnos a HSI.");
    if (code === "hsi_no_disponible") return T("admin.hsi.err_hsi_unavailable", null, "HSI no responde en este momento.");
    // The demo's refusal and any other code: one shared translation (TASK-48).
    return I18n && I18n.apiError ? I18n.apiError(code, fallback) : fallback;
  }

  function pad(n) { return String(n).length < 2 ? "0" + n : "" + n; }

  function hsiFecha(iso) {
    if (!iso) return "";
    var d = new Date(String(iso).replace(" ", "T"));
    if (isNaN(d.getTime())) return "";
    var formatted = Dates.corta(
      d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()),
      pad(d.getHours()) + ":" + pad(d.getMinutes())
    );
    return formatted || "";
  }

  function hsiCuando(iso) {
    return (iso && Dates.relativo(iso)) || "";
  }

  function nombre(value) { return Format.nombre(value); }

  function setControlsDisabled(disabled) {
    $$("[data-hsi-apply], .hsi-alt, #hsi-rescan-btn").forEach(function (el) {
      el.disabled = !!disabled;
    });
  }

  function beginLoad() {
    if (loadCtl) loadCtl.abort();
    loadCtl = new AbortController();
    return loadCtl;
  }

  function beginMutation(kind) {
    if (!alive() || flight) return null;
    var token = { kind: kind, gen: generation, controller: new AbortController() };
    flight = token;
    setControlsDisabled(true);
    return token;
  }

  function endMutation(token) {
    if (flight !== token) return;
    flight = null;
    if (alive()) setControlsDisabled(false);
  }

  function fetchOutcome(path, opts) {
    opts = Object.assign({ credentials: "same-origin" }, opts || {});
    return fetch(path, opts).then(function (r) {
      return r.json().then(
        function (j) { return { ok: r.ok, status: r.status, json: j, response: r }; },
        function () { return { ok: r.ok, status: r.status, json: null, response: r }; }
      );
    }).catch(function (err) {
      if (err && err.name === "AbortError") return { aborted: true, ok: false, status: 0, json: null, response: null };
      return { ok: false, status: 0, json: null, response: null };
    });
  }

  function isCurrentFn(gen) {
    return function () { return owns(gen); };
  }

  function classify(r, gen, current = isCurrentFn(gen)) {
    if (!current()) return Promise.resolve(false);
    if (r.aborted) return Promise.resolve(false);
    if (r.status !== 401 && r.status !== 403) return Promise.resolve(current());
    if (r.json && r.json.error === "demo_solo_lectura") return Promise.resolve(current());
    return Session.confirmar(r.response, r.json, current).then(function (transitioned) {
      if (!current()) return false;
      if (transitioned) {
        if (alive()) {
          host.innerHTML = "";
          clearStatus();
          dispose();
        }
        return false;
      }
      return current();
    });
  }

  function renderLoadError(withRetry) {
    if (!alive()) return;
    var retry = withRetry
      ? '<button type="button" class="btn btn-quiet" id="hsi-retry-btn">' + esc(T("admin.hsi.retry", null, "Reintentar")) + "</button>"
      : "";
    host.innerHTML = '<p class="adm-empty hsi-load-error" role="status" aria-live="polite"><span>' +
      esc(T("admin.hsi.load_error", null, "No se pudo cargar la sincronización con HSI.")) + "</span>" +
      retry + "</p>";
  }

  function renderLoading() {
    if (!alive()) return;
    host.innerHTML = '<p class="adm-empty">' + esc(T("admin.hsi.loading", null, "Cargando…")) + "</p>";
  }

  function requestLink(c) {
    var label = esc(nombre(c.paciente) || T("admin.hsi.request_n", { id: c.solicitud_id }, "Solicitud {id}"));
    return '<a href="/admin/solicitudes">' + label + "</a>";
  }

  // How a turno reaches HSI when it is not the server sending it, and why.
  // With the server sending, the journal's own share already says it.
  function stateHtml() {
    if (!estado || estado.modo === "servidor" || !estado.modo_texto) return "";
    return '<h2 class="hsi-modo" data-hsi-mode="' + esc(estado.modo) + '">' + esc(estado.modo_texto) + "</h2>" +
      (estado.motivo_texto ? '<p class="hsi-motivo">' + esc(estado.motivo_texto) + "</p>" : "");
  }

  // One row per center: its HSI institution number, or «Sin número» — marked
  // where the extension loads the turnos, because it is the one reading it.
  function centersHtml() {
    var centros = (estado && estado.centros) || [];
    if (!centros.length) return "";
    var colCentro = T("admin.hsi.col_center", null, "Centro");
    var colNumero = T("admin.hsi.col_institution", null, "Institución en HSI");
    var falta = T("admin.hsi.no_number", null, "Sin número");
    var marca = estado.modo === "extension" ? '<span class="hsi-falta-dot"></span>' : "";
    var filas = centros.map(function (c) {
      var name = esc(c.nombre || c.slug || "");
      var centro = c.slug
        ? '<a href="/admin/centers?item=' + encodeURIComponent(c.slug) + '">' + name + "</a>"
        : name;
      var numero = c.institucion
        ? esc(c.institucion)
        : '<span class="hsi-falta" data-hsi-missing>' + marca + esc(falta) + "</span>";
      return '<tr><td data-label="' + esc(colCentro) + '">' + centro + "</td>" +
        '<td data-label="' + esc(colNumero) + '">' + numero + "</td></tr>";
    }).join("");
    return '<div class="hsi-title"><h2>' + esc(T("admin.hsi.centers_heading", null, "Instituciones en HSI")) + "</h2></div>" +
      '<div class="cnt-table-wrap"><table class="cnt-table hsi-centros"><thead><tr>' +
      '<th class="cnt-th">' + esc(colCentro) + '</th><th class="cnt-th">' + esc(colNumero) + "</th>" +
      "</tr></thead><tbody>" + filas + "</tbody></table></div>";
  }

  function render() {
    if (!alive()) return;
    if (stateOnly()) {
      host.innerHTML = stateHtml() + centersHtml();
      return;
    }
    if (!panel) {
      if (loadFailed) renderLoadError(true);
      else renderLoading();
      return;
    }
    var m = panel.metricas || {};
    var hero = m.futuras
      ? '<p class="hsi-hero"><b>' + esc(m.pct_futuras) + "%</b> " + esc(T("admin.hsi.hero_synced", null, "de los turnos futuros aprobados están agendados en HSI")) + "</p>"
      : '<p class="hsi-hero">' + esc(T("admin.hsi.hero_none", null, "No hay turnos futuros para sincronizar con HSI.")) + "</p>";
    var conflictos = panel.conflictos;
    var cols = {
      fecha: T("admin.hsi.col_date", null, "Fecha"),
      paciente: T("admin.hsi.col_patient", null, "Paciente"),
      medico: T("admin.hsi.col_doctor", null, "Médico"),
      problema: T("admin.hsi.col_problem", null, "Problema"),
      resolucion: T("admin.hsi.col_resolution", null, "Resolución"),
      otras: T("admin.hsi.col_other", null, "Otras opciones")
    };
    var sin = '<span class="hsi-sin">—</span>';
    var cuerpo;
    if (!conflictos.length) {
      cuerpo = '<p class="hsi-vacio">' + esc(T("admin.hsi.no_conflicts", null, "No hay conflictos que necesiten a una persona: lo demás se resuelve solo.")) + "</p>";
    } else {
      var filas = conflictos.map(function (c) {
        var fecha = c.fecha ? esc(hsiFecha(c.fecha)) : sin;
        var quien = c.solicitud_id ? requestLink(c) : esc(nombre(c.paciente) || T("admin.queue.attendance.sin_nombre", null, "paciente sin nombre"));
        var medico = c.medico ? esc(nombre(c.medico)) : '<span class="hsi-sin">' + esc(T("admin.hsi.unassigned", null, "Sin asignar")) + "</span>";
        var rs = Array.isArray(c.resoluciones) ? c.resoluciones : [];
        var rec = rs[0];
        var recBtn = rec
          ? '<button type="button" class="btn" data-hsi-apply data-cf="' + esc(c.conflicto_id) + '" data-accion="' + esc(rec.accion) + '">' + esc(rec.label) + "</button>"
          : sin;
        var alt = rs.length > 1
          ? '<select class="hsi-alt" data-cf="' + esc(c.conflicto_id) + '" aria-label="' + esc(T("admin.hsi.other_resolution_aria", null, "Otra resolución")) + '"><option value="">' + esc(T("admin.hsi.other_option", null, "Otra opción…")) + "</option>" +
            rs.slice(1).map(function (r) { return '<option value="' + esc(r.accion) + '">' + esc(r.label) + "</option>"; }).join("") + "</select>"
          : sin;
        return "<tr>" +
          '<td class="hsi-td-fecha" data-label="' + esc(cols.fecha) + '">' + fecha + "</td>" +
          '<td class="hsi-td-pac" data-label="' + esc(cols.paciente) + '">' + quien + "</td>" +
          '<td class="hsi-td-med" data-label="' + esc(cols.medico) + '">' + medico + "</td>" +
          '<td class="hsi-td-prob" data-label="' + esc(cols.problema) + '">' + esc(c.problema || "") + "</td>" +
          '<td class="hsi-td-rec" data-label="' + esc(cols.resolucion) + '">' + recBtn + "</td>" +
          '<td class="hsi-td-alt" data-label="' + esc(cols.otras) + '">' + alt + "</td>" +
          "</tr>";
      }).join("");
      cuerpo = '<div class="cnt-table-wrap hsi-tabla-wrap"><table class="cnt-table hsi-tabla">' +
        "<thead><tr>" +
        '<th class="cnt-th">' + esc(cols.fecha) + '</th><th class="cnt-th">' + esc(cols.paciente) + "</th>" +
        '<th class="cnt-th">' + esc(cols.medico) + '</th><th class="cnt-th">' + esc(cols.problema) + "</th>" +
        '<th class="cnt-th">' + esc(cols.resolucion) + '</th><th class="cnt-th">' + esc(cols.otras) + "</th>" +
        "</tr></thead><tbody>" + filas + "</tbody></table></div>";
    }
    var resueltos = (panel.resueltos || []).length
      ? '<details class="hsi-resueltos"><summary>' + esc(T("admin.hsi.resolved_auto", { n: panel.resueltos.length }, "Resueltos automáticamente ({n})")) + "</summary>" +
        '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr>' +
        '<th class="cnt-th">' + esc(T("admin.hsi.col_when", null, "Cuándo")) + '</th><th class="cnt-th">' + esc(cols.resolucion) + "</th></tr></thead><tbody>" +
        panel.resueltos.map(function (r) { return "<tr><td>" + esc(hsiCuando(r.cuando)) + "</td><td>" + esc(r.texto) + "</td></tr>"; }).join("") +
        "</tbody></table></div></details>"
      : "";
    host.innerHTML = stateHtml() + hero +
      '<div class="hsi-title"><h2>' + esc(T("admin.hsi.conflicts_heading", null, "Conflictos")) + (conflictos.length ? " (" + conflictos.length + ")" : "") + "</h2>" +
      '<button type="button" class="btn btn-quiet" id="hsi-rescan-btn">' + esc(T("admin.hsi.rescan", null, "Volver a revisar")) + "</button></div>" +
      cuerpo + resueltos + centersHtml();
  }

  function adoptPanel(next) {
    if (!validPanel(next)) return false;
    panel = next;
    loadFailed = false;
    render();
    return true;
  }

  function failLoad() {
    if (!alive()) return;
    panel = null;
    loadFailed = true;
    render();
  }

  function loadPanel() {
    if (!alive()) return Promise.resolve();
    var gen = generation;
    if (!panel && !loadFailed) renderLoading();
    else if (loadFailed) renderLoading();
    var ctl = beginLoad();
    var current = function () { return owns(gen) && loadCtl === ctl; };
    return fetchOutcome("/api/admin/hsi-sync", { signal: ctl.signal }).then(function (r) {
      if (r.aborted || !current()) return;
      return classify(r, gen, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok) { failLoad(); return; }
        var parsed = parseSyncBody(r.json);
        if (parsed.kind === "ok") {
          panel = parsed.panel;
          loadFailed = false;
          render();
          return;
        }
        failLoad();
      });
    });
  }

  // The state first: it decides whether there is a journal to read at all.
  // A failed or malformed state read is not the screen's failure — the
  // journal is asked exactly as before and owns the error and its retry.
  function loadState() {
    if (!alive()) return Promise.resolve();
    var gen = generation;
    renderLoading();
    var ctl = beginLoad();
    var current = function () { return owns(gen) && loadCtl === ctl; };
    return fetchOutcome("/api/admin/system/hsi", { signal: ctl.signal }).then(function (r) {
      if (r.aborted || !current()) return;
      return classify(r, gen, current).then(function (go) {
        if (!go || !current()) return;
        estado = r.ok && validState(r.json) ? r.json : null;
        if (stateOnly()) { render(); return; }
        return loadPanel();
      });
    });
  }

  function applyConflict(cf, accion) {
    if (!cf || !accion) return;
    if (accion === "rechazar" && !ask(T("admin.hsi.confirm_reject", null, "¿Rechazar la solicitud? Se le avisa al paciente por email."))) return;
    var token = beginMutation("apply");
    if (!token) return;
    var gen = token.gen;
    return fetchOutcome("/api/admin/hsi-sync/conflicts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accion: accion, conflictos: [Number(cf)] }),
      signal: token.controller.signal
    }).then(function (r) {
      if (r.aborted) return;
      return classify(r, gen).then(function (go) {
        if (!go || flight !== token) return;
        if (!r.ok) {
          toast(errorText(r, T("admin.hsi.apply_failed", null, "No se pudo aplicar.")));
          endMutation(token);
          return;
        }
        var ack = matchingAck(r.json, cf);
        if (!ack) {
          toast(T("admin.hsi.apply_failed", null, "No se pudo aplicar."));
          endMutation(token);
          return;
        }
        if (r.json.panel) adoptPanel(r.json.panel);
        if (ack.ok === true) {
          toast(ack.detalle || T("admin.hsi.resolved_ok", null, "Resuelto."));
        } else {
          toast(T("admin.hsi.could_not", { detail: ack.detalle || "" }, "No se pudo: {detail}"));
        }
        endMutation(token);
        return loadPanel();
      });
    });
  }

  function rescan() {
    var token = beginMutation("rescan");
    if (!token) return;
    var gen = token.gen;
    return fetchOutcome("/api/admin/hsi-sync/resolver", {
      method: "POST",
      signal: token.controller.signal
    }).then(function (r) {
      if (r.aborted) return;
      return classify(r, gen).then(function (go) {
        if (!go || flight !== token) return;
        if (!r.ok || !isObject(r.json) || r.json.ok !== true || !isObject(r.json.resumen)
            || !Number.isSafeInteger(r.json.resumen.resueltos) || r.json.resumen.resueltos < 0) {
          if (r.ok) toast(T("admin.hsi.rescan_failed", null, "No se pudo revisar."));
          else toast(errorText(r, T("admin.hsi.rescan_failed", null, "No se pudo revisar.")));
          endMutation(token);
          return;
        }
        if (r.json.panel) adoptPanel(r.json.panel);
        var n = r.json.resumen.resueltos;
        toast(n
          ? Tn("admin.hsi.rescan_resolved", n, null, "Se resolvió {n} conflicto.", "Se resolvieron {n} conflictos.")
          : T("admin.hsi.rescan_no_change", null, "Sin cambios: siguen igual."));
        endMutation(token);
        return loadPanel();
      });
    });
  }

  function retry() {
    if (!alive() || flight) return;
    loadFailed = false;
    panel = null;
    estado = null;
    loadState();
  }

  function onClick(event) {
    if (!alive() || !event || !event.target) return;
    var el = event.target;
    if (el.closest && el.closest("#hsi-retry-btn")) { retry(); return; }
    if (el.closest && el.closest("#hsi-rescan-btn")) { rescan(); return; }
    var apply = el.closest && el.closest("[data-hsi-apply]");
    if (apply) applyConflict(apply.dataset && apply.dataset.cf, apply.dataset && apply.dataset.accion);
  }

  function onChange(event) {
    if (!alive() || !event || !event.target) return;
    var el = event.target;
    var sel = el.closest && el.closest(".hsi-alt");
    if (sel && sel.value) {
      var action = sel.value;
      // This is an action picker, not persisted state. Reset immediately so
      // cancel, refusal, or a failed request leaves the same action available.
      sel.value = "";
      applyConflict(sel.dataset && sel.dataset.cf, action);
    }
  }

  function bind() {
    injectCss();
    host.addEventListener("click", onClick);
    host.addEventListener("change", onChange);
    var here = loc();
    var history = hist();
    if (here && here.search && history && history.replaceState) {
      history.replaceState({}, "", "/admin/hsi");
    }
    clearStatus();
    return loadState();
  }

  function take(mod, name, check) {
    if (!mod || !check(mod)) throw new Error("Invalid HSI dependency: " + name);
    return mod;
  }

  mounts.set(host, dispose);
  var dependencies = Promise.all([
    importStatic("platform/admin-session.mjs"),
    importStatic("platform/i18n.mjs"),
    importStatic("platform/dates.mjs"),
    importStatic("platform/format.mjs"),
  ]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function (mods) {
    if (!alive()) return;
    Session = take(mods[0], "platform/admin-session.mjs", function (m) { return typeof m.confirmar === "function"; });
    I18n = take(mods[1], "platform/i18n.mjs", function (m) { return typeof m.t === "function"; });
    Dates = take(mods[2], "platform/dates.mjs", function (m) { return typeof m.corta === "function" && typeof m.relativo === "function"; });
    Format = take(mods[3], "platform/format.mjs", function (m) { return typeof m.nombre === "function"; });
    return bind();
  }).catch(function (error) {
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else renderLoadError(false);
  });
  return dispose;
}
