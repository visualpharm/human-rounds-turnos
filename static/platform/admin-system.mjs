/* admin-system.js — sección «Sistema» del panel: página de setup / estado de
 * la integración. Muestra, agrupado en clusters, qué está conectado y qué falta
 * (aplicación, dominio, WhatsApp, email, almacenamiento, OpenRouter).
 *
 * It is loaded by the direct System screen and injects its own CSS once.
 *
 * Datos: GET /api/admin/system (solo_admin). Cada cluster: {clave,titulo,estado,
 * resumen,items:[{label,valor,estado,detalle?}]}. estado ∈ ok|warn|error|na. */
const mounts = new WeakMap();
export function mount(host, options = {}) {
  if (!host) throw new Error("System host is required");
  if (mounts.has(host)) mounts.get(host)();
  var disposed = false;
  var request = null;
  var badge = document.getElementById("nav-sistema-badge");
  function alive() { return !disposed && host.isConnected !== false; }
  function dispose() {
    disposed = true;
    if (request) request.abort();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing System dependency: " + name));
  }
  var sessionReady = importStatic("platform/admin-session.mjs");
  var Dates = null;
  var datesReady = window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"]
    ? importStatic("platform/dates.mjs").then(function (m) { Dates = m; })
    : Promise.resolve();

  var I18n = null;
  var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function inyectarCss() {
    if (document.getElementById("sistema-css")) return;
    var st = document.createElement("style");
    st.id = "sistema-css";
    st.textContent = [
      ".sis-card{background:var(--panel);border:1px solid var(--border,#e4e4e0);border-radius:12px;padding:18px 20px;margin:0 0 16px;}",
      ".sis-card-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:12px;}",
      ".sis-card-head h2{font-size:17px;margin:0;font-weight:650;}",
      // Everything healthy: one closed line at the end of the screen.
      ".sis-rest{border-top:1px solid var(--border,#e4e4e0);}",
      ".sis-rest>summary{cursor:pointer;padding:12px 0;min-height:44px;box-sizing:border-box;font-size:17px;font-weight:650;color:var(--text,#1c1c1c);}",
      ".sis-rest[open]>summary{margin-bottom:8px;}",
      ".sis-dot{display:inline-block;width:9px;height:9px;border-radius:50%;flex:0 0 auto;}",
      ".sis-dot--ok{background:var(--done);}",
      ".sis-dot--warn{background:var(--warn);}",
      ".sis-dot--error{background:var(--danger);}",
      ".sis-dot--na{background:var(--text-4);}",
      // table-layout:fixed + ancho FIJO en la columna de etiqueta para que
      // TODAS las tarjetas (dominio, WhatsApp, email, almacenamiento…) alineen
      // el valor en la misma x. Con el viejo width:1%+nowrap cada tabla ajustaba
      // su etiqueta a su propio label más largo ("Dominio definitivo" vs
      // "API key") y los valores quedaban desalineados de una tarjeta a la otra.
      ".sis-tabla{width:100%;border-collapse:collapse;table-layout:fixed;}",
      ".sis-tabla td{padding:7px 6px;vertical-align:top;border-top:1px solid var(--border,#eee);}",
      ".sis-tabla tr:first-child td{border-top:0;}",
      // La etiqueta envuelve dentro de sus 200px: una larga («Consulta pública
      // de respaldo») baja de línea en vez de cortarse o pisar el valor.
      ".sis-td-lab{color:var(--hint-ink,var(--text-2));width:200px;padding-right:20px;overflow-wrap:break-word;}",
      ".sis-td-val{color:var(--text,#1c1c1c);}",
      ".sis-td-val .sis-dot{margin-right:8px;position:relative;top:-1px;}",
      ".sis-detalle{display:block;color:var(--text,#20231f);font-size:16px;margin-top:2px;overflow-wrap:anywhere;}",
      ".sis-fix{color:var(--accent,#0e8b50);font-size:13px;font-weight:600;text-decoration:none;}",
      ".sis-fix:hover{color:var(--accent-hover);}",
      "@media(max-width:640px){.sis-tabla td{display:block;border-top:0;padding:2px 0;}",
      ".sis-tabla tr{display:block;border-top:1px solid var(--border,#eee);padding:8px 0;}",
      ".sis-tabla tr:first-child{border-top:0;}.sis-td-lab{width:auto;padding-right:0;}",
      ".sis-td-val,.sis-fix{overflow-wrap:anywhere;}}",
    ].join("");
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  // Only a warning or an error carries a dot; green and «not applicable» are plain text.
  function dot(estado) {
    return estado === "warn" || estado === "error" ? '<span class="sis-dot sis-dot--' + estado + '"></span>' : "";
  }
  function asks(estado) { return estado === "warn" || estado === "error"; }
  function sameAsTitle(it, c) {
    return String(it.label || "").trim().toLowerCase() === String(c.titulo || "").trim().toLowerCase();
  }

  function actualizarBadge(data) {
    if (!alive() || !badge || badge.isConnected === false || !data) return;
    var total = Number(data.total || (data.clusters || []).length || 0);
    var ok = Number(data.ok || 0);
    var pct = total ? Math.round(ok * 100 / total) : 0;
    badge.textContent = pct + "%";
    badge.title = T("admin.system.sistema.badge_title", { ok: ok, total: total }, "{ok} de {total} integraciones en verde");
    badge.setAttribute("aria-label", badge.title);
    badge.style.display = "";
  }

  function render(data) {
    var sec = host;
    if (!alive()) return;
    if (!data) { sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.common.loading", null, "Cargando…")) + '</p>'; return; }
    var clusters = data.clusters || [];
    actualizarBadge(data);
    // The screen opens on what asks for something: each cluster with a
    // warning or an error shows those rows only. Everything healthy waits
    // behind ONE closed line at the end. No «N de M en verde» counter and no
    // dot on a green row: the only marks are the ones that need a hand.
    var top = [], rest = [];
    clusters.forEach(function (c) {
      var filas = (c.items || []).map(function (it) {
        var detail = it.detalle || "";
        if (Dates && it.backup && it.backup.last_success) {
          detail = Dates.relativo(it.backup.last_success) + (detail ? " · " + detail : "");
        }
        // `cuando` is an instant with the installation's offset: «hace 5
        // minutos» while fresh, then the installation's day and hour — never
        // the reader's clock; the server's plain date is the fallback.
        var valor = it.valor;
        if (Dates && Dates.instanteServidor && it.cuando) valor = Dates.instanteServidor(it.cuando) || it.valor;
        var det = detail ? '<span class="sis-detalle">' + esc(detail) + "</span>" : "";
        // Un item en rojo/amarillo con un campo de Settings detrás: link
        // directo a ese campo, no sólo el nombre del problema (responsable del producto, fase 4:
        // "cada item en rojo linkea a su campo de Settings").
        var settingsHref = "/admin/settings?field=" + encodeURIComponent(it.campo || "");
        var link = (it.campo && it.estado !== "ok")
          ? ' <a class="sis-fix" href="' + settingsHref + '">' + esc(T("admin.system.sistema.configure_link", null, "Configurar →")) + '</a>' : "";
        if (it.ai_register) link = ' <a class="sis-fix" href="/admin/ai-register">' + esc(T("ai.register.title", null, "Registro de IA")) + '</a>';
        // A row may name the screen that shows it in full («Ver por centro →»):
        // a same-site admin path with its text, both from the server.
        if (it.enlace && /^\/admin\/[a-z0-9/-]*$/.test(it.enlace.href || "") && it.enlace.texto) {
          link = ' <a class="sis-fix" href="' + esc(it.enlace.href) + '">' + esc(it.enlace.texto) + '</a>';
        }
        var value = "<td class='sis-td-val'" + (sameAsTitle(it, c) ? " colspan='2'" : "") + ">" +
          dot(it.estado) + esc(valor) + link + det + "</td>";
        // A row labelled like its own cluster («Dominio» under «Dominio»)
        // keeps only the value.
        return "<tr>" + (sameAsTitle(it, c) ? "" : "<td class='sis-td-lab'>" + esc(it.label) + "</td>") + value + "</tr>";
      });
      function table(rows) { return '<table class="sis-tabla"><tbody>' + rows.join("") + "</tbody></table>"; }
      // Only the title: the cluster's `resumen` repeated what its own rows
      // already say («Email sin conectar» over «Sin configurar») — critical
      // review 08.11, D10 (responsable del producto: no small print under titles).
      function card(rows) {
        return '<div class="sis-card" data-cluster="' + esc(c.clave || "") + '"><div class="sis-card-head"><h2>' +
          esc(c.titulo) + "</h2></div>" + table(rows) + "</div>";
      }
      // One card per cluster: a cluster that asks for something (as a whole
      // or in any row) goes on top whole, so its flagged row keeps its
      // context; the dot marks only the flagged rows.
      var flagged = asks(c.estado) || (c.items || []).some(function (it) { return asks(it.estado); });
      (flagged ? top : rest).push(card(filas));
    });
    sec.innerHTML = top.join("") + (rest.length
      ? '<details class="sis-rest"' + (top.length ? "" : " open") + "><summary>" +
        esc(T("admin.system.sistema.rest", null, "Sin novedades")) + "</summary>" + rest.join("") + "</details>"
      : "");
  }

  function cargar(session) {
    if (!alive()) return;
    var sec = host;
    if (!sec.innerHTML) sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.common.loading", null, "Cargando…")) + '</p>';
    request = new AbortController();
    return fetch("/api/admin/system", { credentials: "same-origin", signal: request.signal })
      .then(function (r) {
        if (!alive()) return null;
        var boundary = (r.status === 401 || r.status === 403)
          ? session.confirmar(r, undefined, alive)
          : Promise.resolve(false);
        return boundary.then(function (transitioned) {
          if (!alive()) return null;
          if (transitioned) { dispose(); return null; }
          if (!r.ok) throw new Error("System request failed");
          return r.json();
        });
      })
      .then(function (j) {
        if (!alive()) return;
        if (!j || !Array.isArray(j.clusters)) throw new Error("Invalid System response");
        render(j);
      });
  }

  mounts.set(host, dispose);
  var dependencies = Promise.all([sessionReady, i18nReady, datesReady]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function (modules) {
    if (!alive()) return;
    inyectarCss();
    host.style.display = "";
    return cargar(modules[0]);
  }).catch(function (error) {
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else host.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.system.sistema.load_error", null, "No se pudo cargar el estado del sistema.")) + '</p>';
  });
  return dispose;
}
