/* static/platform/admin-knowledge.mjs — wiki for the appointment assistant.  The server keeps the source
   of truth; this module deliberately only manages the admin experience. */
"use strict";
function importDep(name, nodeUrl) {
  var url = typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC[name];
  return url ? import(url) : import(nodeUrl);
}
var sessionReady = importDep("platform/admin-session.mjs", new URL("./admin-session.mjs", import.meta.url));
sessionReady.catch(function () {});
var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url)).catch(function () { return null; });
function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
// Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
function Tn(key, n, vars, one, other) {
  if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
  var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
  return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
}
// The panel's one progressive-disclosure rule (empty / no-match / data).
var Filters = await importDep("platform/admin-filter-controls.mjs", new URL("./admin-filter-controls.mjs", import.meta.url));
// A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
  // Friendly dates through the shared formatter (dates.mjs) — never raw ISO
  // next to a person (the product owner, 2026-09-05).
  var Dates = null;
  var datesReady = importDep("platform/dates.mjs", new URL("./dates.mjs", import.meta.url)).then(function (m) { Dates = m; }).catch(function () {});
  function whenText(iso) { return (iso && Dates) ? Dates.dia(iso) : ""; }
  function reviewText(verifiedIso, days) { var due = plusDays(verifiedIso, days); return due ? T("admin.content.knowledge.proxima_revision", { when: whenText(due) }, "Próxima revisión: {when}") : ""; }
  function refreshWhen(form) {
    form.querySelectorAll("[data-kn-when]").forEach(function (el) {
      var name = el.dataset.knWhen;
      if (name === "review") el.textContent = reviewText(form.elements.source_verified_at && form.elements.source_verified_at.value, form.elements.source_review_every_days && form.elements.source_review_every_days.value);
      else el.textContent = whenText(form.elements[name] && form.elements[name].value);
    });
  }
  function plusDays(iso, days) { var d = /^\d{4}-\d{2}-\d{2}$/.test(iso || "") ? new Date(iso + "T12:00:00") : null; if (!d || !days) return ""; d.setDate(d.getDate() + Number(days)); return d.toISOString().slice(0, 10); }
  // Start with categories closed: a growing wiki must not make navigation a
  // second, taller document. Search and a selected deep link reveal the branch.
  var state = { items: [], selected: null, saving: false, collapsed: new Set(), treeInitialized: false };
  var dialog = { inerted: [], keydown: null, returnHref: "", returnId: "", returnNew: "" };
  // Pinamar/Provincia de Buenos Aires son nombres de jurisdicción real (como
  // un nombre de lugar, no se traducen); Nación/Global/Interno sí son
  // categorías de chrome y viajan por T(). "global" (packs/global/
  // conocimiento.json, fase 3b 2026-08-06) es el nivel más agnóstico de
  // país -- contenido válido para cualquier instalación.
  var JURIS = { pinamar: "Pinamar", provincia: "Provincia de Buenos Aires",
    nacion: T("admin.content.knowledge.jur_nacion", null, "Nación"),
    global: T("admin.content.knowledge.jur_global", null, "Global"),
    interno: T("admin.content.knowledge.jur_interno", null, "Interno") };

  // One flat layout, no boxed panes (the product owner, 2026-09-05): the tree reads like
  // the admin sidebar (same 14 px, same --text-2, small aligned chevrons);
  // the toolbar is ONE line — search, new article, new category, golden
  // check; labels sit LEFT of narrow inputs; dates are shown friendly.
  var css =
    ".kn-wrap{min-width:0;container:knowledge / inline-size;}" +
    ".kn-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 18px;}.kn-toolbar .kn-search{flex:1 1 220px;max-width:420px;}.kn-toolbar .btn{padding:0 14px;font-size:var(--adm-btn-font,14px);white-space:nowrap;}" +
    ".kn-search{box-sizing:border-box;padding:8px 12px;border:1px solid var(--border-strong);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:16px;}" +
    ".kn-layout{display:grid;grid-template-columns:minmax(220px,260px) minmax(0,1fr);gap:32px;align-items:start;}.kn-list,.kn-editor{min-width:0;box-sizing:border-box;}" +
    // The tree's chevron is a 44px button whose icon sits ON the panel's left
    // edge, where the title and the search start. Its touch zone reaches one
    // gutter to the left (never past the phone's screen edge) and 8px over the
    // name's own padding to the right.
    ".kn-tree{display:flex;flex-direction:column;gap:1px;}.kn-category-row{display:grid;grid-template-columns:20px minmax(0,1fr);align-items:center;}.kn-toggle{position:relative;z-index:1;display:inline-flex;align-items:center;justify-content:flex-start;box-sizing:border-box;width:44px;height:44px;margin:-6px 0 -6px calc(-1 * var(--adm-gutter,16px));padding:0 0 0 var(--adm-gutter,16px);border:0;border-radius:6px;background:transparent;color:var(--hint-ink);font-size:0;cursor:pointer;}.kn-toggle:hover{color:var(--hover-ink);}.kn-toggle:focus-visible{outline:2px solid var(--accent);outline-offset:1px;}.kn-toggle span{display:block;width:12px;height:12px;background:currentColor;-webkit-mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;transition:transform .15s ease;}.kn-toggle[aria-expanded=false] span{transform:rotate(-90deg);}" +
    ".kn-children{display:flex;flex-direction:column;gap:1px;}.kn-children[hidden]{display:none;}.kn-node{display:block;min-height:32px;box-sizing:border-box;padding:6px 10px;border-radius:8px;color:var(--hint-ink,var(--text-2));font-size:14px;line-height:1.4;overflow-wrap:anywhere;text-decoration:none;}.kn-node:hover{background:var(--hover-bg);color:var(--hover-ink);}.kn-node.on{background:var(--panel);color:var(--text);font-weight:650;}.kn-node--article{margin-left:20px;}.kn-node small{display:none;}" +
    ".kn-empty{margin:12px 0 0;color:var(--hint-ink);font-size:14px;line-height:1.5;}.kn-link{color:var(--accent);background:none;border:0;padding:0;font:inherit;cursor:pointer;}.kn-link:hover{color:var(--accent-hover);}" +
    ".kn-form{display:flex;flex-direction:column;align-items:stretch;gap:16px;min-height:0;}.kn-title-row{display:flex;align-items:center;gap:8px;min-width:0;}.kn-title{min-width:0;flex:1 1 auto;width:100%;min-height:48px;resize:none;overflow:hidden;border:0;border-bottom:1px solid var(--border-strong);border-radius:0;padding:6px 0;background:transparent;color:var(--text);font:700 clamp(24px,2.2vw,28px)/1.2 var(--font);text-align:left;}.kn-title:focus{outline:2px solid var(--accent);outline-offset:4px;}.kn-title-generate{flex:0 0 auto;width:44px;min-height:44px;padding:0;}.kn-title-generate-ico{display:block;width:22px;height:22px;margin:auto;background:currentColor;-webkit-mask:url(/static/icons/chat-sparkles-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/chat-sparkles-sf-regular.svg) center/contain no-repeat;}.kn-generate-state{display:none;margin:0;color:var(--hint-ink);font-size:15px;}.kn-generate-state:not(:empty){display:block;}.kn-generate-state[data-state=error]{color:var(--rojo);}" +
    ".kn-field{display:flex;flex-direction:column;gap:5px;color:var(--hint-ink,var(--text-2));font-size:15px;}.kn-field input,.kn-field select,.kn-field textarea,.kn-row input,.kn-row select{box-sizing:border-box;max-width:100%;min-height:38px;padding:8px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:16px;}.kn-field textarea{width:100%;min-height:100px;resize:vertical;line-height:1.45;}.kn-field--summary textarea{min-height:70px;}.kn-field--content{flex:1 1 auto;min-height:0;gap:0;}.kn-field--content textarea{min-height:clamp(280px,48vh,640px);height:100%;}" +
    ".kn-rows{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:10px 14px;align-items:center;}.kn-row{display:contents;}.kn-row>span{color:var(--hint-ink,var(--text-2));font-size:15px;white-space:nowrap;}.kn-row>div{display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0;}.kn-row select{width:auto;max-width:100%;}.kn-row input[type=date]{width:170px;}.kn-row input[type=number]{width:7ch;}.kn-row input[type=url],.kn-row input[name=tags]{width:100%;max-width:62ch;}.kn-when{color:var(--hint-ink);font-size:15px;}" +
    ".kn-sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}.kn-optional{margin:0;border:0;background:transparent;}.kn-optional>summary{width:max-content;min-height:40px;padding:8px 0;color:var(--hint-ink);cursor:pointer;font-size:15px;}.kn-optional-body{display:grid;gap:14px;padding:8px 0 0;}" +
    ".kn-validation{margin:0;color:var(--rojo);font-size:15px;}.kn-actions{display:flex;justify-content:flex-start;align-items:center;gap:10px;flex-wrap:wrap;margin-top:0;}.kn-save{min-height:48px!important;padding:10px 18px!important;font-weight:700;}.kn-more{position:relative;margin-left:auto;}.kn-more>summary{display:flex;align-items:center;justify-content:center;width:40px;height:40px;list-style:none;cursor:pointer;border:1px solid var(--border-strong);border-radius:8px;color:var(--text);font-size:23px;line-height:1;}.kn-more>summary::-webkit-details-marker{display:none;}.kn-more-menu{position:absolute;z-index:2;right:0;bottom:calc(100% + 6px);display:grid;gap:4px;min-width:max-content;padding:6px;border:1px solid var(--border);border-radius:8px;background:var(--panel);box-shadow:var(--shadow-pop);}.kn-more:not([open]) .kn-more-menu{display:none;}.kn-more-menu .btn{min-height:var(--adm-btn-h,36px);font-size:var(--adm-btn-font,14px);white-space:nowrap;}" +
    ".kn-save-state{display:none;font-size:15px;color:var(--hint-ink);}.kn-save-state[data-state=error]{display:block;color:var(--rojo);}.kn-save-state[data-state=dirty],.kn-save-state[data-state=saving],.kn-save-state[data-state=success]{display:block;}.kn-editor-close{display:none;}" +
    ".kn-about{max-width:62ch;color:var(--text);font-size:16px;line-height:1.55;}.kn-about summary{cursor:pointer;min-height:44px;line-height:44px;}.kn-about p{margin:0 0 12px;}.kn-about ul{margin:0;padding-left:20px;}.kn-about li{margin:0 0 6px;}" +
    ".kn-load-error{display:flex;align-items:center;gap:12px;flex-wrap:wrap;color:var(--text);font-size:16px;}.kn-load-error .btn{min-height:44px;font-size:16px;}" +
    ".kn-golden{margin:0 0 18px;}.kn-golden-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:16px;color:var(--text);}.kn-golden-head[data-state=fail]{color:var(--rojo);}.kn-golden-list{margin:8px 0 0;padding:0;list-style:none;display:grid;gap:4px;}.kn-golden-list li{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px;font-size:15px;color:var(--text);line-height:1.4;}.kn-golden-list li.fail{color:var(--rojo);}.kn-golden-mark{display:block;width:18px;height:18px;margin-top:2px;background:currentColor;-webkit-mask:url(/static/icons/golden-ok-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/golden-ok-sf-regular.svg) center/contain no-repeat;}.kn-golden-list li.fail .kn-golden-mark{-webkit-mask-image:url(/static/icons/golden-fail-sf-regular.svg);mask-image:url(/static/icons/golden-fail-sf-regular.svg);}" +
    "body.kn-modal-open{overflow:hidden;}" +
    "@container knowledge (max-width:900px){.kn-layout{grid-template-columns:minmax(0,1fr);}.kn-editor:not(.is-open){display:none;}.kn-editor.is-open{position:fixed;inset:0;z-index:1000;width:100dvw;height:100dvh;max-width:none;max-height:none;overflow:auto;border:0;border-radius:0;padding:clamp(16px,4vw,36px);background:var(--bg);}.kn-editor.is-open .kn-form{width:100%;min-height:calc(100dvh - 72px);}.kn-editor.is-open .kn-field--content{display:flex;flex:1 1 auto;}.kn-editor.is-open .kn-field--content textarea{flex:1 1 auto;height:auto;}.kn-editor-close{display:inline-flex;}}" +
    "@container knowledge (max-width:600px){.kn-editor.is-open{padding:16px;}.kn-rows{grid-template-columns:minmax(0,1fr);}.kn-row>span{white-space:normal;}.kn-toolbar .kn-search{flex-basis:100%;max-width:none;}.kn-save{min-height:48px!important;}}";
  var style = document.createElement("style"); style.textContent = css; document.head.appendChild(style);

  // Escapa TAMBIÉN comillas: esc() se interpola en value="..." y el truco del
  // div.innerHTML no codifica " ni ' — un título con comillas rompía el
  // atributo (XSS). Mismo escaper que admin-content.js.
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function sec() { return document.getElementById("sec-knowledge"); }
  function selectedItemId() {
    return new URLSearchParams(location.search).get("item") || "";
  }
  function itemHref(id) { return location.pathname + "?item=" + encodeURIComponent(id); }
  function setDirectItemUrl(id) {
    var url = new URL(window.location.href);
    if (id) url.searchParams.set("item", id);
    else url.searchParams.delete("item");
    history.pushState(null, "", url.pathname + url.search);
  }
  function endpoint(id) { return "/api/admin/content/knowledge" + (id ? "/" + encodeURIComponent(id) : ""); }
  function api(path, opts, owner) {
    var epoch = lifecycleEpoch;
    function current() { return isCurrent(epoch) && (!owner || owner()); }
    opts = opts || {}; opts.credentials = "same-origin";
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    return fetch(path, opts).then(function (r) {
      if (!current()) throw cancelled();
      var boundary = (r.status === 401 || r.status === 403) ? sessionReady.then(function (mod) {
        if (!current()) throw cancelled();
        return mod.confirmar(r, undefined, current);
      }) : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        if (!current()) throw cancelled();
        if (transitioned) throw new Error("401");
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!current()) throw cancelled();
          if (!r.ok) throw new Error(body.error || body.mensaje || T("admin.content.knowledge.api_error_generico", null, "No se pudo guardar."));
          return body;
        });
      });
    }).catch(function (error) { if (!current() && !error.cancelled) throw cancelled(); throw error; });
  }
  function activeItems() { return state.items; }
  function find(id) { return state.items.find(function (item) { return item.id === id; }) || null; }
  function norm(value) { return String(value || "").toLocaleLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, ""); }
  function search(item, q) { return !q || norm([item.title, item.summary, item.content, (item.tags || []).join(" ")].join(" ")).indexOf(q) >= 0; }
  function titleFor(id) { var item = find(id); return item ? item.title : T("admin.content.knowledge.sin_categoria", null, "Sin categoría"); }
  function renderTree() {
    var root = sec(); if (!root) return;
    var q = norm((root.querySelector("#kn-search") || {}).value || "").trim();
    var matched = activeItems().filter(function (item) { return search(item, q); });
    var parentIds = new Set(matched.map(function (item) { return item.parent_id; }).filter(Boolean));
    var all = activeItems().filter(function (item) {
      return matched.indexOf(item) >= 0 || parentIds.has(item.id);
    });
    var cats = all.filter(function (item) { return item.type === "categoria"; });
    var articles = all.filter(function (item) { return item.type === "articulo"; });
    var tree = root.querySelector("#kn-tree");
    var html = "";
    cats.forEach(function (cat) {
      var children = articles.filter(function (article) { return article.parent_id === cat.id; });
      var collapsed = !q && state.collapsed.has(cat.id);
      html += '<div class="kn-category"><div class="kn-category-row"><button class="kn-toggle" type="button" data-kn-toggle="' + esc(cat.id) + '" aria-expanded="' + String(!collapsed) + '" aria-label="' + (collapsed ? T("admin.content.knowledge.expandir", null, "Expandir ") : T("admin.content.knowledge.contraer", null, "Contraer ")) + esc(cat.title) + '"><span aria-hidden="true"></span></button><a class="kn-node kn-node--category' + (state.selected === cat.id ? " on" : "") + '" href="' + esc(itemHref(cat.id)) + '">' + esc(cat.title) + '</a></div><div class="kn-children"' + (collapsed ? " hidden" : "") + '>';
      children.forEach(function (article) { html += node(article); });
      html += '</div></div>';
    });
    articles.filter(function (article) { return !article.parent_id || !find(article.parent_id); }).forEach(function (article) { html += node(article); });
    // Progressive disclosure (the product owner, 2026-10-26): an empty wiki shows no search
    // and no conversation check, only what goes here; a search that matches
    // nothing keeps the search and offers to clear it.
    var view = Filters.applyDisclosure(root, Filters.disclosure({ total: activeItems().length, shown: all.length, filtered: !!q }));
    if (view === "empty") html = Filters.emptyStateHtml({ title: T("admin.content.knowledge.empty_wiki", null, "Todavía no hay artículos: el asistente del chat responde a los pacientes con lo que escribas acá.") });
    else if (view === "no-match") html = Filters.noMatchHtml({ title: T("admin.content.knowledge.empty_busqueda", null, "Ningún artículo coincide con la búsqueda."), clearLabel: T("admin.content.knowledge.limpiar_busqueda", null, "Limpiar búsqueda") });
    tree.innerHTML = html;
    var clear = tree.querySelector("[data-clear-filters]");
    if (clear) clear.addEventListener("click", function () { var input = root.querySelector("#kn-search"); input.value = ""; renderTree(); input.focus(); });
    tree.querySelectorAll("a.kn-node").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        rememberReturnFocus(link);
        history.pushState(null, "", link.getAttribute("href"));
        renderEditor();
      });
    });
    tree.querySelectorAll("[data-kn-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        var id = button.dataset.knToggle;
        if (state.collapsed.has(id)) state.collapsed.delete(id); else state.collapsed.add(id);
        renderTree();
      });
    });
  }
  function node(item) { return '<a class="kn-node kn-node--article' + (state.selected === item.id ? " on" : "") + '" href="' + esc(itemHref(item.id)) + '">' + esc(item.title) + '<small>' + esc(JURIS[item.jurisdiction] || item.jurisdiction || "") + (item._borrado ? " · " + T("admin.content.knowledge.deshabilitado", null, "Deshabilitado") : "") + '</small></a>'; }
  function options(selected, type) { return activeItems().filter(function (item) { return !type || item.type === type; }).map(function (item) { return '<option value="' + esc(item.id) + '"' + (selected === item.id ? " selected" : "") + '>' + esc(item.title) + '</option>'; }).join(""); }
  function form(item, isNew) {
    item = Object.assign({ type: "articulo", title: "", parent_id: "", jurisdiction: "pinamar", summary: "", content: "", tags: [], status: "borrador", source: {} }, item || {});
    var source = item.source || {}, category = item.type === "categoria";
    var hidden = '<input type="hidden" name="id" value="' + esc(item.id || "") + '"><input type="hidden" name="type" value="' + esc(item.type) + '">';
    var title = '<div class="kn-title-row"><textarea class="kn-title" name="title" rows="1" required aria-label="' + esc(T("admin.content.knowledge.titulo_label", null, "Título")) + '" placeholder="' + esc(T("admin.content.knowledge.sin_titulo", null, "Sin título")) + '">' + esc(item.title) + '</textarea>' + (category ? '' : '<button class="btn kn-title-generate" type="button" data-kn-title aria-label="' + esc(T("admin.content.knowledge.generar_titulo", null, "Generar título")) + '" title="' + esc(T("admin.content.knowledge.generar_titulo", null, "Generar título")) + '"><span class="kn-title-generate-ico" aria-hidden="true"></span></button>') + '<button class="kn-editor-close hr-x" type="button" data-kn-close aria-label="' + esc(T("admin.content.knowledge.cerrar", null, "Cerrar")) + '" title="' + esc(T("admin.content.knowledge.cerrar", null, "Cerrar")) + '"></button></div><p class="kn-generate-state" id="kn-generate-state" aria-live="polite"></p>';
    // Label LEFT of every narrow control (selects, dates, numbers, tags, URL)
    // in one two-column grid; only the two textareas keep a label above.
    function row(label, control, name) { return '<label class="kn-row" for="kn-' + name + '"><span>' + label + '</span><div>' + control + '</div></label>'; }
    function dateCtl(name, value) { return '<input id="kn-' + name + '" name="' + name + '" type="date" value="' + esc(value) + '"><span class="kn-when" data-kn-when="' + name + '">' + esc(whenText(value)) + '</span>'; }
    var rows = "";
    if (!category) rows += row(T("admin.content.knowledge.categoria_label", null, "Categoría"), '<select id="kn-parent_id" name="parent_id"><option value="">' + T("admin.content.knowledge.sin_categoria", null, "Sin categoría") + '</option>' + options(item.parent_id, "categoria") + '</select>', "parent_id");
    rows += row(T("admin.content.knowledge.jurisdiccion_label", null, "Jurisdicción"), '<select id="kn-jurisdiction" name="jurisdiction">' + Object.keys(JURIS).map(function (key) { return '<option value="' + key + '"' + (item.jurisdiction === key ? " selected" : "") + '>' + JURIS[key] + '</option>'; }).join("") + '</select>', "jurisdiction");
    rows += row(T("admin.content.knowledge.estado_label", null, "Estado"), '<select id="kn-status" name="status"><option value="borrador"' + (item.status === "borrador" ? " selected" : "") + '>' + T("admin.content.knowledge.borrador", null, "Borrador") + '</option><option value="publicado"' + (item.status === "publicado" ? " selected" : "") + '>' + T("admin.content.knowledge.publicado", null, "Publicado") + '</option></select>', "status");
    rows += row(T("admin.content.knowledge.etiquetas_label", null, "Etiquetas"), '<input id="kn-tags" name="tags" value="' + esc((item.tags || []).join(", ")) + '" placeholder="' + esc(T("admin.content.knowledge.tags_placeholder", null, "turnos, derivación")) + '">', "tags");
    if (!category) {
      rows += row(T("admin.content.knowledge.fuente_oficial_label", null, "Fuente oficial"), '<input id="kn-source_url" name="source_url" type="url" value="' + esc(source.url) + '" placeholder="https://"><input type="hidden" name="source_title" value="' + esc(source.title) + '"><input type="hidden" name="source_agency" value="' + esc(source.agency) + '">', "source_url");
      rows += row(T("admin.content.knowledge.verificada_el", null, "Verificada el "), dateCtl("source_verified_at", source.verified_at), "source_verified_at");
      rows += row(T("admin.content.knowledge.revisar_cada", null, "Revisar cada "), '<input id="kn-source_review_every_days" name="source_review_every_days" type="number" min="1" value="' + esc(source.review_every_days || 90) + '"><span>' + T("admin.content.knowledge.dias_sufijo", null, " días") + '</span><span class="kn-when" data-kn-when="review">' + esc(reviewText(source.verified_at, source.review_every_days || 90)) + '</span>', "source_review_every_days");
      rows += row(T("admin.content.knowledge.vigente_desde_label", null, "Vigente desde"), dateCtl("valid_from", item.valid_from), "valid_from");
      rows += row(T("admin.content.knowledge.vigente_hasta_label", null, "Vigente hasta"), dateCtl("valid_to", item.valid_to), "valid_to");
    }
    var details = '<details class="kn-optional"><summary>' + T("admin.content.knowledge.detalles_opcionales", null, "Detalles opcionales") + '</summary><div class="kn-optional-body"><label class="kn-field kn-field--summary">' + T("admin.content.knowledge.resumen_label", null, "Resumen") + '<textarea name="summary">' + esc(item.summary) + '</textarea></label><div class="kn-rows">' + rows + '</div></div></details>';
    var body = category ? details : '<label class="kn-field kn-field--content"><span class="kn-sr-only">' + T("admin.content.knowledge.contenido_label", null, "Contenido") + '</span><textarea name="content" required aria-label="' + esc(T("admin.content.knowledge.contenido_label", null, "Contenido")) + '">' + esc(item.content) + '</textarea></label>' + details;
    var more = (!isNew && (item._borrado || item.id)) ? '<details class="kn-more"><summary aria-label="' + esc(T("admin.content.knowledge.mas_acciones", null, "Más acciones")) + '">⋯</summary><div class="kn-more-menu">' + (item._borrado ? '<button class="btn" type="button" data-kn-restore>' + T("admin.content.knowledge.restaurar_btn", null, "Restaurar") + '</button>' : '<button class="btn" type="button" data-kn-delete>' + T("admin.content.knowledge.deshabilitar_btn", null, "Deshabilitar") + '</button>') + '</div></details>' : '';
    return '<form class="kn-form" id="kn-form" novalidate>' + hidden + title + body + '<p class="kn-validation" id="kn-validation" hidden></p><div class="kn-actions"><button class="btn btn-accent kn-save" type="submit">' + (isNew ? T("admin.content.knowledge.crear_btn", null, "Crear") : T("admin.content.knowledge.guardar_btn", null, "Guardar")) + '</button><span class="kn-save-state" id="kn-save-state" aria-live="polite"></span>' + more + '</div></form>';
  }
  function dialogFocusable(root) {
    return Array.prototype.filter.call(root.querySelectorAll("a[href],summary,button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])"), function (el) {
      return el.type !== "hidden" && el.getClientRects().length > 0;
    });
  }
  function rememberReturnFocus(el) {
    el = el || document.activeElement;
    dialog.returnHref = "";
    dialog.returnId = "";
    dialog.returnNew = "";
    if (!el || el === document.body || el === document.documentElement) return;
    dialog.returnId = el.id || "";
    dialog.returnHref = (el.getAttribute && el.getAttribute("href")) || "";
    dialog.returnNew = (el.getAttribute && el.getAttribute("data-kn-new")) || "";
  }
  function restoreReturnFocus() {
    var root = sec(), el = null, i, nodes;
    if (dialog.returnId) el = document.getElementById(dialog.returnId);
    if (!el && dialog.returnHref && root) {
      nodes = root.querySelectorAll("a.kn-node");
      for (i = 0; i < nodes.length; i++) {
        if (nodes[i].getAttribute("href") === dialog.returnHref) { el = nodes[i]; break; }
      }
    }
    if (!el && dialog.returnNew && root) {
      el = root.querySelector('.kn-toolbar [data-kn-new="' + dialog.returnNew + '"]') || document.querySelector('.adm-h1row > [data-kn-new="' + dialog.returnNew + '"]') || root.querySelector('[data-kn-new="' + dialog.returnNew + '"]');
    }
    dialog.returnHref = "";
    dialog.returnId = "";
    dialog.returnNew = "";
    if (el && el.focus) el.focus();
  }
  function rememberHistoryReturnFocus() {
    var id = selectedItemId();
    if (!id) return;
    dialog.returnId = "";
    dialog.returnHref = itemHref(id);
    dialog.returnNew = "";
  }
  function setCoveredInert(editor, on) {
    var i, node, parent, child;
    for (i = 0; i < dialog.inerted.length; i++) dialog.inerted[i].removeAttribute("inert");
    dialog.inerted = [];
    if (!on || !editor) return;
    node = editor;
    while (node && node !== document.body) {
      parent = node.parentElement;
      if (!parent) break;
      for (i = 0; i < parent.children.length; i++) {
        child = parent.children[i];
        if (child !== node && !child.hasAttribute("inert")) {
          child.setAttribute("inert", "");
          dialog.inerted.push(child);
        }
      }
      node = parent;
    }
  }
  function enterDialogFocus(editor) {
    var formEl = editor.querySelector("#kn-form");
    var first = (formEl && dialogFocusable(formEl)[0]) || editor.querySelector("[data-kn-close]") || editor;
    editor.setAttribute("tabindex", "-1");
    if (first && first.focus) first.focus();
  }
  function releaseDialog() {
    setCoveredInert(null, false);
    document.body.classList.remove("kn-modal-open");
    if (dialog.keydown) {
      document.removeEventListener("keydown", dialog.keydown);
      dialog.keydown = null;
    }
  }
  function onDialogKey(e) {
    var editor = document.getElementById("kn-editor");
    if (!editor || editor.getAttribute("role") !== "dialog") return;
    if (e.key === "Escape") { e.preventDefault(); closeEditor(); return; }
    if (e.key !== "Tab") return;
    var items = dialogFocusable(editor);
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first || !editor.contains(document.activeElement)) { e.preventDefault(); last.focus(); }
    } else if (document.activeElement === last || !editor.contains(document.activeElement)) {
      e.preventDefault(); first.focus();
    }
  }
  function syncEditorMode() {
    var root = sec(); if (!root) return;
    var wrap = root.querySelector(".kn-wrap"), editor = root.querySelector("#kn-editor");
    if (!wrap || !editor) return;
    var titleForm = editor.querySelector("#kn-form"); if (titleForm) autoGrowTitle(titleForm);
    var wasPopup = document.body.classList.contains("kn-modal-open");
    var popup = wrap.getBoundingClientRect().width <= 900 && editor.classList.contains("is-open");
    document.body.classList.toggle("kn-modal-open", popup);
    if (popup) {
      editor.setAttribute("role", "dialog");
      editor.setAttribute("aria-modal", "true");
      editor.setAttribute("aria-label", T("admin.content.knowledge.wiki", null, "Wiki"));
      setCoveredInert(editor, true);
      if (!dialog.keydown) {
        dialog.keydown = onDialogKey;
        document.addEventListener("keydown", onDialogKey);
      }
      if (!editor.contains(document.activeElement)) enterDialogFocus(editor);
    } else {
      editor.removeAttribute("role");
      editor.removeAttribute("aria-modal");
      editor.removeAttribute("aria-label");
      editor.removeAttribute("tabindex");
      setCoveredInert(editor, false);
      if (dialog.keydown) {
        document.removeEventListener("keydown", onDialogKey);
        dialog.keydown = null;
      }
      if (wasPopup) restoreReturnFocus();
    }
  }
  // The AI notice (ai.notice.knowledge) is server-rendered hidden above the
  // section; it goes with the AI it covers — the «Generar título» proposal of
  // an article form — and is moved into that form, never shown over the
  // about text, a category or an empty screen.
  var aiNotice = null;
  function syncAiNotice() {
    // Kept by reference: repainting the editor detaches the node it was in.
    var notice = aiNotice = (aiNotice && aiNotice.ownerDocument === document) ? aiNotice : document.querySelector('[data-ai-notice="knowledge"]');
    if (!notice) return;
    var formEl = sec() && sec().querySelector("#kn-form");
    var covers = !!(formEl && formEl.querySelector("[data-kn-title]"));
    if (covers) formEl.insertBefore(notice, formEl.firstChild);
    notice.hidden = !covers;
  }
  function openNew(type) { var editor = sec().querySelector("#kn-editor"); state.selected = null; renderTree(); editor.classList.add("is-open"); editor.innerHTML = form((state.drafts && state.drafts.get("new:" + type)) || { type: type }, true); wireEditor(); syncAiNotice(); syncEditorMode(); }
  function closeEditor() {
    setDirectItemUrl(""); renderEditor();
  }
  function renderEditor() { var editor = sec().querySelector("#kn-editor"); var id = selectedItemId(); var item = find(id); state.selected = item ? item.id : null; if (item && item.parent_id) state.collapsed.delete(item.parent_id); editor.classList.toggle("is-open", !!item); editor.innerHTML = item ? form((state.drafts && state.drafts.get(item.id)) || item, false) : about(); renderTree(); wireEditor(); syncAiNotice(); syncEditorMode(); }
  function sourceAgency(url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return ""; } }
  function values(form) { var f = new FormData(form); var url = String(f.get("source_url") || "").trim(), title = String(f.get("title") || "").trim(); return { id: String(f.get("id") || ""), type: f.get("type"), title: title, parent_id: f.get("parent_id") || null, jurisdiction: f.get("jurisdiction"), summary: String(f.get("summary") || "").trim(), content: String(f.get("content") || "").trim(), tags: String(f.get("tags") || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean), status: f.get("status"), source: { title: String(f.get("source_title") || "").trim() || title || "Fuente oficial", url: url, agency: String(f.get("source_agency") || "").trim() || sourceAgency(url), verified_at: f.get("source_verified_at") || null, review_every_days: Number(f.get("source_review_every_days")) || null }, valid_from: f.get("valid_from") || null, valid_to: f.get("valid_to") || null }; }
  function validate(doc) { if (!doc.title) return T("admin.content.knowledge.error_titulo_vacio", null, "Escribí un título."); if (doc.type === "articulo" && !doc.content) return T("admin.content.knowledge.error_contenido_vacio", null, "El artículo necesita contenido."); if (doc.source.url && !/^https:\/\//.test(doc.source.url)) return T("admin.content.knowledge.error_fuente_https", null, "La fuente debe usar una URL HTTPS oficial."); return ""; }
  function isCurrentForm(form, epoch) { return isCurrent(epoch == null ? lifecycleEpoch : epoch) && (!form || sec().querySelector("#kn-form") === form); }
  function setState(text, kind, form) { var el = isCurrentForm(form) && sec().querySelector("#kn-save-state"); if (el) { el.textContent = text; el.dataset.state = kind || ""; } }
  function setGenerateState(text, kind, form) { var el = isCurrentForm(form) && sec().querySelector("#kn-generate-state"); if (el) { el.textContent = text; el.dataset.state = kind || ""; } }
  function autoGrowTitle(form) { var title = form.querySelector(".kn-title"); if (title) { title.style.height = "auto"; title.style.height = Math.max(48, title.scrollHeight) + "px"; } }
  function generateTitle(form) { if (form._propertiesPromise) return form._propertiesPromise; var epoch = lifecycleEpoch, content = String(form.elements.content.value || "").trim(), button = form.querySelector("[data-kn-title]"), requestedTitle = form.elements.title.value, requestedContent = content; if (!content) { setGenerateState(T("admin.content.knowledge.escribi_contenido_primero", null, "Escribí el contenido primero."), "error", form); return Promise.reject(new Error("sin contenido")); } if (button) button.disabled = true; setGenerateState(T("admin.content.knowledge.actualizando", null, "Actualizando…"), null, form); var current = values(form); var categories = activeItems().filter(function (item) { return item.type === "categoria"; }).map(function (item) { return { id: item.id, title: item.title }; }); form._propertiesPromise = api("/api/admin/content/knowledge/properties", { method: "POST", body: JSON.stringify({ content: content, categories: categories, current: { title: current.title, summary: current.summary, tags: current.tags, parent_id: current.parent_id, jurisdiction: current.jurisdiction } }) }).then(function (out) { if (!isCurrentForm(form, epoch)) throw cancelled(); if (out.properties && out.properties.title && form.elements.title.value === requestedTitle && String(form.elements.content.value || "").trim() === requestedContent) { form.elements.title.value = out.properties.title; autoGrowTitle(form); markDraft(form); setGenerateState(T("admin.content.knowledge.titulo_generado", null, "Título generado."), null, form); } else setGenerateState(T("admin.content.knowledge.titulo_no_reemplazado", null, "El borrador cambió; el título no se reemplazó."), "error", form); return out.properties; }).catch(function (err) { if (!err.cancelled && isCurrentForm(form, epoch)) setGenerateState(errText(err, T("admin.content.knowledge.no_se_pudieron_actualizar", null, "No se pudieron actualizar.")), "error", form); throw err; }).finally(function () { form._propertiesPromise = null; if (button && isCurrentForm(form, epoch)) button.disabled = false; }); return form._propertiesPromise; }
  function save(form, creating) { var epoch = lifecycleEpoch, submittedKey = draftKey(form), doc = values(form), error = validate(doc), errorEl = sec().querySelector("#kn-validation"), button = form.querySelector(".kn-save"); if (form._saving) return Promise.resolve(null); if (error) { errorEl.hidden = false; errorEl.textContent = error; setState(T("admin.content.knowledge.revisa_formulario", null, "Revisá el formulario"), "error", form); return Promise.reject(new Error(error)); } errorEl.hidden = true; form._saving = true; if (button) button.disabled = true; setState(T("admin.content.knowledge.guardando", null, "Guardando…"), "saving", form); return api(creating ? endpoint() : endpoint(doc.id), { method: creating ? "POST" : "PUT", body: JSON.stringify(doc) }).then(function (out) { if (!isCurrentForm(form, epoch)) throw cancelled(); var saved = out && out.item; if (!out || out.ok !== true || !saved || typeof saved.id !== "string" || !saved.id || (!creating && saved.id !== doc.id) || saved.type !== doc.type || typeof saved.title !== "string" || (saved.type !== "categoria" && typeof saved.content !== "string")) throw new Error(T("admin.content.knowledge.api_error_generico", null, "No se pudo guardar.")); var index = state.items.findIndex(function (item) { return item.id === saved.id; }), current = values(form), latestDraft = state.drafts && state.drafts.get(submittedKey), changedDuringSave = JSON.stringify(current) !== JSON.stringify(doc) || (!!latestDraft && JSON.stringify(latestDraft) !== JSON.stringify(doc)), retained = current; if (index >= 0) state.items[index] = saved; else state.items.push(saved); if (creating) { form.elements.id.value = saved.id; retained.id = saved.id; setDirectItemUrl(saved.id); if (state.drafts) state.drafts.delete(submittedKey); } if (changedDuringSave) { state.drafts = state.drafts || new Map(); state.drafts.set(draftKey(form), retained); setState(T("admin.content.knowledge.cambios_sin_guardar", null, "Cambios sin guardar"), "dirty", form); } else { if (state.drafts) state.drafts.delete(draftKey(form)); setState(T("admin.content.knowledge.guardado", null, "Guardado"), "success", form); } if (creating && !changedDuringSave) { renderEditor(); } else { state.selected = saved.id; renderTree(); if (creating && button) button.textContent = T("admin.content.knowledge.guardar_btn", null, "Guardar"); } return saved; }).catch(function (err) { if (!err.cancelled && isCurrentForm(form, epoch)) setState(err.message === "demo_solo_lectura" ? errText(err, "") : T("admin.content.knowledge.error_al_guardar", null, "Error al guardar"), "error", form); throw err; }).finally(function () { form._saving = false; if (button && isCurrentForm(form, epoch)) button.disabled = false; }); }
  function draftKey(form) { return form.elements.id.value || "new:" + form.elements.type.value; }
  function markDraft(form) { state.drafts = state.drafts || new Map(); state.drafts.set(draftKey(form), values(form)); setState(T("admin.content.knowledge.cambios_sin_guardar", null, "Cambios sin guardar"), "dirty"); }
  function wireEditor() { var root = sec(), editor = root.querySelector("#kn-editor"); editor.querySelectorAll("[data-kn-new]").forEach(function (button) { button.addEventListener("click", function () { rememberReturnFocus(button); openNew(button.dataset.knNew); }); }); var close = editor.querySelector("[data-kn-close]"); if (close) close.addEventListener("click", closeEditor); var formEl = editor.querySelector("#kn-form"); if (!formEl) return; var creating = !formEl.elements.id.value, generate = formEl.querySelector("[data-kn-title]"); if (generate) generate.addEventListener("click", function () { generateTitle(formEl).catch(function () {}); }); formEl.addEventListener("input", function () { autoGrowTitle(formEl); markDraft(formEl); refreshWhen(formEl); }); formEl.addEventListener("change", function () { markDraft(formEl); refreshWhen(formEl); }); var readyEpoch = lifecycleEpoch; datesReady.then(function () { if (isCurrentForm(formEl, readyEpoch)) refreshWhen(formEl); }); autoGrowTitle(formEl); formEl.addEventListener("submit", function (event) { event.preventDefault(); save(formEl, !formEl.elements.id.value).catch(function () {}); }); var remove = editor.querySelector("[data-kn-delete]"); if (remove) remove.addEventListener("click", function () { var epoch = lifecycleEpoch, key = draftKey(formEl), dirty = !!(state.drafts && state.drafts.has(key)), controls; if (formEl._deleting || (dirty && !window.confirm(T("admin.content.knowledge.confirmar_descartar_y_deshabilitar", null, "Tenés cambios sin guardar. ¿Descartarlos y deshabilitar este artículo?")))) return; formEl._deleting = true; controls = Array.prototype.slice.call(formEl.querySelectorAll("input, textarea, select, button")); controls.forEach(function (control) { control.disabled = true; }); api(endpoint(formEl.elements.id.value), { method: "DELETE" }).then(function () { if (!isCurrentForm(formEl, epoch)) throw cancelled(); if (state.drafts) state.drafts.delete(key); setDirectItemUrl(""); return load(); }).catch(function (err) { if (err.cancelled || !isCurrentForm(formEl, epoch)) return; formEl._deleting = false; controls.forEach(function (control) { control.disabled = false; }); setState(errText(err, T("admin.content.knowledge.error_al_guardar", null, "Error al guardar")), "error", formEl); }); }); var restore = editor.querySelector("[data-kn-restore]"); if (restore) restore.addEventListener("click", function () { var epoch = lifecycleEpoch; api(endpoint(formEl.elements.id.value) + "/restaurar", { method: "POST", body: "{}" }).then(function () { if (!isCurrentForm(formEl, epoch)) throw cancelled(); return load(); }).catch(function (err) { if (!err.cancelled && isCurrentForm(formEl, epoch)) setState(errText(err, T("admin.content.knowledge.error_al_guardar", null, "Error al guardar")), "error", formEl); }); }); }
  var readSequence = 0, releaseRead = null;
  function load() {
    if (releaseRead) releaseRead();
    var root = sec(), epoch = lifecycleEpoch, sequence = ++readSequence;
    if (!root || !isCurrent(epoch)) return Promise.resolve();
    var priorInert = root.getAttribute("inert"), priorBusy = root.getAttribute("aria-busy"), released = false, retired = false, timer;
    root.setAttribute("inert", ""); root.setAttribute("aria-busy", "true");
    function current() { return !retired && isCurrent(epoch) && sequence === readSequence && root === sec() && root.isConnected; }
    function release() {
      if (released) return;
      released = true;
      clearTimeout(timer);
      if (priorInert === null) root.removeAttribute("inert"); else root.setAttribute("inert", priorInert);
      if (priorBusy === null) root.removeAttribute("aria-busy"); else root.setAttribute("aria-busy", priorBusy);
    }
    function retire() {
      retired = true;
      release();
      if (releaseRead === retire) releaseRead = null;
    }
    releaseRead = retire;
    var deadline = new Promise(function (_, reject) { timer = setTimeout(function () { reject(new Error("knowledge read deadline")); }, 8000); });
    return Promise.race([api(endpoint(), undefined, current), deadline]).then(function (data) {
      if (!current()) return;
      if (!data || !Array.isArray(data.items)) throw new Error("invalid knowledge list");
      state.items = data.items;
      if (!state.treeInitialized) {
        state.items.filter(function (item) { return item.type === "categoria"; }).forEach(function (item) { state.collapsed.add(item.id); });
        state.treeInitialized = true;
      }
      // Publish a usable editor only after its data is ready; dialog focus
      // must run after this owner releases the root.
      release();
      if (state.items.length) renderEditor(); else openNew("articulo");
    }).catch(function (err) {
      if (err.cancelled || !current()) return;
      state.items = []; state.treeInitialized = false; state.collapsed.clear(); state.selected = null;
      releaseDialog();
      root.innerHTML = '<p class="kn-load-error" role="alert"><span>' + esc(T("admin.content.common.load_error", null, "No se pudo cargar.")) + '</span><button type="button" class="btn" data-kn-retry>' + esc(T("admin.content.common.reload", null, "Recargar")) + '</button></p>';
      root.querySelector("[data-kn-retry]").addEventListener("click", function () { location.reload(); });
    }).finally(retire);
  }
  function bootKnowledge() { var root = sec(); if (!root) return; if (!root.dataset.ready) { root.dataset.ready = "1"; rememberHistoryReturnFocus(); titleAction(); Filters.applyDisclosure(root, "loading"); root.innerHTML = '<div class="kn-wrap"><div class="kn-toolbar"><input id="kn-search" data-data-tool class="kn-search" type="search" aria-label="' + esc(T("admin.content.knowledge.buscar_en_wiki_label", null, "Buscar en la wiki")) + '" placeholder="' + esc(T("admin.content.knowledge.buscar_placeholder", null, "Buscar")) + '">' + iconButton('data-kn-new="categoria"', "folder-add", T("admin.content.knowledge.nueva_categoria", null, "Nueva categoría")) + iconButton("data-data-tool data-kn-golden", "chat", T("admin.content.knowledge.probar_conversaciones", null, "Probar conversaciones")) + '</div><div id="kn-golden" class="kn-golden" aria-live="polite"></div><div class="kn-layout"><aside class="kn-list" aria-label="' + T("admin.content.knowledge.wiki", null, "Wiki") + '"><div id="kn-tree" class="kn-tree"></div></aside><section class="kn-editor" id="kn-editor"></section></div></div>'; root.querySelector("#kn-search").addEventListener("input", renderTree); root.querySelector("[data-kn-golden]").addEventListener("click", function () { runGolden(this); }); document.querySelectorAll('.kn-toolbar [data-kn-new], .adm-h1row > [data-kn-new]').forEach(function (button) { button.addEventListener('click', function () { rememberReturnFocus(button); openNew(button.dataset.knNew); }); }); if (window.ResizeObserver) { state.resizeObserver = new ResizeObserver(syncEditorMode); state.resizeObserver.observe(root.querySelector(".kn-wrap")); } else window.addEventListener("resize", syncEditorMode); } if (state.items.length) renderEditor(); else return load(); }
  // Why the wiki exists and how to write for it — shown where the editor
  // would be until an article is chosen (the product owner, 2026-09-05). Body type, no
  // small print.
  function about() {
    // One sentence says what the wiki is for; the writing tips wait behind a
    // disclosure (the product owner, 2026-11-15: five times less text).
    return '<div class="kn-about">' +
      '<p>' + T("admin.content.knowledge.about_p1", null, "El asistente del chat responde a los pacientes con lo que está escrito acá. Si no hay artículo, el chat no inventa: deriva a la guardia o al mostrador.") + '</p>' +
      '<p>' + T("admin.content.knowledge.about_p2", null, "Sólo cuenta lo publicado y vigente. Un borrador o un artículo vencido no llega al chat.") + '</p>' +
      '<details><summary>' + T("admin.content.knowledge.about_tips", null, "Cómo escribir un artículo") + '</summary>' +
      '<ul><li>' + T("admin.content.knowledge.tip_1", null, "Un tema por artículo, con la respuesta en las primeras dos frases.") + '</li>' +
      '<li>' + T("admin.content.knowledge.tip_2", null, "Poné en las etiquetas las palabras que usa el paciente, no las del hospital: «sin padres», «gratis», «receta».") + '</li>' +
      '<li>' + T("admin.content.knowledge.tip_3", null, "Fuente oficial siempre que exista, y una fecha de verificación: el chat la cita.") + '</li>' +
      '<li>' + T("admin.content.knowledge.tip_4", null, "Después de editar, tocá «Probar conversaciones»: cada pregunta modelo tiene que seguir encontrando su artículo.") + '</li></ul></details></div>';
  }
  // "Probar conversaciones": the golden set against the live wiki, verdict
  // per question, reasons on the failures.
  function runGolden(button) {
    var epoch = lifecycleEpoch, root = sec(), out = root.querySelector("#kn-golden");
    if (!out) return;
    button.disabled = true;
    out.innerHTML = '<p class="kn-golden-head">' + T("admin.content.knowledge.golden_corriendo", null, "Probando…") + '</p>';
    api(endpoint() + "/golden", { method: "POST", body: "{}" }).then(function (res) {
      if (!isCurrent(epoch) || !root.contains(out)) throw cancelled();
      var results = res.results || [], failed = results.filter(function (r) { return !r.ok; });
      var head = failed.length
        ? Tn("admin.content.knowledge.golden_fallan", failed.length, { total: results.length }, "{n} de {total} conversaciones ya no encuentra su artículo", "{n} de {total} conversaciones ya no encuentran su artículo")
        : Tn("admin.content.knowledge.golden_ok", results.length, { total: results.length }, "La conversación encuentra su artículo", "Las {total} conversaciones encuentran su artículo");
      var html = '<p class="kn-golden-head" data-state="' + (failed.length ? "fail" : "ok") + '">' + esc(head) + ' <button type="button" class="kn-link" data-kn-golden-close>' + T("admin.content.knowledge.cerrar", null, "Cerrar") + '</button></p><ul class="kn-golden-list">';
      results.forEach(function (r) {
        var why = "";
        if (r.missing_articles && r.missing_articles.length) why += " — " + T("admin.content.knowledge.golden_sin_articulo", { ids: r.missing_articles.map(titleFor).join(", ") }, "no aparece {ids}");
        if (r.missing_facts && r.missing_facts.length) why += " — " + T("admin.content.knowledge.golden_sin_dato", { facts: r.missing_facts.join(", ") }, "falta «{facts}»");
        html += '<li class="' + (r.ok ? "ok" : "fail") + '"><span class="kn-golden-mark" role="img" aria-label="' + esc(r.ok ? T("admin.content.knowledge.golden_marca_ok", null, "Encuentra su artículo") : T("admin.content.knowledge.golden_marca_falla", null, "No encuentra su artículo")) + '"></span><span>' + esc(r.question) + esc(why) + '</span></li>';
      });
      out.innerHTML = html + '</ul>';
      var close = out.querySelector("[data-kn-golden-close]");
      if (close) close.addEventListener("click", function () { out.innerHTML = ""; });
    }).catch(function (err) {
      if (!err.cancelled && isCurrent(epoch) && root.contains(out)) out.innerHTML = '<p class="kn-golden-head" data-state="fail">' + esc(errText(err, T("admin.content.knowledge.api_error_generico", null, "No se pudo guardar."))) + '</p>';
    }).finally(function () { if (isCurrent(epoch) && root.contains(button)) button.disabled = false; });
  }
  function hasUnsavedDrafts() { return !!(state.drafts && state.drafts.size); }
  var mounted = false;
  var lifecycleEpoch = 0;
  function isCurrent(epoch) { return mounted && epoch === lifecycleEpoch; }
  function cancelled() { var error = new Error("editorial controller destroyed"); error.cancelled = true; return error; }
  function onBeforeUnload(event) { if (hasUnsavedDrafts()) { event.preventDefault(); event.returnValue = ""; } }
  function onPopstate() { if (mounted && sec()) { rememberHistoryReturnFocus(); renderEditor(); } }
  export function mount() {
    if (mounted) return;
    mounted = true;
    lifecycleEpoch += 1;
    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("popstate", onPopstate);
    var epoch = lifecycleEpoch;
    return sessionReady.then(function () { if (isCurrent(epoch)) return bootKnowledge(); });
  }
  // One screen header (responsable del producto, 2026-11-08): «Nuevo artículo» sits at the right
  // end of the title's own line (.adm-title-action, admin-direct.css); the
  // row's other actions are bare icons whose name is the tooltip.
  function titleAction() {
    var row = document.querySelector(".adm-h1row");
    if (!row || row.querySelector('[data-kn-new="articulo"]')) return;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-accent adm-title-action";
    button.setAttribute("data-kn-new", "articulo");
    button.textContent = T("admin.content.knowledge.nuevo_articulo", null, "Nuevo artículo");
    row.insertBefore(button, row.querySelector(".alert-bell-wrap"));
  }
  function iconButton(attrs, icon, label) {
    return '<button type="button" class="cnt-gear-btn" ' + attrs + ' aria-label="' + esc(label) + '" title="' + esc(label) + '"><span class="adm-icon adm-icon-' + icon + '" aria-hidden="true"></span></button>';
  }
  export function destroy() {
    var titleButton = document.querySelector('.adm-h1row > [data-kn-new="articulo"]');
    if (titleButton && titleButton.parentNode) titleButton.parentNode.removeChild(titleButton);
    mounted = false;
    lifecycleEpoch += 1;
    readSequence += 1;
    if (releaseRead) releaseRead();
    releaseDialog();
    window.removeEventListener("beforeunload", onBeforeUnload);
    window.removeEventListener("popstate", onPopstate);
    if (state.resizeObserver) { state.resizeObserver.disconnect(); state.resizeObserver = null; }
    else window.removeEventListener("resize", syncEditorMode);
    var root = sec();
    if (root) delete root.dataset.ready;
  }
  export var boot = mount;
