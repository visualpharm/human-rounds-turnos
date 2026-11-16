/* Direct admin screen for effective per-doctor symptom capture.
 *
 * Canonical ES module owned by modules/intake. Named exports; no
 * SanaEntrevistas / sanaRouteEntrevistas globals, no classic twin.
 * Fingerprinted through the intake module registry. The template imports
 * this URL and calls boot() after a failed-import recovery is in place.
 */
"use strict";

function importDep(name) {
  var host =
    (typeof globalThis !== "undefined" && globalThis.window) ||
    (typeof window !== "undefined" ? window : null);
  var fromManifest = host && host.SANA_STATIC && host.SANA_STATIC[name];
  if (fromManifest) return import(fromManifest);
  if (host && typeof host.sanaImportStatic === "function") {
    return host.sanaImportStatic(name);
  }
  return Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
}

var Format = await importDep("platform/format.mjs");
// The one progressive-disclosure rule of the panel (empty / no-match / data).
var Filters = await importDep("platform/admin-filter-controls.mjs");
var sessionReady = importDep("platform/admin-session.mjs");
var I18n = await importDep("platform/i18n.mjs").catch(function () {
  return null;
});
var Explainer = await importDep("platform/intake-explainer.mjs").catch(function () {
  return null;
});
// The panel's one table engine picks the list up on its own (widths, and a
// column empty in every row — «PDF» while no doctor captures — left out).
// Beside the screen, never in its way: a failed load leaves the table as is.
importDep("platform/admin-table.mjs").catch(function () {
  return null;
});
var DATA = null,
  ROOT = null,
  SEARCH = "",
  FILTER = "all",
  ANCHOR = null,
  LAST_SHIFT = false,
  PREVIEW = false,
  MOUNT = 0;
var SORT = { key: "nombre", dir: 1 },
  SAVING = Promise.resolve();
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
// A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c];
  });
}
function api(url, opts) {
  opts = opts || {};
  opts.credentials = "same-origin";
  return fetch(url, opts).then(function (r) {
    var boundary =
      (r.status === 401 || r.status === 403)
        ? sessionReady.then(function (mod) { return mod.confirmar(r); })
        : Promise.resolve(false);
    return boundary.then(function (transitioned) {
      if (transitioned) throw new Error("401");
      return r
        .json()
        .catch(function () {
          return {};
        })
        .then(function (b) {
          if (!r.ok)
            throw new Error(
              errText(
                b,
                T(
                  "admin.reports.entrevistas.error_guardar",
                  null,
                  "No se pudo guardar.",
                ),
              ),
            );
          return b;
        });
    });
  });
}
function root() {
  return ROOT || document.getElementById("sec-entrevistas");
}
function owns(mount, host) {
  return MOUNT === mount && ROOT === host;
}
function name(d) {
  return Format.nombre(d.nombre);
}
function norm(v) {
  v = String(v || "").toLocaleLowerCase();
  return v.normalize ? v.normalize("NFD").replace(/[\u0300-\u036f]/g, "") : v;
}
function visibleDoctors() {
  var q = norm(SEARCH.trim());
  return (DATA.medicos || [])
    .filter(function (d) {
      return (
        (FILTER === "all" ||
          (FILTER === "active" && d.efectivo) ||
          (FILTER === "inactive" && !d.efectivo)) &&
        (!q || norm(name(d) + " " + (d.especialidad || "")).indexOf(q) >= 0)
      );
    })
    .sort(function (a, b) {
      var x = SORT.key === "especialidad" ? a.especialidad : name(a),
        y = SORT.key === "especialidad" ? b.especialidad : name(b);
      return (
        SORT.dir *
        String(x || "").localeCompare(String(y || ""), undefined, {
          sensitivity: "base",
        })
      );
    });
}
function injectCss() {
  if (document.getElementById("ent-css")) return;
  // .ent-mobile-all/.ent-check: align-items:flex-start + flex:0 0 auto para
  // que el checkbox no baje al medio ni se achique si la etiqueta envuelve.
  var s = document.createElement("style");
  s.id = "ent-css";
  s.textContent =
    "#sec-entrevistas{max-width:none;min-width:0;padding:0}.ent-tools{margin:0 0 12px}.ent-tools .cnt-toolbar-filters{width:100%}.ent-tools .cnt-search{max-width:360px}.ent-mobile-all{display:none}.ent-table-wrap{max-width:100%;overflow:visible}.ent-table{width:100%}.ent-table th,.ent-table td{vertical-align:middle;white-space:normal}.ent-table td[data-col=nombre]{font-weight:650}.ent-check{width:18px;height:18px;margin-top:0;flex:0 0 auto;accent-color:var(--accent);cursor:pointer}.ent-check:disabled{cursor:wait}.ent-pdf{color:var(--accent);font-weight:600;text-decoration:none}.ent-pdf:hover{color:var(--accent-hover)}.ent-message{display:block;margin-top:5px;color:var(--danger);font-size:13px}.ent-empty{padding:24px 10px!important;color:var(--hint-ink)!important}@media(max-width:760px){.ent-tools{margin:0 16px}.ent-tools .cnt-toolbar-filters{display:grid;grid-template-columns:minmax(0,1fr)}.ent-tools .cnt-search,.ent-tools .adm-sel{width:100%;max-width:none;min-width:0;min-height:44px;box-sizing:border-box}.ent-mobile-all{display:flex;gap:8px;align-items:flex-start;line-height:22px;padding:11px 0}.ent-mobile-all .ent-check{margin-top:2px}.ent-table thead,.ent-table colgroup,.ent-table col{display:none}.ent-table,.ent-table tbody,.ent-table tr,.ent-table td{display:block;width:100%!important;box-sizing:border-box}.ent-table tr{display:grid;grid-template-columns:minmax(0,1fr) 44px auto;gap:4px 12px;align-items:center;padding:14px 16px}.ent-table tbody tr:nth-child(even){background:var(--bg-2)}.ent-table td{padding:0;border:0}.ent-table td[data-col=nombre]{grid-column:1;font-size:16px}.ent-table td[data-col=especialidad]{grid-column:1;grid-row:2}.ent-table td[data-col=habilitacion]{grid-column:2;grid-row:1 / span 2}.ent-table td[data-col=cartel]{grid-column:3;grid-row:1 / span 2}.ent-table td[data-col=especialidad]:empty,.ent-table td[data-col=cartel]:empty{display:none}.ent-check{width:32px;height:32px}.ent-pdf{display:inline-flex;align-items:center;min-height:44px}.ent-empty{display:block!important;padding:22px 16px!important}.ent-empty::before{content:none!important}}";
  document.head.appendChild(s);
  // Headers are the panel's (.cnt-th: 13 px, grey) with no padding of their
  // own, so a header's text starts where its cells' text starts.
  s.textContent += ".ent-table td,.ent-pdf{font-size:16px}.ent-tools .cnt-search{flex-basis:320px}";
  s.textContent += ".ent-th-check{display:inline-flex;align-items:center;gap:8px}.ent-th-check .ent-check{margin:0}";
  s.textContent += ".ent-hit{display:inline-flex;align-items:center;justify-content:flex-start;min-width:32px;min-height:32px;cursor:pointer}.ent-hit input{margin:0}.ent-empty button,[data-ent-retry]{min-height:44px}@media(max-width:760px){.ent-hit{min-width:44px;min-height:44px;justify-content:center}}";
  // The shared table engine keeps desktop pixel widths when entering its
  // compact mode. This four-column screen still uses a table on tablets.
  s.textContent += "@media(min-width:761px) and (max-width:1024px){.ent-table{width:100%!important;table-layout:auto!important}.ent-table col{width:auto!important}}";
  s.textContent += "@media(max-width:760px){.ent-table tr{grid-template-columns:minmax(0,1fr) 44px 34px}}";
  // Select-all has nothing to act on when no doctor is listed.
  s.textContent += ".ent-mobile-all[hidden]{display:none!important}";
  s.textContent += ".ent-explainer{margin:0 0 32px}@media(max-width:760px){.ent-explainer{margin:0 16px 24px}}";
}
// Activa/Inactiva follows the shared filter rule (Filters.offerFilter): a
// roster worth filtering that has both, or the filter already set so it can
// be cleared — progressive disclosure.
function stateFilterShown() {
  var ds = DATA.medicos || [];
  var on = ds.some(function (d) { return d.efectivo; }),
    off = ds.some(function (d) { return !d.efectivo; });
  return Filters.offerFilter((on ? 1 : 0) + (off ? 1 : 0), FILTER !== "all", ds.length);
}
function filtersHtml() {
  function o(v, label) {
    return (
      '<option value="' +
      v +
      '"' +
      (FILTER === v ? " selected" : "") +
      ">" +
      esc(label) +
      "</option>"
    );
  }
  return (
    '<div class="cnt-toolbar ent-tools"><div class="cnt-toolbar-filters"><input class="adm-search cnt-search" id="ent-search" type="search" value="' +
    esc(SEARCH) +
    '" placeholder="' +
    esc(
      T(
        "admin.reports.entrevistas.buscar",
        null,
        "Médico o especialidad",
      ),
    ) +
    '" aria-label="' +
    esc(
      T(
        "admin.reports.entrevistas.buscar",
        null,
        "Médico o especialidad",
      ),
    ) +
    '">' +
    (stateFilterShown()
      ? '<select class="adm-sel" id="ent-filter" aria-label="' +
        esc(T("admin.reports.entrevistas.filtrar", null, "Filtrar por estado")) +
        '">' +
        o("all", T("admin.reports.entrevistas.filtro_estado", null, "Estado")) +
        o(
          "active",
          T("admin.reports.entrevistas.filtro_activa", null, "Activa"),
        ) +
        o(
          "inactive",
          T("admin.reports.entrevistas.filtro_inactiva", null, "Inactiva"),
        ) +
        "</select>"
      : "") +
    '<label class="ent-mobile-all"' + (visibleDoctors().length ? "" : " hidden") + '><input class="ent-check" id="ent-all-mobile" type="checkbox"> ' +
    esc(
      T(
        "admin.reports.entrevistas.todos_aria",
        null,
        "Cambiar médicos visibles",
      ),
    ) +
    "</label></div></div>"
  );
}
function row(d) {
  var pdf =
    d.efectivo && d.pdf_url
      ? '<a class="ent-pdf" href="' +
        esc(d.pdf_url) +
        '">' +
        esc(T("admin.reports.entrevistas.pdf", null, "PDF")) +
        "</a>"
      : "";
  return (
    '<tr data-medico-row="' +
    esc(d.id) +
    '"><td data-col="nombre">' +
    esc(name(d)) +
    '</td><td data-col="especialidad">' +
    esc(d.especialidad || "") +
    '</td><td data-col="habilitacion" data-admin-binary="editable"><label class="ent-hit"><input class="ent-check" type="checkbox" data-effective-medico="' +
    esc(d.id) +
    '"' +
    (d.efectivo ? " checked" : "") +
    ' aria-label="' +
    esc(
      T(
        "admin.reports.entrevistas.ajuste_medico_aria",
        { name: name(d) },
        "Captura de síntomas para " + name(d),
      ),
    ) +
    '"></label><span class="ent-message" data-row-message="' +
    esc(d.id) +
    '"></span></td><td data-col="cartel">' +
    pdf +
    "</td></tr>"
  );
}
// The panel's sortable header (Médicos, Pacientes, Usuarios): the th itself
// sorts, by click or Enter/Space, with the same ▲/▼ — no button of its own,
// whose padding pushed the header's text off its cells' edge.
function sortHeader(key, label) {
  var on = SORT.key === key;
  return (
    '<th scope="col" data-col="' +
    key +
    '" data-ent-sort="' +
    key +
    '" tabindex="0" aria-sort="' +
    (on ? (SORT.dir > 0 ? "ascending" : "descending") : "none") +
    '" class="cnt-th cnt-th-sortable' +
    (on ? " on" : "") +
    '">' +
    esc(label) +
    (on ? (SORT.dir > 0 ? " ▲" : " ▼") : "") +
    "</th>"
  );
}
// Progressive disclosure (responsable del producto, 2026-10-26): no doctors → no counter, search,
// filter, select-all or header — only what will appear here and where it
// comes from, plus the way to add the first doctor.
function emptyHtml() {
  return Filters.emptyStateHtml({
    title: T(
      "admin.reports.entrevistas.empty_title",
      null,
      "Todavía no hay médicos: cada médico que cargues en Médicos aparece acá para activar su captura de síntomas.",
    ),
    action: {
      label: T("admin.reports.entrevistas.new_doctor", null, "Nuevo médico"),
      attrs: { "data-admin-create": "true", "data-href": "/admin/doctors?item=new" },
    },
  });
}
// Search or Activa/Inactiva hid every doctor: the tools stay, the table goes.
function noMatchHtml() {
  return Filters.noMatchHtml({
    title: T(
      "admin.reports.entrevistas.no_match",
      null,
      "Ningún médico coincide con estos filtros.",
    ),
    clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros"),
    clearAttrs: { "data-ent-clear": "" },
  });
}
function listHtml(ds) {
  if (!ds.length) return noMatchHtml();
  return (
    '<div class="cnt-table-wrap ent-table-wrap"><table class="cnt-table ent-table" data-admin-repeated-mutation="true" data-admin-shift-range="true"><thead><tr>' +
    sortHeader(
      "nombre",
      T("admin.reports.entrevistas.col_medico", null, "Médico"),
    ) +
    sortHeader(
      "especialidad",
      T("admin.reports.entrevistas.col_especialidad", null, "Especialidad"),
    ) +
    // Select-all starts on the same edge as the rows' checkboxes (.ent-hit
    // keeps them at the cell's left), its label in the panel's header look.
    '<th scope="col" data-col="habilitacion" class="cnt-th"><span class="ent-th-check"><input class="ent-check" id="ent-all" type="checkbox" aria-label="' +
    esc(
      T(
        "admin.reports.entrevistas.todos_aria",
        null,
        "Cambiar la captura de los médicos visibles",
      ),
    ) +
    '">' +
    esc(T("admin.reports.entrevistas.col_habilitacion", null, "Captura")) +
    '</span></th><th scope="col" data-col="cartel" class="cnt-th">' +
    esc(T("admin.reports.entrevistas.pdf", null, "PDF")) +
    '</th></tr></thead><tbody id="ent-tbody">' +
    ds.map(row).join("") +
    "</tbody></table></div>"
  );
}
function tableHtml() {
  var ds = visibleDoctors(),
    total = (DATA.medicos || []).length;
  var state = Filters.disclosure({
    total: total,
    shown: ds.length,
    filtered: !!SEARCH.trim() || FILTER !== "all",
  });
  Filters.applyDisclosure(root(), state);
  if (state === "empty") return emptyHtml();
  // Like the panel's other lists: no heading repeating what the table shows
  // and no «N de N médicos» counter — the search and the rows say it.
  return (
    filtersHtml() +
    '<div id="ent-list">' +
    listHtml(ds) +
    "</div>"
  );
}
function updateHeader(r) {
  var bs = [].slice.call(r.querySelectorAll("[data-effective-medico]")),
    all = r.querySelector("#ent-all"),
    on = bs.filter(function (b) {
      return b.checked;
    }).length;
  if (!all) return;
  all.checked = !!bs.length && on === bs.length;
  all.indeterminate = on > 0 && on < bs.length;
  all.disabled = !bs.length;
  var mobile = r.querySelector("#ent-all-mobile");
  if (mobile) {
    mobile.checked = all.checked;
    mobile.indeterminate = all.indeterminate;
    mobile.disabled = all.disabled;
  }
}
// Qué es y qué gana el médico, una sola vez arriba de la tabla (el mismo
// bloque que la pestaña Síntomas del médico). Abierto mientras ningún médico
// la tiene activa; después queda plegado para no empujar la tabla.
function renderExplainer() {
  var host = document.getElementById("ent-explainer");
  if (!host || !Explainer || host.firstChild) return;
  Explainer.ensureCss(document);
  var anyOn = (DATA.medicos || []).some(function (d) { return d.efectivo; });
  host.innerHTML = Explainer.details(T, { className: "ent-explainer", open: !anyOn });
}
function render() {
  var r = root();
  if (!r || !DATA) return;
  renderExplainer();
  r.innerHTML = tableHtml();
  updateHeader(r);
}
function renderList() {
  var r = root();
  var rows = visibleDoctors();
  var tbody = r.querySelector("#ent-tbody");
  var list = r.querySelector("#ent-list");
  var mobileAll = r.querySelector(".ent-mobile-all");
  Filters.applyDisclosure(r, Filters.disclosure({
    total: (DATA.medicos || []).length,
    shown: rows.length,
    filtered: !!SEARCH.trim() || FILTER !== "all",
  }));
  // Typing keeps the search input: only the list below it is repainted, and
  // the table is swapped for the no-match state (and back) as a whole.
  if (tbody && rows.length) tbody.innerHTML = rows.map(row).join("");
  else if (list) list.innerHTML = listHtml(rows);
  if (mobileAll) mobileAll.hidden = !rows.length;
  updateHeader(r);
}
function apply(ids, enabled) {
  (DATA.medicos || []).forEach(function (d) {
    if (ids.indexOf(String(d.id)) >= 0) {
      d.efectivo = enabled;
      d.override = enabled;
      d.pdf_url = enabled ? d.cartel_url + "?format=pdf" : null;
    }
  });
}
function save(ids, enabled, focus) {
  var host = root(),
    mount = MOUNT,
    preview = PREVIEW,
    before = {};
  (DATA.medicos || []).forEach(function (d) {
    if (ids.indexOf(String(d.id)) >= 0)
      before[d.id] = {
        efectivo: d.efectivo,
        override: d.override,
        pdf_url: d.pdf_url,
      };
  });
  apply(ids, enabled);
  render();
  if (preview) {
    requestAnimationFrame(function () {
      if (!owns(mount, host)) return;
      var previewControl = host.querySelector(
        '[data-effective-medico="' +
          CSS.escape(String(focus || ids[0])) +
          '"]',
      );
      if (previewControl) previewControl.focus();
    });
    return Promise.resolve();
  }
  host
    .querySelectorAll("input,select,button")
    .forEach(function (el) {
      el.disabled = true;
    });
  SAVING = SAVING.then(function () {
    return api("/api/admin/intake/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ medico_ids: ids, enabled: enabled }),
    });
  })
    .then(
      function () {
        if (owns(mount, host)) render();
      },
      function (e) {
        if (!owns(mount, host)) return;
        (DATA.medicos || []).forEach(function (d) {
          if (before[d.id]) Object.assign(d, before[d.id]);
        });
        render();
        var m = host.querySelector(
          '[data-row-message="' + CSS.escape(String(focus || ids[0])) + '"]',
        );
        if (m)
          m.textContent = errText(
            e,
            T(
              "admin.reports.entrevistas.error_guardar",
              null,
              "No se pudo guardar.",
            ),
          );
      },
    )
    .then(function () {
      requestAnimationFrame(function () {
        if (!owns(mount, host)) return;
        var el = host.querySelector(
          '[data-effective-medico="' +
            CSS.escape(String(focus || ids[0])) +
            '"]',
        );
        if (el) el.focus();
      });
    });
  return SAVING;
}
function wire(r) {
  if (r.__entWired) return;
  r.__entWired = true;
  r.addEventListener("input", function (e) {
    if (r !== root()) return;
    if (e.target.id === "ent-search") {
      SEARCH = e.target.value;
      ANCHOR = null;
      renderList();
    }
  });
  r.addEventListener("change", function (e) {
    if (r !== root()) return;
    if (e.target.id === "ent-filter") {
      FILTER = e.target.value;
      ANCHOR = null;
      render();
      return;
    }
    var box = e.target.closest("[data-effective-medico]");
    if (box) {
      var ids = [box.getAttribute("data-effective-medico")],
        listed = visibleDoctors().map(function (d) {
          return String(d.id);
        });
      if (LAST_SHIFT && ANCHOR && listed.indexOf(ANCHOR) >= 0) {
        var a = listed.indexOf(ANCHOR),
          b = listed.indexOf(ids[0]);
        ids = listed.slice(Math.min(a, b), Math.max(a, b) + 1);
      }
      ANCHOR = box.getAttribute("data-effective-medico");
      save(ids, box.checked, ANCHOR);
      return;
    }
    if (e.target.id === "ent-all" || e.target.id === "ent-all-mobile")
      save(
        visibleDoctors().map(function (d) {
          return String(d.id);
        }),
        e.target.checked,
      );
  });
  r.addEventListener("click", function (e) {
    if (r !== root()) return;
    if (e.target.closest("[data-ent-clear]")) {
      SEARCH = ""; FILTER = "all"; ANCHOR = null;
      render();
      root().querySelector("#ent-search").focus();
      return;
    }
    if (e.target.closest("[data-ent-retry]")) { route(); return; }
    var create = e.target.closest("[data-admin-create][data-href]");
    if (create) { location.assign(create.getAttribute("data-href")); return; }
    var clickedBox = e.target.closest("[data-effective-medico]");
    if (clickedBox) {
      LAST_SHIFT = e.shiftKey;
      return;
    }
    // The resize handle inside a header resizes, it does not sort.
    if (e.target.closest("[data-tbl-res]")) return;
    var s = e.target.closest("[data-ent-sort]");
    if (s) sortBy(s.getAttribute("data-ent-sort"), false);
  });
  // Same keys as the other lists' sortable headers (Enter / Space).
  r.addEventListener("keydown", function (e) {
    if (r !== root() || (e.key !== "Enter" && e.key !== " ")) return;
    var s = e.target.closest && e.target.closest("[data-ent-sort]");
    if (!s) return;
    e.preventDefault();
    sortBy(s.getAttribute("data-ent-sort"), true);
  });
}
function sortBy(key, keepFocus) {
  SORT = { key: key, dir: SORT.key === key ? -SORT.dir : 1 };
  ANCHOR = null;
  render();
  if (!keepFocus) return;
  var th = root().querySelector('[data-ent-sort="' + key + '"]');
  if (th) th.focus();
}
function route() {
  if (PREVIEW) return;
  injectCss();
  var host = document.getElementById("sec-entrevistas");
  if (!host) return;
  ROOT = host;
  var mount = ++MOUNT;
  wire(host);
  if (DATA) return render();
  host.innerHTML =
    '<p class="adm-empty">' +
    esc(T("admin.reports.entrevistas.cargando", null, "Cargando…")) +
    "</p>";
  return api("/api/admin/intake/settings")
    .then(function (d) {
      if (!owns(mount, host)) return;
      DATA = d;
      render();
    })
    .catch(function () {
      if (!owns(mount, host)) return;
      host.innerHTML =
        '<p class="adm-empty">' +
        esc(
          T(
            "admin.reports.entrevistas.error_carga",
            null,
            "No se pudo cargar.",
          ),
        ) +
        '</p><button class="btn" type="button" data-ent-retry>' +
        esc(T("admin.reports.entrevistas.retry", null, "Reintentar")) + '</button>';
    });
}

export function preview(host, data) {
  ++MOUNT;
  PREVIEW = true;
  ROOT = host;
  DATA = JSON.parse(JSON.stringify(data));
  injectCss();
  wire(host);
  render();
  return Promise.resolve(host);
}

export function boot() {
  return Promise.resolve().then(route);
}
